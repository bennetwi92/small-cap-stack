"""Publish reconstructed sessions as reviewable chart payloads (#488).

The Portfolio page could show reconstructed history (`+ History`, #430) and the Results page could
not: Results reads ``index.json`` + ``charts/<date>.json``, both written from the **live** store
only, so every harvested pre-market session was invisible there and a ``recon`` trade opened in the
Portfolio inspector drew no candles. This module is the missing producer.

**A separate namespace, not a flag on the live one.** Reconstructed dates get their own index
(``recon_index.json``) and their own directory (``charts/recon/<date>.json``); the live
``index.json`` and ``charts/<date>.json`` are untouched, byte for byte. The alternative the issue
offered — one index with a ``source`` field per row — would have made every existing consumer start
returning vendor-derived days the moment this landed, silently and with no tag, which is exactly
the failure #430 built two stores to prevent. A reader has to *ask* for the reconstruction here, the
same way ``build_portfolio_payload`` has to be handed a ``recon_store``. (The rows carry
``source: "recon"`` as well — belt and braces, so a future consumer that does merge the two indexes
still cannot lose the provenance.)

**Bounded, per-date and incremental**, because the alternative is the job that OOM-killed the box
(#264/#273):

- One date is built, written and dropped before the next is read — the #261 lesson, so peak memory
  is one session's charts rather than the archive's.
- Only dates *missing* an output are built, so the normal call does nothing at all.
- The published set is capped at ``recon_charts_max_dates``, newest-first. That cap is about the
  publish pipe rather than memory: ``publish-dashboard`` force-pushes the whole ``dashboard`` tree
  every 15 minutes, and ~500 harvested sessions x ~2 MB would put a gigabyte through it each cycle.
  Dates outside the window are pruned and **counted** — a silent truncation would read as "that is
  all the harvest has" rather than "that is all the payload can hold" (#449's rule, restated).
- Dates the tracker collected **live** are never published from here, so a reconstructed payload can
  never shadow a captured one and the two namespaces stay date-disjoint (their opportunity ids would
  otherwise collide).

The caller is the harvest itself (``python -m small_cap_stack.harvest``): each completed session
publishes its own charts as it lands, so the work is spread one date per ~47-minute session instead
of arriving as an archive sweep. ``harvest charts`` re-runs it for the backlog.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .clock import now_et
from .config import Settings
from .dashboard import build_charts, index_entry, read_json, write_json
from .logging import get_logger
from .portfolio import collected_dates, open_recon_store
from .storage import Store

log = get_logger(__name__)

#: Provenance stamp carried by every row this module writes, matching ``CandidateTrade.source``.
RECON_SOURCE = "recon"

_CHARTS_SUBDIR = "recon"
_INDEX_NAME = "recon_index.json"


def dashboard_dir(s: Settings) -> Path:
    """The published-artifact directory — what ``publish-dashboard`` copies wholesale."""
    return s.data_dir / "dashboard"


def recon_charts_dir(dashboard: Path) -> Path:
    return dashboard / "charts" / _CHARTS_SUBDIR


def recon_charts_path(dashboard: Path, trading_date: date) -> Path:
    """``<dashboard>/charts/recon/<date>.json`` — deliberately a sibling of the live payloads.

    Same parent, different directory: the frontend's one existing chart fetch becomes a fetch with a
    namespace, not a second URL scheme, and ``publish-dashboard``'s ``docker cp .../.`` picks it up
    with no workflow change."""
    return recon_charts_dir(dashboard) / f"{trading_date.isoformat()}.json"


def recon_index_path(dashboard: Path) -> Path:
    return dashboard / _INDEX_NAME


def published_recon_dates(dashboard: Path) -> list[date]:
    """Dates that already have a reconstructed chart payload on disk, ascending."""
    out: list[date] = []
    for p in sorted(recon_charts_dir(dashboard).glob("*.json")):
        try:
            out.append(date.fromisoformat(p.stem))
        except ValueError:  # a stray file in the directory is not an index entry
            continue
    return out


@dataclass(frozen=True)
class ReconPublish:
    """What one publish run did — the operator-facing summary, and what the tests assert on."""

    published: tuple[date, ...]  # dates whose chart payload was (re)built this run
    failed: tuple[date, ...]  # dates that raised; the run continued past them
    pruned: tuple[date, ...]  # published dates dropped for falling outside the budget window
    indexed: tuple[date, ...]  # every date the rewritten index now offers, newest-first
    capped_dates_dropped: int  # harvested dates the budget refused to publish
    overlap_dates_dropped: int  # harvested dates the tracker also collected live

    def summary(self) -> dict[str, Any]:
        return {
            "published": [d.isoformat() for d in self.published],
            "failed": [d.isoformat() for d in self.failed],
            "pruned": [d.isoformat() for d in self.pruned],
            "indexed": len(self.indexed),
            "capped_dates_dropped": self.capped_dates_dropped,
            "overlap_dates_dropped": self.overlap_dates_dropped,
        }


_EMPTY = ReconPublish((), (), (), (), 0, 0)


def _budget_window(
    recon_all: Iterable[date], live_dates: set[date], s: Settings
) -> tuple[list[date], int, int]:
    """Split the harvested dates into (publishable window, capped-out count, live-overlap count).

    Newest-first and truncated at ``recon_charts_max_dates``: the harvest walks *backwards* from the
    live record, so the newest reconstructed dates are the ones contiguous with it — the segment a
    reader can actually read as one continuous history rather than a detached older slab.
    """
    dates = list(recon_all)
    overlap = sum(1 for d in dates if d in live_dates)
    eligible = sorted((d for d in dates if d not in live_dates), reverse=True)
    budget = s.recon_charts_max_dates
    if budget > 0 and len(eligible) > budget:
        return eligible[:budget], len(eligible) - budget, overlap
    return eligible, 0, overlap


def publish_recon_charts(
    s: Settings,
    *,
    recon_store: Store | None = None,
    live_store: Store | None = None,
    dates: Iterable[date] | None = None,
    limit: int = 0,
    now: datetime | None = None,
) -> ReconPublish:
    """Bring ``charts/recon/`` + ``recon_index.json`` up to date. Returns what it did.

    ``dates`` restricts the run to specific sessions (what the harvest passes as each one lands);
    omit it to build every publishable date that has no payload yet. ``limit`` caps how many are
    built in one call — the index rewrite and the prune still run, so a limited call always leaves
    a consistent index rather than a half-updated one.

    Never raises for one bad date: a session that blows up is logged and counted, because the caller
    is a multi-week overnight job and losing the night over one unreadable partition would be a far
    worse trade than publishing the other 29 sessions.
    """
    store = recon_store if recon_store is not None else open_recon_store(s)
    if store is None:  # recon_subdir="" — the feature is switched off
        return _EMPTY
    stamp = now or now_et().astimezone(UTC)
    out = dashboard_dir(s)

    # The live store decides the overlap, exactly as `build_portfolio_payload` does: a date the
    # tracker watched is ground truth, and publishing a reconstructed payload for it would put two
    # charts under the same opportunity ids with nothing to tell them apart.
    live = set(collected_dates(live_store if live_store is not None else Store(s.data_dir)))
    window, capped, overlap = _budget_window(collected_dates(store), live, s)
    window_set = set(window)

    entries: dict[str, dict[str, Any]] = {}
    existing = read_json(recon_index_path(out))
    for e in (existing or {}).get("dates", []) or []:
        if isinstance(e, dict) and isinstance(e.get("date"), str):
            entries[e["date"]] = e
    on_disk = set(published_recon_dates(out))

    if dates is None:
        # The default call is a no-op once the window is published: a date is rebuilt only when its
        # payload OR its index row is missing (losing the index alone must not orphan the files).
        todo = [d for d in window if d not in on_disk or d.isoformat() not in entries]
    else:
        wanted = set(dates)
        todo = [d for d in window if d in wanted]
        for d in sorted(wanted - window_set, reverse=True):
            log.info(
                "dashboard.recon_charts_skipped",
                date=d.isoformat(),
                reason="live" if d in live else "outside-budget-window",
            )
    if limit > 0:
        todo = todo[:limit]

    published: list[date] = []
    failed: list[date] = []
    for d in todo:
        try:
            # Built, written and dropped one date at a time. Holding the payloads to assemble the
            # index at the end is the retention #261 removed from the archive backfill; the index
            # only ever needs each date's row, which `index_entry` reduces it to.
            charts = build_charts(store, s, d, stamp)
            write_json(recon_charts_path(out, d), charts)
            entries[d.isoformat()] = index_entry(d, charts, source=RECON_SOURCE)
            del charts
        except Exception as exc:  # noqa: BLE001 — see the docstring: one bad date, not the night
            log.warning("dashboard.recon_charts_failed", date=d.isoformat(), error=str(exc))
            failed.append(d)
        else:
            published.append(d)

    pruned: list[date] = []
    for d in sorted(on_disk - window_set, reverse=True):
        recon_charts_path(out, d).unlink(missing_ok=True)
        pruned.append(d)

    # The index offers exactly what is on disk *now* and inside the window — so a pruned date, a
    # failed build and a hand-deleted file all fall out of it rather than 404ing the page.
    live_files = set(published_recon_dates(out))
    indexed = sorted(
        (d for d in window_set if d in live_files and d.isoformat() in entries), reverse=True
    )
    write_json(
        recon_index_path(out),
        {
            "generated_utc": stamp.isoformat(),
            "source": RECON_SOURCE,
            # Never a silent cap (#449): the page states its own span, and "30 sessions" has to be
            # readable as a budget rather than as everything the harvest has.
            "max_dates": s.recon_charts_max_dates,
            "capped_dates_dropped": capped,
            "overlap_dates_dropped": overlap,
            "dates": [entries[d.isoformat()] for d in indexed],
        },
    )
    result = ReconPublish(
        published=tuple(published),
        failed=tuple(failed),
        pruned=tuple(pruned),
        indexed=tuple(indexed),
        capped_dates_dropped=capped,
        overlap_dates_dropped=overlap,
    )
    if published or pruned or failed:
        log.info("dashboard.recon_charts", **result.summary())
    return result
