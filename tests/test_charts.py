"""Tests for the annotated candlestick chart projection (#113).

The valuable, exhaustively-tested bit is the annotation math: every marker index must map to the
correct bar over synthetic series (store-raw / compute-on-read means the rendering must be exact).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from small_cap_stack.charts import build_opportunity_chart
from small_cap_stack.config import Settings

_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _bar(i: int, o: float, h: float, low: float, c: float):  # noqa: ANN202 - test helper
    from small_cap_stack.capture import Bar

    return Bar(start=_T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c, volume=1e3)


# Same bull flag as test_rmetrics: pole then flag, breakout 6.1, entry 6.15 (+5t), stop 5.6.
_POLE = _bar(0, 5.0, 6.2, 4.9, 6.0)
_FLAG = _bar(1, 6.0, 6.1, 5.6, 5.7)


def test_bars_serialised_in_order() -> None:
    bars = [_POLE, _FLAG]
    cd = build_opportunity_chart(bars, _settings())
    assert [b["t"] for b in cd.bars] == [int(_POLE.start.timestamp()), int(_FLAG.start.timestamp())]
    assert cd.bars[0] == {
        "t": int(_POLE.start.timestamp()),
        "o": 5.0,
        "h": 6.2,
        "l": 4.9,
        "c": 6.0,
        "v": 1e3,
    }


def test_triggered_markers_map_to_bars() -> None:
    bars = [
        _POLE,
        _FLAG,
        _bar(2, 5.7, 7.0, 5.7, 6.9),  # entry bar (high 7.0 >= 6.15)
        _bar(3, 6.9, 7.64, 6.8, 7.5),  # higher high -> Max R here
    ]
    cd = build_opportunity_chart(bars, _settings())
    assert cd.triggered and not cd.stopped_out
    assert cd.levels == {"entry": 6.15, "stop": 5.6}
    assert cd.markers["entry"] == 2
    assert cd.markers["max_r"] == 3  # entry_index (2) + bars_to_max_r (1)
    assert cd.markers["stop"] is None
    assert cd.markers["first_hit"] is None  # no appearance supplied


def test_stopped_out_marks_the_stop_bar() -> None:
    bars = [
        _POLE,
        _FLAG,
        _bar(2, 5.7, 6.2, 5.7, 6.0),  # entry
        _bar(3, 6.0, 6.1, 5.5, 5.5),  # low 5.5 <= stop 5.6 -> stopped here
    ]
    cd = build_opportunity_chart(bars, _settings())
    assert cd.triggered and cd.stopped_out
    assert cd.markers["entry"] == 2
    assert cd.markers["stop"] == 3


def test_same_bar_trigger_and_stop_share_the_index() -> None:
    bars = [_POLE, _FLAG, _bar(2, 5.7, 6.3, 5.4, 5.5)]  # trigger AND stop on bar 2
    cd = build_opportunity_chart(bars, _settings())
    assert cd.triggered and cd.stopped_out
    assert cd.markers["entry"] == 2 and cd.markers["stop"] == 2
    assert cd.markers["max_r"] == 2  # bars_to_max_r == 0 -> the 0R marker sits on the entry bar
    assert cd.max_r == 0.0


def test_setup_but_not_triggered_keeps_levels_without_trade_markers() -> None:
    bars = [_POLE, _FLAG, _bar(2, 5.7, 6.0, 5.65, 5.8)]  # high 6.0 < entry 6.15
    cd = build_opportunity_chart(bars, _settings())
    assert not cd.triggered
    assert cd.levels == {"entry": 6.15, "stop": 5.6}  # where a fill would have been
    assert cd.markers["entry"] is None
    assert cd.markers["max_r"] is None
    assert cd.markers["stop"] is None


def test_no_setup_has_null_levels_and_markers() -> None:
    bars = [_bar(0, 6.0, 6.1, 5.9, 5.95), _bar(1, 5.95, 6.0, 5.8, 5.85)]  # all red, no pole
    cd = build_opportunity_chart(bars, _settings())
    assert cd.levels == {"entry": None, "stop": None}
    assert cd.markers == {"first_hit": None, "entry": None, "max_r": None, "stop": None}
    assert len(cd.bars) == 2  # bars still drawn


def test_first_hit_maps_to_first_bar_at_or_after_appearance() -> None:
    bars = [_POLE, _FLAG, _bar(2, 5.7, 7.0, 5.7, 6.9)]
    # Appearance at +7min lands between bar 1 (+5) and bar 2 (+10) -> marker on bar 2.
    cd = build_opportunity_chart(bars, _settings(), first_hit=_T0 + timedelta(minutes=7))
    assert cd.markers["first_hit"] == 2
    # Exactly on a bar start is inclusive.
    cd_exact = build_opportunity_chart(bars, _settings(), first_hit=_T0 + timedelta(minutes=5))
    assert cd_exact.markers["first_hit"] == 1


def test_first_hit_after_all_bars_is_null() -> None:
    bars = [_POLE, _FLAG]
    cd = build_opportunity_chart(bars, _settings(), first_hit=_T0 + timedelta(minutes=60))
    assert cd.markers["first_hit"] is None


def test_first_hit_gates_the_entry_marker() -> None:
    # Setup forms at +5 but only triggers at +15; appearance at +12 gates the pre-appearance
    # breakout, so the entry marker sits on the post-appearance trigger bar (#99).
    bars = [
        _POLE,
        _FLAG,
        _bar(2, 5.7, 6.0, 5.65, 5.8),  # +10: no trigger
        _bar(3, 5.8, 7.0, 5.75, 6.9),  # +15: triggers here
    ]
    cd = build_opportunity_chart(bars, _settings(), first_hit=_T0 + timedelta(minutes=12))
    assert cd.triggered and cd.markers["entry"] == 3
