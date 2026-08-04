"""The overnight harvest (#431): the guards, the resume contract, and the store it writes.

This job runs unattended on a box that has already killed itself once (#264), for ~45 nights,
writing into the store the paper book reads (#430). Three classes of bug here are invisible until
they have already cost something, so they get the most tests:

1. **Provenance** — writing vendor rows into the live Phase-1 store. Undetectable after the fact: a
   reconstructed partition is byte-identical in shape to a captured one.
2. **Resume** — a half-written session merged into the book. Also undetectable: a partial day
   extracts perfectly well, just from fewer symbols.
3. **The window** — running during the tracker's morning. Detectable, expensively.

The schema tests are the other half: the harvest's whole value is that
``portfolio.extract_day_trades`` reads its output unchanged, so a renamed column is a silent zero.
"""

from __future__ import annotations

import json
import urllib.error
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from small_cap_stack.clock import ET
from small_cap_stack.config import Settings
from small_cap_stack.harvest import (
    HARVEST_DATASETS,
    Checkpoint,
    DailyRow,
    HarvestConfigError,
    HostGuard,
    MassiveSource,
    RunWindow,
    aggregate,
    candidates,
    discard_partial,
    harvest_daily,
    harvest_session,
    harvest_store,
    plan_sessions,
    reconstruct_hit,
    rolling_window_volume,
    run_harvest,
    stored_universe,
    sweep_floors,
    to_bars,
    trading_sessions,
    trim_session,
    universe_rows,
)
from small_cap_stack.harvest import __main__ as cli_mod
from small_cap_stack.harvest.checkpoint import CHECKPOINT_VERSION
from small_cap_stack.harvest.runner import checkpoint_path
from small_cap_stack.harvest.source import HarvestError
from small_cap_stack.portfolio import extract_day_trades
from small_cap_stack.storage import Store

# A quiet weekday well inside the XNYS calendar; every fixture below is anchored to it.
DAY = date(2026, 7, 2)
PREV = date(2026, 7, 1)


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    kw.setdefault("recon_subdir", "recon")
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        data_dir=tmp_path / "data",
        **kw,
    )


def _et(h: int, m: int, day: date = DAY) -> datetime:
    return datetime.combine(day, time(h, m), tzinfo=ET).astimezone(UTC)


def _agg_row(ts: datetime, price: float, volume: float) -> dict[str, Any]:
    """One vendor aggregate row (their wire shape: epoch-ms ``t``, ohlcv)."""
    return {
        "t": int(ts.timestamp() * 1000),
        "o": price,
        "h": price * 1.01,
        "l": price * 0.99,
        "c": price,
        "v": volume,
    }


def _candle_minutes(
    start: datetime, o: float, h: float, low: float, c: float, volume: float
) -> list[dict[str, Any]]:
    """Five minute rows that fold to exactly one ``(o, h, l, c, v)`` 5-min candle.

    The harvest reconstructs the appearance on the MINUTE series and runs the engine on the 5-min
    one, so a fixture has to be authored at minute resolution and still produce the candles the
    detector is meant to see. First open, max high, min low, last close, summed volume — the same
    fold :func:`aggregate` does.
    """
    per = volume / 5.0
    shapes = [(o, o, o, o), (o, h, o, h), (h, h, low, low), (low, h, low, c), (c, c, c, c)]
    return [
        _agg_row_ohlc(start + timedelta(minutes=i), *shape, per) for i, shape in enumerate(shapes)
    ]


def _agg_row_ohlc(
    ts: datetime, o: float, h: float, low: float, c: float, volume: float
) -> dict[str, Any]:
    return {"t": int(ts.timestamp() * 1000), "o": o, "h": h, "l": low, "c": c, "v": volume}


#: One pre-market runner with a real engine-v2 bull flag in it, authored as 5-min candles:
#: thin until 07:00, a two-bar green pole to $3.80, a two-candle pullback making lower highs and
#: retracing well under 50%, then the breakout. The whole point is that the harvested store feeds a
#: *takeable* trade through ``portfolio.extract_day_trades`` — a tape that merely ramps proves the
#: plumbing and nothing about the engine.
_FLAG_CANDLES: tuple[tuple[tuple[int, int], float, float, float, float, float], ...] = (
    ((7, 0), 3.00, 3.05, 2.98, 3.02, 120_000.0),  # volume clears the 100k trailing gate here
    ((7, 5), 3.05, 3.40, 3.03, 3.38, 300_000.0),  # pole bar 1: green, higher high
    ((7, 10), 3.38, 3.80, 3.36, 3.78, 400_000.0),  # pole peak: closes strong (4% upper wick)
    ((7, 15), 3.75, 3.76, 3.60, 3.62, 80_000.0),  # consolidation: lower high, light volume
    ((7, 20), 3.62, 3.70, 3.55, 3.58, 60_000.0),  # ...lower again; the low here is the stop
    ((7, 25), 3.60, 4.20, 3.58, 4.15, 500_000.0),  # breakout through 3.70 + 1 tick
    ((7, 30), 4.15, 4.60, 4.10, 4.55, 300_000.0),  # ...and it runs, so the trade is not stopped out
)


def _runner_minutes(day: date = DAY) -> list[dict[str, Any]]:
    """A whole session's minute bars for one runner: quiet, flag, breakout, then a flat day.

    Deliberately quiet until 07:00 so the reconstruction has to *wait* for a gate — an appearance on
    bar 0 would satisfy most of these tests without exercising anything.
    """
    rows: list[dict[str, Any]] = []
    for i in range(180):  # 04:00-07:00: on the tape, nowhere near the volume gate
        rows.append(_agg_row(_et(4, 0, day) + timedelta(minutes=i), 3.00, 200.0))
    for (hh, mm), o, h, low, c, vol in _FLAG_CANDLES:
        rows.extend(_candle_minutes(_et(hh, mm, day), o, h, low, c, vol))
    for i in range(85):  # 07:35-09:00, still on the scanner
        rows.append(_agg_row(_et(7, 35, day) + timedelta(minutes=i), 4.50, 40_000.0))
    for i in range(390):  # the regular session, so the stored 5-min series spans a full day
        rows.append(_agg_row(_et(9, 30, day) + timedelta(minutes=i), 4.60, 10_000.0))
    return rows


