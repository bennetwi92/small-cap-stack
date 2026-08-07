"""The overnight pre-market harvest: sessions in, a second store out (#431).

Rebuilds pre-market trading days the tracker never saw, from purchased vendor minute bars, into the
reconstructed-history store #430 built the consumer for (``data/recon``,
``Settings.recon_subdir``). A rebuilt day is picked up by the next ``portfolio.json`` build with no
registration step, published as ``books_all`` alongside the untouched live ``books``.

## Two phases, because one input is required rather than convenient

**Phase 1 —** :func:`harvest_daily`. Grouped-daily bars for the whole window, one request per
session for the entire US market (~500 calls, under two hours at the free tier). It lands two
things: the candidate universe, and the **previous daily close**. #428 established that close as a
*required* input — without it the appearance reconstruction fires a median 18 min early — so every
later night's minute-bar work is wrong if this hasn't run. Sessions are walked ascending here so
each session's previous close comes from the response already in hand, halving the call count.

**Phase 2 —** :func:`harvest_session`, driven by :func:`run_harvest`. Minute bars per candidate,
**newest-first**. That ordering is #430's decision restated: the deliverable is not a backtest in
six weeks, it is a slightly deeper sample every morning, contiguous with the live window so the
combined book has no hole in the middle. Stopping early — any morning, for any reason — leaves a
usable contiguous history rather than a ragged edge.

## Memory: the #273 failure mode, restated and designed against

    for session in sessions:                  # one trading day
        for symbol in candidates:             # ~217 of them
            bars = fetch(symbol, session)     # ~960 rows, discarded before the next symbol
            rows += derive(bars)
        write_one_file_per_dataset(session)   # then drop the rows too

Peak resident set is one symbol-day of minute bars plus one session's accumulated rows. It does not
grow with the number of sessions harvested — exactly the property ``build_portfolio_payload`` lacked
when it OOM-killed the box (#264/#273). The guards in :mod:`.guard` enforce the rest.

## What gets stored, and why the two bar grids differ

The vendor returns a **whole session** per request, so trimming the window saves storage, not API
budget — and the budget is the scarce thing. That makes the grids independent decisions:

- ``bars`` — **5-min, full session** ``[chart_start, capture_end)``, byte-identical in shape to the
  live dataset (same :func:`~small_cap_stack.capture.bar_record`) — including IBKR's flat
  zero-volume filler candles for no-trade periods, which :func:`.reconstruct.aggregate` synthesises
  because the engine counts *bars*, not minutes (#442). Full session rather than
  pre-market-only on purpose: the paper book's exit walk marks an unresolved trade to the *last
  bar it can see* (``portfolio.exit.simulate_exit``), so a series truncated at 09:30 would close
  every still-open 09:10 entry at 09:25 and report it as the trade's result. That is a silent
  downward bias on exactly the trades that were working. #431 asked for pre-market only to bound
  the payload; the extra ~78 five-minute rows a symbol-day cost nothing measurable and remove the
  bias, so the restriction is kept where it *does* buy something — the minute grid below.
- ``bars_1m`` — **1-min, pre-market only** ``[04:00, 09:30)``, ~330 rows. This is the raw series the
  appearance was reconstructed from, kept because *store raw, compute derived on read* is the
  repo's core principle and here it has a price tag: a methodology change that has to re-fetch
  costs another 45 nights. Nothing in the package reads it; it exists so a future rule can be
  replayed without re-buying the data. ``harvest_store_minute_bars=False`` turns it off if disk
  ever gets tight.
- ``scanner_hits`` — one row per minute bar whose gates pass, not just the first. Run segmentation
  reads the gaps between hits (#36); a single first-appearance row would collapse a pop-fade-pop
  day into one run.
- ``opportunities`` — one row per symbol-day that produced an appearance.
- ``daily_universe`` — phase 1's stored output (harvest bookkeeping, not a live-store dataset).

There is deliberately no ``fundamentals`` row: the vendor sells no share float, and float is
context rather than a filter in the book's extraction path (``portfolio.extract``), so a
reconstructed candidate carries ``float_shares=None`` instead of a fabricated number.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..capture import bar_record, opportunity_id, opportunity_record, scanner_hit_record
from ..clock import ET
from ..config import Settings
from ..logging import get_logger
from ..market_calendar import is_trading_day
from ..scanner import Candidate
from ..storage import Store
from .checkpoint import Checkpoint
from .guard import HostGuard, RunWindow, peak_rss_mb
from .prefilter import DailyRow, candidates, universe_rows
from .reconstruct import PREMARKET, aggregate, reconstruct_hit, to_bars, trim_session
from .source import HarvestEntitlementError, HarvestError, HarvestSource

log = get_logger(__name__)

#: Every dataset the harvest writes. :func:`discard_partial` clears exactly these for a date, so a
#: new one added here is automatically covered by the "a half-written day is never merged" rule.
HARVEST_DATASETS = ("opportunities", "scanner_hits", "bars", "bars_1m")

#: The reconstructed candidate's ``con_id``. Live rows carry the IBKR contract id; the vendor has no
#: such thing and inventing one would make a reconstructed row indistinguishable from a captured one
#: on inspection. 0 reads as "no contract" everywhere it is touched (``_candidate_from_row`` only
#: uses it to re-request bars from IBKR, which never happens for a reconstructed day).
RECON_CON_ID = 0

#: Below this many candidates the failure RATIO is not applied — a proportion of a handful is noise,
#: and a real session carries ~217. Deliberately not a setting: nobody would tune it, and the
#: total-failure clause covers the small-session case at any size.
_MIN_CANDIDATES_FOR_RATIO = 10


class HarvestConfigError(RuntimeError):
    """The harvest was pointed somewhere it must not write."""


def harvest_store(s: Settings) -> Store:
    """The reconstructed-history store, with the one check that must never be skipped.

    The harvest writes vendor-derived rows. Pointing it at ``data_dir`` would put them in the
    Phase-1 store, where every existing reader — the EOD report, charts, the canary,
    ``collected_dates`` — would treat them as collected truth, and #430's whole provenance split
    (live ``books`` vs ``books_all``) would be silently defeated. Nothing about that failure is
    visible after the fact: a reconstructed partition is byte-identical in shape to a captured one.
    """
    if not s.recon_subdir:
        raise HarvestConfigError(
            "recon_subdir is empty — the reconstructed-history store is switched off, so there is "
            "nowhere for the harvest to write that is not the live Phase-1 store."
        )
    root = (s.data_dir / s.recon_subdir).resolve()
    if root == s.data_dir.resolve():
        raise HarvestConfigError(f"refusing to harvest into the live store at {root}")
    return Store(root)


def checkpoint_path(s: Settings) -> Path:
    """Beside the store it describes, so a restored backup carries its own progress marker."""
    return harvest_store(s).data_dir / "harvest-checkpoint.json"


def exclusions_path(s: Settings) -> Path:
    """Where the cached ETF/ETN symbol set lives — beside the checkpoint, for the same reason."""
    return harvest_store(s).data_dir / "excluded-symbols.json"


def load_exclusions(path: Path) -> frozenset[str]:
    """The cached exclusion set, or empty when it has never been fetched.

    Empty means *exclude nothing*, which is the pre-#443 behaviour. That is the correct failure
    direction only because :func:`refresh_exclusions` logs loudly when it cannot fetch — an empty
    set that nobody announced would silently reinstate the bug it exists to fix.
    """
    if not path.exists():
        return frozenset()
    raw = json.loads(path.read_text())
    return frozenset(str(x) for x in raw.get("symbols") or [])


def refresh_exclusions(source: HarvestSource, path: Path, s: Settings) -> frozenset[str]:
    """Fetch the ETF/ETN universe once and cache it. Returns the set to filter with.

    Reference data, not per-date: one fetch covers the whole harvest, at ~10–20 calls against a
    ~500-session job. Refreshed when older than ``harvest_exclusions_max_age_days`` so a harvest
    running for weeks eventually notices new listings — though the harvest walks *backwards*, so
    newly-listed products matter less the longer it runs.

    A fetch failure is **not** fatal. Losing a night of phase 1 because a reference endpoint
    hiccuped would be a worse trade than filtering with yesterday's list (or, on the very first
    run, with none) — but it is logged at warning either way, because an unfiltered universe is
    the defect this function exists to prevent.
    """
    cached = load_exclusions(path)
    # Keyed on the FILE existing, not on the set being non-empty: an empty result is a legitimate
    # answer (a vendor with no products of these types), and treating it as "never fetched" would
    # re-spend the reference calls every single night for as long as that stayed true.
    if path.exists() and not _stale(path, s.harvest_exclusions_max_age_days):
        return cached
    symbols: set[str] = set()
    try:
        for ticker_type in s.harvest_exclude_ticker_types:
            for active in (True, False):
                symbols.update(source.tickers_of_type(ticker_type, active=active))
    except Exception as exc:  # noqa: BLE001 — see the docstring: degrade, don't lose the night
        log.warning("harvest.exclusions_fetch_failed", error=str(exc), cached=len(cached))
        return cached
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "types": list(s.harvest_exclude_ticker_types),
                "fetched_at": datetime.now(UTC).isoformat(),
                "symbols": sorted(symbols),
            },
            indent=2,
        )
    )
    log.info("harvest.exclusions", symbols=len(symbols), types=s.harvest_exclude_ticker_types)
    return frozenset(symbols)


def _stale(path: Path, max_age_days: int) -> bool:
    if max_age_days <= 0:
        return False
    age = datetime.now(UTC) - datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return age > timedelta(days=max_age_days)


# ------------------------------------------------------------------------------------------------
# Results
# ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionResult:
    """One session's outcome — the per-session progress line, and the memory early-warning."""

    trading_date: date
    candidates: int
    opportunities: int
    scanner_hits: int
    bars_5m: int
    bars_1m: int
    calls: int
    seconds: float
    peak_rss_mb: float
    #: Symbols the vendor could not be reached for (#446). NOT the same as symbols that produced
    #: nothing: a quiet name is a legitimate zero, a 403 is not, and only counting the second lets
    #: an outage be told apart from a thin morning after the fact.
    failed: int = 0
    complete: bool = True  # False when the session was abandoned (nothing was written)

    def line(self) -> str:
        return (
            f"{self.trading_date} cands={self.candidates} opps={self.opportunities} "
            f"hits={self.scanner_hits} bars5={self.bars_5m} bars1={self.bars_1m} "
            f"failed={self.failed} calls={self.calls} {self.seconds:.0f}s "
            f"rss={self.peak_rss_mb:.0f}MB" + ("" if self.complete else " INCOMPLETE(discarded)")
        )


