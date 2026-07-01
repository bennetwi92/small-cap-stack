"""Tests for raw capture (#14, #62): discovery on the tick, bars in an end-of-day batch."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

from small_cap_stack.capture import Bar, CaptureService, NewsItem, opportunity_id
from small_cap_stack.config import Settings
from small_cap_stack.scanner import Candidate
from small_cap_stack.storage import Store

_TRADING_DATE = date(2026, 6, 29)


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

    async def fetch_day_bars(self, candidate: Candidate, *, trading_date: date) -> list[Bar]:
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


def _svc(store: Store, bars: object, news: FakeNews) -> CaptureService:
    return CaptureService(store=store, bars=bars, news=news, settings=_settings())  # type: ignore[arg-type]


def test_opportunity_id_format() -> None:
    assert opportunity_id(date(2026, 6, 29), "azi") == "2026-06-29:AZI"


def test_scan_tick_records_discovery_but_no_bars(tmp_path: Path) -> None:
    store = Store(tmp_path)
    svc = _svc(
        store,
        FakeBars([_bar(30), _bar(35)]),
        FakeNews([NewsItem("2026-06-29 12:00:00", "DJ-N", "Big news", "a1")]),
    )
    now = datetime(2026, 6, 29, 13, 40, tzinfo=UTC)
    asyncio.run(svc.on_scan_tick([_candidate()], now))

    assert store.read("opportunities").height == 1
    assert store.read("scanner_hits").height == 1
    assert store.read("news").height == 1
    assert store.read("bars").is_empty()  # bars are NOT captured on the tick anymore


def test_eod_batch_writes_day_bars(tmp_path: Path) -> None:
    store = Store(tmp_path)
    svc = _svc(store, FakeBars([_bar(30), _bar(35), _bar(40)]), FakeNews([]))
    now = datetime(2026, 6, 29, 9, 40, tzinfo=UTC)
    asyncio.run(svc.on_scan_tick([_candidate("AAA"), _candidate("BBB")], now))
    assert store.read("bars").is_empty()

    asyncio.run(svc.capture_day_bars(_TRADING_DATE))
    bars = store.read("bars")
    assert bars.height == 6  # 3 bars x 2 opportunities
    assert set(bars["symbol"].to_list()) == {"AAA", "BBB"}


def test_eod_batch_dedups_duplicate_opportunities(tmp_path: Path) -> None:
    # A duplicate opportunities row (a mid-day restart re-opened the name) must not fire a
    # redundant historical request / duplicate bar write for the same symbol.
    store = Store(tmp_path)
    bars = FakeBars([_bar(30), _bar(35)])
    svc = _svc(store, bars, FakeNews([]))
    asyncio.run(svc.on_scan_tick([_candidate("AAA")], datetime(2026, 6, 29, 9, 40, tzinfo=UTC)))
    dup = store.read("opportunities").row(0, named=True)  # same opportunity_id, appended again
    store.append("opportunities", [dup], partition_date=_TRADING_DATE)

    asyncio.run(svc.capture_day_bars(_TRADING_DATE))
    assert bars.calls == 1  # fetched once, not once per duplicate row
    assert store.read("bars").height == 2  # one symbol's bars, not doubled


def test_second_tick_does_not_reopen_but_logs_hit(tmp_path: Path) -> None:
    store = Store(tmp_path)
    svc = _svc(store, FakeBars([_bar(30)]), FakeNews([]))
    now = datetime(2026, 6, 29, 9, 40, tzinfo=UTC)
    asyncio.run(svc.on_scan_tick([_candidate()], now))
    asyncio.run(svc.on_scan_tick([_candidate()], now))

    assert store.read("opportunities").height == 1  # not reopened
    assert store.read("scanner_hits").height == 2  # both appearances logged


def test_hydration_prevents_reopen_after_restart(tmp_path: Path) -> None:
    store = Store(tmp_path)
    now = datetime(2026, 6, 29, 9, 40, tzinfo=UTC)
    asyncio.run(_svc(store, FakeBars([]), FakeNews([])).on_scan_tick([_candidate()], now))
    assert store.read("opportunities").height == 1

    # A fresh service (simulating a mid-day restart) must rehydrate its open-set from storage
    # and NOT re-open the already-known opportunity.
    fresh = _svc(store, FakeBars([]), FakeNews([]))
    asyncio.run(fresh.on_scan_tick([_candidate()], now))
    assert store.read("opportunities").height == 1  # still not reopened
    assert store.read("scanner_hits").height == 2


class BoomBars:
    """A bar source that raises for one symbol but works for the rest."""

    def __init__(self, bad_symbol: str, good: list[Bar]) -> None:
        self.bad_symbol = bad_symbol
        self.good = good

    async def fetch_day_bars(self, candidate: Candidate, *, trading_date: date) -> list[Bar]:
        if candidate.symbol == self.bad_symbol:
            raise RuntimeError("ib timeout")
        return list(self.good)


def test_one_symbol_failure_does_not_stall_the_batch(tmp_path: Path) -> None:
    store = Store(tmp_path)
    svc = _svc(store, BoomBars("BAD", [_bar(30)]), FakeNews([]))
    now = datetime(2026, 6, 29, 9, 40, tzinfo=UTC)
    asyncio.run(svc.on_scan_tick([_candidate("BAD"), _candidate("GOOD")], now))

    asyncio.run(svc.capture_day_bars(_TRADING_DATE))
    bars = store.read("bars")
    assert bars.height == 1  # only GOOD's bar persisted
    assert bars["symbol"].to_list() == ["GOOD"]
