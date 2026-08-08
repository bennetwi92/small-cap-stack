"""The fundamentals pass: point-in-time share counts for reconstructed sessions (#563).

The minute-bar harvest (:mod:`.runner`) rebuilds a session's *price* history and stops there — the
bar vendor sells no share data — so every ``recon`` trade in ``books_all`` has carried
``float_shares: None`` since #431. This pass fills ``data/recon/fundamentals/dt=…`` from SEC EDGAR
(:mod:`.edgar`), keyed by the same ``opportunity_id`` the live tracker uses, so
``portfolio.extract`` reads it through the unchanged ``_funds_for`` seam.

## Why it is a separate pass rather than a step inside a session

The two halves are priced completely differently. A minute-bar session is ~217 vendor calls at a
13-second fixed sleep — 47 minutes, the thing that makes the harvest a ~45-night job. EDGAR is free,
capped at 10 req/s, and returns a company's *entire* filing history in one response, so a whole
backfill of every harvested date costs about one call per distinct symbol. Bolting that onto the
session loop would put a cheap job inside an expensive one's deadline arithmetic for no reason, and
would mean a date already harvested before this existed could never be filled in.

## Resume, without a second checkpoint

A date is done when its ``fundamentals`` partition exists. Nothing else records it. That falls out
of two properties the store already has, and is stronger than a checkpoint flag would be:

- the partition is written in **one append at the end of the date**, like every other harvest
  dataset, so a kill leaves the date with no files and it is simply re-planned;
- ``fundamentals`` is in :data:`.runner.HARVEST_DATASETS`, so re-harvesting a date's bars drops its
  share counts too — and because "done" is read off the disk, the next run rebuilds them against the
  new opportunity list rather than leaving rows keyed to symbols that no longer appear.

A row is written for **every** opportunity, including the ones EDGAR has nothing for (share count
``None``). Recording "asked, no answer" as a fact is what makes the pass terminate: an absent row is
indistinguishable from an unharvested date, so writing only the hits would re-ask about every
un-findable name on every future run, forever.

That is only safe because a *transport* failure is never written as a null. It counts as a failure,
and enough of them abandon the date whole (:func:`.runner.abandon_reason`) — the same contract
:func:`.runner.harvest_session` gives. Without that split, one rejected ``User-Agent`` would pin a
permanent null on every symbol of every date it touched.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

from ..config import Settings
from ..fundamentals import AsOfShares, Fundamentals, PointInTimeFundamentals, fundamentals_record
from ..logging import get_logger
from ..storage import Store
from .edgar import EDGAR_SOURCE, EdgarError, EdgarFundamentals
from .runner import abandon_reason

log = get_logger(__name__)

FUNDAMENTALS_DATASET = "fundamentals"

#: The dataset whose partitions say a date has been harvested at all. Planning reads directory
#: names rather than rows: ``Store.append`` skips an empty record list, so a partition that exists
#: has at least one opportunity in it, and a glob costs nothing against a DuckDB read per date.
_SOURCE_DATASET = "opportunities"


class EdgarNotConfigured(EdgarError):
    """No ``User-Agent`` was configured, so every request would 403.

    Raised up front instead of letting the pass discover it once per symbol: SEC's fair-access
    policy requires an identifying contact string, there is no sensible default, and a harvest that
    ran anyway would abandon every date it touched while looking like an SEC outage.
    """


def edgar_source(s: Settings) -> EdgarFundamentals:
    """The configured EDGAR client, or a refusal naming the setting that is missing."""
    ua = s.harvest_edgar_user_agent.strip()
    if not ua:
        raise EdgarNotConfigured(
            "HARVEST_EDGAR_USER_AGENT is not set. SEC's fair-access policy requires a contact "
            "string identifying the requester (e.g. 'small-cap-stack you@example.com'); without "
            "one every EDGAR request is a 403."
        )
    return EdgarFundamentals(user_agent=ua, min_interval_sec=s.harvest_edgar_min_interval_sec)


@dataclass(frozen=True)
class FundamentalsResult:
    """One date's outcome — the progress line, and the split that makes a failure legible."""

    trading_date: date
    opportunities: int
    #: Rows carrying a share count knowable on the session.
    resolved: int
    #: Rows written with a null count — EDGAR answered and had nothing for that name on that date.
    unresolved: int
    #: Symbols EDGAR could not be *reached* for. Deliberately not the same as ``unresolved``: a
    #: filer with no cover-page tag is a legitimate zero, a 403 is not, and only counting the
    #: second lets an outage be told apart from a thin corner of the universe after the fact.
    failed: int
    calls: int
    complete: bool = True  # False when the date was abandoned (nothing was written)

    def line(self) -> str:
        return (
            f"{self.trading_date} opps={self.opportunities} shares={self.resolved} "
            f"none={self.unresolved} failed={self.failed} calls={self.calls}"
            + ("" if self.complete else " INCOMPLETE(discarded)")
        )


