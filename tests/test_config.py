"""Tests for settings loading."""

from __future__ import annotations

from datetime import time

import pytest

from tests.support import settings


def test_defaults() -> None:
    s = settings()
    assert s.ibkr_port == 4002
    assert s.ibkr_trading_mode == "paper"
    assert s.scan_start == time(4, 0)
    assert s.scan_end == time(11, 59)


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IBKR_PORT", "7497")
    monkeypatch.setenv("IBKR_TRADING_MODE", "live")
    monkeypatch.setenv("SCAN_START", "03:30")
    s = settings()
    assert s.ibkr_port == 7497
    assert s.ibkr_trading_mode == "live"
    assert s.scan_start == time(3, 30)
