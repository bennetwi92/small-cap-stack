"""Tests for fundamentals parsing + capture wiring (#17)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from small_cap_stack.capture import CaptureService
from small_cap_stack.config import Settings
from small_cap_stack.fundamentals import (
    ChainedFundamentals,
    Fundamentals,
    FundamentalsSource,
    YFinanceFundamentals,
    build_fundamentals,
    from_finra,
    from_fmp,
    from_info,
    merge_fundamentals,
)
from small_cap_stack.scanner import Candidate
from small_cap_stack.storage import Store


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def _cand(symbol: str = "AZI") -> Candidate:
    return Candidate(rank=0, symbol=symbol, con_id=1, exchange="NASDAQ")


def test_from_info_full() -> None:
    info = {
        "floatShares": 8_000_000,
        "sharesOutstanding": 12_000_000,
        "shortPercentOfFloat": 0.21,
        "sharesShort": 1_680_000,
    }
    f = from_info(info, "AZI")
    assert f == Fundamentals("AZI", 8_000_000, 12_000_000, 0.21, "yfinance", 1_680_000)


def test_from_info_missing_and_bad_values() -> None:
    f = from_info({"floatShares": None, "sharesOutstanding": "n/a"}, "NNBR")
    assert f.float_shares is None
    assert f.shares_outstanding is None
    assert f.short_percent is None
    assert f.short_interest_shares is None
    assert f.source == "yfinance"


def test_from_fmp_full() -> None:
    rows = [{"symbol": "AZI", "floatShares": 8_000_000, "outstandingShares": 12_000_000}]
    f = from_fmp(rows, "AZI")
    assert f == Fundamentals("AZI", 8_000_000, 12_000_000, None, "fmp")


def test_from_fmp_empty_or_no_share_data() -> None:
    assert from_fmp([], "AZI") is None
    assert from_fmp([{"symbol": "AZI", "freeFloat": 41.2}], "AZI") is None


def test_from_finra_record() -> None:
    f = from_finra({"currentShortPositionQuantity": 2_000_000}, "AZI")
    assert f == Fundamentals("AZI", None, None, None, "finra", 2_000_000)


def test_from_finra_missing_quantity() -> None:
    assert from_finra({"settlementDate": "2026-06-15"}, "AZI") is None


def test_merge_empty_and_all_none() -> None:
    assert merge_fundamentals([]) is None
    assert merge_fundamentals([None, None]) is None


def test_merge_priority_per_field_and_provenance() -> None:
    fmp = Fundamentals("AZI", 8_000_000, 12_000_000, None, "fmp")
    finra = Fundamentals("AZI", None, None, None, "finra", short_interest_shares=2_000_000)
    yf = Fundamentals("AZI", 7_000_000, 11_000_000, 0.30, "yfinance", 1_500_000)
    merged = merge_fundamentals([fmp, finra, yf])
    assert merged is not None
    # FMP wins float/shares; FINRA's raw short interest drives the derived short_percent.
    assert merged.float_shares == 8_000_000
    assert merged.shares_outstanding == 12_000_000
    assert merged.short_interest_shares == 2_000_000
    assert merged.short_percent == 2_000_000 / 8_000_000
    # yfinance contributed nothing used -> excluded from provenance.
    assert merged.source == "fmp+finra"


def test_merge_backfills_from_lower_priority() -> None:
    fmp = Fundamentals("AZI", 8_000_000, None, None, "fmp")
    yf = Fundamentals("AZI", 7_000_000, 11_000_000, 0.30, "yfinance")
    merged = merge_fundamentals([fmp, yf])
    assert merged is not None
    assert merged.float_shares == 8_000_000  # FMP
    assert merged.shares_outstanding == 11_000_000  # yfinance backfill
    assert merged.short_percent == 0.30  # yfinance directly
    assert merged.source == "fmp+yfinance"


def test_merge_prefers_explicit_percent_over_derivable() -> None:
    # A higher-priority source giving short_percent directly wins over derivation.
    finra = Fundamentals("AZI", 8_000_000, None, 0.25, "finra", short_interest_shares=2_000_000)
    merged = merge_fundamentals([finra])
    assert merged is not None
    assert merged.short_percent == 0.25


class _FakeSource:
    def __init__(self, f: Fundamentals | None | Exception) -> None:
        self._f = f

    async def fetch(self, candidate: Candidate) -> Fundamentals | None:
        if isinstance(self._f, Exception):
            raise self._f
        return self._f


def test_chained_merges_and_survives_errors() -> None:
    fmp = Fundamentals("AZI", 8_000_000, 12_000_000, None, "fmp")
    finra = Fundamentals("AZI", None, None, None, "finra", short_interest_shares=2_000_000)
    chain = ChainedFundamentals(
        [_FakeSource(fmp), _FakeSource(RuntimeError("boom")), _FakeSource(finra)]
    )
    merged = asyncio.run(chain.fetch(_cand()))
    assert merged is not None
    assert merged.float_shares == 8_000_000
    assert merged.short_interest_shares == 2_000_000
    assert merged.source == "fmp+finra"


def test_chained_all_none_returns_none() -> None:
    chain = ChainedFundamentals([_FakeSource(None), _FakeSource(None)])
    assert asyncio.run(chain.fetch(_cand())) is None


def test_build_fundamentals_yfinance_only_without_keys() -> None:
    src = build_fundamentals(_settings())
    assert isinstance(src, YFinanceFundamentals)


def test_build_fundamentals_chains_when_keyed() -> None:
    src = build_fundamentals(
        _settings(fmp_api_key="k", finra_client_id="id", finra_client_secret="sec")
    )
    assert isinstance(src, ChainedFundamentals)
    sources: list[FundamentalsSource] = list(src.sources)
    assert len(sources) == 3  # FMP, FINRA, yfinance
    assert isinstance(sources[-1], YFinanceFundamentals)


def test_build_fundamentals_finra_needs_both_credentials() -> None:
    # Only one half of the FINRA credential pair -> FINRA not added (yfinance fallback only).
    src = build_fundamentals(_settings(finra_client_id="id"))
    assert isinstance(src, YFinanceFundamentals)


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
