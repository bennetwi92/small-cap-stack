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

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

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
    stop_index: int | None = None  # bar index where the stop was breached (stop-first convention)
    bars_to_max_r: int | None = None
    flag_len: int | None = None  # consolidation count of the traded setup (#98)
    retracement: float | None = None  # flag's retracement into the pole, fraction (#98)


def _iter_setups(bars: list[Bar], settings: Settings) -> Iterator[tuple[int, BullFlag]]:
    """Every index whose prefix ends in a valid bull flag, earliest first."""
    for i in range(1, len(bars)):
        bf = detect_with_settings(bars[: i + 1], settings)
        if bf is not None:
            yield i, bf


def _first_trigger(bars: list[Bar], setup_idx: int, entry_trigger: float) -> int | None:
    """Index of the first bar after the setup whose high reaches the entry trigger."""
    for j in range(setup_idx + 1, len(bars)):
        if bars[j].high >= entry_trigger:
            return j
    return None


def _measure(bars: list[Bar], bf: BullFlag, risk: float, entry_j: int) -> RMetrics:
    """Track a filled trade from its entry bar: Max R, MAE, stop-out (stop-first convention)."""
    entry = bf.entry_trigger
    bar = bars[entry_j]
    min_low = bar.low
    bars_to_max_r = 0
    stopped_out = False
    stop_index: int | None = None
    if bar.low <= bf.stop:
        # Same-bar trigger+stop: stop-first convention credits no favourable excursion.
        max_high = entry
        stopped_out = True
        stop_index = entry_j
    else:
        max_high = bar.high
        for k in range(entry_j + 1, len(bars)):
            b = bars[k]
            if b.low <= bf.stop:  # check the stop first (conservative intrabar ordering)
                min_low = min(min_low, b.low)
                stopped_out = True
                stop_index = k
                break
            if b.high > max_high:
                max_high = b.high
                bars_to_max_r = k - entry_j
            min_low = min(min_low, b.low)
    return RMetrics(
        setup_found=True,
        triggered=True,
        entry_trigger=entry,
        stop=bf.stop,
        initial_risk=risk,
        entry_price=entry,
        entry_index=entry_j,
        max_r=round((max_high - entry) / risk, 3),
        mae_r=round((entry - min_low) / risk, 3),
        stopped_out=stopped_out,
        stop_index=stop_index,
        bars_to_max_r=bars_to_max_r,
        flag_len=bf.flag_len,
        retracement=bf.retracement,
    )


def compute_r_metrics(
    bars: list[Bar], settings: Settings, *, first_hit: datetime | None = None
) -> RMetrics:
    """Measure the notional trade: R from the first setup that triggers an entry.

    ``first_hit`` (the moment the symbol appeared on the scanner for this run) gates the entry:
    a setup may *form* before appearance — its pole/flag can sit in the pre-appearance lookback —
    but it may only *trigger* at/after ``first_hit`` (issue #99). A breakout that already fired
    before we were aware is skipped in favour of a later setup, so R is never credited from a move
    we couldn't have taken. ``first_hit=None`` disables the gate (any trigger counts).
    """
    first_valid: tuple[BullFlag, float] | None = None
    for setup_idx, bf in _iter_setups(bars, settings):
        risk = round(bf.entry_trigger - bf.stop, 6)
        if risk <= 0:
            continue
        if first_valid is None:
            first_valid = (bf, risk)  # earliest actionable setup (for untriggered reporting)
        trig_j = _first_trigger(bars, setup_idx, bf.entry_trigger)
        if trig_j is None:
            continue  # this setup never triggers — try a later one
        if first_hit is not None and bars[trig_j].start < first_hit:
            continue  # triggered before appearance: not actionable — try a later setup
        return _measure(bars, bf, risk, trig_j)

    if first_valid is None:
        return RMetrics(setup_found=False)
    bf, risk = first_valid
    return RMetrics(
        setup_found=True,
        triggered=False,
        entry_trigger=bf.entry_trigger,
        stop=bf.stop,
        initial_risk=risk,
        flag_len=bf.flag_len,
        retracement=bf.retracement,
    )
