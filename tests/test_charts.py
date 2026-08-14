"""Tests for the annotated candlestick chart projection (#113, timestamp markers #141).

The valuable, exhaustively-tested bit is the annotation math: every marker must carry the epoch
timestamp of the correct bar over synthetic series (store-raw / compute-on-read means the rendering
must be exact), and ``chart_bars`` must render a wider series without moving the markers.
"""

from __future__ import annotations

import json
from dataclasses import fields
from datetime import timedelta

from small_cap_stack.bullflag.features import FeatureVector
from small_cap_stack.charts import build_opportunity_chart
from small_cap_stack.config import Settings
from tests.support import T0 as _T0
from tests.support import bar as _bar
from tests.support import settings


def _settings() -> Settings:
    return settings()


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
    # R-metrics are computed over chart_bars (the full day); the markers still land on the same
    # candles here because the extra pre-open/late bars don't change the setup or the Max R bar.
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


def test_max_r_measures_past_the_run_window_when_the_full_day_is_drawn() -> None:
    """A trade still open at the run's end keeps running: measure to the stop, not to the boundary.

    The run window closes when the *scanner* stops hitting, which is not a trade event — clipping
    there truncated live trades and made the chart's Max R disagree with the EOD report's (which
    measures over the full day). Regression for the run-window/full-day split.
    """
    run = [
        *_SETUP,
        _bar(3, 5.7, 7.0, 5.7, 6.9),  # entry bar (entry 6.11, stop 5.6 -> risk 0.51)
        _bar(4, 6.9, 7.64, 6.8, 7.5),  # best bar INSIDE the run window
    ]
    # The move continues after the run window ends, never trading back down to the 5.6 stop.
    full_day = [*run, _bar(5, 7.5, 8.15, 7.4, 8.1)]

    clipped = build_opportunity_chart(run, _settings())
    full = build_opportunity_chart(run, _settings(), chart_bars=full_day)

    assert clipped.max_r is not None and full.max_r is not None
    # Same trade, same risk — the full day just sees the rest of it.
    assert clipped.levels == full.levels == {"entry": 6.11, "stop": 5.6}
    assert full.max_r > clipped.max_r
    assert full.markers["max_r"] == _ts(5)  # the later high, outside the run window
    assert clipped.markers["max_r"] == _ts(4)


def test_chart_bars_defaults_to_the_run_window() -> None:
    bars = [_LAUNCH, _POLE]
    cd = build_opportunity_chart(bars, _settings())
    assert [b["t"] for b in cd.bars] == [int(b.start.timestamp()) for b in bars]


# --- engine-v2 overlay block (#216) --------------------------------------------------------------
# The `engine` block carries the detector's read of the DRAWN series so the review page can overlay
# the same segmentation the spike (viz_engine) shows: per-bar H/L/E tokens, pole/cons segment,
# gates/score, and the prior-cycle exhaustion context. Every coordinate is an epoch timestamp.


def test_engine_block_present_for_a_setup() -> None:
    bars = [*_SETUP, _bar(3, 5.7, 7.0, 5.7, 6.9), _bar(4, 6.9, 7.64, 6.8, 7.5)]
    eng = build_opportunity_chart(bars, _settings()).engine
    assert eng["setup"] is True
    # Segment lands on the launch/pole/flag bars, as timestamps into the drawn series.
    assert eng["segment"]["base_t"] == _ts(0)
    assert eng["segment"]["peak_t"] == _ts(1)
    assert eng["segment"]["cons_end_t"] == _ts(2)
    assert eng["segment"]["pole_len"] == 1
    assert eng["segment"]["token_string"] == "HL"
    # Levels mirror the surfaced entry trigger; the breakout bar (index 3) is the trigger.
    assert eng["levels"]["entry_trigger"] == 6.11
    assert eng["trigger_t"] == _ts(3)
    assert isinstance(eng["passed"], bool)
    assert eng["gates"] and all({"name", "passed"} <= g.keys() for g in eng["gates"])
    # Per-bar tokens: one per bar after the first (the step INTO that bar).
    assert eng["tokens"] == [
        {"t": _ts(1), "tok": "H"},
        {"t": _ts(2), "tok": "L"},
        {"t": _ts(3), "tok": "H"},
        {"t": _ts(4), "tok": "H"},
    ]


