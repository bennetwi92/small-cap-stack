"""Annotated candlestick chart data per opportunity (issue #113).

A **pure rendering projection** over already-captured raw data — no new capture, fully backcastable
over collected days (store-raw / compute-on-read). Given a run's 5-min bars, the strategy settings,
and the run's scanner appearance, it produces the OHLC series plus the annotation markers a
front-end needs to draw where the notional trade would have played out:

- ``first_hit`` — the bar at/after the symbol first appeared on the scanner;
- ``entry`` — the bar whose high crossed the entry trigger (the fill);
- ``max_r`` — the bar that set peak favourable excursion (Max R);
- ``stop`` — the bar whose low breached the stop (stop-first convention).

The marker *indices* and the entry/stop price *levels* come straight from :func:`compute_r_metrics`,
so the chart never re-derives the entry/stop/stop-first logic — one source of truth. Rendering
itself is done client-side in the GitHub Pages dashboard (issue #70); this module only shapes the
JSON. It stays offline-friendly (no broker, no plotting deps), so it runs in cloud dev.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .capture import Bar
from .config import Settings
from .rmetrics import bar_interval, compute_r_metrics


@dataclass(frozen=True)
class ChartData:
    """Bars + annotations for one opportunity's chart (JSON-ready via ``dataclasses.asdict``)."""

    bars: list[dict[str, float | int]]  # {"t": epoch_s, "o", "h", "l", "c", "v"} per 5-min bar
    levels: dict[str, float | None]  # {"entry": trigger, "stop": stop} — None when no setup formed
    markers: dict[str, int | None]  # bar index per event: first_hit / entry / max_r / stop
    triggered: bool
    stopped_out: bool
    max_r: float | None


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


def build_opportunity_chart(
    bars: list[Bar], settings: Settings, *, first_hit: datetime | None = None
) -> ChartData:
    """Shape one run's bars + trade annotations for the dashboard candlestick chart.

    ``first_hit`` gates the entry exactly as the EOD analysis does (#99): a setup may form in the
    pre-appearance lookback but may only *trigger* at/after the scanner appearance. The entry/stop
    levels are surfaced even when the setup never triggered (from the earliest actionable setup), so
    the chart still shows where a fill *would* have been.
    """
    rm = compute_r_metrics(bars, settings, first_hit=first_hit)
    max_r_idx = (
        rm.entry_index + rm.bars_to_max_r
        if rm.entry_index is not None and rm.bars_to_max_r is not None
        else None
    )
    return ChartData(
        bars=[
            {
                "t": int(b.start.timestamp()),
                "o": b.open,
                "h": b.high,
                "l": b.low,
                "c": b.close,
                "v": b.volume,
            }
            for b in bars
        ],
        levels={"entry": rm.entry_trigger, "stop": rm.stop},
        markers={
            "first_hit": _bar_containing(bars, first_hit) if first_hit is not None else None,
            "entry": rm.entry_index,
            "max_r": max_r_idx,
            "stop": rm.stop_index,
        },
        triggered=rm.triggered,
        stopped_out=rm.stopped_out,
        max_r=rm.max_r,
    )
