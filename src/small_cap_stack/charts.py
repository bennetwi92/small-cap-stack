"""Annotated candlestick chart data per opportunity (issue #113).

A **pure rendering projection** over already-captured raw data — no new capture, fully backcastable
over collected days (store-raw / compute-on-read). Given a run's 5-min bars, the strategy settings,
and the run's scanner appearance, it produces the OHLC series plus the annotation markers a
front-end needs to draw where the notional trade would have played out:

- ``first_hit`` — the bar at/after the symbol first appeared on the scanner;
- ``entry`` — the bar whose high crossed the entry trigger (the fill);
- ``max_r`` — the bar that set peak favourable excursion (Max R);
- ``stop`` — the bar whose low breached the stop (stop-first convention).

The marker indices and the entry/stop price *levels* come straight from :func:`compute_r_metrics`,
so the chart never re-derives the entry/stop/stop-first logic — one source of truth. Markers are
emitted as **epoch timestamps** (not array indices) so the front-end can place them on a bar series
whose indices differ from the run window's — e.g. the review workbench's full-day series (#141),
supplied via ``chart_bars``. Rendering itself is done client-side in the GitHub Pages dashboard
(issue #70); this module only shapes the JSON. It stays offline-friendly (no broker, no plotting
deps), so it runs in cloud dev.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .bullflag import (
    contiguous_prior_cycles,
    detect_day_with_settings,
    segment_cycles,
    significant_cycles,
    token_eps,
    tokenize,
)
from .capture import Bar, bar_interval, compute_vwap
from .config import Settings
from .rmetrics import compute_r_metrics


@dataclass(frozen=True)
class ChartData:
    """Bars + annotations for one opportunity's chart (JSON-ready via ``dataclasses.asdict``)."""

    # {"t": epoch_s, "o", "h", "l", "c", "v", "vwap"} per 5-min bar. ``vwap`` is the running
    # intraday VWAP (typical-price weighted, anchored at the day's 04:00 bar); None until volume
    # accumulates, so front-ends guard on null before drawing the line (VWAP overlay).
    bars: list[dict[str, float | int | None]]
    levels: dict[str, float | None]  # {"entry": trigger, "stop": stop} — None when no setup formed
    markers: dict[str, int | None]  # epoch-s per event: first_hit / entry / max_r / stop (#141)
    triggered: bool
    stopped_out: bool
    max_r: float | None
    engine: dict[str, Any]  # engine-v2 detector's read of the drawn series (overlay #216)


def _bar_containing(bars: list[Bar], t: datetime) -> int | None:
    """Index of the bar whose interval [start, start+bar) contains ``t`` — where the appearance
    marker sits. A symbol that appeared mid-bar marks *that* bar, not the next one, matching the
    bar-close entry gate (#122): the earlier "first bar at/after ``t``" drew the dot a bar late
    (JEM: 08:45 vs the 08:40 consolidation bar). Falls back to the next bar for a ``t`` that lands
    in a pre-market gap; None if ``t`` is after the last bar closes."""
    interval = bar_interval(bars)
    for i, b in enumerate(bars):
        if b.start <= t < b.start + interval:
            return i
        if b.start > t:  # t fell in a gap before this bar (no bar covers it) -> mark this bar
            return i
    return None


def _marker_ts(bars: list[Bar], idx: int | None) -> int | None:
    """Resolve a marker index (into the run ``bars``) to its bar's epoch seconds, or None.

    Emitting the timestamp — not the index — lets the front-end place the marker on *any* bar
    series that shares these bars' start times (the run window, or the full trading day the review
    workbench renders). The index still comes from :func:`compute_r_metrics`, so the R-metrics
    engine stays the single source of truth; we only translate its coordinate."""
    if idx is None or not 0 <= idx < len(bars):
        return None
    return int(bars[idx].start.timestamp())