class FakeSource:
    """A :class:`HarvestSource` with no network, no sleeping, and a call counter that matters.

    The call count is asserted rather than incidental: at the free tier a call *is* 13 seconds of a
    finite night, so "how many requests did that session cost" is the harvest's only real budget.
    """

    def __init__(
        self,
        grouped: dict[date, list[dict[str, Any]]] | None = None,
        minutes: dict[tuple[str, date], list[dict[str, Any]]] | None = None,
        fail_on: set[str] | None = None,
    ) -> None:
        self._grouped = grouped or {}
        self._minutes = minutes or {}
        self._fail_on = fail_on or set()
        self.calls = 0
        self.requested: list[tuple[str, date]] = []

    def grouped_daily(self, day: date) -> list[dict[str, Any]]:
        self.calls += 1
        return list(self._grouped.get(day, []))

    def minute_bars(self, symbol: str, day: date) -> list[dict[str, Any]]:
        self.calls += 1
        self.requested.append((symbol, day))
        if symbol in self._fail_on:
            raise RuntimeError(f"vendor blew up on {symbol}")
        return list(self._minutes.get((symbol, day), []))


def _grouped_row(sym: str, *, high: float, close: float, volume: float) -> dict[str, Any]:
    return {"T": sym, "h": high, "l": close * 0.9, "c": close, "v": volume}


def _daily(symbol: str, change_pct: float = 500.0) -> DailyRow:
    """A stored-universe row that clears every prefilter gate — the harvest's unit of work."""
    return DailyRow(
        symbol=symbol,
        high=6.0,
        low=3.0,
        close=6.0,
        day_volume=9e6,
        prev_close=1.0,
        day_change_pct=change_pct,
    )


# ================================================================================================
# reconstruct: bars in, an appearance out
# ================================================================================================


def test_aggregate_anchors_buckets_to_the_et_hour_not_the_first_bar() -> None:
    """A symbol whose first print is off the 5-min grid must not shift every later boundary."""
    base = _et(9, 32)  # deliberately off the grid
    minute = [_agg_row(base + timedelta(minutes=i), 10.0 + i * 0.1, 1000.0) for i in range(13)]
    five = aggregate(to_bars(minute), minutes=5)
    ets = [b.start.astimezone(ET) for b in five]
    assert all(t.minute % 5 == 0 for t in ets)
    assert ets[0].strftime("%H:%M") == "09:30"


def test_aggregate_preserves_volume_and_extremes_and_never_fills_a_gap() -> None:
    bars = to_bars(
        [_agg_row(_et(4, 0) + timedelta(minutes=i), 10.0 + i, 100.0 * (i + 1)) for i in range(3)]
        # a 40-minute hole: IBKR omits no-trade periods, so synthesising flat candles would hand
        # the detector price action that never happened
        + [_agg_row(_et(4, 40), 12.0, 5.0)]
    )
    five = aggregate(bars, minutes=5)
    assert len(five) == 2
    assert sum(b.volume for b in five) == sum(b.volume for b in bars)
    assert five[0].high == max(b.high for b in bars[:3])
    assert five[0].low == min(b.low for b in bars[:3])


def test_rolling_window_volume_is_a_true_trailing_sum_on_minute_bars() -> None:
    """Why the harvest buys minute data at all: a 5-bar rolling sum, not one bar's volume."""
    bars = to_bars([_agg_row(_et(4, 0) + timedelta(minutes=i), 5.0, 1000.0) for i in range(10)])
    vols = rolling_window_volume(bars, minutes=5)
    assert vols[0] == 1000.0
    assert vols[4] == 5000.0  # five minute-bars inside the trailing window
    assert vols[9] == 5000.0  # ...and it stays a window, not a cumulative sum


def test_trim_session_drops_an_adjacent_days_overnight_prints() -> None:
    """One day's request can hand back a neighbouring session; those must not fold into bar 0."""
    bars = to_bars([_agg_row(_et(5, 0), 5.0, 1.0), _agg_row(_et(5, 0, PREV), 5.0, 1.0)])
    kept = trim_session(bars, DAY, time(4, 0), time(9, 30))
    assert [b.start.astimezone(ET).date() for b in kept] == [DAY]
    # ...and None means "the caller already knows these are one session" (the #428 fixtures).
    assert len(trim_session(bars, None, time(4, 0), time(9, 30))) == 2


def test_reconstruction_waits_for_the_volume_gate_and_reports_it_as_binding() -> None:
    s = _settings(Path("/tmp"))
    bars = trim_session(to_bars(_runner_minutes()), DAY, time(4, 0), time(9, 30))
    recon = reconstruct_hit(bars, s, symbol="AAAA", trading_date=DAY, prev_close=1.0)
    assert recon.found
    assert recon.hit_time is not None
    # Thin until 07:00: the appearance cannot land before the surge, and it is volume that binds.
    assert recon.hit_time.astimezone(ET).time() >= time(7, 0)
    assert "volume_5m" in (recon.binding_gate or "")


def test_change_gate_abstains_without_a_previous_close_and_bites_with_one() -> None:
    """#428's headline: without the previous close the reconstruction fires ~18 min early."""
    s = _settings(Path("/tmp"))
    bars = trim_session(to_bars(_runner_minutes()), DAY, time(4, 0), time(9, 30))
    blind = reconstruct_hit(bars, s, trading_date=DAY, prev_close=None)
    gated = reconstruct_hit(bars, s, trading_date=DAY, prev_close=3.5)  # needs > $3.85 to pass
    assert blind.hit_time is not None and gated.hit_time is not None
    assert gated.hit_time > blind.hit_time
    assert blind.change_decidable is False
    assert gated.change_decidable is True


def test_hit_times_carry_every_passing_bar_so_run_segmentation_still_works() -> None:
    """One row per passing minute — a lone first-appearance row collapses pop-fade-pop to 1 run."""
    s = _settings(Path("/tmp"))
    bars = trim_session(to_bars(_runner_minutes()), DAY, time(4, 0), time(9, 30))
    recon = reconstruct_hit(bars, s, trading_date=DAY, prev_close=1.0)
    assert len(recon.hit_times) > 1
    assert recon.hit_times[0] == recon.hit_time
    assert list(recon.hit_times) == sorted(recon.hit_times)


