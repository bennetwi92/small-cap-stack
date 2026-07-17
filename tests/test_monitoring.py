"""Tests for monitoring (#5): heartbeat + metrics."""

from __future__ import annotations

import asyncio
from pathlib import Path

from small_cap_stack.monitoring import SCAN_TICKS, Heartbeat, disk_used_pct, mem_available_mb


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


def test_mem_available_reads_proc_meminfo(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:  4014356 kB\nMemFree:   123456 kB\nMemAvailable: 1024000 kB\n")
    assert mem_available_mb(meminfo) == 1000.0


def test_mem_available_none_where_unreadable(tmp_path: Path) -> None:
    assert mem_available_mb(tmp_path / "absent") is None  # e.g. macOS dev box
    garbled = tmp_path / "garbled"
    garbled.write_text("MemAvailable: lots\n")
    assert mem_available_mb(garbled) is None


def test_disk_used_pct(tmp_path: Path) -> None:
    pct = disk_used_pct(tmp_path)
    assert pct is not None and 0.0 <= pct <= 100.0
    assert disk_used_pct(tmp_path / "absent") is None