@dataclass(frozen=True)
class HarvestRun:
    """The summary a night's run prints and the workflow logs."""

    sessions: tuple[SessionResult, ...]
    stopped_because: str
    calls: int
    seconds: float
    peak_rss_mb: float

    @property
    def completed(self) -> tuple[date, ...]:
        return tuple(r.trading_date for r in self.sessions if r.complete)

    def summary(self) -> str:
        return (
            f"sessions={len(self.completed)} calls={self.calls} "
            f"{self.seconds / 60.0:.0f}min peak_rss={self.peak_rss_mb:.0f}MB "
            f"stopped={self.stopped_because}"
        )


# ------------------------------------------------------------------------------------------------
# Planning
# ------------------------------------------------------------------------------------------------


def trading_sessions(start: date, end: date, s: Settings) -> list[date]:
    """Trading dates in ``[start, end]``, ascending, per the XNYS calendar (+ manual overrides)."""
    out: list[date] = []
    day = start
    while day <= end:
        if is_trading_day(day, extra_closed=s.calendar_closed_dates):
            out.append(day)
        day += timedelta(days=1)
    return out


def plan_sessions(
    s: Settings,
    *,
    today: date,
    done: Sequence[date] = (),
    live_dates: Sequence[date] = (),
    not_before: date | None = None,
) -> list[date]:
    """Sessions still to harvest, **newest-first** (#430's ordering decision).

    Excludes dates the live store already collected: on an overlap the payload resolves to live
    anyway (#430), so spending ~218 calls on one is spending a night to have the result thrown
    away. Excludes ``today`` too — the vendor's own session is not final until the close, and the
    harvest runs overnight against finished days.

    ``not_before`` is the checkpoint's discovered entitlement floor (#440): dates on or before it
    are not for sale, so planning them would report a backlog that can never shrink and spend a
    call every night rediscovering it. ``harvest_lookback_days`` says how far back we *want* to go;
    this says how far back the vendor will actually go.
    """
    skip = set(done) | set(live_dates)
    start = today - timedelta(days=s.harvest_lookback_days)
    if not_before is not None:
        start = max(start, not_before + timedelta(days=1))
    sessions = trading_sessions(start, today - timedelta(days=1), s)
    return sorted((d for d in sessions if d not in skip), reverse=True)


