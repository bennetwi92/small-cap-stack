"""Extraction: read a day's captured bars and yield the qualifying pre-market trades (#230).

Reuses the report seams (``day_opportunities`` / ``symbol_runs`` / ``day_chart_bars``) so the book
sees exactly what the results page does. Split out of the old single-file ``portfolio.py`` (#259)
with no behaviour change.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from ..capture import Bar, bar_interval
from ..config import Settings
from ..report import _funds_for, day_chart_bars, day_opportunities, symbol_runs
from ..rmetrics import RMetrics, compute_r_metrics, measure_resolved_trade, resolve_entry_bar
from ..storage import Store
from .models import CandidateTrade


def _qualify(
    rm_entry_index: int | None,
    rm_entry_price: float | None,
    rm_entry_fill: float | None,
    rm_stop: float | None,
    rm_risk: float | None,
    takeable: bool,
) -> bool:
    """Can the book act on this run's R-metrics? Pure for straightforward testing.

    ⚠️ **This no longer decides *whether* a setup is one we'd take** (#567). Selection — the price
    band and the trigger-time window — moved into the engine, where it sits beside the shape gates
    and reaches here already folded into ``takeable``. What is left is the book's own question:
    given a setup the engine selected, are the numbers usable to size and simulate a position?

    If you are looking for "why didn't the book take X", the answer is in the engine now:
    ``DaySetup.in_price_band`` / ``in_window`` say which selection rule rejected it.
    """
    if not takeable:  # shape gates + triggered + not exhausted + selected (price band, window)
        return False
    if rm_entry_index is None or rm_entry_price is None or rm_entry_fill is None:
        return False
    return not (rm_stop is None or rm_risk is None or rm_risk <= 0)


def _resolved(
    store: Store,
    trading_date: date,
    oid: str,
    day_bars: list[Bar],
    rm: RMetrics,
) -> tuple[list[Bar], dict[str, object], str]:
    """Re-cut a same-bar entry against 1-min bars. Returns ``(bars, measurement, outcome)``.

    Only the ``"ran"`` outcome changes anything: the entry 5-min bar is replaced by the span we were
    actually in and the trade is re-measured over that series. Every other outcome — including the
    absence of minute bars — leaves the conservative reading exactly as it was, which is the modal
    answer and not merely the safe one.
    """
    assert rm.entry_index is not None and rm.entry_fill is not None and rm.stop is not None
    minute = store.read("bars_1m", dt=trading_date)
    if minute.is_empty():
        return day_bars, {}, "unresolved"
    sub = minute.filter(pl.col("opportunity_id") == oid)
    mins = [
        Bar(
            start=r["bar_start_utc"],
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            volume=r["volume"],
        )
        for r in sub.unique(subset="bar_start_utc", keep="first")
        .sort("bar_start_utc")
        .iter_rows(named=True)
    ]
    entry_bar = day_bars[rm.entry_index]
    res = resolve_entry_bar(
        mins,
        entry_trigger=rm.entry_trigger if rm.entry_trigger is not None else rm.entry_fill,
        entry_fill=rm.entry_fill,
        stop=rm.stop,
        bar_start=entry_bar.start,
        bar_end=entry_bar.start + bar_interval(day_bars),
    )
    if not res.ran or res.synthetic_bar is None:
        return day_bars, {}, res.outcome
    recut = list(day_bars)
    recut[rm.entry_index] = res.synthetic_bar
    m = measure_resolved_trade(
        recut, entry_fill=rm.entry_fill, stop=rm.stop, entry_index=rm.entry_index
    )
    return recut, m, res.outcome


def extract_day_trades(
    store: Store,
    s: Settings,
    trading_date: date,
    *,
    source: str = "live",
    resolve_store: Store | None = None,
) -> list[CandidateTrade]:
    """Qualifying pre-market engine-v2 trades for one day, in trigger-time order.

    Reuses the EOD report's segmentation + R-metrics so the paper book never drifts from the
    review/results pages: same runs, same detector, same appearance/staleness/exhaustion gating.

    ``source`` stamps provenance onto every candidate (#430). It is a property of the *store* being
    read, not of anything in the rows, which is exactly why it has to be passed in: a reconstructed
    partition is byte-identical in shape to a captured one, so nothing downstream could tell them
    apart on inspection. Pass ``"recon"`` when reading the harvested store."""
    opps = day_opportunities(store, trading_date)
    if opps.is_empty():
        return []
    bars_df = store.read("bars", dt=trading_date)
    scans = store.read("scanner_hits", dt=trading_date)
    # Float is context, never a filter — and NOT because it "already ran upstream". It never runs.
    # The IBKR scan gates on price / change / 5-min volume only; float is enrichment written after a
    # name is flagged (`capture._open_opportunity`), and nothing in the engine's selection rules
    # (price band, trigger window) or the book's sizing reads it. `gates.py::float_gate` exists, but
    # its only consumer is the EOD report's `float_ok` count. So the book does take names over
    # `float_max_shares` — put the gate in the engine's selection tier if that should change; don't
    # assume it happened somewhere else. `tests/test_portfolio_extract.py` pins this.
    # Read through the same `_funds_for` seam the EOD report uses so the book quotes the same
    # source-merged number the results/review pages do (fmp first), rather than a second opinion.
    # NOTE: adding this read means `payload._EXTRACT_DATASETS` must list `fundamentals` too, or the
    # EOD fundamentals backfill (`capture.capture_missing_fundamentals`) would land a float without
    # busting the day's candidate-cache fingerprint.
    funds = store.read("fundamentals", dt=trading_date)
    excluded = {sym.upper() for sym in s.portfolio_exclude_symbols}
    out: list[CandidateTrade] = []
    for row in opps.iter_rows(named=True):
        if str(row["symbol"]).upper() in excluded:  # ETFs mis-captured pre-#226 — never a candidate
            continue
        oid = row["opportunity_id"]
        day_bars = day_chart_bars(bars_df, oid, s)
        if not day_bars:
            continue
        float_shares, _short_percent = _funds_for(funds, oid)
        for run in symbol_runs(row, bars_df, scans, s):
            rm = compute_r_metrics(day_bars, s, first_hit=run.first_hit)
            if not _qualify(
                rm.entry_index,
                rm.entry_price,
                rm.entry_fill,
                rm.stop,
                rm.initial_risk,
                rm.takeable,
            ):
                continue
            assert rm.entry_index is not None  # narrowed by _qualify
            assert rm.entry_price is not None and rm.entry_fill is not None
            assert rm.stop is not None and rm.initial_risk is not None
            # A same-bar entry+stop is an assumption about intrabar order (#581). When the caller
            # opted into a finer grid, ask it — and only a "ran" verdict changes any number (#583).
            trade_bars: list[Bar] = day_bars
            resolution: dict[str, object] = {}
            outcome: str | None = None
            if resolve_store is not None and rm.same_bar_stop:
                trade_bars, resolution, outcome = _resolved(
                    resolve_store, trading_date, oid, day_bars, rm
                )
            out.append(
                CandidateTrade(
                    trading_date=trading_date,
                    symbol=row["symbol"],
                    seg_id=run.seg_id,
                    run=run.idx,
                    trigger_at=day_bars[rm.entry_index].start,
                    entry_price=float(resolution.get("entry_price", rm.entry_price)),  # type: ignore[arg-type]
                    entry_fill=rm.entry_fill,
                    stop=rm.stop,
                    risk=float(resolution.get("initial_risk", rm.initial_risk)),  # type: ignore[arg-type]
                    entry_index=rm.entry_index,
                    bars=tuple(trade_bars),
                    float_shares=float_shares,
                    max_r=float(resolution["max_r"]) if resolution else rm.max_r,  # type: ignore[arg-type]
                    max_gain_pct=(
                        float(resolution["max_gain_pct"]) if resolution else rm.max_gain_pct  # type: ignore[arg-type]
                    ),
                    same_bar_stop=rm.same_bar_stop,
                    fill_above_entry_bar_high=rm.fill_above_entry_bar_high,
                    entry_resolution=outcome,
                    source=source,
                )
            )
    # A **total** order (#381). Sorting on trigger_at alone is a stable sort over an upstream row
    # order, so two candidates triggering on the same bar were separated by however the store
    # happened to yield them — and `portfolio_max_trades_per_day` then took a different pair
    # whenever such a tie straddled the day's cap. `day_opportunities` is deterministic again, but
    # the tiebreak is what makes selection independent of upstream ordering at all.
    out.sort(key=lambda c: (c.trigger_at, c.symbol, c.seg_id, c.run))
    return out