# ================================================================================================
# prefilter: the harvest's budget
# ================================================================================================


def test_universe_rows_apply_the_locked_gates_but_not_the_volume_floor() -> None:
    s = _settings(Path("/tmp"))
    grouped = [
        _grouped_row("RUNNER", high=6.0, close=5.0, volume=50_000.0),  # +200%, under the floor
        _grouped_row("CHEAP", high=0.5, close=0.4, volume=9_000_000.0),  # below the price band
        _grouped_row("RICH", high=90.0, close=88.0, volume=9_000_000.0),  # above it
        _grouped_row("FLAT", high=2.05, close=2.0, volume=9_000_000.0),  # +2.5%: under the change
        _grouped_row("NEWLY", high=8.0, close=7.0, volume=1_000.0),  # no previous close
    ]
    rows = universe_rows(grouped, {"RUNNER": 2.0, "CHEAP": 0.2, "RICH": 30.0, "FLAT": 2.0}, s)
    syms = {r.symbol for r in rows}
    assert syms == {"RUNNER", "NEWLY"}  # a first-day symbol is kept: it is exactly the target shape
    # The volume floor is compute-on-read, so a 50k name is STORED and filtered later — that is what
    # makes `sweep_floors` free rather than a 500-call re-pull.
    assert candidates(rows, min_day_volume=100_000.0) == []
    assert len(candidates(rows, min_day_volume=1_000.0)) == 2


def test_candidate_order_is_total_so_a_truncated_night_is_reproducible() -> None:
    rows = [
        DailyRow("BBBB", 5.0, 4.0, 5.0, 1e6, 1.0, 50.0),
        DailyRow("AAAA", 5.0, 4.0, 5.0, 1e6, 1.0, 50.0),  # a tie on change
        DailyRow("CCCC", 9.0, 4.0, 9.0, 1e6, 1.0, 90.0),
    ]
    out = candidates(rows, min_day_volume=0.0)
    assert [r.symbol for r in out] == ["CCCC", "AAAA", "BBBB"]
    assert candidates(list(reversed(rows)), min_day_volume=0.0) == out


def test_sweep_floors_reports_what_a_tighter_floor_would_cut() -> None:
    rows = [
        DailyRow(f"S{i}", 5.0, 4.0, 5.0, float(v), 1.0, 50.0)
        for i, v in enumerate([120_000, 400_000, 900_000, 5_000_000])
    ]
    table = {int(r["floor"]): r for r in sweep_floors(rows, [100_000, 500_000, 1_000_000])}
    assert table[100_000]["candidates"] == 4
    assert table[500_000]["candidates"] == 2
    assert table[1_000_000]["candidates"] == 1
    assert table[500_000]["dropped"] == 2
    assert table[500_000]["retained_pct"] == 50.0


# ================================================================================================
# guard: the window, and the box
# ================================================================================================


def test_run_window_wraps_midnight() -> None:
    """``start <= t < stop`` is false for every instant of a wrapping window — the whole point."""
    w = RunWindow(start=time(17, 0), stop=time(3, 0))
    assert w.is_open(datetime.combine(DAY, time(22, 0), tzinfo=ET))
    assert w.is_open(datetime.combine(DAY, time(1, 30), tzinfo=ET))
    assert w.is_open(datetime.combine(DAY, time(17, 0), tzinfo=ET))  # floor inclusive
    assert not w.is_open(datetime.combine(DAY, time(3, 0), tzinfo=ET))  # stop exclusive
    assert not w.is_open(datetime.combine(DAY, time(4, 0), tzinfo=ET))  # the scan window
    assert not w.is_open(datetime.combine(DAY, time(16, 30), tzinfo=ET))  # the EOD report


def test_run_window_deadline_is_the_next_stop_in_et() -> None:
    w = RunWindow(start=time(17, 0), stop=time(3, 0))
    evening = datetime.combine(DAY, time(22, 0), tzinfo=ET)
    assert w.deadline(evening) == datetime.combine(DAY + timedelta(days=1), time(3, 0), tzinfo=ET)
    small_hours = datetime.combine(DAY, time(1, 0), tzinfo=ET)
    assert w.deadline(small_hours) == datetime.combine(DAY, time(3, 0), tzinfo=ET)
    # Pinned to ET wall clock, so it stays 03:00 across a DST change rather than drifting an hour
    # into the 03:45 eod_backfill.
    spring = datetime.combine(date(2027, 3, 13), time(23, 0), tzinfo=ET)
    assert w.deadline(spring).astimezone(ET).time() == time(3, 0)


def test_host_guard_reports_the_binding_floor_and_tolerates_unreadable_metrics(
    tmp_path: Path,
) -> None:
    ok = HostGuard(min_mem_available_mb=0.0, min_disk_free_mb=0.0).check(str(tmp_path))
    assert ok.ok and ok.reason == ""
    tight = HostGuard(min_mem_available_mb=0.0, min_disk_free_mb=1e12).check(str(tmp_path))
    assert not tight.ok and "disk" in tight.reason


# ================================================================================================
# checkpoint: what survives an OOM kill
# ================================================================================================


def test_checkpoint_round_trips_and_is_written_atomically(tmp_path: Path) -> None:
    path = tmp_path / "cp.json"
    cp = Checkpoint.load(path)
    assert cp.done == set()
    cp.mark_session(DAY, calls=218)
    cp.mark_daily(PREV, calls=1)
    again = Checkpoint.load(path)
    assert again.done == {DAY}
    assert again.daily_done == {PREV}
    assert again.calls == 219
    assert again.newest == DAY
    assert not list(tmp_path.glob("*.tmp"))  # the temp file never survives a completed write


def test_checkpoint_refuses_an_unknown_version_rather_than_resetting(tmp_path: Path) -> None:
    """Silently starting over would re-spend up to 45 nights of API budget."""
    path = tmp_path / "cp.json"
    path.write_text(f'{{"version": {CHECKPOINT_VERSION + 1}, "done": ["2026-07-02"]}}')
    with pytest.raises(ValueError, match="checkpoint version"):
        Checkpoint.load(path)


# ================================================================================================
# runner: where it writes, and what it writes
# ================================================================================================


