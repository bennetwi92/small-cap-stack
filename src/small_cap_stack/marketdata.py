"""ib_async market-data adapter: implements capture's BarSource + NewsSource.

Thin live glue (exercised against a real Gateway, not unit-tested). 5-min bars use a
``keepUpToDate=True`` historical subscription: the initial request is made *once* per symbol and
IBKR then *pushes* updates into the returned ``BarDataList`` in place — so the capture loop reads
an in-memory snapshot each tick instead of re-requesting (no historical-pacing pressure). Streams
are stateful, so they are re-established on reconnect via :meth:`IBKRMarketData.resubscribe` and
torn down at end of day via :meth:`IBKRMarketData.cancel_all`. News stays a one-shot request.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from ib_async import IB, Stock

from .capture import Bar, NewsItem
from .config import Settings
from .logging import get_logger
from .scanner import Candidate

log = get_logger(__name__)

_NEWS_FMT = "%Y-%m-%d %H:%M:%S.0"
# Soft ceiling: IBKR allows ~50 simultaneous historical subscriptions; warn before we get close.
_MAX_STREAMS_WARN = 45


class IBKRMarketData:
    """Fetches 5-min bars (streaming) and per-symbol news (one-shot) via ib_async."""

    def __init__(self, ib: IB, settings: Settings) -> None:
        self.ib = ib
        self.settings = settings
        self._streams: dict[str, Any] = {}  # symbol -> live, self-updating BarDataList
        self._contracts: dict[str, Stock] = {}  # symbol -> contract, for replay after reconnect

    def _contract(self, c: Candidate) -> Stock:
        return Stock(c.symbol, "SMART", c.currency or "USD")

    async def _subscribe(self, symbol: str, contract: Stock, lookback_sec: int) -> Any:
        """Open a keepUpToDate 5-min bar stream; IBKR pushes updates into the returned list."""
        async with asyncio.timeout(self.settings.ibkr_request_timeout_sec):
            bar_list = await self.ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",  # required empty for keepUpToDate
                durationStr=f"{lookback_sec} S",
                barSizeSetting="5 mins",
                whatToShow="TRADES",
                useRTH=False,
                formatDate=2,
                keepUpToDate=True,
            )
        self._streams[symbol] = bar_list
        self._contracts[symbol] = contract
        if len(self._streams) >= _MAX_STREAMS_WARN:
            log.warning("marketdata.stream_count_high", streams=len(self._streams))
        return bar_list

    @staticmethod
    def _settled(bar_list: Any) -> list[Bar]:
        """Map a BarDataList to completed bars, dropping the last (still-forming) bar.

        With keepUpToDate the final element is the live bar being updated in place, so excluding
        it means only finalised 5-min bars are ever persisted (append-only). The capture loop's
        own dedup still guards against any duplicate/late bars IBKR may re-emit.
        """
        settled = list(bar_list)[:-1]
        out: list[Bar] = []
        for b in settled:
            start = cast(datetime, b.date)
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
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

    async def fetch_5m_bars(self, candidate: Candidate, *, lookback_sec: int) -> list[Bar]:
        """Ensure a stream exists for the symbol, then return its settled-bar snapshot."""
        bar_list = self._streams.get(candidate.symbol)
        if bar_list is None:
            bar_list = await self._subscribe(
                candidate.symbol, self._contract(candidate), lookback_sec
            )
        return self._settled(bar_list)

    async def resubscribe(self) -> None:
        """Re-open every bar stream after a (re)connect (the old BarDataLists are dead)."""
        contracts = dict(self._contracts)
        self._streams.clear()
        for symbol, contract in contracts.items():
            await self._subscribe(symbol, contract, self.settings.capture_bars_lookback_sec)
        if contracts:
            log.info("marketdata.streams_replayed", count=len(contracts))

    def cancel_all(self) -> None:
        """Cancel all live bar streams (call at end of the capture window / new session)."""
        for bar_list in self._streams.values():
            try:
                self.ib.cancelHistoricalData(bar_list)
            except Exception:  # noqa: BLE001 — best-effort teardown; never break shutdown
                log.warning("marketdata.cancel_failed")
        self._streams.clear()
        self._contracts.clear()

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
