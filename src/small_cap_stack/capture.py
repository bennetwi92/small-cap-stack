"""Raw capture per opportunity (issue #14): the evolving longitudinal record.

When a candidate is flagged it becomes an *opportunity* (`<trading_date>:<symbol>`). We write
its static-at-flag facts once (opportunities + news), log every scanner appearance
(scanner_hits), and keep appending its 5-min bars until the capture window closes. Everything
is append-only via the Store; nothing is mutated. Float/short-interest land with #17.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from .config import Settings
from .fundamentals import FundamentalsSource, NullFundamentals, fundamentals_record
from .logging import get_logger
from .monitoring import BARS_APPENDED, OPPORTUNITIES
from .scanner import Candidate
from .storage import Store

log = get_logger(__name__)


def opportunity_id(trading_date: date, symbol: str) -> str:
    """Stable id: one opportunity per symbol per trading day."""
    return f"{trading_date.isoformat()}:{symbol.upper()}"


@dataclass(frozen=True)
class Bar:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class NewsItem:
    time: str
    provider: str
    headline: str
    article_id: str


class BarSource(Protocol):
    async def fetch_5m_bars(self, candidate: Candidate, *, lookback_sec: int) -> list[Bar]: ...


class NewsSource(Protocol):
    async def fetch_news(
        self, candidate: Candidate, *, lookback_days: int, limit: int
    ) -> list[NewsItem]: ...


def opportunity_record(
    c: Candidate, oid: str, first_seen: datetime, trading_date: date
) -> dict[str, Any]:
    return {
        "opportunity_id": oid,
        "symbol": c.symbol,
        "con_id": c.con_id,
        "trading_date": trading_date,
        "first_seen_utc": first_seen.astimezone(UTC),
        "first_rank": c.rank,
    }


def scanner_hit_record(oid: str, c: Candidate, ts: datetime) -> dict[str, Any]:
    return {"opportunity_id": oid, "symbol": c.symbol, "ts_utc": ts.astimezone(UTC), "rank": c.rank}


def news_record(oid: str, symbol: str, n: NewsItem) -> dict[str, Any]:
    return {
        "opportunity_id": oid,
        "symbol": symbol,
        "time": n.time,
        "provider": n.provider,
        "headline": n.headline,
        "article_id": n.article_id,
    }


def bar_record(oid: str, symbol: str, b: Bar) -> dict[str, Any]:
    return {
        "opportunity_id": oid,
        "symbol": symbol,
        "bar_start_utc": b.start.astimezone(UTC),
        "open": b.open,
        "high": b.high,
        "low": b.low,
        "close": b.close,
        "volume": b.volume,
    }


@dataclass
class _Active:
    candidate: Candidate
    last_bar_start: datetime | None = None


@dataclass
class CaptureService:
    """Persists the evolving record for each flagged opportunity into the Store."""

    store: Store
    bars: BarSource
    news: NewsSource
    settings: Settings
    fundamentals: FundamentalsSource = field(default_factory=NullFundamentals)
    _active: dict[str, _Active] = field(default_factory=dict)

    async def on_scan_tick(self, candidates: Sequence[Candidate], now: datetime) -> None:
        """Record this scan tick: new opportunities, scanner hits, then bars for all active."""
        trading_date = now.date()
        for c in candidates:
            oid = opportunity_id(trading_date, c.symbol)
            if oid not in self._active:
                await self._open_opportunity(oid, c, now, trading_date)
            self.store.append(
                "scanner_hits", [scanner_hit_record(oid, c, now)], partition_date=trading_date
            )
        await self.capture_bars(now)

    async def _open_opportunity(
        self, oid: str, c: Candidate, now: datetime, trading_date: date
    ) -> None:
        self.store.append(
            "opportunities",
            [opportunity_record(c, oid, now, trading_date)],
            partition_date=trading_date,
        )
        try:
            items = await self.news.fetch_news(
                c, lookback_days=self.settings.news_lookback_days, limit=self.settings.news_max
            )
        except Exception:  # noqa: BLE001 — news is best-effort; never block opening the record
            log.warning("capture.news_fetch_failed", opportunity_id=oid)
            items = []
        if items:
            self.store.append(
                "news", [news_record(oid, c.symbol, n) for n in items], partition_date=trading_date
            )
        fund = await self.fundamentals.fetch(c)
        if fund is not None:
            self.store.append(
                "fundamentals", [fundamentals_record(oid, fund, now)], partition_date=trading_date
            )
        self._active[oid] = _Active(candidate=c)
        OPPORTUNITIES.inc()
        log.info(
            "capture.opportunity_opened",
            opportunity_id=oid,
            news=len(items),
            float_shares=(fund.float_shares if fund else None),
        )

    async def capture_bars(self, now: datetime) -> None:
        """Append any new 5-min bars for every active opportunity."""
        trading_date = now.date()
        for oid, active in self._active.items():
            try:
                bars = await self.bars.fetch_5m_bars(
                    active.candidate, lookback_sec=self.settings.capture_bars_lookback_sec
                )
            except Exception:  # noqa: BLE001 — one symbol's data hiccup must not stall the tick
                log.warning("capture.bars_fetch_failed", opportunity_id=oid)
                continue
            new = [
                b for b in bars if active.last_bar_start is None or b.start > active.last_bar_start
            ]
            if not new:
                continue
            self.store.append(
                "bars",
                [bar_record(oid, active.candidate.symbol, b) for b in new],
                partition_date=trading_date,
            )
            active.last_bar_start = max(b.start for b in new)
            BARS_APPENDED.inc(len(new))
            log.info("capture.bars_appended", opportunity_id=oid, count=len(new))

    def reset(self) -> None:
        """Clear active opportunities (call at end of the capture window / new session)."""
        self._active.clear()
