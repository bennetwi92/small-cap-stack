"""Tests for the IBKR market-data adapter mapping (#4, #62) — mock IBKR, no live Gateway."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

from small_cap_stack.config import Settings
from small_cap_stack.marketdata import IBKRMarketData
from small_cap_stack.scanner import Candidate


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _candidate(symbol: str = "AZI") -> Candidate:
    return Candidate(rank=0, symbol=symbol, con_id=42, exchange="NASDAQ")


def _bar(dt: datetime, o: float, h: float, low: float, c: float, vol: float) -> SimpleNamespace:
    return SimpleNamespace(date=dt, open=o, high=h, low=low, close=c, volume=vol)


class FakeIB:
    """Minimal stand-in for ib_async.IB covering the methods the adapter calls."""

    def __init__(self) -> None:
        self.bar_kwargs: dict[str, Any] = {}
        self.news_args: tuple[Any, ...] = ()
        self.hist_calls = 0
        # Two bars on 2026-06-29 ET plus one that belongs to the prior day's extended session.
        self.bars = [
            _bar(datetime(2026, 6, 29, 3, 0, tzinfo=UTC), 9, 9, 9, 9, 1),  # 2026-06-28 23:00 ET
            _bar(datetime(2026, 6, 29, 8, 0, tzinfo=UTC), 2.0, 2.5, 1.9, 2.4, 12345),  # 04:00 ET
            _bar(datetime(2026, 6, 29, 15, 0, tzinfo=UTC), 2.4, 2.6, 2.3, 2.55, 6789),  # 11:00 ET
        ]

    async def reqHistoricalDataAsync(self, contract: Any, **kwargs: Any) -> list[Any]:
        self.bar_kwargs = kwargs
        self.hist_calls += 1
        return self.bars

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


def test_fetch_day_bars_one_shot_filtered_to_trading_day() -> None:
    ib = FakeIB()
    md = IBKRMarketData(ib, _settings())
    bars = asyncio.run(md.fetch_day_bars(_candidate(), trading_date=date(2026, 6, 29)))

    # The prior-day extended-session bar is dropped; only the requested day's bars remain.
    assert len(bars) == 2
    assert bars[0].start == datetime(2026, 6, 29, 8, 0, tzinfo=UTC)
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close) == (2.0, 2.5, 1.9, 2.4)
    assert bars[0].volume == 12345.0
    # a single, finalised (not keepUpToDate) 5-min TRADES request covering the whole session
    assert ib.hist_calls == 1
    assert ib.bar_kwargs["barSizeSetting"] == "5 mins"
    assert ib.bar_kwargs["whatToShow"] == "TRADES"
    assert ib.bar_kwargs["useRTH"] is False
    assert ib.bar_kwargs["durationStr"] == "1 D"
    assert ib.bar_kwargs["keepUpToDate"] is False
    assert ib.bar_kwargs["endDateTime"] == ""


def test_fetch_news_maps() -> None:
    ib = FakeIB()
    md = IBKRMarketData(ib, _settings())
    items = asyncio.run(md.fetch_news(_candidate(), lookback_days=7, limit=10))

    assert len(items) == 1
    assert items[0].provider == "DJ-N"
    assert items[0].headline == "Big news"
    assert items[0].article_id == "a1"
    assert ib.news_args[0] == 42  # conId passed through
