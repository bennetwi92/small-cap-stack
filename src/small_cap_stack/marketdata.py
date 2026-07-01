"""ib_async market-data adapter: implements capture's BarSource + NewsSource.

Thin live glue (exercised against a real Gateway, not unit-tested). Phase-1 places no orders and
the account's feed is ~15 min delayed, so bars are **not streamed** — instead the day's 5-min
bars are pulled once per flagged symbol in an end-of-day batch (#62): a single
``reqHistoricalData`` returns the whole session (04:00 ET → close) in one request. This removes
the fragile keepUpToDate streaming (no restart gaps / duplicate bars) and tolerates delayed data.
News stays a one-shot request per opportunity.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from ib_async import IB, Stock

from .capture import Bar, NewsItem
from .clock import ET
from .config import Settings
from .logging import get_logger
from .scanner import Candidate

log = get_logger(__name__)

_NEWS_FMT = "%Y-%m-%d %H:%M:%S.0"


class IBKRMarketData:
    """Fetches a day's 5-min bars (one-shot historical) and per-symbol news via ib_async."""

    def __init__(self, ib: IB, settings: Settings) -> None:
        self.ib = ib
        self.settings = settings

    def _contract(self, c: Candidate) -> Stock:
        return Stock(c.symbol, "SMART", c.currency or "USD")

    async def fetch_day_bars(self, candidate: Candidate, *, trading_date: date) -> list[Bar]:
        """One historical request for the full day's 5-min bars, kept to ``trading_date`` (ET).

        ``useRTH=False`` includes the pre-market session; the request is not ``keepUpToDate`` so
        every returned bar is finalised. The duration may spill into the prior day's extended
        session, so bars are filtered to the requested trading day by their ET calendar date.
        """
        contract = self._contract(candidate)
        async with asyncio.timeout(self.settings.ibkr_request_timeout_sec):
            rows = await self.ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",  # up to now; run after close so the whole session is settled
                durationStr=self.settings.eod_bars_duration,
                barSizeSetting="5 mins",
                whatToShow="TRADES",
                useRTH=False,
                formatDate=2,  # UTC timestamps
                keepUpToDate=False,
            )
        out: list[Bar] = []
        for b in rows:
            start = cast(datetime, b.date)
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            if start.astimezone(ET).date() != trading_date:
                continue  # drop bars belonging to an adjacent day's extended session
            out.append(
                Bar(
                    start=start,
                    open=float(b.open),
                    high=float(b.high),
                    low=float(b.low),
                    close=float(b.close),
                    volume=float(b.volume),
                )
            )
        return out

    async def fetch_news(
        self, candidate: Candidate, *, lookback_days: int, limit: int
    ) -> list[NewsItem]:
        end = datetime.now(UTC)
        start = end - timedelta(days=lookback_days)
        async with asyncio.timeout(self.settings.ibkr_request_timeout_sec):
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
