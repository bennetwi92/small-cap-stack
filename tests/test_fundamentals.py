"""Tests for fundamentals parsing + capture wiring (#17)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from small_cap_stack.capture import CaptureService
from small_cap_stack.config import Settings
from small_cap_stack.fundamentals import Fundamentals, _to_float, _to_int, from_info
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


def test_from_info_nan_maps_to_none() -> None:
    # yfinance returns NaN/inf for unknown fields — must map to None, not crash (int(nan) raises).
    f = from_info(
        {
            "floatShares": float("nan"),
            "sharesOutstanding": float("inf"),
            "shortPercentOfFloat": float("nan"),
        },
        "NANC",
    )
    assert f.float_shares is None
    assert f.shares_outstanding is None
    assert f.short_percent is None


def test_numeric_coercion_edges() -> None:
    assert _to_int(float("nan")) is None and _to_int(float("inf")) is None
    assert _to_float(float("nan")) is None and _to_float(float("-inf")) is None
    assert _to_int(True) is None and _to_float(False) is None  # bool is not a numeric datum
    assert _to_int("12.9") == 12 and _to_float("0.21") == 0.21
    assert _to_int(8_000_000) == 8_000_000


class _FakeFundamentals:
    def __init__(self, f: Fundamentals | None) -> None:
        self._f = f

    async def fetch(self, candidate: Candidate) -> Fundamentals | None:
        return self._f


class _NoBars:
    async def fetch_day_bars(self, candidate: Candidate, *, trading_date: object) -> list:  # type: ignore[type-arg]
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
