"""Scanner ingestion (issue #13): discover candidate tickers via the IBKR API scanner.

Uses the definition validated in spike #8 — rank by % gain, filter to the strategy universe
(price $1–50, today's change > 10%, trailing 5-min volume > 100k via the native
``stVolume5minAbove`` filter). The float / short-interest / news / bull-flag checks are
post-filters applied downstream (the gate engine, #15), not scanner parameters.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from ib_async import ScannerSubscription, TagValue
from pydantic import BaseModel

from .config import Settings
from .logging import get_logger

log = get_logger(__name__)


class Candidate(BaseModel):
    """A ticker surfaced by the scanner (before gate checks)."""

    rank: int
    symbol: str
    con_id: int
    exchange: str
    currency: str = "USD"
    sec_type: str = "STK"


class ScannerClient(Protocol):
    """The slice of ``ib_async.IB`` the scanner needs (kept narrow for testing)."""

    async def reqScannerDataAsync(
        self,
        subscription: ScannerSubscription,
        scannerSubscriptionOptions: list[TagValue] = ...,
        scannerSubscriptionFilterOptions: list[TagValue] = ...,
    ) -> list[Any]: ...


def build_subscription(settings: Settings) -> tuple[ScannerSubscription, list[TagValue]]:
    """Build the ScannerSubscription + filter TagValues for the strategy universe."""
    sub = ScannerSubscription(
        instrument="STK",
        locationCode=settings.scan_location,
        scanCode=settings.scan_code,
        abovePrice=settings.scan_min_price,
        belowPrice=settings.scan_max_price,
        numberOfRows=min(settings.scan_max_rows, 50),
    )
    filters = [
        TagValue("priceAbove", str(settings.scan_min_price)),
        TagValue("priceBelow", str(settings.scan_max_price)),
        TagValue("changePercAbove", str(settings.scan_change_pct)),
        TagValue("stVolume5minAbove", str(settings.scan_min_5m_volume)),
    ]
    return sub, filters


def _to_candidate(row: Any) -> Candidate:
    c = row.contractDetails.contract
    return Candidate(
        rank=row.rank,
        symbol=c.symbol,
        con_id=c.conId,
        exchange=(c.primaryExchange or c.exchange),
        currency=c.currency or "USD",
        sec_type=c.secType or "STK",
    )


class Scanner:
    """Runs the API scanner and maps results to Candidates."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def scan(self, client: ScannerClient) -> list[Candidate]:
        sub, filters = build_subscription(self.settings)
        # Bound the request like every other IBKR call: a hung scanner would otherwise wedge the
        # tick forever and (max_instances=1) silently skip all later ticks (#163-C2).
        async with asyncio.timeout(self.settings.ibkr_request_timeout_sec):
            rows = await client.reqScannerDataAsync(sub, [], filters)
        candidates = [_to_candidate(r) for r in rows[: self.settings.scan_max_rows]]
        log.info("scan.results", count=len(candidates))
        return candidates
