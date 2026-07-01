"""Bull-flag detection (issue #16): pure functions over a 5-min OHLCV series.

Strategy pattern (Warrior-style, see README): a short up-thrust *pole* of green extension
candles, immediately followed by a shallow *flag* of red consolidation candles that holds above
the pole's base. The breakout/entry is **5 ticks above the high of the last complete consolidation
candle** (decisions.md); the stop is the consolidation low. Constraints: **≤2 green extension
candles, ≤2 red consolidation candles**.

Pure and replayable — runs over the cached raw bars, so the definition can change retroactively.
"""

from __future__ import annotations

from dataclasses import dataclass

from .capture import Bar
from .config import Settings


def classify(bar: Bar) -> str:
    """green (close>open), red (close<open), or flat."""
    if bar.close > bar.open:
        return "green"
    if bar.close < bar.open:
        return "red"
    return "flat"


@dataclass(frozen=True)
class BullFlag:
    pole_len: int
    flag_len: int  # number of consolidation candles (#98 — bucket profitability by this)
    breakout_level: float  # high of the last complete consolidation candle
    entry_trigger: float  # breakout_level + entry offset (5 ticks)
    stop: float  # consolidation (flag) low
    # How deep the flag pulls back into the pole, as a fraction of the pole's height (#98):
    # 0.0 = held at the pole high, →1.0 = retraced to the pole base. Always in [0, 1) because the
    # flag low must hold above the pole base for a valid setup.
    retracement: float


def detect(
    bars: list[Bar],
    *,
    max_green: int = 2,
    max_red: int = 2,
    entry_offset: float = 0.05,
) -> BullFlag | None:
    """Detect a bull flag at the END of the series (the just-formed setup), else None."""
    if len(bars) < 2:
        return None

    j = len(bars) - 1

    # Trailing red run = the consolidation/flag.
    flag: list[Bar] = []
    while j >= 0 and classify(bars[j]) == "red" and len(flag) < max_red:
        flag.append(bars[j])
        j -= 1
    if not flag:
        return None  # no consolidation
    if j >= 0 and classify(bars[j]) == "red":
        return None  # consolidation longer than max_red -> invalid setup
    flag.reverse()

    # Green run immediately before = the pole.
    pole: list[Bar] = []
    while j >= 0 and classify(bars[j]) == "green" and len(pole) < max_green:
        pole.append(bars[j])
        j -= 1
    if not pole:
        return None  # no pole
    if j >= 0 and classify(bars[j]) == "green":
        return None  # pole longer than max_green -> violates the strategy
    pole.reverse()

    # The pullback must hold above the pole's base (not erase the move).
    pole_base = pole[0].low
    flag_low = min(b.low for b in flag)
    if flag_low <= pole_base:
        return None

    # Retracement of the flag into the pole, as a fraction of the pole's height. The pole is a run
    # of green candles so pole_high > pole_base (denominator > 0); flag_low is clamped into [base,
    # high] so the ratio stays in [0, 1) (flag_low > base was just enforced).
    pole_high = max(b.high for b in pole)
    retracement = (pole_high - min(flag_low, pole_high)) / (pole_high - pole_base)

    breakout = flag[-1].high
    return BullFlag(
        pole_len=len(pole),
        flag_len=len(flag),
        breakout_level=round(breakout, 4),
        entry_trigger=round(breakout + entry_offset, 4),
        stop=round(flag_low, 4),
        retracement=round(retracement, 4),
    )


def detect_with_settings(bars: list[Bar], settings: Settings) -> BullFlag | None:
    return detect(
        bars,
        max_green=settings.bull_flag_max_green,
        max_red=settings.bull_flag_max_red,
        entry_offset=settings.entry_offset_ticks * settings.tick_size,
    )
