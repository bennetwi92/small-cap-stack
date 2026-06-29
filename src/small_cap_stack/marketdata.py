"""ib_async market-data adapter: implements capture's BarSource + NewsSource.

Thin live glue (exercised against a real Gateway, not unit-tested). Polls historical 5-min
bars (so there's no persistent subscription state to rebuild after a reconnect) and per-symbol
historical news.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from ib_async import IB, Stock

from .capture import Bar, NewsItem
from .config import Settings
from .scanner import Candidate

_NEWS_FMT = "%Y-%m-%d %H:%M:%S.0"


class IBKRMarketData:
    """Fetches 5-min bars and per-symbol news via ib_async."""

    def __init__(self, ib: IB, settings: Settings) -> None:
        self.ib = ib
        self.settings = settings

    def _contract(self, c: Candidate) -> Stock:
        return Stock(c.symbol, "SMART", c.currency or "USD")

    async def fetch_5m_bars(self, candidate: Candidate, *, lookback_sec: int) -> list[Bar]:
        rows = await self.ib.reqHistoricalDataAsync(
            self._contract(candidate),
            endDateTime="",
            durationStr=f"{lookback_sec} S",
            barSizeSetting="5 mins",
            whatToShow="TRADES",
            useRTH=False,
            formatDate=2,
        )
        return [
            Bar(
                start=cast(datetime, b.date),
                open=float(b.open),
                high=float(b.high),
                low=float(b.low),
                close=float(b.close),
                volume=float(b.volume),
            )
            for b in rows
        ]

    async def fetch_news(
        self, candidate: Candidate, *, lookback_days: int, limit: int
    ) -> list[NewsItem]:
        end = datetime.now(UTC)
        start = end - timedelta(days=lookback_days)
        rows = cast(
            "list[Any]",
            await self.ib.reqHistoricalNewsAsync(
                candidate.con_id,
                self.settings.news_providers,
                start.strftime(_NEWS_FMT),
                end.strftime(_NEWS_FMT),
                limit,
            ),
        )
        return [
            NewsItem(
                time=str(n.time),
                provider=n.providerCode,
                headline=n.headline,
                article_id=n.articleId,
            )
            for n in rows
        ]