def test_engine_block_no_setup_still_carries_tokens() -> None:
    bars = [_bar(0, 6.0, 6.1, 5.9, 5.95), _bar(1, 5.95, 6.0, 5.8, 5.85)]  # all red, no pole
    eng = build_opportunity_chart(bars, _settings()).engine
    assert eng["setup"] is False
    assert "segment" not in eng
    assert eng["tokens"] == [{"t": _ts(1), "tok": "L"}]  # 6.1 -> 6.0 = lower high


# --- the results table's columns: outcome restatements + the whole feature vector ----------------
# The results grid ranks opportunities by any single engine input, so the payload must carry every
# feature the detector gated/scored on — and carry it as *valid JSON* (an inf would kill the page).


def test_max_gain_pct_and_mae_restate_the_measured_trade() -> None:
    bars = [*_SETUP, _bar(3, 5.7, 7.0, 5.7, 6.9), _bar(4, 6.9, 7.64, 6.8, 7.5)]
    cd = build_opportunity_chart(bars, _settings())
    assert cd.triggered is True
    # Fill = breakout 6.1 + 3 ticks = 6.13; the peak high is 7.64.
    assert cd.max_gain_pct == round((7.64 - 6.13) / 6.13, 5)
    # Never stopped: MAE is measured off the entry bar's low (5.7).
    assert cd.mae_r == round((6.13 - 5.7) / (6.13 - 5.6), 3)


def test_no_setup_leaves_the_outcome_columns_empty() -> None:
    bars = [_bar(0, 6.0, 6.1, 5.9, 5.95), _bar(1, 5.95, 6.0, 5.8, 5.85)]
    cd = build_opportunity_chart(bars, _settings())
    assert cd.max_gain_pct is None and cd.mae_r is None


def test_engine_block_carries_every_feature() -> None:
    bars = [*_SETUP, _bar(3, 5.7, 7.0, 5.7, 6.9), _bar(4, 6.9, 7.64, 6.8, 7.5)]
    eng = build_opportunity_chart(bars, _settings()).engine
    feats = eng["features"]
    # Every FeatureVector field is published except bars_before_scan (always None until the
    # scanner_hits join lands) — a new engine feature must show up here or the table can't rank it.
    expected = {f.name for f in fields(FeatureVector)} - {"bars_before_scan"}
    assert set(feats) == expected
    # Spot-check the gate inputs against the fixture's geometry: the pole runs from the LAUNCH
    # bar's low (4.6 — the base is a low, not an open) to the peak high 6.5, the flag lows at 5.6,
    # and the peak bar traded 2000 against the flag's 1000.
    assert feats["pole_len"] == 1 and feats["cons_len"] == 1
    assert feats["token_string"] == "HL"
    assert feats["retracement"] == round((6.5 - 5.6) / (6.5 - 4.6), 4)
    assert feats["pole_height_pct"] == round((6.5 - 4.6) / 4.6, 4)
    assert feats["vol_ratio"] == 2.0
    assert feats["peak_gt_cons"] is True and feats["holds_base"] is True
    # Gates now carry the measured value, so a rejection reads as "by how much".
    assert all({"name", "passed", "value"} <= g.keys() for g in eng["gates"])
    assert {g["name"]: g["value"] for g in eng["gates"]}["cons_retracement"] == feats["retracement"]


def test_infinite_features_are_published_as_null_not_infinity() -> None:
    # A consolidation that traded no volume makes vol_ratio +inf. json.dumps writes a bare
    # `Infinity`, which is NOT valid JSON — the browser's response.json() throws and the whole
    # results page dies. Every float leaving charts.py must be finite or null.
    bars = [
        _LAUNCH,
        _POLE,
        _bar(2, 6.4, 6.1, 5.6, 5.7, vol=0.0),
        _bar(3, 5.7, 7.0, 5.7, 6.9),
    ]
    eng = build_opportunity_chart(bars, _settings()).engine
    assert eng["features"]["vol_ratio"] is None
    assert {g["name"]: g["value"] for g in eng["gates"]}["vol_peak_gt_cons"] is None
    json.loads(json.dumps(eng))  # round-trips as strict JSON (allow_nan would have hidden this)
    assert "Infinity" not in json.dumps(eng)


