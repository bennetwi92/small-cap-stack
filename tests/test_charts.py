"""Tests for the annotated candlestick chart projection (#113, timestamp markers #141).

The valuable, exhaustively-tested bit is the annotation math: every marker must carry the epoch
timestamp of the correct bar over synthetic series (store-raw / compute-on-read means the rendering
must be exact), and ``chart_bars`` must render a wider series without moving the markers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from small_cap_stack.charts import build_opportunity_chart
from small_cap_stack.config import Settings

_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _bar(i: int, o: float, h: float, low: float, c: float, vol: float = 1e3):  # noqa: ANN202
    from small_cap_stack.capture import Bar

    return Bar(start=_T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c, volume=vol)


def _ts(i: int) -> int:
    """Epoch seconds of the 5-min bar at index ``i`` (what a marker on that bar should carry)."""
    return int((_T0 + timedelta(minutes=5 * i)).timestamp())


# Same bull flag as test_rmetrics: launch bar + one higher-high pole bar (heavier volume) + a red
# flag at index 2. breakout 6.1, entry 6.11 (+1t, v2), stop 5.6. The breakout is always a later bar.
_LAUNCH = _bar(0, 5.0, 5.8, 4.6, 5.7)
_POLE = _bar(1, 5.7, 6.5, 5.6, 6.4, vol=2000)
_FLAG = _bar(2, 6.4, 6.1, 5.6, 5.7)
_SETUP = [_LAUNCH, _POLE, _FLAG]


def test_bars_serialised_in_order() -> None:
    bars = [_LAUNCH, _POLE]
    cd = build_opportunity_chart(bars, _settings())
    assert [b["t"] for b in cd.bars] == [
        int(_LAUNCH.start.timestamp()),
        int(_POLE.start.timestamp()),
    ]
    assert cd.bars[0] == {
        "t": int(_LAUNCH.start.timestamp()),
        "o": 5.0,
        "h": 5.8,
        "l": 4.6,
        "c": 5.7,
        "v": 1e3,
    }


def test_triggered_markers_map_to_bars() -> None:
    bars = [
        *_SETUP,
        _bar(3, 5.7, 7.0, 5.7, 6.9),  # entry bar (high 7.0 >= 6.15)
        _bar(4, 6.9, 7.64, 6.8, 7.5),  # higher high -> Max R here
    ]
    cd = build_opportunity_chart(bars, _settings())
    assert cd.triggered and not cd.stopped_out
    assert cd.levels == {"entry": 6.11, "stop": 5.6}
    assert cd.markers["entry"] == _ts(3)
    assert cd.markers["max_r"] == _ts(4)  # entry_index (3) + bars_to_max_r (1)
    assert cd.markers["stop"] is None
    assert cd.markers["first_hit"] is None  # no appearance supplied


def test_stopped_out_marks_the_stop_bar() -> None:
    bars = [
        *_SETUP,
        _bar(3, 5.7, 6.2, 5.7, 6.0),  # entry
        _bar(4, 6.0, 6.1, 5.5, 5.5),  # low 5.5 <= stop 5.6 -> stopped here
    ]
    cd = build_opportunity_chart(bars, _settings())
    assert cd.triggered and cd.stopped_out
    assert cd.markers["entry"] == _ts(3)
    assert cd.markers["stop"] == _ts(4)


def test_same_bar_trigger_and_stop_share_the_index() -> None:
    bars = [*_SETUP, _bar(3, 5.7, 6.3, 5.4, 5.5)]  # trigger AND stop on bar 3
    cd = build_opportunity_chart(bars, _settings())
    assert cd.triggered and cd.stopped_out
    assert cd.markers["entry"] == _ts(3) and cd.markers["stop"] == _ts(3)
    # bars_to_max_r == 0 -> the 0R marker sits on the entry bar
    assert cd.markers["max_r"] == _ts(3)
    assert cd.max_r == 0.0


def test_setup_but_not_triggered_keeps_levels_without_trade_markers() -> None:
    # v2: a setup-found-but-not-triggered case is a STALE break — the flag forms and its
    # consolidation runs on, but the only break comes >30 min after appearance, so the trigger is
    # dropped (#130). Its levels are still surfaced; no trade markers.
    bars = [
        *_SETUP,
        _bar(3, 5.7, 5.9, 5.6, 5.8),
        _bar(4, 5.8, 5.9, 5.6, 5.7),
        _bar(5, 5.7, 5.9, 5.6, 5.8),
        _bar(6, 5.8, 5.9, 5.6, 5.7),
        _bar(7, 5.7, 5.9, 5.6, 5.8),
        _bar(8, 5.8, 7.0, 5.8, 6.9),  # +40 min: breaks, but too stale to be takeable
    ]
    cd = build_opportunity_chart(bars, _settings(), first_hit=_T0)  # appeared at +0
    assert not cd.triggered
    assert cd.levels == {"entry": 5.91, "stop": 5.6}  # where a fill would have been
    assert cd.markers["entry"] is None
    assert cd.markers["max_r"] is None
    assert cd.markers["stop"] is None


def test_no_setup_has_null_levels_and_markers() -> None:
    bars = [_bar(0, 6.0, 6.1, 5.9, 5.95), _bar(1, 5.95, 6.0, 5.8, 5.85)]  # all red, no pole
    cd = build_opportunity_chart(bars, _settings())
    assert cd.levels == {"entry": None, "stop": None}
    assert cd.markers == {"first_hit": None, "entry": None, "max_r": None, "stop": None}
    assert len(cd.bars) == 2  # bars still drawn


def test_first_hit_marks_the_bar_that_contains_the_appearance() -> None:
    bars = [*_SETUP, _bar(3, 5.7, 7.0, 5.7, 6.9)]  # bars at +0/+5/+10/+15 (5-min)
    # Appearance at +7 lands INSIDE bar 1 [+5, +10) -> marker on bar 1, not the next bar (#122).
    cd = build_opportunity_chart(bars, _settings(), first_hit=_T0 + timedelta(minutes=7))
    assert cd.markers["first_hit"] == _ts(1)
    # A later mid-bar appearance marks its own bar.
    cd2 = build_opportunity_chart(bars, _settings(), first_hit=_T0 + timedelta(minutes=12))
    assert cd2.markers["first_hit"] == _ts(2)
    # Exactly on a bar start marks that bar (inclusive).
    cd_exact = build_opportunity_chart(bars, _settings(), first_hit=_T0 + timedelta(minutes=5))
    assert cd_exact.markers["first_hit"] == _ts(1)


def test_first_hit_after_all_bars_is_null() -> None:
    bars = [_LAUNCH, _POLE]
    cd = build_opportunity_chart(bars, _settings(), first_hit=_T0 + timedelta(minutes=60))
    assert cd.markers["first_hit"] is None


def test_first_hit_gates_the_entry_marker() -> None:
    # Setup forms at +10 but only triggers at +20; appearance at +17 sits after the flag and before
    # the trigger, so the entry marker lands on the post-appearance trigger bar (#99).
    bars = [
        *_SETUP,
        _bar(3, 5.7, 6.0, 5.65, 5.8),  # +15: no trigger
        _bar(4, 5.8, 7.0, 5.75, 6.9),  # +20: triggers here
    ]
    cd = build_opportunity_chart(bars, _settings(), first_hit=_T0 + timedelta(minutes=17))
    assert cd.triggered and cd.markers["entry"] == _ts(4)


def test_chart_bars_renders_full_series_without_moving_markers() -> None:
    # R-metrics are computed over the run window, but chart_bars renders a wider full-day series.
    run = [
        *_SETUP,
        _bar(3, 5.7, 7.0, 5.7, 6.9),  # entry bar
        _bar(4, 6.9, 7.64, 6.8, 7.5),  # Max R bar
    ]
    # A pre-open bar (-2) and a late bar (6) that exist in the full day but not the run window.
    full_day = [_bar(-2, 4.0, 4.2, 3.9, 4.1), *run, _bar(6, 7.5, 7.6, 7.2, 7.3)]
    cd = build_opportunity_chart(run, _settings(), chart_bars=full_day)

    # The whole day is drawn…
    assert [b["t"] for b in cd.bars] == [int(b.start.timestamp()) for b in full_day]
    # …but the markers still carry the run bars' timestamps, landing on the right full-day candle.
    assert cd.markers["entry"] == _ts(3)
    assert cd.markers["max_r"] == _ts(4)
    assert cd.levels == {"entry": 6.11, "stop": 5.6}


def test_chart_bars_defaults_to_the_run_window() -> None:
    bars = [_LAUNCH, _POLE]
    cd = build_opportunity_chart(bars, _settings())
    assert [b["t"] for b in cd.bars] == [int(b.start.timestamp()) for b in bars]