def discard_partial(store: Store, trading_date: date) -> int:
    """Delete every harvest partition for ``trading_date``; returns how many were removed.

    A date is either fully harvested or absent — never both. The store is append-only, so a second
    run over a date that already has files would *add* rows rather than replace them: the day would
    extract duplicate opportunities, and its cache fingerprint would flip on every rebuild. Called
    before (re)harvesting any date the checkpoint does not list as done.
    """
    removed = 0
    for dataset in HARVEST_DATASETS:
        part = store.data_dir / dataset / f"dt={trading_date.isoformat()}"
        if part.exists():
            shutil.rmtree(part)
            removed += 1
    return removed


# ------------------------------------------------------------------------------------------------
# Phase 1 — grouped daily
# ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyResult:
    trading_date: date
    universe: int
    rows: int
    calls: int


def harvest_daily(
    source: HarvestSource,
    store: Store,
    s: Settings,
    sessions: Sequence[date],
    *,
    checkpoint: Checkpoint | None = None,
    deadline: datetime | None = None,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    on_result: Callable[[DailyResult], None] | None = None,
) -> list[DailyResult]:
    """Store the candidate universe for each session in ``sessions`` (given ascending).

    Ascending on purpose: session *n*'s previous close is session *n−1*'s grouped-daily response,
    which is already in hand, so the whole window costs ~one call per session instead of two. The
    first session in a run pays one extra call for the session before it.
    """
    ordered = sorted(sessions)
    results: list[DailyResult] = []
    prev_close: dict[str, float] = {}
    prev_day: date | None = None
    floor = checkpoint.entitlement_floor if checkpoint is not None else None
    # Fetched here rather than per session: it is reference data, and phase 1 is the only place the
    # stored universe is decided (#443).
    exclude = refresh_exclusions(source, exclusions_path(s), s)
    for day in ordered:
        if deadline is not None and now_fn() >= deadline:
            break
        prior = _prior_session(day, s)
        # Two ways a session is unreachable, and only one of them moves the floor (#440). A date on
        # or before the floor is not for sale. A date whose *prior* is not for sale is for sale but
        # ungateable — #428 makes the previous close a required input — so it is skipped as a
        # consequence, without being recorded: recording it would make each skip disqualify the
        # next session in turn, and the cascade would swallow the entire window.
        if floor is not None and day <= floor:
            continue
        if floor is not None and prior is not None and prior <= floor:
            log.info("harvest.daily_unseedable", date=day.isoformat(), prior=prior.isoformat())
            continue
        before = source.calls
        if prev_day != prior:  # first session of the run, or a gap in the requested list
            if prior is None:
                prev_close = {}
            else:
                try:
                    prev_close = _close_map(source.grouped_daily(prior))
                except HarvestEntitlementError as exc:
                    floor = _note_floor(checkpoint, floor, prior, exc)
                    prev_day = None
                    continue
        try:
            grouped = source.grouped_daily(day)
        except HarvestEntitlementError as exc:
            floor = _note_floor(checkpoint, floor, day, exc)
            prev_day = None
            continue
        rows = universe_rows(grouped, prev_close, s, exclude)
        store.append("daily_universe", [r.as_record() for r in rows], partition_date=day)
        result = DailyResult(day, len(grouped), len(rows), source.calls - before)
        results.append(result)
        if checkpoint is not None:
            checkpoint.mark_daily(day, calls=result.calls)
        if on_result is not None:
            on_result(result)
        log.info("harvest.daily", date=day.isoformat(), universe=len(grouped), rows=len(rows))
        prev_close = _close_map(grouped)  # this session becomes the next one's previous close
        prev_day = day
        del grouped
    return results