def test_harvest_store_refuses_to_write_into_the_live_store(tmp_path: Path) -> None:
    """The failure that leaves no trace: vendor rows indistinguishable from collected ones."""
    with pytest.raises(HarvestConfigError, match="recon_subdir is empty"):
        harvest_store(_settings(tmp_path, recon_subdir=""))
    store = harvest_store(_settings(tmp_path))
    assert store.data_dir.name == "recon"
    assert store.data_dir != (tmp_path / "data")
    assert checkpoint_path(_settings(tmp_path)).parent == store.data_dir


def test_plan_sessions_is_newest_first_and_skips_live_and_done_dates(tmp_path: Path) -> None:
    s = _settings(tmp_path, harvest_lookback_days=14)
    sessions = plan_sessions(s, today=date(2026, 7, 10))
    assert sessions == sorted(sessions, reverse=True)  # #430's ordering decision
    assert date(2026, 7, 10) not in sessions  # today is not final until the close
    assert all(d.weekday() < 5 for d in sessions)
    trimmed = plan_sessions(
        s, today=date(2026, 7, 10), done=[date(2026, 7, 9)], live_dates=[date(2026, 7, 8)]
    )
    assert date(2026, 7, 9) not in trimmed and date(2026, 7, 8) not in trimmed


def test_trading_sessions_skips_a_holiday() -> None:
    s = _settings(Path("/tmp"))
    around_july_4 = trading_sessions(date(2026, 7, 2), date(2026, 7, 7), s)
    assert date(2026, 7, 3) not in around_july_4  # observed Independence Day
    assert date(2026, 7, 2) in around_july_4 and date(2026, 7, 6) in around_july_4


def test_harvest_daily_reuses_each_response_as_the_next_sessions_previous_close(
    tmp_path: Path,
) -> None:
    """~1 call/session instead of 2 — the difference between one night and two for phase 1."""
    s = _settings(tmp_path)
    store = harvest_store(s)
    sessions = trading_sessions(date(2026, 7, 6), date(2026, 7, 8), s)
    grouped = {
        d: [_grouped_row("RUNNER", high=6.0, close=5.0, volume=9e6)]
        for d in [*sessions, date(2026, 7, 2)]
    }
    grouped[date(2026, 7, 2)] = [_grouped_row("RUNNER", high=2.0, close=2.0, volume=9e6)]
    source = FakeSource(grouped=grouped)
    results = harvest_daily(source, store, s, sessions)
    assert len(results) == len(sessions)
    assert source.calls == len(sessions) + 1  # one extra for the session before the first
    rows = stored_universe(store, sessions[0])
    assert [r.symbol for r in rows] == ["RUNNER"]
    assert rows[0].prev_close == 2.0  # carried from 2026-07-02, the prior session
    assert rows[0].day_change_pct == 200.0


def test_harvest_session_writes_exactly_one_file_per_dataset(tmp_path: Path) -> None:
    """For this store read cost tracks FILE count (#318/#319/#321), and the portfolio re-extracts a
    day whenever its partition files change — so a day dribbled out in fragments costs twice."""
    s = _settings(tmp_path)
    store = harvest_store(s)
    rows = [_daily("AAAA", 500.0), _daily("BBBB", 400.0)]
    source = FakeSource(
        minutes={("AAAA", DAY): _runner_minutes(), ("BBBB", DAY): _runner_minutes()}
    )
    result = harvest_session(source, store, s, DAY, rows)

    assert result.complete and result.opportunities == 2
    for dataset in HARVEST_DATASETS:
        files = list((store.data_dir / dataset / f"dt={DAY.isoformat()}").glob("*.parquet"))
        assert len(files) == 1, f"{dataset} landed {len(files)} files"


def test_harvested_bars_span_the_full_session_while_minute_bars_stay_pre_market(
    tmp_path: Path,
) -> None:
    """The 5-min series must not stop at 09:30: ``simulate_exit`` marks an unresolved trade to the
    LAST bar it can see, so a truncated series would close every still-open 09:10 entry at 09:25 and
    report that as the trade's result — a silent downward bias on the trades that were working."""
    s = _settings(tmp_path)
    store = harvest_store(s)
    source = FakeSource(minutes={("AAAA", DAY): _runner_minutes()})
    harvest_session(source, store, s, DAY, [_daily("AAAA")])

    five = store.read("bars", dt=DAY)
    minute = store.read("bars_1m", dt=DAY)
    last_five = max(five["bar_start_utc"].to_list()).astimezone(ET).time()
    last_minute = max(minute["bar_start_utc"].to_list()).astimezone(ET).time()
    assert last_five > time(9, 30) and last_five < s.capture_end
    assert last_minute < time(9, 30)


def test_minute_bars_can_be_switched_off_without_touching_the_engine_series(
    tmp_path: Path,
) -> None:
    s = _settings(tmp_path, harvest_store_minute_bars=False)
    store = harvest_store(s)
    source = FakeSource(minutes={("AAAA", DAY): _runner_minutes()})
    result = harvest_session(source, store, s, DAY, [_daily("AAAA")])
    assert result.bars_1m == 0
    assert not (store.data_dir / "bars_1m").exists()
    assert not store.read("bars", dt=DAY).is_empty()  # the engine's series is written either way


