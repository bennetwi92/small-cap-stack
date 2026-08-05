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

import io
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
from small_cap_stack.harvest.source import HarvestEntitlementError, HarvestError
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
        entitled_from: date | None = None,
        tickers: dict[tuple[str, bool], list[str]] | None = None,
    ) -> None:
        self._grouped = grouped or {}
        self._minutes = minutes or {}
        self._fail_on = fail_on or set()
        #: (vendor ticker type, active) -> symbols, for the #443 exclusion fetch.
        self._tickers = tickers or {}
        self.ticker_queries: list[tuple[str, bool]] = []
        #: Dates before this are refused the way the real vendor refuses them (#440) — a 403 whose
        #: body says the plan does not reach that far back, not a transport failure.
        self._entitled_from = entitled_from
        self.calls = 0
        self.requested: list[tuple[str, date]] = []
        self.grouped_requested: list[date] = []

    def _entitlement_check(self, day: date) -> None:
        if self._entitled_from is not None and day < self._entitled_from:
            raise HarvestEntitlementError(
                f"HTTP 403 on .../{day.isoformat()}: "
                '{"status":"NOT_AUTHORIZED","message":"Attempted to request data past historical '
                'entitlements. Please upgrade your plan at https://polygon.io/pricing"}'
            )

    def grouped_daily(self, day: date) -> list[dict[str, Any]]:
        self.calls += 1
        self.grouped_requested.append(day)
        self._entitlement_check(day)
        return list(self._grouped.get(day, []))

    def tickers_of_type(self, ticker_type: str, *, active: bool) -> list[str]:
        self.calls += 1
        self.ticker_queries.append((ticker_type, active))
        return list(self._tickers.get((ticker_type, active), []))

    def minute_bars(self, symbol: str, day: date) -> list[dict[str, Any]]:
        self.calls += 1
        self.requested.append((symbol, day))
        self._entitlement_check(day)
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


def test_aggregate_preserves_volume_and_extremes_and_fills_a_gap_the_way_ibkr_does() -> None:
    """This test asserted the opposite until #442 measured it.

    The vendor omits no-trade minutes; IBKR emits a flat zero-volume candle. The engine counts
    *bars*, so the two are not interchangeable — see
    ``test_ibkr_fixtures_are_contiguous_with_flat_filler_bars`` for the evidence, and
    ``test_a_hole_in_the_consolidation_does_not_manufacture_a_shorter_flag`` for what it costs.
    """
    bars = to_bars(
        [_agg_row(_et(4, 0) + timedelta(minutes=i), 10.0 + i, 100.0 * (i + 1)) for i in range(3)]
        + [_agg_row(_et(4, 40), 12.0, 5.0)]
    )
    five = aggregate(bars, minutes=5)

    ets = [b.start.astimezone(ET).strftime("%H:%M") for b in five]
    assert ets == ["04:00", "04:05", "04:10", "04:15", "04:20", "04:25", "04:30", "04:35", "04:40"]
    # Volume is conserved: the filler carries none, so no liquidity is invented.
    assert sum(b.volume for b in five) == sum(b.volume for b in bars)
    assert five[0].high == max(b.high for b in bars[:3])
    assert five[0].low == min(b.low for b in bars[:3])
    # Each filler is flat at the previous close — a period where nothing printed, not a price move.
    for filler in five[1:-1]:
        assert filler.volume == 0.0
        assert filler.open == filler.high == filler.low == filler.close == five[0].close


def test_aggregate_fills_only_the_interior_never_a_tail() -> None:
    """None of the 25 real fixtures ends on a zero-volume bar, so a tail would be invented."""
    bars = to_bars([_agg_row(_et(4, 0), 10.0, 100.0), _agg_row(_et(4, 10), 11.0, 100.0)])
    five = aggregate(bars, minutes=5)
    assert [b.start.astimezone(ET).strftime("%H:%M") for b in five] == ["04:00", "04:05", "04:10"]
    assert five[-1].volume == 100.0  # ends on the traded bar, nothing appended past it