def effective_deadline(window: RunWindow, s: Settings, started: datetime) -> datetime:
    """When this run must be checkpointed and stopped — the window's stop, or the EOD recess (#455).

    The widened 12:30-03:00 window contains the box's two EOD jobs (``eod_bars_fetch`` 16:20,
    ``eod_report`` 16:30), and ``eod_report`` runs ``build_portfolio_payload`` — the ~1.5 GB,
    still-growing (#273) job that OOM-killed this box in #264. A run that started in the afternoon
    must therefore be *finished* before then, not merely willing to stop.

    ``HostGuard`` cannot provide that. It is checked once per session, and a session is ~217
    candidates x 13 s = 47 minutes, so with a 12:30 start the boundaries fall at 15:38 and 16:25 —
    the harvest is *inside* a session across both EOD jobs, holding 1 GB with ``MemorySwapMax=0``,
    with nothing sampling host memory at all. A deadline is enforced between symbols and by the
    "don't start what you cannot finish" pre-check, so it bounds where the container can still be
    running; the guard only bounds where a *new session* may begin.

    Evening runs are unaffected: the recess is in the past by then, so the window's own stop wins.
    """
    stop = window.deadline(started)
    et = started.astimezone(ET)
    if et.time() >= s.harvest_eod_recess_et:
        return stop  # started after the recess (the evening run) — nothing to duck
    recess = datetime.combine(et.date(), s.harvest_eod_recess_et, tzinfo=ET)
    return min(stop, recess)


