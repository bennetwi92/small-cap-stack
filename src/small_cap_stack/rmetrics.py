"""R-multiple measurement (issue #18): would the notional trade have triggered, and how far?

Phase-1 places no orders — but to learn the strategy we measure, per opportunity, what *would*
have happened:
- the bull-flag setup defines the **entry trigger** (5 ticks above the last complete consolidation
  candle's high) and the **stop** (consolidation low) — so initial risk R = entry - stop;
- did a later bar's high reach the entry trigger (did it fill)?
- after entry, the peak favourable excursion in R (**Max R**) and the worst adverse excursion
  (MAE in R), and whether the stop was hit.

Intrabar ordering is unknowable from OHLC alone, so measurement adopts a deliberately
conservative **stop-first** convention: on (and after) the entry bar, if a bar breaches the
stop we treat the trade as closed at the stop on that bar — its adverse excursion is recorded
but its high is *not* credited to Max R, and no later bar is measured. This never overstates the
strategy's edge (the whole point of Phase-1 measurement).

Pure and replayable over the cached raw bars, so the entry/stop definition can change and be
recomputed retroactively.
"""

from __future__ import annotations

from dataclasses import dataclass

from .bullflag import BullFlag, detect_with_settings
from .capture import Bar
from .config import Settings


@dataclass(frozen=True)
class RMetrics:
    setup_found: bool
    triggered: bool = False
    entry_trigger: float | None = None
    stop: float | None = None
    initial_risk: float | None = None
    entry_price: float | None = None
    entry_index: int | None = None
    max_r: float | None = None  # peak favourable excursion, in R
    mae_r: float | None = None  # worst adverse excursion after entry, in R
    stopped_out: bool = False
    bars_to_max_r: int | None = None


def _first_setup(bars: list[Bar], settings: Settings) -> tuple[int, BullFlag] | None:
    """Earliest index whose prefix ends in a valid bull flag."""
    for i in range(1, len(bars)):
        bf = detect_with_settings(bars[: i + 1], settings)
        if bf is not None:
            return i, bf
    return None


def compute_r_metrics(bars: list[Bar], settings: Settings) -> RMetrics:
    found = _first_setup(bars, settings)
    if found is None:
        return RMetrics(setup_found=False)
    setup_idx, bf = found
    risk = round(bf.entry_trigger - bf.stop, 6)
    if risk <= 0:
        return RMetrics(setup_found=False)

    triggered = False
    entry_index: int | None = None
    max_high: float | None = None
    min_low: float | None = None
    stopped_out = False
    bars_to_max_r: int | None = None

    for j in range(setup_idx + 1, len(bars)):
        bar = bars[j]
        if not triggered:
            if bar.high < bf.entry_trigger:
                continue
            triggered = True
            entry_index = j
            min_low = bar.low
            bars_to_max_r = 0
            # Same-bar trigger+stop: stop-first convention credits no favourable excursion.
            if bar.low <= bf.stop:
                max_high = bf.entry_trigger
                stopped_out = True
                break
            max_high = bar.high
            continue
        # post-entry tracking — check the stop first (conservative intrabar ordering).
        assert max_high is not None and min_low is not None and entry_index is not None
        if bar.low <= bf.stop:
            min_low = min(min_low, bar.low)
            stopped_out = True
            break
        if bar.high > max_high:
            max_high = bar.high
            bars_to_max_r = j - entry_index
        min_low = min(min_low, bar.low)

    if not triggered:
        return RMetrics(
            setup_found=True,
            triggered=False,
            entry_trigger=bf.entry_trigger,
            stop=bf.stop,
            initial_risk=risk,
        )

    assert max_high is not None and min_low is not None
    entry = bf.entry_trigger
    return RMetrics(
        setup_found=True,
        triggered=True,
        entry_trigger=entry,
        stop=bf.stop,
        initial_risk=risk,
        entry_price=entry,
        entry_index=entry_index,
        max_r=round((max_high - entry) / risk, 3),
        mae_r=round((entry - min_low) / risk, 3),
        stopped_out=stopped_out,
        bars_to_max_r=bars_to_max_r,
    )
