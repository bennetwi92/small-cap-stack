"""Extraction: read a day's captured bars and yield the qualifying pre-market trades (#230).

Reuses the report seams (``day_opportunities`` / ``symbol_runs`` / ``day_chart_bars``) so the book
sees exactly what the results page does. Split out of the old single-file ``portfolio.py`` (#259)
with no behaviour change.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from ..capture import Bar
from ..clock import ET
from ..config import Settings
from ..report import day_chart_bars, day_opportunities, symbol_runs
from ..rmetrics import compute_r_metrics
from ..storage import Store
from .models import CandidateTrade


def _qualify(
    rm_entry_index: int | None,
    rm_entry_price: float | None,
    rm_entry_fill: float | None,
    rm_stop: float | None,
    rm_risk: float | None,
    takeable: bool,
    day_bars: Sequence[Bar],
    s: Settings,
) -> bool:
    """Apply the #230 selection rules to one run's R-metrics. Pure for straightforward testing."""
    if not takeable:  # engine-v2 pass + triggered + not exhausted
        return False
    if rm_entry_index is None or rm_entry_price is None or rm_entry_fill is None:
        return False
    if rm_stop is None or rm_risk is None or rm_risk <= 0:
        return False
    if not (s.portfolio_entry_price_min <= rm_entry_fill <= s.portfolio_entry_price_max):
        return False
    trigger_bar = day_bars[rm_entry_index]
    return trigger_bar.start.astimezone(ET).time() < s.portfolio_premarket_cutoff


def extract_day_trades(store: Store, s: Settings, trading_date: date) -> list[CandidateTrade]:
    """Qualifying pre-market engine-v2 trades for one day, in trigger-time order.

    Reuses the EOD report's segmentation + R-metrics so the paper book never drifts from the
    review/results pages: same runs, same detector, same appearance/staleness/exhaustion gating."""
    opps = day_opportunities(store, trading_date)
    if opps.is_empty():
        return []
    bars_df = store.read("bars", dt=trading_date)
    scans = store.read("scanner_hits", dt=trading_date)
    excluded = {sym.upper() for sym in s.portfolio_exclude_symbols}
    out: list[CandidateTrade] = []
    for row in opps.iter_rows(named=True):
        if str(row["symbol"]).upper() in excluded:  # ETFs mis-captured pre-#226 — never a candidate
            continue
        oid = row["opportunity_id"]
        day_bars = day_chart_bars(bars_df, oid, s)
        if not day_bars:
            continue
        for run in symbol_runs(row, bars_df, scans, s):
            rm = compute_r_metrics(day_bars, s, first_hit=run.first_hit)
            if not _qualify(
                rm.entry_index,
                rm.entry_price,
                rm.entry_fill,
                rm.stop,
                rm.initial_risk,
                rm.takeable,
                day_bars,
                s,
            ):
                continue
            assert rm.entry_index is not None  # narrowed by _qualify
            assert rm.entry_price is not None and rm.entry_fill is not None
            assert rm.stop is not None and rm.initial_risk is not None
            out.append(
                CandidateTrade(
                    trading_date=trading_date,
                    symbol=row["symbol"],
                    seg_id=run.seg_id,
                    run=run.idx,
                    trigger_at=day_bars[rm.entry_index].start,
                    entry_price=rm.entry_price,
                    entry_fill=rm.entry_fill,
                    stop=rm.stop,
                    risk=rm.initial_risk,
                    entry_index=rm.entry_index,
                    bars=tuple(day_bars),
                )
            )
    # A **total** order (#381). Sorting on trigger_at alone is a stable sort over an upstream row
    # order, so two candidates triggering on the same bar were separated by however the store
    # happened to yield them — and `portfolio_max_trades_per_day` then took a different pair
    # whenever such a tie straddled the day's cap. `day_opportunities` is deterministic again, but
    # the tiebreak is what makes selection independent of upstream ordering at all.
    out.sort(key=lambda c: (c.trigger_at, c.symbol, c.seg_id, c.run))
    return out
