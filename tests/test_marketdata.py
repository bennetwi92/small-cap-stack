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


def _candidate() -> Candidate:
    return Candidate(rank=0, symbol="AZI", con_id=42, exchange="NASDAQ")


class FakeIB:
    """Minimal stand-in for ib_async.IB covering the two methods the adapter calls."""

    def __init__(self) -> None:
        self.bar_kwargs: dict[str, Any] = {}
        self.news_args: tuple[Any, ...] = ()

    async def reqHistoricalDataAsync(self, contract: Any, **kwargs: Any) -> list[Any]:
        self.bar_kwargs = kwargs
        return [
            SimpleNamespace(
                date=datetime(2026, 6, 29, 15, 50, tzinfo=UTC),
                open=2.0,
                high=2.5,
                low=1.9,
                close=2.4,
                volume=12345,
            ),
            SimpleNamespace(
                date=datetime(2026, 6, 29, 15, 55, tzinfo=UTC),
                open=2.4,
                high=2.6,
                low=2.3,
                close=2.55,
                volume=6789,
            ),
        ]

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


def test_fetch_5m_bars_maps_and_passes_params() -> None:
    ib = FakeIB()
    md = IBKRMarketData(ib, _settings())
    bars = asyncio.run(md.fetch_5m_bars(_candidate(), lookback_sec=1800))

    assert len(bars) == 2
    assert bars[0].start == datetime(2026, 6, 29, 15, 50, tzinfo=UTC)
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close) == (2.0, 2.5, 1.9, 2.4)
    assert bars[0].volume == 12345.0
    # uses 5-min TRADES bars including extended hours
    assert ib.bar_kwargs["barSizeSetting"] == "5 mins"
    assert ib.bar_kwargs["whatToShow"] == "TRADES"
    assert ib.bar_kwargs["useRTH"] is False
    assert ib.bar_kwargs["durationStr"] == "1800 S"


def test_fetch_news_maps() -> None:
    ib = FakeIB()
    md = IBKRMarketData(ib, _settings())
    items = asyncio.run(md.fetch_news(_candidate(), lookback_days=7, limit=10))

    assert len(items) == 1
    assert items[0].provider == "DJ-N"
    assert items[0].headline == "Big news"
    assert items[0].article_id == "a1"
    assert ib.news_args[0] == 42  # conId passed through