def test_one_symbols_vendor_failure_does_not_stall_the_session(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    store = harvest_store(s)
    rows = [_daily("BOOM", 900.0), _daily("AAAA", 500.0)]
    source = FakeSource(minutes={("AAAA", DAY): _runner_minutes()}, fail_on={"BOOM"})
    result = harvest_session(source, store, s, DAY, rows)
    assert result.opportunities == 1
    assert [sym for sym, _ in source.requested] == ["BOOM", "AAAA"]


def test_a_candidate_that_never_clears_the_gates_intraday_is_not_an_opportunity(
    tmp_path: Path,
) -> None:
    """The daily bar says +500%; the minute tape never puts 100k through a 5-min window."""
    s = _settings(tmp_path)
    store = harvest_store(s)
    thin = [_agg_row(_et(4, 0) + timedelta(minutes=i), 5.0, 10.0) for i in range(200)]
    source = FakeSource(minutes={("THIN", DAY): thin})
    result = harvest_session(source, store, s, DAY, [_daily("THIN")])
    assert result.opportunities == 0
    assert store.read("opportunities", dt=DAY).is_empty()


def test_harvest_max_candidates_caps_the_session(tmp_path: Path) -> None:
    s = _settings(tmp_path, harvest_max_candidates=1)
    store = harvest_store(s)
    rows = [_daily("AAAA", 500.0), _daily("BBBB", 400.0)]
    source = FakeSource(minutes={("AAAA", DAY): _runner_minutes()})
    harvest_session(source, store, s, DAY, rows)
    assert [sym for sym, _ in source.requested] == ["AAAA"]


# ================================================================================================
# the schema contract: the paper book must read this store unchanged
# ================================================================================================


def test_extract_day_trades_reads_the_harvested_store_and_stamps_recon(tmp_path: Path) -> None:
    """The harvest's entire value. A renamed column here is a silent zero in ``books_all``."""
    s = _settings(
        tmp_path,
        portfolio_premarket_earliest=time(4, 0),
        portfolio_premarket_cutoff=time(9, 15),
        portfolio_entry_price_min=1.0,
    )
    store = harvest_store(s)
    source = FakeSource(minutes={("AAAA", DAY): _runner_minutes()})
    harvest_session(source, store, s, DAY, [_daily("AAAA")])

    trades = extract_day_trades(store, s, DAY, source="recon")
    assert trades, "the harvested day produced no extractable candidate"
    for t in trades:
        assert t.source == "recon"  # #430's provenance stamp, carried into every book
        assert t.float_shares is None  # the vendor sells no float; none is invented
        assert t.trading_date == DAY
        assert t.entry_fill > t.stop


def test_harvested_opportunity_and_hit_rows_match_the_live_schema(tmp_path: Path) -> None:
    """Written through the live record builders, so the columns cannot drift apart by hand."""
    s = _settings(tmp_path)
    store = harvest_store(s)
    source = FakeSource(minutes={("AAAA", DAY): _runner_minutes()})
    harvest_session(source, store, s, DAY, [_daily("AAAA")])

    opps = store.read("opportunities", dt=DAY)
    assert set(opps.columns) >= {
        "opportunity_id",
        "symbol",
        "con_id",
        "exchange",
        "currency",
        "trading_date",
        "first_seen_utc",
        "first_rank",
    }
    assert opps["opportunity_id"].to_list() == [f"{DAY.isoformat()}:AAAA"]
    assert opps["con_id"].to_list() == [0]  # no IBKR contract exists for a reconstructed row
    hits = store.read("scanner_hits", dt=DAY)
    assert set(hits.columns) >= {"opportunity_id", "symbol", "ts_utc", "rank"}
    assert len(hits) > 1


# ================================================================================================
# run_harvest: the night
# ================================================================================================


def _seeded_store(s: Settings, days: Sequence[date]) -> Store:
    """A store with phase 1 already done for ``days`` — what phase 2 expects to find."""
    store = harvest_store(s)
    for d in days:
        store.append(
            "daily_universe",
            [_daily("AAAA").as_record()],
            partition_date=d,
        )
    return store


def _clock(*moments: datetime) -> Any:
    """A now() that walks the given moments and then holds the last one."""
    seq = list(moments)

    def now() -> datetime:
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return now


def test_run_harvest_refuses_to_start_inside_the_scan_window(tmp_path: Path) -> None:
    """04:00 ET is the tracker's morning. Being launched at the right time is not the same
    guarantee as refusing the wrong one — a late timer only trips the second."""
    s = _settings(tmp_path)
    store = _seeded_store(s, [DAY])
    source = FakeSource(minutes={("AAAA", DAY): _runner_minutes()})
    run = run_harvest(
        source,
        store,
        s,
        [DAY],
        checkpoint=Checkpoint.load(checkpoint_path(s)),
        window=RunWindow(),
        now_fn=_clock(datetime.combine(DAY, time(4, 0), tzinfo=ET)),
    )
    assert run.sessions == ()
    assert run.stopped_because.startswith("outside-window")
    assert source.calls == 0  # nothing was fetched, so nothing was spent


def test_run_harvest_stops_before_a_session_that_would_overrun_the_hard_stop(
    tmp_path: Path,
) -> None:
    """An abandoned session writes nothing, so its calls buy nothing. Keep them for tomorrow."""
    s = _settings(tmp_path, harvest_rate_sleep_sec=600.0)  # one candidate = 10 minutes
    store = _seeded_store(s, [DAY])
    source = FakeSource(minutes={("AAAA", DAY): _runner_minutes()})
    five_to_three = datetime.combine(DAY, time(2, 55), tzinfo=ET)  # 5 min before the 03:00 stop
    run = run_harvest(
        source,
        store,
        s,
        [DAY],
        checkpoint=Checkpoint.load(checkpoint_path(s)),
        window=RunWindow(),
        now_fn=_clock(five_to_three),
    )
    assert run.completed == ()
    assert "hard-stop" in run.stopped_because
    assert source.calls == 0


def test_run_harvest_hard_stop_mid_session_discards_the_partial_day(tmp_path: Path) -> None:
    """A half-written day extracts perfectly well — just from half the symbols. Nothing downstream
    could tell, which is exactly why it is discarded rather than merged."""
    s = _settings(tmp_path, harvest_rate_sleep_sec=0.0)
    store = _seeded_store(s, [DAY])
    store.append(
        "daily_universe",
        [_daily("BBBB", 400.0).as_record()],
        partition_date=DAY,
    )
    source = FakeSource(
        minutes={("AAAA", DAY): _runner_minutes(), ("BBBB", DAY): _runner_minutes()}
    )
    cp = Checkpoint.load(checkpoint_path(s))
    evening = datetime.combine(DAY, time(22, 0), tzinfo=ET)
    past_stop = datetime.combine(DAY + timedelta(days=1), time(3, 30), tzinfo=ET)
    # start / loop guard / estimate / session start / first symbol -> then past the deadline
    run = run_harvest(
        source,
        store,
        s,
        [DAY],
        checkpoint=cp,
        window=RunWindow(),
        now_fn=_clock(evening, evening, evening, evening, evening, past_stop),
    )
    assert run.completed == ()
    assert "hard-stop" in run.stopped_because
    assert cp.done == set()  # the date is NOT claimed
    assert store.read("opportunities", dt=DAY).is_empty()
    for dataset in HARVEST_DATASETS:
        assert not (store.data_dir / dataset / f"dt={DAY.isoformat()}").exists()


def test_run_harvest_marks_and_skips_completed_sessions(tmp_path: Path) -> None:
    s = _settings(tmp_path, harvest_rate_sleep_sec=0.0)
    store = _seeded_store(s, [DAY])
    cp = Checkpoint.load(checkpoint_path(s))
    evening = datetime.combine(DAY, time(22, 0), tzinfo=ET)
    source = FakeSource(minutes={("AAAA", DAY): _runner_minutes()})
    run = run_harvest(
        source, store, s, [DAY], checkpoint=cp, window=RunWindow(), now_fn=_clock(evening)
    )
    assert run.completed == (DAY,)
    assert cp.calls == run.calls > 0
    assert Checkpoint.load(checkpoint_path(s)).done == {DAY}
    # ...and the next night plans around it rather than re-buying the same session.
    assert DAY not in plan_sessions(s, today=DAY + timedelta(days=1), done=sorted(cp.done))


def test_resuming_an_unmarked_date_discards_its_leftovers_and_reproduces_the_rows(
    tmp_path: Path,
) -> None:
    """The resume contract: a re-run of the same date yields identical rows, never doubled ones.

    The store is append-only, so without the discard a second pass would ADD a second partition
    file — the day would extract duplicate opportunities and its cache fingerprint would flip on
    every rebuild.
    """
    s = _settings(tmp_path, harvest_rate_sleep_sec=0.0)
    store = _seeded_store(s, [DAY])
    evening = datetime.combine(DAY, time(22, 0), tzinfo=ET)
    minutes = {("AAAA", DAY): _runner_minutes()}

    first = harvest_session(FakeSource(minutes=minutes), store, s, DAY, stored_universe(store, DAY))
    before = store.read("bars", dt=DAY).sort("bar_start_utc")

    # A kill left the files but never marked the checkpoint; the next night comes back to it.
    cp = Checkpoint.load(checkpoint_path(s))
    assert cp.done == set()
    run = run_harvest(
        FakeSource(minutes=minutes),
        store,
        s,
        [DAY],
        checkpoint=cp,
        window=RunWindow(),
        now_fn=_clock(evening),
    )
    after = store.read("bars", dt=DAY).sort("bar_start_utc")
    assert run.completed == (DAY,)
    assert len(list((store.data_dir / "bars" / f"dt={DAY.isoformat()}").glob("*.parquet"))) == 1
    assert after.equals(before)
    assert run.sessions[0].opportunities == first.opportunities


def test_run_harvest_stops_cleanly_when_the_box_runs_out_of_headroom(tmp_path: Path) -> None:
    """#264: the OOM that took CI offline for 5h37m. Stop AT a checkpoint, not killed at one."""
    s = _settings(tmp_path, harvest_min_disk_free_mb=1e12, harvest_rate_sleep_sec=0.0)
    store = _seeded_store(s, [DAY])
    source = FakeSource(minutes={("AAAA", DAY): _runner_minutes()})
    run = run_harvest(
        source,
        store,
        s,
        [DAY],
        checkpoint=Checkpoint.load(checkpoint_path(s)),
        window=RunWindow(),
        now_fn=_clock(datetime.combine(DAY, time(22, 0), tzinfo=ET)),
    )
    assert run.completed == ()
    assert run.stopped_because.startswith("host-headroom")
    assert source.calls == 0


def test_a_session_without_a_stored_universe_is_skipped_but_never_marked_done(
    tmp_path: Path,
) -> None:
    """A missing phase-1 universe is a gap to fill, not a result — marking it would hide the gap."""
    s = _settings(tmp_path, harvest_rate_sleep_sec=0.0)
    store = harvest_store(s)
    cp = Checkpoint.load(checkpoint_path(s))
    run = run_harvest(
        FakeSource(),
        store,
        s,
        [DAY],
        checkpoint=cp,
        window=RunWindow(),
        now_fn=_clock(datetime.combine(DAY, time(22, 0), tzinfo=ET)),
    )
    assert run.completed == () and cp.done == set()


def test_max_sessions_bounds_a_night(tmp_path: Path) -> None:
    s = _settings(tmp_path, harvest_rate_sleep_sec=0.0)
    days = [DAY, date(2026, 7, 1)]
    store = _seeded_store(s, days)
    source = FakeSource(
        minutes={("AAAA", d): _runner_minutes(d) for d in days},
    )
    run = run_harvest(
        source,
        store,
        s,
        days,
        checkpoint=Checkpoint.load(checkpoint_path(s)),
        window=RunWindow(),
        now_fn=_clock(datetime.combine(DAY, time(22, 0), tzinfo=ET)),
        max_sessions=1,
    )
    assert len(run.completed) == 1
    assert run.stopped_because == "max-sessions"


def test_discard_partial_only_touches_the_named_date(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    store = harvest_store(s)
    source = FakeSource(minutes={("AAAA", d): _runner_minutes(d) for d in (DAY, PREV)})
    for d in (DAY, PREV):
        harvest_session(source, store, s, d, [_daily("AAAA")])
    assert discard_partial(store, DAY) == len(HARVEST_DATASETS)
    assert store.read("bars", dt=DAY).is_empty()
    assert not store.read("bars", dt=PREV).is_empty()


# ================================================================================================
# source: the rate limit is the design
# ================================================================================================


def test_massive_source_sleeps_between_calls_and_never_leaks_the_key(monkeypatch: Any) -> None:
    slept: list[float] = []
    src = MassiveSource(api_key="SECRET", rate_sleep_sec=13.0, sleep=slept.append)

    pages = [{"results": [{"T": "A", "c": 1.0}], "next_url": "https://x/y"}, {"results": []}]
    seen: list[str] = []

    def fake(url: str) -> dict[str, Any]:
        seen.append(url)
        src.calls += 1
        return pages.pop(0)

    monkeypatch.setattr(src, "_get_url", fake)
    rows = src.aggregates("AAAA", start=DAY, end=DAY)
    assert len(rows) == 1  # pagination followed to exhaustion
    assert "apiKey=SECRET" in seen[0]
    assert "adjusted=false" in seen[0]  # the $1-50 gate is on prices AS TRADED

    # ...and the real transport sleeps before every call after the first.
    src2 = MassiveSource(api_key="k", rate_sleep_sec=13.0, sleep=slept.append)
    src2.calls = 1
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("no network"))
    )
    with pytest.raises(Exception):  # noqa: B017 — the point is that it slept first
        src2.get("/v2/whatever")
    assert 13.0 in slept