def test_ibkr_fixtures_are_contiguous_with_flat_filler_bars() -> None:
    """The measurement that overturned #442's premise, pinned so it cannot drift back.

    `aggregate` used to say "IBKR's historical bars omit periods with no trades". The 25 committed
    real-market cases in `tests/fixtures/review_cases/` are the ground truth, and they say the
    opposite. If this ever fails, the fill rule needs revisiting — not the other way round.
    """
    cases = sorted(Path("tests/fixtures/review_cases").glob("*.json"))
    assert len(cases) == 25
    gaps: set[float] = set()
    zero_volume = total = 0
    for path in cases:
        bars = json.loads(path.read_text())["bars"]
        starts = [datetime.fromisoformat(b[0]) for b in bars]
        gaps |= {(b - a).total_seconds() / 60.0 for a, b in zip(starts, starts[1:], strict=False)}
        zero_volume += sum(1 for b in bars if float(b[5]) == 0.0)
        total += len(bars)
        assert float(bars[-1][5]) > 0.0, f"{path.stem} ends on a filler bar"
    assert gaps == {5.0}, f"IBKR series are not contiguous on the 5-min grid: {sorted(gaps)}"
    assert zero_volume / total > 0.05, "no flat filler bars — the premise would need re-measuring"


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
    # Grouped-daily calls specifically: `calls` also carries the one-off #443 reference fetch, and
    # what this test is about is the per-session economy of phase 1.
    assert len(source.grouped_requested) == len(sessions) + 1  # +1 for the session before the first
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


# ================================================================================================
# The entitlement floor (#440): the vendor's window is shorter than the one we planned
# ================================================================================================
#
# The bug this section pins down took the harvest from "45 nights" to "never": phase 1 walks
# ascending and pays one extra call for the session BEFORE the oldest planned one, which sits one
# session past a lookback set at the entitlement edge. The HarvestError propagated straight out to
# `main`, so a single unbuyable date at the far end of a 477-session window cost 100% of the job,
# every night, for as long as it ran.


def _entitlement_window(s: Settings, today: date) -> list[date]:
    """The sessions `plan_sessions` would hand phase 1 for ``today``, ascending."""
    return sorted(plan_sessions(s, today=today))


def test_a_seed_call_past_the_entitlement_no_longer_kills_the_night(tmp_path: Path) -> None:
    """The production failure of 2026-08-04, in miniature.

    The oldest planned session is servable; the previous close it needs is not. That must cost the
    one session it actually blocks, not the whole run.
    """
    s = _settings(tmp_path, harvest_lookback_days=6)
    store = harvest_store(s)
    today = date(2026, 7, 10)
    sessions = _entitlement_window(s, today)
    assert sessions[0] == date(2026, 7, 6)  # its prior is 2026-07-02 (the 3rd is the holiday)
    grouped = {
        d: [_grouped_row("RUNNER", high=6.0, close=5.0, volume=9e6)]
        for d in [*sessions, date(2026, 7, 2)]
    }
    source = FakeSource(grouped=grouped, entitled_from=date(2026, 7, 6))
    cp = Checkpoint.load(checkpoint_path(s))

    results = harvest_daily(source, store, s, sessions, checkpoint=cp)

    harvested = [r.trading_date for r in results]
    assert harvested == sessions[1:], "everything reachable should have been harvested"
    assert cp.entitlement_floor == date(2026, 7, 2)  # the date actually refused
    assert stored_universe(store, sessions[1])  # and its universe really landed


def test_the_unseedable_session_does_not_cascade_into_the_whole_window(tmp_path: Path) -> None:
    """The trap in the obvious fix.

    Session D is skipped because its *prior* is unbuyable. Recording D as the floor too would make
    D+1 unseedable (its prior is D), then D+2, and so on — the floor would walk the length of the
    window and the harvest would report a clean run having stored nothing.
    """
    s = _settings(tmp_path, harvest_lookback_days=6)
    store = harvest_store(s)
    today = date(2026, 7, 10)
    sessions = _entitlement_window(s, today)
    grouped = {
        d: [_grouped_row("RUNNER", high=6.0, close=5.0, volume=9e6)]
        for d in [*sessions, date(2026, 7, 2)]
    }
    source = FakeSource(grouped=grouped, entitled_from=date(2026, 7, 6))
    cp = Checkpoint.load(checkpoint_path(s))

    harvest_daily(source, store, s, sessions, checkpoint=cp)

    assert cp.entitlement_floor == date(2026, 7, 2), "the floor walked forward off a derived skip"
    assert len(cp.daily_done) == len(sessions) - 1