def _engine_block(
    bars: list[Bar], settings: Settings, first_hit: datetime | None
) -> dict[str, Any]:
    """The engine-v2 detector's read of ``bars``, shaped for the review overlay (#216).

    Runs :func:`detect_day_with_settings` over the **same series the chart draws** (the full day
    when ``chart_bars`` was supplied to :func:`build_opportunity_chart`), so every emitted
    coordinate is an epoch timestamp into those bars — the front-end lays the pole/consolidation
    and prior-cycle bands, the H/L/E token row, and the base/peak/trigger markers straight onto the
    candles, mirroring ``spikes/viz_engine.py``. The token walk and significant cycles are
    recomputed with the same ``eps`` / volume floor ``detect_day`` uses internally, so the drawn
    prior-cycle bands match its ``cycle_num`` badge exactly.

    Always emits the per-bar ``tokens`` (meaningful even with no setup); ``setup: False`` when no
    pole forms, otherwise the full segment / gates / score / cycle context.
    """
    eps = token_eps(settings)
    tokens = tokenize(bars, eps=eps)
    # tokens[i-1] is the H/L/E step INTO bar i (bar 0 has no incoming step) — mirrors the spike's
    # per-bar token row.
    token_row = [
        {"t": int(bars[i].start.timestamp()), "tok": tokens[i - 1]} for i in range(1, len(bars))
    ]
    setup = detect_day_with_settings(bars, settings, first_hit)
    if setup is None:
        return {"setup": False, "tokens": token_row}

    seg = setup.segment
    # The prior significant cycles that COUNT toward exhaustion — same inputs detect_day used — so
    # the drawn bands match its cycle_num (a gapped earlier cycle is significant but not a counted
    # prior, so it isn't drawn).
    sig = significant_cycles(
        bars, segment_cycles(tokens), min_volume=settings.scan_min_5m_volume // 2
    )
    prior_cycles = [
        {
            "t0": int(bars[c.pole_start + 1].start.timestamp()),
            "t1": int(bars[c.cons_end].start.timestamp()),
            "n": n,
        }
        for n, c in enumerate(contiguous_prior_cycles(bars, sig, seg.base_idx), 1)
    ]
    trig_t = (
        int(bars[setup.trigger_idx].start.timestamp()) if setup.trigger_idx is not None else None
    )
    return {
        "setup": True,
        "passed": setup.passed,
        "takeable": setup.takeable,
        "score": setup.score,
        "contributions": dict(setup.contributions),
        "cycle_num": setup.cycle_num,
        "total_significant_cycles": setup.total_significant_cycles,
        "exhausted": setup.exhausted,
        "segment": {
            "base_t": int(bars[seg.base_idx].start.timestamp()),
            "peak_t": int(bars[seg.peak_idx].start.timestamp()),
            "cons_end_t": int(bars[seg.cons_end_idx].start.timestamp()),
            "pole_len": seg.pole_len,
            "cons_len": seg.cons_len,
            "token_string": "".join(seg.tokens),
        },
        "gates": [{"name": g.name, "passed": g.passed} for g in setup.gates],
        "levels": {
            "entry_trigger": setup.entry_trigger,
            "entry_fill": setup.entry_fill,
            "breakout": setup.breakout_level,
            "stop": setup.stop,
        },
        "trigger_t": trig_t,
        "prior_cycles": prior_cycles,
        "tokens": token_row,
    }


def build_opportunity_chart(
    bars: list[Bar],
    settings: Settings,
    *,
    first_hit: datetime | None = None,
    chart_bars: list[Bar] | None = None,
) -> ChartData:
    """Shape one run's trade annotations for the dashboard candlestick chart.

    ``first_hit`` gates the entry exactly as the EOD analysis does (#99): a setup may form in the
    pre-appearance lookback but may only *trigger* at/after the scanner appearance. The entry/stop
    levels are surfaced even when the setup never triggered (from the earliest actionable setup), so
    the chart still shows where a fill *would* have been.

    ``chart_bars`` chooses the series everything is computed and drawn over: pass the symbol's
    **full trading day** to render the un-clipped review-workbench series (#141). Defaults to
    ``bars`` (the legacy run-window chart).

    R-metrics, the engine block and the markers all read that one series, so the chart's ``max_r``
    matches the EOD report's (``report.py`` measures over the full day too) and the verdict always
    describes the setup the R was measured from. The run window must not be used here: it ends when
    the *scanner* stops hitting, which would truncate a live trade at a boundary the trade itself
    never saw (and would hide the exhaustion cycles ``detect_day`` counts across the day, #180).
    """
    render_bars = chart_bars if chart_bars is not None else bars
    rm = compute_r_metrics(render_bars, settings, first_hit=first_hit)
    max_r_idx = (
        rm.entry_index + rm.bars_to_max_r
        if rm.entry_index is not None and rm.bars_to_max_r is not None
        else None
    )
    first_hit_idx = _bar_containing(render_bars, first_hit) if first_hit is not None else None
    # Intraday VWAP over the *drawn* series. ``render_bars`` is the full trading day (04:00–16:00)
    # on every live chart path, so the cumulation anchors at the 04:00 open exactly as retail
    # platforms do; the legacy run-window default seeds at the window start (that chart is retired).
    vwaps = compute_vwap(render_bars)
    return ChartData(
        bars=[
            {
                "t": int(b.start.timestamp()),
                "o": b.open,
                "h": b.high,
                "l": b.low,
                "c": b.close,
                "v": b.volume,
                "vwap": vw,
            }
            for b, vw in zip(render_bars, vwaps, strict=True)
        ],
        levels={"entry": rm.entry_trigger, "stop": rm.stop},
        markers={
            "first_hit": _marker_ts(render_bars, first_hit_idx),
            "entry": _marker_ts(render_bars, rm.entry_index),
            "max_r": _marker_ts(render_bars, max_r_idx),
            "stop": _marker_ts(render_bars, rm.stop_index),
        },
        triggered=rm.triggered,
        stopped_out=rm.stopped_out,
        max_r=rm.max_r,
        engine=_engine_block(render_bars, settings, first_hit),
    )