def _note_floor(
    checkpoint: Checkpoint | None, floor: date | None, refused: date, exc: HarvestError
) -> date:
    """Record a refused date as the entitlement floor and return the floor to plan against.

    Logged at warning rather than swallowed: the harvest quietly harvesting a shorter window than
    ``harvest_lookback_days`` asks for is exactly the sort of thing that should be visible in
    ``journalctl`` the first night it happens, not inferred from a session count months later.
    """
    log.warning("harvest.entitlement_floor", date=refused.isoformat(), error=str(exc))
    if checkpoint is not None:
        checkpoint.note_entitlement_floor(refused)
    return refused if floor is None else max(floor, refused)


def _prior_session(day: date, s: Settings) -> date | None:
    """The trading session immediately before ``day`` (searching back at most a fortnight)."""
    probe = day - timedelta(days=1)
    for _ in range(14):
        if is_trading_day(probe, extra_closed=s.calendar_closed_dates):
            return probe
        probe -= timedelta(days=1)
    return None


def _close_map(grouped: Sequence[dict[str, Any]]) -> dict[str, float]:
    return {str(r["T"]): float(r["c"]) for r in grouped if r.get("T") and r.get("c")}


def stored_universe(store: Store, trading_date: date) -> list[DailyRow]:
    """Phase 1's stored rows for a session, back as :class:`DailyRow`."""
    df = store.read("daily_universe", dt=trading_date)
    if df.is_empty():
        return []
    return [DailyRow.from_record(r) for r in df.iter_rows(named=True)]


# ------------------------------------------------------------------------------------------------
# Phase 2 — one session
# ------------------------------------------------------------------------------------------------