def test_the_floor_is_persisted_so_the_next_night_neither_replans_nor_reprobes(
    tmp_path: Path,
) -> None:
    """A refusal costs a call. Paying it once a night forever is a slow leak, and a backlog that
    counts dates nobody can buy is a progress bar that never reaches the end."""
    s = _settings(tmp_path, harvest_lookback_days=6)
    store = harvest_store(s)
    today = date(2026, 7, 10)
    sessions = _entitlement_window(s, today)
    grouped = {
        d: [_grouped_row("RUNNER", high=6.0, close=5.0, volume=9e6)]
        for d in [*sessions, date(2026, 7, 2)]
    }
    source = FakeSource(grouped=grouped, entitled_from=date(2026, 7, 6))
    cp = Checkpoint.load(checkpoint_path(s))
    harvest_daily(source, store, s, sessions, checkpoint=cp)

    reloaded = Checkpoint.load(checkpoint_path(s))
    assert reloaded.entitlement_floor == date(2026, 7, 2)  # survived the process boundary

    # Night two plans against it: the refused date is gone from the window entirely.
    planned = plan_sessions(
        s, today=today, done=sorted(reloaded.done), not_before=reloaded.entitlement_floor
    )
    assert date(2026, 7, 2) not in planned
    assert all(d > date(2026, 7, 2) for d in planned)

    # ...and re-running phase 1 asks the vendor nothing about the dates it already knows are dead.
    second = FakeSource(grouped=grouped, entitled_from=date(2026, 7, 6))
    harvest_daily(second, store, s, sessions, checkpoint=reloaded)
    assert date(2026, 7, 2) not in second.grouped_requested


def test_a_plain_403_still_stops_the_night_rather_than_trimming_the_window(tmp_path: Path) -> None:
    """A revoked key is also a 403. Misreading it as an entitlement edge would trim the plan to
    nothing every night while reporting a clean run — worse than the crash being fixed here."""
    s = _settings(tmp_path, harvest_lookback_days=6)
    store = harvest_store(s)
    sessions = _entitlement_window(s, date(2026, 7, 10))

    class KeyRevoked(FakeSource):
        def grouped_daily(self, day: date) -> list[dict[str, Any]]:
            self.calls += 1
            raise HarvestError(
                'HTTP 403 on .../x: {"status":"NOT_AUTHORIZED","message":"Unknown API Key"}'
            )

    cp = Checkpoint.load(checkpoint_path(s))
    with pytest.raises(HarvestError, match="Unknown API Key"):
        harvest_daily(KeyRevoked(), store, s, sessions, checkpoint=cp)
    assert cp.entitlement_floor is None


def test_phase_2_never_marks_an_entitlement_blocked_session_done(tmp_path: Path) -> None:
    """`_accumulate_symbol` swallows per-symbol failures, which for a wholly unbuyable date would
    mean an empty session marked complete — indistinguishable ever after from a quiet day."""
    s = _settings(tmp_path, harvest_rate_sleep_sec=0.0)
    store = harvest_store(s)
    newer, older = DAY, date(2026, 7, 1)
    for d in (newer, older):
        store.append("daily_universe", [_daily("AAAA").as_record()], partition_date=d)
    source = FakeSource(
        minutes={("AAAA", d): _runner_minutes(d) for d in (newer, older)},
        entitled_from=newer,
    )
    cp = Checkpoint.load(checkpoint_path(s))

    run = run_harvest(
        source,
        store,
        s,
        [newer, older],  # newest-first, as plan_sessions returns them
        checkpoint=cp,
        window=RunWindow(),
        now_fn=_clock(datetime.combine(newer, time(22, 0), tzinfo=ET)),
    )

    assert run.completed == (newer,)
    assert cp.done == {newer}, "the unbuyable date must stay pending, not be recorded as harvested"
    assert cp.entitlement_floor == older
    assert "entitlement-floor" in run.stopped_because
    for dataset in HARVEST_DATASETS:
        assert not (store.data_dir / dataset / f"dt={older.isoformat()}").exists()


