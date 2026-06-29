"""Tests for raw capture (#14): the evolving opportunity record."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

from small_cap_stack.capture import Bar, CaptureService, NewsItem, opportunity_id
from small_cap_stack.config import Settings
from small_cap_stack.scanner import Candidate
from small_cap_stack.storage import Store


def _settings(**o: object) -> Settings:
    return Settings(_env_file=None, **o)  # type: ignore[call-arg]


def _bar(minute: int) -> Bar:
    return Bar(
        start=datetime(2026, 6, 29, 13, minute, tzinfo=UTC),
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=1000.0,
    )


class FakeBars:
    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars
        self.calls = 0

    async def fetch_5m_bars(self, candidate: Candidate, *, lookback_sec: int) -> list[Bar]:
        self.calls += 1
        return list(self.bars)


class FakeNews:
    def __init__(self, items: list[NewsItem]) -> None:
        self.items = items

    async def fetch_news(
        self, candidate: Candidate, *, lookback_days: int, limit: int
    ) -> list[NewsItem]:
        return list(self.items)


def _candidate(symbol: str = "AZI") -> Candidate:
    return Candidate(rank=0, symbol=symbol, con_id=42, exchange="NASDAQ")


def test_opportunity_id_format() -> None:
    assert opportunity_id(date(2026, 6, 29), "azi") == "2026-06-29:AZI"


def test_capture_opens_and_records_everything(tmp_path: Path) -> None:
    store = Store(tmp_path)
    svc = CaptureService(
        store=store,
        bars=FakeBars([_bar(30), _bar(35)]),
        news=FakeNews([NewsItem("2026-06-29 12:00:00", "DJ-N", "Big news", "a1")]),
        settings=_settings(),
    )
    now = datetime(2026, 6, 29, 13, 40, tzinfo=UTC)
    asyncio.run(svc.on_scan_tick([_candidate()], now))

    assert store.read("opportunities").height == 1
    assert store.read("scanner_hits").height == 1
    assert store.read("news").height == 1
    assert store.read("bars").height == 2


def test_second_tick_does_not_reopen_but_logs_hit(tmp_path: Path) -> None:
    store = Store(tmp_path)
    svc = CaptureService(
        store=store, bars=FakeBars([_bar(30)]), news=FakeNews([]), settings=_settings()
    )
    now = datetime(2026, 6, 29, 9, 40, tzinfo=UTC)
    asyncio.run(svc.on_scan_tick([_candidate()], now))
    asyncio.run(svc.on_scan_tick([_candidate()], now))

    assert store.read("opportunities").height == 1  # not reopened
    assert store.read("scanner_hits").height == 2  # both appearances logged


def test_bars_are_deduped_across_ticks(tmp_path: Path) -> None:
    store = Store(tmp_path)
    bars = FakeBars([_bar(30), _bar(35)])
    svc = CaptureService(store=store, bars=bars, news=FakeNews([]), settings=_settings())
    now = datetime(2026, 6, 29, 9, 40, tzinfo=UTC)

    asyncio.run(svc.on_scan_tick([_candidate()], now))
    assert store.read("bars").height == 2

    bars.bars = [_bar(30), _bar(35), _bar(40)]  # one new bar
    asyncio.run(svc.capture_bars(now))
    assert store.read("bars").height == 3  # only the new bar appended
