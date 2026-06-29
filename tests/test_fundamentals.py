"""Tests for fundamentals parsing + capture wiring (#17)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from small_cap_stack.capture import CaptureService
from small_cap_stack.config import Settings
from small_cap_stack.fundamentals import Fundamentals, from_info
from small_cap_stack.scanner import Candidate
from small_cap_stack.storage import Store


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_from_info_full() -> None:
    info = {"floatShares": 8_000_000, "sharesOutstanding": 12_000_000, "shortPercentOfFloat": 0.21}
    f = from_info(info, "AZI")
    assert f == Fundamentals("AZI", 8_000_000, 12_000_000, 0.21, "yfinance")


def test_from_info_missing_and_bad_values() -> None:
    f = from_info({"floatShares": None, "sharesOutstanding": "n/a"}, "NNBR")
    assert f.float_shares is None
    assert f.shares_outstanding is None
    assert f.short_percent is None
    assert f.source == "yfinance"


class _FakeFundamentals:
    def __init__(self, f: Fundamentals | None) -> None:
        self._f = f

    async def fetch(self, candidate: Candidate) -> Fundamentals | None:
        return self._f


class _NoBars:
    async def fetch_5m_bars(self, candidate: Candidate, *, lookback_sec: int) -> list:  # type: ignore[type-arg]
        return []


class _NoNews:
    async def fetch_news(self, candidate: Candidate, *, lookback_days: int, limit: int) -> list:  # type: ignore[type-arg]
        return []


def _svc(tmp: Path, fund: Fundamentals | None) -> CaptureService:
    return CaptureService(
        store=Store(tmp),
        bars=_NoBars(),
        news=_NoNews(),
        settings=_settings(),
        fundamentals=_FakeFundamentals(fund),
    )


def test_capture_writes_fundamentals(tmp_path: Path) -> None:
    fund = Fundamentals("AZI", 8_000_000, 12_000_000, 0.21, "yfinance")
    svc = _svc(tmp_path, fund)
    now = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)
    asyncio.run(
        svc.on_scan_tick([Candidate(rank=0, symbol="AZI", con_id=1, exchange="NASDAQ")], now)
    )

    df = svc.store.read("fundamentals")
    assert df.height == 1
    assert df["float_shares"].to_list() == [8_000_000]
    assert df["source"].to_list() == ["yfinance"]


def test_capture_skips_fundamentals_when_none(tmp_path: Path) -> None:
    svc = _svc(tmp_path, None)
    now = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)
    asyncio.run(
        svc.on_scan_tick([Candidate(rank=0, symbol="AZI", con_id=1, exchange="NASDAQ")], now)
    )
    assert svc.store.read("fundamentals").is_empty()