def _partition(store: Store, dataset: str, trading_date: date) -> Path:
    return store.data_dir / dataset / f"dt={trading_date.isoformat()}"


def _partition_dates(store: Store, dataset: str) -> set[date]:
    root = store.data_dir / dataset
    if not root.exists():
        return set()
    out: set[date] = set()
    for part in root.glob("dt=*"):
        try:
            out.add(date.fromisoformat(part.name.removeprefix("dt=")))
        except ValueError:  # a stray directory is not a date; ignore rather than crash the plan
            continue
    return out


def plan_fundamentals(store: Store) -> list[date]:
    """Harvested sessions with no share counts yet, **newest-first**.

    Same ordering as the minute-bar harvest and for the same reason (#430): stopping early should
    leave the recent, contiguous end filled rather than a ragged middle.
    """
    pending = _partition_dates(store, _SOURCE_DATASET) - _partition_dates(
        store, FUNDAMENTALS_DATASET
    )
    return sorted(pending, reverse=True)


def discard_partial_fundamentals(store: Store, trading_date: date) -> bool:
    """Drop just this pass's partition for a date, leaving the harvested bars alone.

    :func:`.runner.discard_partial` is the whole-date version, used when a date's *bars* are being
    rebuilt; this is the narrow one, for re-running the fundamentals pass over a date whose bars are
    fine. The store is append-only, so a second run over a date that already has files would *add*
    rows rather than replace them — the day would then carry two share counts per opportunity and
    its cache fingerprint would flip on every rebuild.
    """
    part = _partition(store, FUNDAMENTALS_DATASET, trading_date)
    if not part.exists():
        return False
    shutil.rmtree(part)
    return True


def _record(oid: str, symbol: str, trading_date: date, shares: AsOfShares | None) -> dict[str, Any]:
    """One ``fundamentals`` row — the live schema, plus the provenance that makes it auditable.

    Built from :func:`~..fundamentals.fundamentals_record` rather than by hand, for the reason
    :mod:`.runner` gives about the other record builders: the recon store's whole value is that the
    live readers work on it unchanged, and a hand-rolled schema is one renamed column away from
    silently extracting nothing.

    ``ts_utc`` is **midnight UTC of the trading date**, not a capture time. Live rows are stamped
    with the moment the tracker fetched them; there is no such moment here, and using "now" would
    make a re-run of the same date produce different bytes for the same fact. Midnight UTC is
    deterministic and unmistakably synthetic — a live capture always lands inside the session.

    The dates that carry the meaning are the two extra columns. The live store has neither, and
    does not need them: ``Store.read`` unions by name, and the two roots are read separately anyway.
    """
    f = Fundamentals(
        symbol=symbol,
        float_shares=shares.float_shares if shares else None,
        shares_outstanding=shares.shares_outstanding if shares else None,
        short_percent=None,  # short interest has no historical source wired (#110)
        source=shares.source if shares else EDGAR_SOURCE,
    )
    return {
        **fundamentals_record(oid, f, datetime.combine(trading_date, time.min, tzinfo=UTC)),
        "as_of": shares.as_of if shares else None,
        "filed": shares.filed if shares else None,
        "form": shares.form if shares else None,
    }


