"""Golden parity (#179, engine-v2.md §11): the v2 pipeline reproduces the legacy detector.

For strict (non-``E``) in-window shapes, ``detect_setup(...).as_bullflag()`` must equal the legacy
``detect(...)`` field-for-field when run with legacy-equivalent params, and the two must accept /
reject the same shapes. This pins the #180 cut-over: any behavioural change then comes purely from
the settings flip (caps 8/6→4/4, entry 5→3 ticks, ``min_pole_pct`` 2%), not from the rewrite.

Parity is scoped to strict poles: v2's ``E``-tolerant base intentionally diverges for equal-high
poles (see features.py / engine-v2.md §11), so fixtures use clearly separated highs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from small_cap_stack.bullflag import detect, detect_setup
from small_cap_stack.capture import Bar

_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)  # 10:00 ET -> in window


def _bar(i: int, o: float, h: float, low: float, c: float, vol: float = 1000.0) -> Bar:
    return Bar(start=_T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c, volume=vol)


# Legacy-equivalent params: legacy detect() defaults are max_pole=8, max_flag=6, entry_offset=0.05,
# no height gate, no window gate.
_LEGACY_PARAMS = {
    "max_pole": 8,
    "max_cons": 6,
    "min_pole_pct": 0.0,
    "entry_offset": 0.05,
    "eps": 0.01,
    "gate_window": False,
}

# Accepted strict setups.
_SINGLE = [
    _bar(0, 5.0, 5.8, 4.6, 5.6, vol=1000),
    _bar(1, 5.6, 6.5, 5.5, 6.4, vol=2000),
    _bar(2, 6.0, 6.1, 5.6, 5.7, vol=800),
]
_MULTI = [
    _bar(0, 3.9, 4.0, 3.5, 3.95),
    _bar(1, 3.95, 5.0, 3.9, 4.9, vol=2000),
    _bar(2, 4.9, 6.0, 4.8, 5.9, vol=2000),
    _bar(3, 5.9, 7.0, 5.8, 6.9, vol=2000),
    _bar(4, 6.9, 6.5, 5.9, 6.0, vol=800),
]
_MULTIBAR_FLAG = [
    _bar(0, 4.0, 4.2, 3.8, 4.1),
    _bar(1, 4.1, 6.0, 4.0, 5.9, vol=3000),
    _bar(2, 5.9, 5.8, 5.4, 5.5, vol=900),
    _bar(3, 5.5, 5.6, 5.2, 5.3, vol=700),
]
_ACCEPTED = [_SINGLE, _MULTI, _MULTIBAR_FLAG]

# Rejections the legacy detector makes.
_DEEP_RETRACE = [  # flag_low 4.8 -> retracement (6.5-4.8)/(6.5-4.6) = 0.89 > 0.50
    _bar(0, 5.0, 5.8, 4.6, 5.6, vol=1000),
    _bar(1, 5.6, 6.5, 5.5, 6.4, vol=2000),
    _bar(2, 6.0, 6.1, 4.8, 5.0, vol=800),
]
_WICKY_PEAK = [  # peak upper wick 0.9 > 0.50
    _bar(0, 5.0, 5.8, 4.6, 5.6, vol=1000),
    _bar(1, 5.6, 6.5, 5.5, 5.6, vol=2000),
    _bar(2, 5.5, 5.5, 5.2, 5.3, vol=800),
]
_LOW_VOL_POLE = [  # peak vol 700 <= flag vol 900 -> not clean
    _bar(0, 5.0, 5.8, 4.6, 5.6, vol=1000),
    _bar(1, 5.6, 6.5, 5.5, 6.4, vol=700),
    _bar(2, 6.0, 6.1, 5.6, 5.7, vol=900),
]
_REJECTED = [_DEEP_RETRACE, _WICKY_PEAK, _LOW_VOL_POLE]


def _accepted(bars: list[Bar]) -> bool:
    s = detect_setup(bars, **_LEGACY_PARAMS)
    return s is not None and s.passed


def test_accepted_setups_match_legacy_field_for_field() -> None:
    for bars in _ACCEPTED:
        legacy = detect(bars)  # legacy defaults are the legacy-equivalent params above
        assert legacy is not None
        setup = detect_setup(bars, **_LEGACY_PARAMS)
        assert setup is not None and setup.passed
        assert setup.as_bullflag() == legacy


def test_rejections_agree_with_legacy() -> None:
    for bars in _REJECTED:
        assert detect(bars) is None
        assert _accepted(bars) is False


def test_accept_reject_partition_agrees() -> None:
    for bars in _ACCEPTED:
        assert detect(bars) is not None and _accepted(bars)
    for bars in _REJECTED:
        assert detect(bars) is None and not _accepted(bars)


def test_one_tick_flag_is_intended_eps_divergence() -> None:
    # A flag exactly 1 tick below the peak: legacy accepts (strict lower-high), but v2 treats the
    # 1-tick step as E (flat) under eps=1 tick and rejects (no L in the consolidation). This is the
    # intended eps noise filter, NOT a parity bug — it's why parity is scoped to clearly-separated
    # highs. Such a razor-thin pullback is not a meaningful consolidation.
    bars = [
        _bar(0, 5.0, 5.8, 4.6, 5.6, vol=1000),
        _bar(1, 5.6, 6.5, 5.5, 6.4, vol=2000),
        _bar(2, 6.0, 6.49, 5.6, 5.7, vol=800),  # high 6.49 == peak 6.50 - 1 tick
    ]
    assert detect(bars) is not None  # legacy accepts
    assert detect_setup(bars, **_LEGACY_PARAMS) is None  # v2: 1-tick step is E -> no L -> no shape
