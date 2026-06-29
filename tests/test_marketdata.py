"""Tests for the IBKR market-data adapter mapping (#4) — mock IBKR, no live Gateway."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from small_cap_stack.config import Settings
from small_cap_stack.marketdata import IBKRMarketData
from small_cap_stack.scanner import Candidate


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _candidate(symbol: str = "AZI") -> Candidate:
    return Candidate(rank=0, symbol=symbol, con_id=42, exchange="NASDAQ")


def _bar(minute: int, o: float, h: float, low: float, c: float, vol: float) -> SimpleNamespace:
    return SimpleNamespace(
        date=datetime(2026, 6, 29, 15, minute, tzinfo=UTC),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=vol,
    )


class FakeIB:
    """Minimal stand-in for ib_async.IB covering the methods the adapter calls."""

    def __init__(self) -> None:
        self.bar_kwargs: dict[str, Any] = {}
        self.news_args: tuple[Any, ...] = ()
        self.hist_calls = 0
        self.cancelled: list[Any] = []
        # A live BarDataList: two settled bars + a trailing forming bar (kept up to date).
        self.bars = [
            _bar(50, 2.0, 2.5, 1.9, 2.4, 12345),
            _bar(55, 2.4, 2.6, 2.3, 2.55, 6789),
            _bar(0, 2.55, 2.7, 2.5, 2.6, 42),  # forming bar (16:00) — must be dropped
        ]

    async def reqHistoricalDataAsync(self, contract: Any, **kwargs: Any) -> list[Any]:
        self.bar_kwargs = kwargs
        self.hist_calls += 1
        return self.bars

    def cancelHistoricalData(self, bar_list: Any) -> None:
        self.cancelled.append(bar_list)

    async def reqHistoricalNewsAsync(self, *args: Any) -> list[Any]:
        self.news_args = args
        return [
            SimpleNamespace(
                time="2026-06-29 12:00:00.0",
                providerCode="DJ-N",
                headline="Big news",
                articleId="a1",
            )
        ]


def test_fetch_5m_bars_streams_and_drops_forming_bar() -> None:
    ib = FakeIB()
    md = IBKRMarketData(ib, _settings())
    bars = asyncio.run(md.fetch_5m_bars(_candidate(), lookback_sec=1800))

    # The trailing forming bar is excluded; only settled bars are returned.
    assert len(bars) == 2
    assert bars[0].start == datetime(2026, 6, 29, 15, 50, tzinfo=UTC)
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close) == (2.0, 2.5, 1.9, 2.4)
    assert bars[0].volume == 12345.0
    # opens a keepUpToDate 5-min TRADES stream including extended hours
    assert ib.bar_kwargs["barSizeSetting"] == "5 mins"
    assert ib.bar_kwargs["whatToShow"] == "TRADES"
    assert ib.bar_kwargs["useRTH"] is False
    assert ib.bar_kwargs["durationStr"] == "1800 S"
    assert ib.bar_kwargs["keepUpToDate"] is True
    assert ib.bar_kwargs["endDateTime"] == ""


def test_second_fetch_reads_cache_without_new_request() -> None:
    ib = FakeIB()
    md = IBKRMarketData(ib, _settings())
    asyncio.run(md.fetch_5m_bars(_candidate(), lookback_sec=1800))
    asyncio.run(md.fetch_5m_bars(_candidate(), lookback_sec=1800))
    assert ib.hist_calls == 1  # subscription opened once; later ticks read the live list


def test_resubscribe_reopens_all_streams() -> None:
    ib = FakeIB()
    md = IBKRMarketData(ib, _settings())
    asyncio.run(md.fetch_5m_bars(_candidate("AZI"), lookback_sec=1800))
    asyncio.run(md.fetch_5m_bars(_candidate("BZI"), lookback_sec=1800))
    assert ib.hist_calls == 2
    asyncio.run(md.resubscribe())
    assert ib.hist_calls == 4  # both streams re-requested after a (re)connect


def test_cancel_all_tears_down_streams() -> None:
    ib = FakeIB()
    md = IBKRMarketData(ib, _settings())
    asyncio.run(md.fetch_5m_bars(_candidate("AZI"), lookback_sec=1800))
    asyncio.run(md.fetch_5m_bars(_candidate("BZI"), lookback_sec=1800))
    md.cancel_all()
    assert len(ib.cancelled) == 2
    # after teardown a fetch opens a fresh stream
    asyncio.run(md.fetch_5m_bars(_candidate("AZI"), lookback_sec=1800))
    assert ib.hist_calls == 3


def test_fetch_news_maps() -> None:
    ib = FakeIB()
    md = IBKRMarketData(ib, _settings())
    items = asyncio.run(md.fetch_news(_candidate(), lookback_days=7, limit=10))

    assert len(items) == 1
    assert items[0].provider == "DJ-N"
    assert items[0].headline == "Big news"
    assert items[0].article_id == "a1"
    assert ib.news_args[0] == 42  # conId passed through