def test_note_entitlement_floor_only_ever_moves_forward(tmp_path: Path) -> None:
    """The entitlement is a rolling window, so a date outside it stays outside it. A floor that
    could move backwards would re-open dates already paid for in refused calls."""
    cp = Checkpoint.load(tmp_path / "cp.json")
    assert cp.note_entitlement_floor(date(2024, 8, 2)) is True
    assert cp.note_entitlement_floor(date(2024, 1, 1)) is False  # older tells us nothing new
    assert cp.entitlement_floor == date(2024, 8, 2)
    assert cp.note_entitlement_floor(date(2024, 9, 1)) is True
    assert Checkpoint.load(cp.path).entitlement_floor == date(2024, 9, 1)


def test_a_checkpoint_written_before_the_floor_existed_still_loads(tmp_path: Path) -> None:
    """The field was added WITHOUT a version bump on purpose: `load` refuses an unknown version,
    so bumping it would brick the box's existing checkpoint — the record of its spent budget."""
    path = tmp_path / "cp.json"
    path.write_text(
        json.dumps(
            {
                "version": CHECKPOINT_VERSION,
                "done": [DAY.isoformat()],
                "daily_done": [],
                "calls": 218,
                "updated_at": "2026-08-04T22:02:00+00:00",
            }
        )
    )
    cp = Checkpoint.load(path)
    assert cp.entitlement_floor is None
    assert cp.done == {DAY} and cp.calls == 218


def test_massive_source_tells_an_entitlement_refusal_from_any_other_403(monkeypatch: Any) -> None:
    """The classification happens at the transport, from the body — the only place it can."""
    bodies = {
        "entitled": b'{"status":"NOT_AUTHORIZED","message":"Attempted to request data past '
        b'historical entitlements. Please upgrade your plan."}',
        "revoked": b'{"status":"NOT_AUTHORIZED","message":"Unknown API Key"}',
    }

    def _raise(body: bytes) -> Any:
        def boom(*_a: Any, **_k: Any) -> Any:
            raise urllib.error.HTTPError(
                "https://x/y?apiKey=SECRET", 403, "forbidden", {}, io.BytesIO(body)
            )  # type: ignore[arg-type]

        return boom

    src = MassiveSource(api_key="SECRET", rate_sleep_sec=0.0, sleep=lambda _: None)
    monkeypatch.setattr("urllib.request.urlopen", _raise(bodies["entitled"]))
    with pytest.raises(HarvestEntitlementError) as entitled:
        src.grouped_daily(DAY)
    assert "SECRET" not in str(entitled.value)

    monkeypatch.setattr("urllib.request.urlopen", _raise(bodies["revoked"]))
    with pytest.raises(HarvestError) as revoked:
        src.grouped_daily(DAY)
    assert not isinstance(revoked.value, HarvestEntitlementError)


# ================================================================================================
# Reconstruction fidelity (#442): the bars the engine reads, and the interval it reads them at
# ================================================================================================
#
# Both defects here are invisible downstream: they produce a plausible trade with a real R attached,
# from a setup that could not have been taken, or an appearance credited minutes late. The tests
# below are behavioural on purpose — they compare the reconstruction against the IBKR-shaped series
# the engine was calibrated on, rather than asserting the reconstruction's own output back at it.


def _five_min_rows(
    candles: Sequence[tuple[tuple[int, int], float, float, float, float, float]], day: date = DAY
) -> list[dict[str, Any]]:
    """Minute rows that fold to exactly the given 5-min candles — omitting untraded periods."""
    rows: list[dict[str, Any]] = []
    for (hh, mm), o, h, low, c, vol in candles:
        rows.extend(_candle_minutes(_et(hh, mm, day), o, h, low, c, vol))
    return rows


