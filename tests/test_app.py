"""Tests for the application wiring (scheduler jobs, restart window, services)."""

from __future__ import annotations

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
    assert app.transport.registry is app.subscriptions


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