def harvest_session(
    source: HarvestSource,
    store: Store,
    s: Settings,
    trading_date: date,
    rows: Sequence[DailyRow],
    *,
    deadline: datetime | None = None,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SessionResult:
    """Rebuild one pre-market session into the store. Nothing is written until it completes.

    That is the crash contract, not a stylistic choice: the parquet files land in one append per
    dataset at the end, so a kill — the hard stop, an OOM, a hard reboot — leaves the date with no
    files at all, and the checkpoint (written by the caller, after this returns) never claims it.
    It is also what makes "one file per ``dt=`` partition" true, which for this store *is* the read
    cost (#318/#319/#321).

    **A day with no data and a day the vendor refused look identical on disk** — both write nothing,
    because ``Store.append`` skips empty records. So failures are counted, not merely logged (#446):
    without that, a truncated API key produced ~11 dates a night marked harvested with an empty
    store, permanently, and nothing anywhere said so.
    """
    started = now_fn()
    cands = candidates(rows, min_day_volume=s.harvest_min_day_volume)
    if s.harvest_max_candidates > 0:
        cands = cands[: s.harvest_max_candidates]
    calls_before = source.calls

    opps: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []
    bars5: list[dict[str, Any]] = []
    bars1: list[dict[str, Any]] = []
    failed = 0
    consecutive = 0

    def abandon(why: str) -> SessionResult:
        """Give the session up, writing nothing. The caller must not mark it done."""
        log.warning(
            "harvest.session_abandoned",
            date=trading_date.isoformat(),
            reason=why,
            failed=failed,
            candidates=len(cands),
        )
        return SessionResult(
            trading_date=trading_date,
            candidates=len(cands),
            opportunities=0,
            scanner_hits=0,
            bars_5m=0,
            bars_1m=0,
            calls=source.calls - calls_before,
            seconds=(now_fn() - started).total_seconds(),
            peak_rss_mb=peak_rss_mb(),
            failed=failed,
            complete=False,
        )

    for rank, row in enumerate(cands, start=1):
        if deadline is not None and now_fn() >= deadline:
            # Out of night mid-session. Return the work as INCOMPLETE and write nothing: a session
            # is the unit that can be resumed, a half-session is not (a partial day extracts
            # perfectly well, just from half the symbols, and nothing downstream could tell).
            return abandon("hard-stop")
        if _accumulate_symbol(source, s, trading_date, row, rank, opps, hits, bars5, bars1):
            consecutive = 0
            continue
        failed += 1
        consecutive += 1
        # The circuit breaker. One symbol failing is ordinary — a delisted ticker, a bad print.
        # Five in a row is the vendor, and every further attempt costs 5 calls and ~95 seconds of
        # a finite night (the retry ladder) to learn the same thing again.
        if consecutive >= s.harvest_max_consecutive_failures:
            return abandon(f"{consecutive} consecutive symbol failures")

    # The quality floor, checked once at the end: scattered failures never trip the breaker but can
    # still leave a session sampled from a fraction of its universe — which extracts perfectly well
    # and is indistinguishable, afterwards, from a genuinely thin day.
    #
    # Two clauses, because a ratio is meaningless on a tiny candidate list: on a two-name session
    # one ordinary delisted ticker is 50%, and abandoning that would throw away good work. Total
    # failure is the signal that survives at any size — it is the shape a revoked key makes.
    if cands and failed == len(cands):
        return abandon("every symbol failed")
    budget = len(cands) * s.harvest_max_failure_ratio
    if len(cands) >= _MIN_CANDIDATES_FOR_RATIO and failed > budget:
        return abandon(f"{failed}/{len(cands)} symbols failed")

    store.append("opportunities", opps, partition_date=trading_date)
    store.append("scanner_hits", hits, partition_date=trading_date)
    store.append("bars", bars5, partition_date=trading_date)
    if s.harvest_store_minute_bars:
        store.append("bars_1m", bars1, partition_date=trading_date)
    return SessionResult(
        trading_date=trading_date,
        candidates=len(cands),
        opportunities=len(opps),
        scanner_hits=len(hits),
        bars_5m=len(bars5),
        bars_1m=len(bars1),
        calls=source.calls - calls_before,
        seconds=(now_fn() - started).total_seconds(),
        peak_rss_mb=peak_rss_mb(),
    )


def _accumulate_symbol(
    source: HarvestSource,
    s: Settings,
    trading_date: date,
    row: DailyRow,
    rank: int,
    opps: list[dict[str, Any]],
    hits: list[dict[str, Any]],
    bars5: list[dict[str, Any]],
    bars1: list[dict[str, Any]],
) -> bool:
    """Fetch one symbol-day, derive its rows, and drop the bars. The streaming step of the loop.

    Everything this touches is local and freed on return — the accumulating lists are the only
    thing that survives, and they are one session's worth by construction.

    Returns whether the symbol was *reached*, which is deliberately not the same as whether it
    produced anything (#446). A name that did not trade pre-market, or never cleared the gates, is
    a legitimate zero and returns True; only a vendor/transport failure returns False. Conflating
    the two would make the session's failure count track market quietness instead of vendor health,
    and the whole point of the count is to tell those apart.
    """
    try:
        raw = source.minute_bars(row.symbol, trading_date)
    except HarvestEntitlementError:
        # Not this symbol's problem — the whole DATE is past what the vendor sells, so every
        # remaining symbol would fail the same way. Letting it out is the point: swallowed here it
        # would write a session of zero opportunities that the checkpoint then marks done, which is
        # indistinguishable from a genuinely quiet day and would never be revisited (#440).
        raise
    except Exception:  # noqa: BLE001 — one symbol's failure must never stall a night's session
        log.warning("harvest.symbol_failed", symbol=row.symbol, date=trading_date.isoformat())
        return False
    if not raw:
        return True
    all_bars = to_bars(raw)
    premarket = trim_session(all_bars, trading_date, PREMARKET[0], PREMARKET[1])
    if not premarket:
        return True

    # The appearance is reconstructed on the MINUTE series — a true trailing 5-min rolling sum, the
    # closest analogue to IBKR's continuously-updated stVolume5minAbove. #428 measured that at a
    # median −0.34 min against the live tracker, versus +3.16 min off the 5-min grid.
    recon = reconstruct_hit(
        premarket,
        s,
        symbol=row.symbol,
        trading_date=trading_date,
        prev_close=row.prev_close,
        window_minutes=5,
        # We ASKED the vendor for minute bars, so say so rather than letting `bar_interval` infer it
        # back off the data (#442). On a thin pre-market tape — which the 100k day-volume floor
        # admits a great many of — the modal gap is not one minute, and an over-long interval both
        # credits the appearance late and collapses the trailing-volume window to a single bar.
        interval=timedelta(minutes=1),
    )
    if not recon.found or recon.hit_time is None:
        # A candidate on the daily bar that never cleared the gates intraday: not an opportunity,
        # but the symbol was reached, so it is a zero rather than a failure.
        return True

    session_bars = aggregate(
        trim_session(all_bars, trading_date, s.chart_start, s.capture_end), minutes=5
    )
    if not session_bars:
        return True

    oid = opportunity_id(trading_date, row.symbol)
    cand = Candidate(
        rank=rank, symbol=row.symbol, con_id=RECON_CON_ID, exchange="SMART", currency="USD"
    )
    # Reuse the live record builders rather than writing dicts by hand: the reconstructed store's
    # whole value is that `portfolio.extract_day_trades` can read it unchanged, and a hand-rolled
    # schema is one renamed column away from silently extracting nothing.
    opps.append(opportunity_record(cand, oid, recon.hit_time, trading_date))
    hits.extend(scanner_hit_record(oid, cand, ts) for ts in recon.hit_times)
    bars5.extend(bar_record(oid, row.symbol, b) for b in session_bars)
    if s.harvest_store_minute_bars:
        bars1.extend(bar_record(oid, row.symbol, b) for b in premarket)
    return True


# ------------------------------------------------------------------------------------------------
# Phase 2 — the night
# ------------------------------------------------------------------------------------------------


@dataclass
class _Night:
    """Mutable bookkeeping for one :func:`run_harvest` call (keeps the loop readable)."""

    results: list[SessionResult] = field(default_factory=list)
    calls: int = 0
    stopped: str = "sessions-exhausted"


def run_harvest(
    source: HarvestSource,
    store: Store,
    s: Settings,
    sessions: Sequence[date],
    *,
    checkpoint: Checkpoint,
    window: RunWindow,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    ignore_window: bool = False,
    max_sessions: int = 0,
    on_session: Callable[[SessionResult], None] | None = None,
) -> HarvestRun:
    """Harvest ``sessions`` (newest-first) until the night runs out, the box gets tight, or they do.

    The three stop conditions are all *clean*: each leaves the checkpoint accurate and the store
    holding only whole sessions. ``ignore_window`` exists for the on-box smoke test and is the one
    way to run outside the configured window — the CLI makes it two deliberate flags, because a
    confirmation the caller auto-answers protects nobody (#261).
    """
    started = now_fn()
    if not ignore_window and not window.is_open(started):
        log.warning("harvest.refused", window=window.describe(), now=started.isoformat())
        return HarvestRun((), f"outside-window ({window.describe()})", 0, 0.0, peak_rss_mb())

    deadline = effective_deadline(window, s, started)
    guard = HostGuard(
        min_mem_available_mb=s.harvest_min_mem_available_mb,
        min_disk_free_mb=s.harvest_min_disk_free_mb,
    )
    night = _Night()

    for day in sessions:
        if max_sessions and len(night.results) >= max_sessions:
            night.stopped = "max-sessions"
            break
        if now_fn() >= deadline:
            night.stopped = "eod-recess" if deadline < window.deadline(started) else "hard-stop"
            break
        headroom = guard.check(str(store.data_dir))
        if not headroom.ok:
            night.stopped = f"host-headroom ({headroom.reason})"
            log.warning("harvest.host_headroom", reason=headroom.reason)
            break

        rows = stored_universe(store, day)
        if not rows:
            # Phase 1 hasn't run for this date (or the session had no qualifying names at all).
            # Either way there is nothing to fetch and no previous close to gate on, so skip it
            # WITHOUT marking it done — a missing universe is a phase-1 gap to fill, not a result.
            log.warning("harvest.no_universe", date=day.isoformat())
            continue
        estimate = _session_seconds(rows, s)
        if now_fn() + timedelta(seconds=estimate) > deadline:
            # Don't start what the night cannot finish: an abandoned session writes nothing, so the
            # calls it burned buy nothing either. Stopping here keeps them for tomorrow.
            night.stopped = (
                "eod-recess (next session would overrun)"
                if deadline < window.deadline(started)
                else "hard-stop (next session would overrun)"
            )
            break

        discard_partial(store, day)  # a previous kill may have left files for an unmarked date
        try:
            result = harvest_session(source, store, s, day, rows, deadline=deadline, now_fn=now_fn)
        except HarvestEntitlementError as exc:
            # Sessions run newest-first, so everything still pending is OLDER and equally unbuyable:
            # stop the night rather than spend it discovering that 200 times. The date is NOT marked
            # done — an entitlement wall yields an empty session, and a checkpoint that claims it
            # would bury the gap forever.
            discard_partial(store, day)
            _note_floor(checkpoint, checkpoint.entitlement_floor, day, exc)
            night.stopped = f"entitlement-floor ({day.isoformat()})"
            break
        night.results.append(result)
        night.calls += result.calls
        if result.complete:
            checkpoint.mark_session(day, calls=result.calls)
        else:
            discard_partial(store, day)
            night.stopped = "hard-stop (mid-session)"
        log.info("harvest.session", line=result.line())
        if on_session is not None:
            on_session(result)
        if not result.complete:
            break

    return HarvestRun(
        sessions=tuple(night.results),
        stopped_because=night.stopped,
        calls=night.calls,
        seconds=(now_fn() - started).total_seconds(),
        peak_rss_mb=peak_rss_mb(),
    )


def _session_seconds(rows: Sequence[DailyRow], s: Settings) -> float:
    """How long a session will take, to the only precision that matters: the rate limit.

    At the free tier every call is a fixed sleep, so wall clock is ``candidates × rate_sleep`` plus
    noise. Deliberately ignores per-call latency and per-symbol compute — both are seconds against
    a 13-second sleep, and an estimate that runs *under* is the dangerous direction.
    """
    n = len(candidates(rows, min_day_volume=s.harvest_min_day_volume))
    if s.harvest_max_candidates > 0:
        n = min(n, s.harvest_max_candidates)
    return n * s.harvest_rate_sleep_sec
