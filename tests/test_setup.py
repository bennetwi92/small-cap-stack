"""Tests for stage 4 assembly (#179) and the trigger/fill entry split (#182/#190).

The trigger (mechanical breakout confirmation, +1 tick) and the fill (conservative
slippage-modeled price used for R-measurement, +3 ticks) are distinct concepts, confirmed by the
trader: "the 3 ticks does become a slippage modelled fill price for R. The trigger is always the
tick above the last high in the consolidation. Often I actually fill at that price anyway. 3 ticks
is being conservative."
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from small_cap_stack.bullflag import detect_setup, detect_setup_with_settings
from small_cap_stack.capture import Bar
from small_cap_stack.config import Settings

_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)  # 10:00 ET -> in window


def _bar(i: int, o: float, h: float, low: float, c: float, vol: float = 1000.0) -> Bar:
    return Bar(start=_T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c, volume=vol)


# A clean single-bar-pole setup (base=green launch, peak=green thrust, 1-bar cons).
_BARS = [
    _bar(0, 5.0, 5.8, 4.6, 5.6, vol=1000),
    _bar(1, 5.6, 6.5, 5.5, 6.4, vol=2000),
    _bar(2, 6.0, 6.1, 5.6, 5.7, vol=800),
]


def test_trigger_and_fill_are_distinct_at_default_offsets() -> None:
    setup = detect_setup(_BARS)
    assert setup is not None
    assert setup.breakout_level == 6.1
    assert setup.entry_trigger == pytest.approx(6.11)  # +1 tick (default entry_offset=0.01)
    assert setup.entry_fill == pytest.approx(6.13)  # +3 ticks (default fill_offset=0.03)
    assert setup.entry_fill > setup.entry_trigger  # the fill is always the more conservative price


def test_custom_offsets_are_independent() -> None:
    setup = detect_setup(_BARS, entry_offset=0.02, fill_offset=0.05)
    assert setup is not None
    assert setup.entry_trigger == pytest.approx(6.12)
    assert setup.entry_fill == pytest.approx(6.15)


def test_settings_driven_offsets_match_locked_ticks() -> None:
    settings = Settings()
    assert settings.bull_flag_trigger_offset_ticks == 1
    assert settings.bull_flag_fill_offset_ticks == 3
    setup = detect_setup_with_settings(_BARS, settings)
    assert setup is not None
    tick = settings.tick_size
    assert setup.entry_trigger == pytest.approx(setup.breakout_level + 1 * tick)
    assert setup.entry_fill == pytest.approx(setup.breakout_level + 3 * tick)


def test_trigger_and_fill_are_distinct_levels() -> None:
    """The 1-tick mechanical trigger and the 3-tick conservative R fill must not collapse (#182)."""
    setup = detect_setup(_BARS)
    assert setup is not None
    assert setup.entry_trigger != setup.entry_fill
    assert setup.entry_fill > setup.entry_trigger