def test_a_hole_in_the_consolidation_does_not_manufacture_a_shorter_flag() -> None:
    """The #442 failure, end to end through the real detector.

    A pole, then a SEVEN-candle consolidation of which three printed nothing. `bull_flag_max_cons`
    is 4, so the live engine — which sees IBKR's flat filler for the quiet candles — rejects it.
    Before the fix the reconstruction deleted those candles and handed the detector a compliant
    4-candle flag: a trade that never existed, with a real R attached.
    """
    from small_cap_stack.bullflag.day import detect_day_with_settings

    s = _settings(Path("/tmp"))
    traded = [
        ((7, 0), 3.00, 3.05, 2.98, 3.02, 120_000.0),
        ((7, 5), 3.05, 3.40, 3.03, 3.38, 300_000.0),
        ((7, 10), 3.38, 3.80, 3.36, 3.78, 400_000.0),  # pole peak
        ((7, 15), 3.75, 3.76, 3.60, 3.62, 80_000.0),  # consolidation 1
        ((7, 20), 3.62, 3.70, 3.58, 3.60, 60_000.0),  # consolidation 2
        # 07:25, 07:30, 07:35 print nothing at all
        ((7, 40), 3.60, 3.66, 3.55, 3.58, 40_000.0),  # consolidation 6
        ((7, 45), 3.58, 3.62, 3.54, 3.56, 30_000.0),  # consolidation 7
        ((7, 50), 3.56, 4.20, 3.55, 4.15, 500_000.0),  # breakout
    ]
    minute_rows = _five_min_rows(traded)

    filled = aggregate(to_bars(minute_rows), minutes=5)  # what the fix produces
    punctured = aggregate(to_bars(minute_rows), minutes=5, fill=False)  # the old behaviour

    assert len(filled) == len(punctured) + 3, "the three quiet candles should be restored"

    # Anchor the appearance late enough that the 30-minute staleness bound is not what decides
    # this — the whole point is to isolate the candle COUNT as the difference between the two.
    first_hit = _et(7, 25)
    good = detect_day_with_settings(filled, s, first_hit)
    bad = detect_day_with_settings(punctured, s, first_hit)

    # The consolidation really is 7 candles long and the cap is 4, so the live-shaped series has
    # no takeable setup...
    assert good is None or not good.takeable, (
        f"a 7-candle consolidation must not be takeable at max_cons={s.bull_flag_max_cons}"
    )
    # ...and the punctured series is exactly what made it look compliant.
    assert bad is not None and bad.takeable, (
        "the old behaviour no longer manufactures a takeable flag — rewrite this test's premise"
    )
    # The measurable difference: deleting the quiet candles shortened the flag past the cap.
    assert bad.segment.cons_len <= s.bull_flag_max_cons
    assert good is not None and good.segment.cons_len > s.bull_flag_max_cons


def test_the_appearance_time_is_not_credited_late_on_a_thin_tape() -> None:
    """`bar_interval` returns the MODAL gap. A name printing every 3 minutes infers 3 minutes, so
    every appearance is stamped 2 minutes late and the trailing-volume window collapses."""
    s = _settings(Path("/tmp"))
    # Prints every third minute from 04:00; the 04:30 print alone clears the 100k volume gate.
    rows = [
        _agg_row(_et(4, 0) + timedelta(minutes=i), 5.0, 150_000.0 if i == 30 else 500.0)
        for i in range(0, 60, 3)
    ]
    bars = to_bars(rows)

    inferred = reconstruct_hit(bars, s, prev_close=1.0)
    known = reconstruct_hit(bars, s, prev_close=1.0, interval=timedelta(minutes=1))

    assert known.hit_time is not None and inferred.hit_time is not None
    # The spike bar starts at 04:30; on a minute grid it is knowable at 04:31.
    assert known.hit_time.astimezone(ET).strftime("%H:%M") == "04:31"
    assert inferred.hit_time.astimezone(ET).strftime("%H:%M") == "04:33"  # the bug, 2 min late
    # And the late interval loses hits, which is what `report.symbol_runs` segments a day on.
    assert len(known.hit_times) > len(inferred.hit_times)


