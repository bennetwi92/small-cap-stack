"""Fundamentals: float / shares outstanding / short interest (issue #17).

IBKR (Reuters) fundamentals require a paid entitlement (paper returns error 10358), so the
free Phase-1 source is **yfinance** (no API key; what tradepilot used). The float gate (#15)
consumes ``float_shares``. Values are captured raw at flag time and recomputed on read, so we
can swap in a more reliable source (FMP float / FINRA short interest) later without re-collecting.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .scanner import Candidate


@dataclass(frozen=True)
class Fundamentals:
    symbol: str
    float_shares: int | None
    shares_outstanding: int | None
    short_percent: float | None
    source: str


class FundamentalsSource(Protocol):
    async def fetch(self, candidate: Candidate) -> Fundamentals | None: ...


class NullFundamentals:
    """Default no-op source (used in tests / when fundamentals are disabled)."""

    async def fetch(self, candidate: Candidate) -> Fundamentals | None:
        return None


def _to_int(v: object) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return int(v)
    if isinstance(v, str):
        try:
            return int(float(v))
        except ValueError:
            return None
    return None


def _to_float(v: object) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def from_info(info: dict[str, Any], symbol: str) -> Fundamentals:
    """Map a yfinance ``Ticker.info`` dict to Fundamentals (tolerant of missing keys)."""
    return Fundamentals(
        symbol=symbol,
        float_shares=_to_int(info.get("floatShares")),
        shares_outstanding=_to_int(info.get("sharesOutstanding")),
        short_percent=_to_float(info.get("shortPercentOfFloat")),
        source="yfinance",
    )


def fundamentals_record(oid: str, f: Fundamentals, ts: datetime) -> dict[str, Any]:
    return {
        "opportunity_id": oid,
        "symbol": f.symbol,
        "ts_utc": ts.astimezone(UTC),
        "float_shares": f.float_shares,
        "shares_outstanding": f.shares_outstanding,
        "short_percent": f.short_percent,
        "source": f.source,
    }


class YFinanceFundamentals:
    """Free float/short source via yfinance (blocking lib run off-thread)."""

    async def fetch(self, candidate: Candidate) -> Fundamentals | None:
        try:
            info = await asyncio.to_thread(self._info, candidate.symbol)
        except Exception:  # noqa: BLE001 — best-effort; never break capture on a data hiccup
            return None
        if not info:
            return None
        return from_info(info, candidate.symbol)

    @staticmethod
    def _info(symbol: str) -> dict[str, Any]:
        import yfinance

        return dict(yfinance.Ticker(symbol).info)
