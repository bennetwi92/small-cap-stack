"""Tests for the application wiring (scheduler jobs, restart window, services)."""

from __future__ import annotations

from datetime import datetime, time

import pytest

from small_cap_stack import app as appmod
from small_cap_stack.app import Application
from small_cap_stack.clock import ET
from small_cap_stack.config import Settings


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_scheduler_registers_jobs() -> None:
    app = Application(_settings())
    ids = {job.id for job in app.scheduler.get_jobs()}
    assert ids == {"tick", "scan_start", "scan_end", "eod_bars", "eod_report"}


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