def test_the_harvest_passes_the_interval_it_asked_the_vendor_for(tmp_path: Path) -> None:
    """End to end: the stored appearance must not depend on how densely the symbol happened to
    trade. This is the plumbing assertion behind the test above."""
    s = _settings(tmp_path, harvest_rate_sleep_sec=0.0)
    store = harvest_store(s)
    # A deliberately sparse pre-market: one print every 3 minutes until the volume spike.
    sparse = [
        _agg_row(_et(4, 0) + timedelta(minutes=i), 3.0, 200_000.0 if i == 60 else 400.0)
        for i in range(0, 200, 3)
    ]
    sparse += [_agg_row(_et(9, 30) + timedelta(minutes=i), 3.0, 1000.0) for i in range(390)]
    source = FakeSource(minutes={("THIN", DAY): sparse})
    harvest_session(source, store, s, DAY, [_daily("THIN")])

    opps = store.read("opportunities", dt=DAY)
    assert not opps.is_empty()
    first_seen = opps.row(0, named=True)["first_seen_utc"]
    # 04:60 -> the spike bar starts 05:00, knowable at 05:01 on the minute grid it was fetched at.
    assert first_seen.astimezone(ET).strftime("%H:%M") == "05:01"


# ================================================================================================
# The harvested universe (#443): who is a candidate at all
# ================================================================================================
#
# Both defects here change the candidate POPULATION rather than any single row, which is why they
# are more dangerous than they look: `portfolio_max_trades_per_day` takes the first two triggers of
# a day, so an admitted ETN displaces a real name and a deleted runner is never replaced. Neither
# shows up as an error anywhere downstream.


def _grouped_ohlc(
    sym: str, *, high: float, low: float, close: float, volume: float = 9e6
) -> dict[str, Any]:
    return {"T": sym, "h": high, "l": low, "c": close, "v": volume}


def test_the_price_band_admits_a_runner_that_traded_out_of_the_top_of_it() -> None:
    """The live scanner filters on LAST PRICE at each tick, so a $38 name that runs to $55 is on it
    for most of the pre-market. Testing the day's HIGH against the ceiling deleted it entirely —
    and deleted the biggest movers preferentially, biasing expectancy downward."""
    s = _settings(Path("/tmp"))
    grouped = [
        _grouped_ohlc("BIGRUN", high=55.0, low=38.0, close=52.0),  # left the band intraday
        _grouped_ohlc("OKRUN", high=49.0, low=38.0, close=47.0),  # stayed inside it
    ]
    kept = {r.symbol for r in universe_rows(grouped, {"BIGRUN": 40.0, "OKRUN": 40.0}, s)}
    assert kept == {"BIGRUN", "OKRUN"}


def test_the_price_band_still_rejects_names_that_were_never_in_it() -> None:
    """Overlap, not "anything goes": a name whose whole day sat outside the band is still out."""
    s = _settings(Path("/tmp"))
    grouped = [
        _grouped_ohlc("PENNY", high=0.80, low=0.40, close=0.75),  # high never reached $1
        _grouped_ohlc("BLUE", high=310.0, low=280.0, close=300.0),  # low never came under $50
        _grouped_ohlc("EDGE", high=51.0, low=49.5, close=50.5),  # straddles the ceiling -> in
    ]
    prev = {"PENNY": 0.5, "BLUE": 200.0, "EDGE": 40.0}
    assert {r.symbol for r in universe_rows(grouped, prev, s)} == {"EDGE"}


def test_etfs_and_etns_are_excluded_the_way_the_live_scan_excludes_them(tmp_path: Path) -> None:
    """`scanner.py` drops them with IBKR's `stkTypes exc:` filter; the vendor's grouped-daily is
    every US-listed ticker. Leveraged single-stock ETNs are the market's most reliable producers of
    the exact "+10%, $1-50, >100k" day this strategy hunts, so without this the harvested universe
    is a different population from the tracker's."""
    s = _settings(tmp_path)
    store = harvest_store(s)
    sessions = trading_sessions(date(2026, 7, 6), date(2026, 7, 8), s)
    rows = [
        _grouped_ohlc("REAL", high=6.0, low=3.0, close=5.0),
        _grouped_ohlc("CONL", high=6.0, low=3.0, close=5.0),  # a leveraged single-stock ETN
        _grouped_ohlc("SQQQ", high=6.0, low=3.0, close=5.0),  # a leveraged ETF
    ]
    source = FakeSource(
        grouped=dict.fromkeys([*sessions, date(2026, 7, 2)], rows),
        tickers={("ETN", True): ["CONL"], ("ETF", True): ["SQQQ"]},
    )
    harvest_daily(source, store, s, sessions)

    for day in sessions:
        assert [r.symbol for r in stored_universe(store, day)] == ["REAL"]