def test_engine_block_maps_onto_full_day_bars() -> None:
    # The engine runs over the DRAWN series (chart_bars = the full day), so its timestamps land on
    # the real launch/pole/flag/trigger bars even though their full-day indices differ.
    run = [*_SETUP, _bar(3, 5.7, 7.0, 5.7, 6.9), _bar(4, 6.9, 7.64, 6.8, 7.5)]
    full_day = [_bar(-2, 4.0, 4.2, 3.9, 4.1), *run, _bar(6, 7.5, 7.6, 7.2, 7.3)]
    eng = build_opportunity_chart(run, _settings(), chart_bars=full_day).engine
    assert eng["setup"] is True
    assert eng["segment"]["peak_t"] == _ts(1)
    assert eng["segment"]["cons_end_t"] == _ts(2)
    assert eng["trigger_t"] == _ts(3)


# --- appearance marker across a pre-market gap (#533) ---------------------------------------
# `_bar_containing` has two exits and only one was covered. The uncovered one is the gap path:
# pre-market bars are sparse for a thin name, so a scanner appearance can land in a *hole* — a
# minute no bar covers. Getting it wrong draws the "seen" dot on the wrong candle, which is the
# class of bug #407 was, and it is invisible without data that has a hole in it.


def test_appearance_in_a_premarket_gap_marks_the_next_bar() -> None:
    """A `first_hit` no bar contains marks the first bar *after* it, not None and not the one
    before. The dot means "we knew about it by here", so the bar that carries it must be one the
    trader could have acted on."""
    from small_cap_stack.charts import _bar_containing

    # A hole where i=2 would be. Three real 5-min steps and one 10-min jump, so the *modal*
    # spacing stays 5 — `bar_interval` takes the mode precisely so a hole can't inflate it, and a
    # two-bar fixture would have made the gap itself the interval and silently covered the hole.
    bars = [
        _bar(0, 5.0, 5.8, 4.6, 5.7),
        _bar(1, 5.7, 6.5, 5.6, 6.4),
        _bar(3, 6.4, 6.1, 5.6, 5.7),  # i=2 is missing: nothing covers 14:10–14:15
        _bar(4, 5.7, 6.0, 5.5, 5.9),
    ]
    in_the_gap = _T0 + timedelta(minutes=12)  # inside the hole, after bar 1 closes
    assert _bar_containing(bars, in_the_gap) == 2  # the bar at i=3 — the next one, not the last


def test_appearance_inside_a_bar_marks_that_bar_not_the_next() -> None:
    """The covered path, pinned beside the gap one so the pair reads as a contrast — a `t` a bar
    genuinely contains marks *that* bar (#122: the earlier rule drew it a bar late)."""
    from small_cap_stack.charts import _bar_containing

    bars = [_bar(0, 5.0, 5.8, 4.6, 5.7), _bar(1, 5.7, 6.5, 5.6, 6.4)]
    assert _bar_containing(bars, _T0 + timedelta(minutes=3)) == 0  # mid-bar-0
    assert _bar_containing(bars, _T0) == 0  # exactly on the open
    assert _bar_containing(bars, _T0 + timedelta(minutes=5)) == 1  # exactly on bar 1's open


def test_appearance_after_the_last_bar_marks_nothing() -> None:
    """The third exit: `t` past the close of the last bar has no bar to mark, and must return
    None rather than the final index — a dot on the last candle would assert we saw the name in a
    window we have no data for."""
    from small_cap_stack.charts import _bar_containing

    bars = [_bar(0, 5.0, 5.8, 4.6, 5.7)]
    assert _bar_containing(bars, _T0 + timedelta(minutes=20)) is None
