"""Tests for the application wiring (scheduler jobs, restart window, services)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

from small_cap_stack import app as appmod
from small_cap_stack.app import Application
from small_cap_stack.clock import ET
from small_cap_stack.config import Settings
from small_cap_stack.storage import Store

_DAY = date(2026, 7, 2)


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _seed_day(store: Store, day: date) -> None:
    """One opportunity for ``day`` — enough for a non-empty EOD report (no bars needed)."""
    ts = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
    store.append(
        "opportunities",
        [
            {
                "opportunity_id": f"{day}:AAA",
                "symbol": "AAA",
                "con_id": 1,
                "trading_date": day,
                "first_seen_utc": ts,
                "first_rank": 0,
            }
        ],
        partition_date=day,
    )
    store.append(
        "scanner_hits",
        [{"opportunity_id": f"{day}:AAA", "symbol": "AAA", "ts_utc": ts, "rank": 0}],
        partition_date=day,
    )


def test_scheduler_registers_jobs() -> None:
    app = Application(_settings())
    ids = {job.id for job in app.scheduler.get_jobs()}
    assert ids == {"tick", "scan_start", "scan_end", "eod_bars", "eod_report", "eod_backfill"}


def test_builds_services() -> None:
    app = Application(_settings())
    assert app.supervisor is not None
    assert app.capture is not None
    assert app.transport is not None


def test_is_expected_restart_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 6, 29, 23, 47, tzinfo=ET))
    inside = Application(_settings(gateway_restart=time(23, 45), gateway_restart_window_min=10))
    assert inside._is_expected_restart() is True  # 23:45–23:55 contains 23:47
    outside = Application(_settings(gateway_restart=time(10, 0), gateway_restart_window_min=10))
    assert outside._is_expected_restart() is False


def test_refresh_stats_charts_writes_todays_session_after_bars(tmp_path: Path) -> None:
    app = Application(_settings(data_dir=tmp_path))
    _seed_day(app.store, _DAY)
    # 16:25 ET is past eod_bars_fetch (16:20) — the day's session is complete, so the tick's
    # catch-up refresh advances the dashboard to today even without the 16:30 EOD job firing.
    app._refresh_stats_charts(datetime(2026, 7, 2, 16, 25, tzinfo=ET))

    stats = tmp_path / "dashboard" / "stats.json"
    assert stats.exists()
    assert json.loads(stats.read_text())["trading_date"] == "2026-07-02"
    assert (tmp_path / "dashboard" / "charts.json").exists()
    # The dated review payload + navigation index publish alongside the legacy files (#141).
    assert (tmp_path / "dashboard" / "charts" / "2026-07-02.json").exists()
    index = json.loads((tmp_path / "dashboard" / "index.json").read_text())
    assert [d["date"] for d in index["dates"]] == ["2026-07-02"]


def test_refresh_stats_charts_noop_before_bars(tmp_path: Path) -> None:
    app = Application(_settings(data_dir=tmp_path))
    _seed_day(app.store, _DAY)
    # Mid-session (10:00 ET): the day isn't done, so the previous session must stay put — the tick
    # leaves stats.json untouched (it's reviewable all day, #117).
    app._refresh_stats_charts(datetime(2026, 7, 2, 10, 0, tzinfo=ET))
    assert not (tmp_path / "dashboard" / "stats.json").exists()


def test_refresh_stats_charts_skips_empty_day(tmp_path: Path) -> None:
    app = Application(_settings(data_dir=tmp_path))  # store seeded with nothing (e.g. a weekend)
    app._refresh_stats_charts(datetime(2026, 7, 2, 16, 25, tzinfo=ET))
    # No opportunities -> no write, so a non-trading day never clobbers the last real session.
    assert not (tmp_path / "dashboard" / "stats.json").exists()


def test_refresh_stats_charts_disabled(tmp_path: Path) -> None:
    app = Application(_settings(data_dir=tmp_path, dashboard_enabled=False))
    _seed_day(app.store, _DAY)
    app._refresh_stats_charts(datetime(2026, 7, 2, 16, 25, tzinfo=ET))
    assert not (tmp_path / "dashboard" / "stats.json").exists()


# --- trading-calendar gate (#137) ---------------------------------------------------------------


def test_on_tick_skips_scan_on_non_trading_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Saturday 2026-07-04, 10:00 ET — inside the scan window, but not a session. The scan block
    # must not run; the status export still does (the dashboard stays live 24/7).
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 4, 10, 0, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path))
    monkeypatch.setattr(app.transport, "is_connected", lambda: True)
    # The scheduler is never started in tests, so its jobs have no next_run_time yet.
    monkeypatch.setattr(app.scheduler, "get_jobs", list)

    async def boom_scan(ib: object) -> list[object]:
        raise AssertionError("scanner must not run on a non-trading day")

    monkeypatch.setattr(app.scanner, "scan", boom_scan)
    asyncio.run(app._on_tick())
    assert (tmp_path / "dashboard" / "status.json").exists()


def test_on_tick_scans_on_a_trading_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Thursday 2026-07-02, 10:00 ET — the same setup must reach the scanner.
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 2, 10, 0, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path))
    monkeypatch.setattr(app.transport, "is_connected", lambda: True)
    scanned = []

    async def fake_scan(ib: object) -> list[object]:
        scanned.append(True)
        return []

    monkeypatch.setattr(app.scanner, "scan", fake_scan)
    asyncio.run(app._on_tick())
    assert scanned == [True]


def test_eod_jobs_noop_on_non_trading_day(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The 2026-07-03 incident day (Independence Day observed): both EOD jobs must return early.
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 3, 16, 25, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path))

    async def boom_batch(trading_date: date) -> None:
        raise AssertionError("EOD batch must not run on a non-trading day")

    monkeypatch.setattr(app, "_eod_ibkr_batch", boom_batch)

    def boom_report(*a: object, **k: object) -> object:
        raise AssertionError("EOD report must not build on a non-trading day")

    monkeypatch.setattr(appmod, "build_eod_report", boom_report)
    asyncio.run(app._on_eod_bars())
    asyncio.run(app._on_eod_report())


def test_eod_backfill_filters_to_trading_days(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Monday 2026-07-06 03:45 ET with a 3-day lookback: Sun 07-05 and Sat 07-04 drop out, and the
    # job still runs (gating the whole job on a weekend would strand a failed Friday EOD).
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 6, 3, 45, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path, backfill_days=3))
    monkeypatch.setattr(app.transport, "is_connected", lambda: True)
    funds: list[date] = []

    async def fake_funds(d: date) -> None:
        funds.append(d)

    monkeypatch.setattr(app, "_backfill_fundamentals", fake_funds)
    seen: list[list[date]] = []

    async def fake_backfill(dates: list[date]) -> list[date]:
        seen.append(list(dates))
        return []

    monkeypatch.setattr(app.capture, "backfill_recent", fake_backfill)
    asyncio.run(app._on_eod_backfill())
    assert funds == [date(2026, 7, 6)]
    assert seen == [[date(2026, 7, 6)]]


# --- tick instrumentation (#321) ----------------------------------------------------------------


def test_status_json_carries_coarse_health_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The acceptance test for "reachable without SSH" (#321), scrubbed by #340/#344: the payload
    # is public, so it carries coarse verdicts and counters — never raw seconds or headroom
    # numbers (those stay in Prometheus/SSH).
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 2, 10, 0, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path))
    monkeypatch.setattr(app.scheduler, "get_jobs", list)
    _seed_day(app.store, _DAY)
    asyncio.run(app._on_tick())  # disconnected -> no scan, but the status export runs

    s = json.loads((tmp_path / "dashboard" / "status.json").read_text())
    assert "timings" not in s  # the #344 scrub: no timing numbers on the public surface
    h = s["health"]
    assert set(h) == {"tick", "ticks_over_budget_total", "jobs_missed_total", "mem_ok", "disk_ok"}
    assert h["tick"] in {"ok", "slow", "over_budget"}
    assert h["ticks_over_budget_total"] >= 0
    assert h["mem_ok"] in {True, False, None}  # None where /proc/meminfo is absent (macOS dev)
    assert h["disk_ok"] in {True, False}
    # File counts: the number that would have caught #318 (scanner_hits at 32k files).
    assert s["data"]["opportunities"]["files"] == 1
    assert s["data"]["scanner_hits"]["files"] == 1


def test_canary_written_and_throttled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The canary (#346) rides the tick's status export but on its own throttle: with the clock
    # frozen, a second tick inside the interval must not recompute it.
    calls: list[int] = []

    def fake_canary(*args: object, **kwargs: object) -> dict[str, int]:
        calls.append(1)
        return {"built": len(calls)}

    monkeypatch.setattr(appmod, "build_canary", fake_canary)
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 2, 10, 0, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path))
    monkeypatch.setattr(app.scheduler, "get_jobs", list)
    asyncio.run(app._on_tick())
    asyncio.run(app._on_tick())
    assert calls == [1]
    assert json.loads((tmp_path / "dashboard" / "canary.json").read_text()) == {"built": 1}


def test_over_budget_tick_increments_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from small_cap_stack.monitoring import metric_value

    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 2, 10, 0, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path, dashboard_enabled=False))
    # Fake clock: the tick's start/end perf_counter reads land 45s apart (> half the 60s budget).
    ticks = iter([0.0, 45.0, 90.0, 135.0])
    monkeypatch.setattr(appmod.time, "perf_counter", lambda: next(ticks))
    before = metric_value("scs_ticks_over_budget_total")
    asyncio.run(app._on_tick())
    assert metric_value("scs_ticks_over_budget_total") == before + 1
    assert metric_value("scs_tick_seconds") == 45.0


def test_heartbeat_pings_on_completion_not_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The ping moved to the END of the tick (#321): a tick that raises must NOT ping, so a
    # persistently failing (or wedged) tick goes silent and Healthchecks alerts.
    monkeypatch.setattr(appmod, "now_et", lambda: datetime(2026, 7, 2, 10, 0, tzinfo=ET))
    app = Application(_settings(data_dir=tmp_path, dashboard_enabled=False))
    monkeypatch.setattr(app.transport, "is_connected", lambda: True)
    pings: list[bool] = []

    async def fake_ping() -> None:
        pings.append(True)

    monkeypatch.setattr(app.heartbeat, "ping", fake_ping)

    async def boom_scan(ib: object) -> list[object]:
        raise RuntimeError("scanner down")

    monkeypatch.setattr(app.scanner, "scan", boom_scan)
    with pytest.raises(RuntimeError):
        asyncio.run(app._on_tick())
    assert pings == []  # no ping -> the dead-man's switch can actually fire

    async def ok_scan(ib: object) -> list[object]:
        return []

    monkeypatch.setattr(app.scanner, "scan", ok_scan)
    asyncio.run(app._on_tick())
    assert pings == [True]  # a completed tick pings