def test_the_exclusion_set_asks_for_delisted_products_too(tmp_path: Path) -> None:
    """A two-year window walks over dates on which ETNs now delisted were very much trading, so an
    active-only list would leave exactly the oldest part of the harvest unfiltered."""
    s = _settings(tmp_path)
    store = harvest_store(s)
    sessions = trading_sessions(date(2026, 7, 6), date(2026, 7, 7), s)
    rows = [
        _grouped_ohlc("REAL", high=6.0, low=3.0, close=5.0),
        _grouped_ohlc("DEADETN", high=6.0, low=3.0, close=5.0),
    ]
    source = FakeSource(
        grouped=dict.fromkeys([*sessions, date(2026, 7, 2)], rows),
        tickers={("ETN", False): ["DEADETN"]},  # only in the DELISTED list
    )
    harvest_daily(source, store, s, sessions)

    assert (("ETN", False)) in source.ticker_queries
    assert all(
        t in source.ticker_queries
        for t in [(x, a) for x in s.harvest_exclude_ticker_types for a in (True, False)]
    )
    assert [r.symbol for r in stored_universe(store, sessions[0])] == ["REAL"]


def test_the_exclusion_set_is_fetched_once_for_the_whole_run_not_per_session(
    tmp_path: Path,
) -> None:
    """It is reference data. Re-fetching per session would cost ~500x for an answer that does not
    vary by date — and at 13s a call, that is nights."""
    s = _settings(tmp_path)
    store = harvest_store(s)
    sessions = trading_sessions(date(2026, 7, 6), date(2026, 7, 9), s)
    rows = [_grouped_ohlc("REAL", high=6.0, low=3.0, close=5.0)]
    source = FakeSource(grouped=dict.fromkeys([*sessions, date(2026, 7, 2)], rows), tickers={})
    harvest_daily(source, store, s, sessions)
    assert len(source.ticker_queries) == 2 * len(s.harvest_exclude_ticker_types)

    # A second run reads the cached file rather than re-querying.
    again = FakeSource(grouped=dict.fromkeys([*sessions, date(2026, 7, 2)], rows), tickers={})
    harvest_daily(again, store, s, [sessions[0]])
    assert again.ticker_queries == []


def test_a_reference_fetch_failure_degrades_instead_of_losing_the_night(tmp_path: Path) -> None:
    """Losing a night of phase 1 because a reference endpoint hiccuped is a worse trade than
    filtering with the cached list — but it must be logged, not silent."""
    s = _settings(tmp_path)
    store = harvest_store(s)
    sessions = trading_sessions(date(2026, 7, 6), date(2026, 7, 7), s)
    rows = [
        _grouped_ohlc("REAL", high=6.0, low=3.0, close=5.0),
        _grouped_ohlc("CONL", high=6.0, low=3.0, close=5.0),
    ]

    class Broken(FakeSource):
        def tickers_of_type(self, ticker_type: str, *, active: bool) -> list[str]:
            raise HarvestError("reference endpoint is down")

    good = FakeSource(
        grouped=dict.fromkeys([*sessions, date(2026, 7, 2)], rows),
        tickers={("ETN", True): ["CONL"]},
    )
    harvest_daily(good, store, s, [sessions[0]])  # populates the cache

    broken = Broken(grouped=dict.fromkeys([*sessions, date(2026, 7, 2)], rows))
    harvest_daily(broken, store, s, [sessions[1]])  # the night still runs...
    assert [r.symbol for r in stored_universe(store, sessions[1])] == [
        "REAL"
    ]  # ...and still filters