def test_massive_source_from_env_refuses_without_a_key(monkeypatch: Any) -> None:
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    with pytest.raises(Exception, match="MASSIVE_API_KEY"):
        MassiveSource.from_env()


# ================================================================================================
# CLI: the operator surface (this is how the box actually runs it)
# ================================================================================================


def _at_et(monkeypatch: Any, hh: int, mm: int = 0) -> None:
    """Pin the CLI's wall clock. The window guard reads `datetime.now(ET)` directly, so this is
    what makes "would it refuse at 05:00?" testable rather than a claim in a docstring."""
    fixed = datetime.combine(DAY, time(hh, mm), tzinfo=ET)

    class _Now(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return fixed.astimezone(tz) if tz else fixed

    monkeypatch.setattr(cli_mod, "datetime", _Now)


def _json_out(capsys: Any) -> Any:
    """The CLI's JSON payload, ignoring any structlog lines sharing stdout."""
    out = capsys.readouterr().out
    start = out.index("\n{\n") + 1 if "\n{\n" in out else out.index("{")
    return json.loads(out[start:])


def _cli(monkeypatch: Any, s: Settings, argv: list[str]) -> int:
    """Run the CLI against a temp store, with logging left alone and no vendor key needed."""
    monkeypatch.setattr(cli_mod, "get_settings", lambda: s)
    monkeypatch.setattr(cli_mod, "configure_logging", lambda **_: None)
    return cli_mod.main(argv)


def test_cli_run_needs_two_flags_to_leave_the_window(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    """A confirmation the caller can auto-answer protects nobody (#261) — and what is being
    protected here is the tracker's own morning."""
    s = _settings(tmp_path)
    assert _cli(monkeypatch, s, ["run", "--ignore-window"]) == 2
    assert "--force" in capsys.readouterr().err


def test_cli_status_reports_the_window_and_what_is_left(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    s = _settings(tmp_path, harvest_lookback_days=10)
    cp = Checkpoint.load(checkpoint_path(s))
    cp.mark_session(DAY)
    assert _cli(monkeypatch, s, ["status", "--today", "2026-07-10"]) == 0
    payload = _json_out(capsys)
    assert payload["sessions_done"] == 1
    assert payload["window"] == "17:00–03:00 ET"
    assert DAY.isoformat() not in payload["next_sessions"]
    assert payload["next_hard_stop"].endswith("03:00:00-04:00")  # ET, not UTC or host-local


def test_cli_sweep_measures_the_floors_from_stored_rows_without_calling_the_vendor(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    """The #431 pre-flight: what a tighter day-volume floor would cut, before spending a night."""
    s = _settings(tmp_path)
    store = harvest_store(s)
    store.append(
        "daily_universe",
        [
            DailyRow(f"S{i}", 6.0, 3.0, 6.0, float(v), 1.0, 500.0).as_record()
            for i, v in enumerate([120_000, 400_000, 900_000, 5_000_000])
        ],
        partition_date=DAY,
    )
    assert _cli(monkeypatch, s, ["sweep", "--floors", "100000,500000"]) == 0
    payload = _json_out(capsys)
    assert payload["dates"] == 1
    assert payload["mean_candidates_per_day"] == {"100000": 4.0, "500000": 2.0}
    # Fewer candidates a session => more sessions a night. That is the whole decision.
    assert payload["sessions_per_8h_night"]["500000"] > payload["sessions_per_8h_night"]["100000"]


def test_cli_prefilter_costs_nothing_and_shows_what_a_session_would_fetch(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    s = _settings(tmp_path)
    store = harvest_store(s)
    store.append("daily_universe", [_daily("AAAA").as_record()], partition_date=DAY)
    assert _cli(monkeypatch, s, ["prefilter", "--today", DAY.isoformat()]) == 0
    payload = _json_out(capsys)
    assert payload["candidates"] == 1
    assert payload["estimated_minutes"] == pytest.approx(13.0 / 60.0, abs=0.1)
    assert payload["top"][0]["symbol"] == "AAAA"


def test_cli_reports_a_missing_key_as_operator_error_not_a_traceback(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    s = _settings(tmp_path, harvest_lookback_days=10)
    # Pinned inside the window: without this the test passes only when the suite happens to run
    # between 17:00 and 03:00 ET, because `daily` now refuses outside it before ever looking for
    # a key. A time-dependent test is a test that lies about which failure it is asserting.
    _at_et(monkeypatch, 22, 0)
    assert _cli(monkeypatch, s, ["daily", "--today", "2026-07-10", "--limit", "1"]) == 2
    assert "MASSIVE_API_KEY" in capsys.readouterr().err


# ================================================================================================
# source: retries, and never echoing the key
# ================================================================================================


def test_massive_source_retries_a_429_then_gives_up_without_leaking_the_key(
    monkeypatch: Any,
) -> None:
    """A blocked key has no second copy, so 429s are backed off rather than absorbed — and the
    failure message must never carry the URL, because the URL carries the key."""
    src = MassiveSource(api_key="SECRET", rate_sleep_sec=0.0, max_retries=2, sleep=lambda _: None)
    attempts = 0

    def boom(*_a: Any, **_k: Any) -> Any:
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError("https://x/y?apiKey=SECRET", 429, "slow down", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(HarvestError) as exc:
        src.grouped_daily(DAY)
    assert attempts == 3  # the initial call plus max_retries
    assert "SECRET" not in str(exc.value)
    assert "429" in str(exc.value)


@pytest.mark.parametrize("command", ["run", "daily"])
def test_cli_refuses_both_vendor_spending_commands_inside_the_scan_window(
    monkeypatch: Any, tmp_path: Path, capsys: Any, command: str
) -> None:
    """`daily` used to compute a deadline but never ask whether the window was OPEN, so a 05:00
    dispatch would have started ~500 calls and ~2h of work straight through the scan window. A
    guard that covers one of the two vendor-spending commands is not a guard."""
    s = _settings(tmp_path, harvest_lookback_days=10)
    _at_et(monkeypatch, 5, 0)  # the tracker's morning
    assert _cli(monkeypatch, s, [command, "--today", "2026-07-10", "--limit", "1"]) == 3
    err = capsys.readouterr().err
    assert "refusing to start at 05:00 ET" in err
    assert "17:00" in err


@pytest.mark.parametrize("command", ["run", "daily"])
def test_cli_lets_both_through_inside_the_window(
    monkeypatch: Any, tmp_path: Path, capsys: Any, command: str
) -> None:
    """Past the guard, the next thing either hits is the missing key — which proves the refusal
    above was the window and not something incidental."""
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    s = _settings(tmp_path, harvest_lookback_days=10)
    _at_et(monkeypatch, 22, 0)
    assert _cli(monkeypatch, s, [command, "--today", "2026-07-10", "--limit", "1"]) == 2
    assert "MASSIVE_API_KEY" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["run", "daily"])
def test_cli_override_still_needs_two_flags_on_both(
    monkeypatch: Any, tmp_path: Path, capsys: Any, command: str
) -> None:
    s = _settings(tmp_path, harvest_lookback_days=10)
    _at_et(monkeypatch, 5, 0)
    assert _cli(monkeypatch, s, [command, "--ignore-window"]) == 2
    assert "--force" in capsys.readouterr().err


def test_cli_read_only_commands_are_safe_at_any_hour(monkeypatch: Any, tmp_path: Path) -> None:
    """status/sweep/prefilter touch no vendor and spend nothing, so they are never window-gated —
    checking what the harvest is doing must not itself require waiting until 17:00."""
    s = _settings(tmp_path, harvest_lookback_days=10)
    _at_et(monkeypatch, 5, 0)
    for command in ("status", "sweep", "prefilter"):
        assert _cli(monkeypatch, s, [command, "--today", "2026-07-10"]) == 0


# ================================================================================================
# auto: what the nightly timer actually runs
# ================================================================================================


def test_run_alone_does_nothing_on_a_box_where_phase_1_never_ran(tmp_path: Path) -> None:
    """The failure this whole command exists for: `run` skips every session with "no universe",
    spends nothing, and exits cleanly — a nightly job that looks like it works and does not."""
    s = _settings(tmp_path, harvest_rate_sleep_sec=0.0)
    store = harvest_store(s)  # no daily_universe anywhere
    source = FakeSource(minutes={("AAAA", DAY): _runner_minutes()})
    cp = Checkpoint.load(checkpoint_path(s))
    run = run_harvest(
        source,
        store,
        s,
        [DAY],
        checkpoint=cp,
        window=RunWindow(),
        now_fn=_clock(datetime.combine(DAY, time(22, 0), tzinfo=ET)),
    )
    assert run.completed == () and source.calls == 0 and cp.done == set()


def test_auto_fills_phase_1_then_harvests_in_the_same_night(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    """Night one end to end: no universe on disk, and `auto` lands both phases — the session whose
    universe was written minutes earlier is harvested tonight, not left for tomorrow."""
    s = _settings(tmp_path, harvest_rate_sleep_sec=0.0, harvest_lookback_days=6)
    today = date(2026, 7, 9)
    sessions = trading_sessions(today - timedelta(days=6), today - timedelta(days=1), s)
    grouped = {
        d: [_grouped_row("AAAA", high=6.0, close=6.0, volume=9e6)]
        for d in [*sessions, date(2026, 7, 1), date(2026, 7, 2)]
    }
    grouped[date(2026, 7, 2)] = [_grouped_row("AAAA", high=1.5, close=1.5, volume=9e6)]
    source = FakeSource(
        grouped=grouped, minutes={("AAAA", d): _runner_minutes(d) for d in sessions}
    )
    monkeypatch.setattr(cli_mod, "MassiveSource", type("F", (), {"from_env": lambda **_: source}))
    _at_et(monkeypatch, 22, 0)

    assert _cli(monkeypatch, s, ["auto", "--today", today.isoformat()]) == 0
    payload = _json_out(capsys)
    assert payload["daily_sessions"] == len(sessions)  # phase 1 filled
    assert payload["daily_remaining"] == 0
    assert payload["harvested"], "phase 2 never ran against the universe phase 1 had just written"
    cp = Checkpoint.load(checkpoint_path(s))
    assert cp.daily_done and cp.done


def test_auto_skips_phase_1_once_it_is_complete(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    """Every night after the first: straight to phase 2, no grouped-daily calls re-spent."""
    s = _settings(tmp_path, harvest_rate_sleep_sec=0.0, harvest_lookback_days=6)
    store = harvest_store(s)
    today = date(2026, 7, 9)
    sessions = trading_sessions(today - timedelta(days=6), today - timedelta(days=1), s)
    cp = Checkpoint.load(checkpoint_path(s))
    for d in sessions:
        store.append("daily_universe", [_daily("AAAA").as_record()], partition_date=d)
        cp.mark_daily(d)
    source = FakeSource(minutes={("AAAA", d): _runner_minutes(d) for d in sessions})
    monkeypatch.setattr(cli_mod, "MassiveSource", type("F", (), {"from_env": lambda **_: source}))
    _at_et(monkeypatch, 22, 0)

    assert _cli(monkeypatch, s, ["auto", "--today", today.isoformat(), "--limit", "1"]) == 0
    payload = _json_out(capsys)
    assert payload["daily_sessions"] == 0
    assert len(payload["harvested"]) == 1
    # Only minute-bar calls: nothing was re-spent on a universe already on disk.
    assert all(sym == "AAAA" for sym, _ in source.requested)


def test_auto_refuses_outside_the_window_like_the_others(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    s = _settings(tmp_path)
    _at_et(monkeypatch, 5, 0)
    assert _cli(monkeypatch, s, ["auto"]) == 3
    assert "refusing to start at 05:00 ET" in capsys.readouterr().err
