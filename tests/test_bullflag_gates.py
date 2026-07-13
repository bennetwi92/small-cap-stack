"""Tests for engine-v2 stage 4a gates (#179): each gate's boundary + accept/reject aggregation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from small_cap_stack.bullflag import evaluate, extract, segment_at_end, tokenize
from small_cap_stack.bullflag.features import FeatureVector
from small_cap_stack.bullflag.gates import passed
from small_cap_stack.capture import Bar

_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)  # 10:00 ET -> in window


def _bar(i: int, o: float, h: float, low: float, c: float, vol: float = 1000.0) -> Bar:
    return Bar(start=_T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c, volume=vol)


# Clean setup: retracement ~0.47, wick 0.1, pole_height ~0.41, peak vol > cons vol.
_BARS = [
    _bar(0, 5.0, 5.8, 4.6, 5.6, vol=1000),
    _bar(1, 5.6, 6.5, 5.5, 6.4, vol=2000),
    _bar(2, 6.0, 6.1, 5.6, 5.7, vol=800),
]
_DEFAULTS = {
    "max_pole": 4,
    "max_cons": 4,
    "max_peak_wick": 0.50,
    "min_pole_pct": 0.02,
    "max_retracement": 0.50,
}


def _fv() -> FeatureVector:
    seg = segment_at_end(_BARS, tokenize(_BARS, eps=0.01), max_pole=4, max_cons=4)
    assert seg is not None
    return extract(_BARS, seg)


def test_clean_setup_passes_all() -> None:
    gates = evaluate(_fv(), **_DEFAULTS)
    assert passed(gates) is True
    assert {g.name for g in gates} == {
        "pole_len",
        "cons_len",
        "vol_peak_gt_cons",
        "wick_peak",
        "peak_green",
        "pole_height",
        "cons_retracement",
        "cons_holds_base",
    }


def test_pole_height_gate_boundary() -> None:
    # pole_height ~0.41; a 0.99 floor rejects it (the pole_height gate specifically).
    gates = evaluate(_fv(), **{**_DEFAULTS, "min_pole_pct": 0.99})
    assert passed(gates) is False
    assert next(g for g in gates if g.name == "pole_height").passed is False


def test_retracement_gate_boundary() -> None:
    assert passed(evaluate(_fv(), **{**_DEFAULTS, "max_retracement": 0.10})) is False


def test_wick_gate_boundary() -> None:
    assert passed(evaluate(_fv(), **{**_DEFAULTS, "max_peak_wick": 0.05})) is False


def test_window_gate_optional() -> None:
    assert not any(g.name == "loc_in_window" for g in evaluate(_fv(), **_DEFAULTS))
    with_win = evaluate(_fv(), **_DEFAULTS, gate_window=True)
    loc = next(g for g in with_win if g.name == "loc_in_window")
    assert loc.passed is True  # 10:00 ET is in window