def harvest_fundamentals(
    source: PointInTimeFundamentals,
    store: Store,
    s: Settings,
    trading_date: date,
) -> FundamentalsResult:
    """Write one date's point-in-time share counts. Nothing is written unless the date completes."""
    calls_before = source.calls
    opps = store.read(_SOURCE_DATASET, dt=trading_date)
    if opps.is_empty():
        # Not an error and not a completed date: an unharvested date has no opportunities either,
        # and `plan_fundamentals` will keep offering it until its bars land.
        return FundamentalsResult(trading_date, 0, 0, 0, 0, source.calls - calls_before)

    # `opportunity_id` is `date:SYMBOL`, so this is already one row per symbol-day; deduped anyway,
    # because a partition rewritten by hand could hold more.
    symbols: dict[str, str] = {}
    for row in opps.iter_rows(named=True):
        symbols.setdefault(str(row["opportunity_id"]), str(row["symbol"]))

    records: list[dict[str, Any]] = []
    resolved = 0
    failed = 0
    consecutive = 0
    for oid, symbol in symbols.items():
        try:
            shares = source.shares_asof(symbol, trading_date)
        except EdgarError:
            failed += 1
            consecutive += 1
            log.warning(
                "harvest.fundamentals_symbol_failed",
                symbol=symbol,
                date=trading_date.isoformat(),
                exc_info=True,
            )
            # The circuit breaker, same shape as the minute-bar pass. One symbol failing is
            # ordinary; five in a row is SEC (or our User-Agent), and every further attempt spends
            # the retry ladder to learn the same thing again.
            if consecutive >= s.harvest_max_consecutive_failures:
                return _abandoned(
                    trading_date,
                    symbols,
                    failed,
                    source.calls - calls_before,
                    f"{consecutive} consecutive failures",
                )
            continue
        consecutive = 0
        if shares is not None:
            resolved += 1
        records.append(_record(oid, symbol, trading_date, shares))

    why = abandon_reason(len(symbols), failed, s)
    if why is not None:
        return _abandoned(trading_date, symbols, failed, source.calls - calls_before, why)

    store.append(FUNDAMENTALS_DATASET, records, partition_date=trading_date)
    return FundamentalsResult(
        trading_date=trading_date,
        opportunities=len(symbols),
        resolved=resolved,
        unresolved=len(records) - resolved,
        failed=failed,
        calls=source.calls - calls_before,
    )


def _abandoned(
    trading_date: date, symbols: dict[str, str], failed: int, calls: int, why: str
) -> FundamentalsResult:
    log.warning(
        "harvest.fundamentals_abandoned",
        date=trading_date.isoformat(),
        reason=why,
        failed=failed,
        opportunities=len(symbols),
    )
    return FundamentalsResult(
        trading_date=trading_date,
        opportunities=len(symbols),
        resolved=0,
        unresolved=0,
        failed=failed,
        calls=calls,
        complete=False,
    )


def run_fundamentals(
    source: PointInTimeFundamentals,
    store: Store,
    s: Settings,
    dates: Sequence[date],
    *,
    on_result: Callable[[FundamentalsResult], None] | None = None,
) -> list[FundamentalsResult]:
    """Fill share counts for ``dates``, clearing any existing partition first.

    Each date is independent: an abandoned one writes nothing, is not recorded as done, and the next
    date is still attempted. That is deliberately unlike the minute-bar pass, which stops the night
    on the first entitlement wall — there, everything older is equally unbuyable; here, one
    unreachable date says nothing about the next.
    """
    results: list[FundamentalsResult] = []
    for day in dates:
        discard_partial_fundamentals(store, day)
        result = harvest_fundamentals(source, store, s, day)
        results.append(result)
        log.info("harvest.fundamentals", line=result.line())
        if on_result is not None:
            on_result(result)
    return results
