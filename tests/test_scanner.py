"""Tests for scanner ingestion (#13): subscription building + result mapping."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from ib_async import ScannerSubscription, TagValue

from small_cap_stack.config import Settings
from small_cap_stack.scanner import Candidate, Scanner, build_subscription


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _row(rank: int, symbol: str, con_id: int, exchange: str = "NASDAQ") -> SimpleNamespace:
    contract = SimpleNamespace(
        symbol=symbol,
        conId=con_id,
        primaryExchange=exchange,
        exchange="SMART",
        currency="USD",
        secType="STK",
    )
    return SimpleNamespace(rank=rank, contractDetails=SimpleNamespace(contract=contract))


class FakeScannerClient:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows
        self.calls = 0
        self.last_filters: Sequence[TagValue] = []

    async def reqScannerDataAsync(
        self,
        subscription: ScannerSubscription,
        scannerSubscriptionOptions: Sequence[TagValue],
        scannerSubscriptionFilterOptions: Sequence[TagValue],
    ) -> list[Any]:
        self.calls += 1
        self.last_filters = scannerSubscriptionFilterOptions
        return list(self._rows)


def test_build_subscription_matches_strategy() -> None:
    sub, filters = build_subscription(_settings())
    assert sub.scanCode == "TOP_PERC_GAIN"
    assert sub.locationCode == "STK.US.MAJOR"
    assert sub.abovePrice == 1.0
    assert sub.belowPrice == 50.0
    tags = {f.tag: f.value for f in filters}
    assert tags["priceAbove"] == "1.0"
    assert tags["priceBelow"] == "50.0"
    assert tags["changePercAbove"] == "10.0"
    assert tags["stVolume5minAbove"] == "100000"  # 5-min window, not day volume
    assert tags["stkTypes"] == "exc:ETF,exc:ETN"  # drop float-less ETFs/ETNs server-side
    assert sub.numberOfRows == 50  # collect the full scanner breadth (API hard cap)


def test_stktypes_filter_omitted_when_no_exclusions() -> None:
    _, filters = build_subscription(_settings(scan_exclude_stock_types=()))
    assert not any(f.tag == "stkTypes" for f in filters)


def test_stktypes_filter_reflects_configured_exclusions() -> None:
    _, filters = build_subscription(_settings(scan_exclude_stock_types=("ETF", "ETN", "CEF")))
    tags = {f.tag: f.value for f in filters}
    assert tags["stkTypes"] == "exc:ETF,exc:ETN,exc:CEF"


def test_numberofrows_capped_at_50() -> None:
    sub, _ = build_subscription(_settings(scan_max_rows=500))
    assert sub.numberOfRows == 50


def test_scan_maps_and_truncates() -> None:
    rows = [_row(i, f"SYM{i}", 1000 + i) for i in range(8)]
    client = FakeScannerClient(rows)
    scanner = Scanner(_settings(scan_max_rows=3))

    result = asyncio.run(scanner.scan(client))

    assert client.calls == 1
    assert len(result) == 3  # truncated to scan_max_rows
    assert all(isinstance(c, Candidate) for c in result)
    assert result[0] == Candidate(
        rank=0, symbol="SYM0", con_id=1000, exchange="NASDAQ", currency="USD", sec_type="STK"
    )


def test_scan_times_out_on_a_hung_request() -> None:
    # A hung scanner request must not wedge the tick forever (with APScheduler max_instances=1 that
    # would silently skip every later tick) — it is bounded like every other IBKR call (#163-C2).
    class HungClient:
        async def reqScannerDataAsync(
            self,
            subscription: ScannerSubscription,
            scannerSubscriptionOptions: Sequence[TagValue] = (),
            scannerSubscriptionFilterOptions: Sequence[TagValue] = (),
        ) -> list[Any]:
            await asyncio.sleep(1)
            return []

    scanner = Scanner(_settings(ibkr_request_timeout_sec=0.05))
    with pytest.raises(TimeoutError):
        asyncio.run(scanner.scan(HungClient()))
