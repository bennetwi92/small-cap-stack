"""Tests for monitoring (#5): heartbeat + metrics."""

from __future__ import annotations

import asyncio

from small_cap_stack.monitoring import SCAN_TICKS, Heartbeat


def test_heartbeat_ping_and_fail_urls() -> None:
    calls: list[str] = []
    hb = Heartbeat("https://hc-ping.com/abc/", fetch=calls.append)
    asyncio.run(hb.ping())
    asyncio.run(hb.fail())
    assert calls == ["https://hc-ping.com/abc", "https://hc-ping.com/abc/fail"]


def test_heartbeat_noop_without_url() -> None:
    calls: list[str] = []
    hb = Heartbeat("", fetch=calls.append)
    asyncio.run(hb.ping())
    asyncio.run(hb.fail())
    assert calls == []


def test_heartbeat_swallows_errors() -> None:
    def boom(_url: str) -> None:
        raise RuntimeError("network down")

    hb = Heartbeat("https://hc-ping.com/abc", fetch=boom)
    asyncio.run(hb.ping())  # must not raise


def test_metric_increments() -> None:
    before = SCAN_TICKS._value.get()  # type: ignore[attr-defined]
    SCAN_TICKS.inc()
    assert SCAN_TICKS._value.get() == before + 1  # type: ignore[attr-defined]
