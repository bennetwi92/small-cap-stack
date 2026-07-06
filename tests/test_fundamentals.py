"""Tests for fundamentals parsing + capture wiring (#17)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from small_cap_stack.capture import CaptureService
from small_cap_stack.config import Settings
from small_cap_stack.fundamentals import (
    FMPFundamentals,
    Fundamentals,
    MultiFundamentals,
    _first_row,
    _to_float,
    _to_int,
    from_fmp,
    from_info,
)
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


def _svc(tmp: Path, *funds: Fundamentals | None) -> CaptureService:
    sources = [_FakeFundamentals(f) for f in funds]
    return CaptureService(
        store=Store(tmp),
        bars=_NoBars(),
        news=_NoNews(),
        settings=_settings(),
        fundamentals=MultiFundamentals(sources),
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


def test_capture_writes_one_row_per_source(tmp_path: Path) -> None:
    # "Store raw" across sources: FMP + yfinance both answer -> two rows, one None dropped.
    fmp = Fundamentals("AZI", 7_900_000, 12_000_000, None, "fmp")
    yf = Fundamentals("AZI", 8_100_000, 12_000_000, 0.21, "yfinance")
    svc = _svc(tmp_path, fmp, None, yf)
    now = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)
    asyncio.run(
        svc.on_scan_tick([Candidate(rank=0, symbol="AZI", con_id=1, exchange="NASDAQ")], now)
    )

    df = svc.store.read("fundamentals")
    assert df.height == 2
    assert set(df["source"].to_list()) == {"fmp", "yfinance"}


# --- FMP mapping ---------------------------------------------------------------------------


def test_from_fmp_full() -> None:
    # short_percent stays None: FMP's freeFloat is % of outstanding, not short interest (#110).
    row = {
        "symbol": "AZI",
        "floatShares": 7_900_000,
        "outstandingShares": 12_000_000,
        "freeFloat": 65.8,
    }
    assert from_fmp(row, "AZI") == Fundamentals("AZI", 7_900_000, 12_000_000, None, "fmp")


def test_from_fmp_missing_and_bad_values() -> None:
    f = from_fmp({"floatShares": None, "outstandingShares": "n/a"}, "NNBR")
    assert f.float_shares is None
    assert f.shares_outstanding is None
    assert f.short_percent is None
    assert f.source == "fmp"


def test_first_row_variants() -> None:
    assert _first_row([{"floatShares": 1}]) == {"floatShares": 1}  # list -> first
    assert _first_row({"floatShares": 1}) == {"floatShares": 1}  # bare dict
    assert _first_row([]) is None  # empty list
    assert _first_row({"Error Message": "Invalid API KEY."}) is None  # error payload
    assert _first_row("nope") is None  # unexpected type


class _FMPStub(FMPFundamentals):
    """FMPFundamentals with the network call replaced by a canned payload (or a raise)."""

    def __init__(self, payload: object) -> None:
        super().__init__(api_key="k")
        self._payload = payload

    def _get(self, symbol: str) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _cand() -> Candidate:
    return Candidate(rank=0, symbol="AZI", con_id=1, exchange="NASDAQ")


def test_fmp_fetch_maps_list_payload() -> None:
    src = _FMPStub([{"symbol": "AZI", "floatShares": 7_900_000, "outstandingShares": 12_000_000}])
    f = asyncio.run(src.fetch(_cand()))
    assert f == Fundamentals("AZI", 7_900_000, 12_000_000, None, "fmp")


def test_fmp_fetch_error_payload_returns_none() -> None:
    src = _FMPStub({"Error Message": "Invalid API KEY."})
    assert asyncio.run(src.fetch(_cand())) is None


def test_fmp_fetch_http_error_returns_none() -> None:
    src = _FMPStub(RuntimeError("boom"))  # network hiccup / quota -> row simply absent
    assert asyncio.run(src.fetch(_cand())) is None


def test_fmp_fetch_no_key_returns_none() -> None:
    assert asyncio.run(FMPFundamentals(api_key="").fetch(_cand())) is None


def test_multi_fundamentals_collects_answering_sources() -> None:
    fmp = Fundamentals("AZI", 7_900_000, 12_000_000, None, "fmp")
    yf = Fundamentals("AZI", 8_100_000, 12_000_000, 0.21, "yfinance")
    multi = MultiFundamentals(
        [_FakeFundamentals(fmp), _FakeFundamentals(None), _FakeFundamentals(yf)]
    )
    out = asyncio.run(multi.fetch_all(_cand()))
    assert {f.source for f in out} == {"fmp", "yfinance"}
