"""Fundamentals: float / shares outstanding / short interest (issues #17, #41).

IBKR (Reuters) fundamentals require a paid entitlement (paper returns error 10358), so the
free baseline source is **yfinance** (no API key; what tradepilot used). Issue #41 hardens this
by adding authoritative sources and merging them by priority with provenance:

- **FMP** (``/shares_float``) for shares outstanding / float.
- **FINRA** consolidated (bi-monthly) short interest — the authoritative short-interest source.
- ``merge_fundamentals`` combines a priority-ordered list per field; the ``source`` column
  records which sources actually contributed (e.g. ``"fmp+finra"``).

Following the project principle (*store raw, compute derived on read*) we persist the raw
short-interest **share count** from FINRA; ``short_percent`` is derived from it and the best
available float, so methodology can change retroactively without re-collecting. Values are
captured raw at flag time, so adding/removing a source never loses data.
"""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .config import Settings
from .scanner import Candidate

_HTTP_TIMEOUT_SEC = 10.0
_FMP_SHARES_FLOAT_URL = "https://financialmodelingprep.com/api/v4/shares_float"
_FINRA_TOKEN_URL = (
    "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token?grant_type=client_credentials"
)
_FINRA_SHORT_INTEREST_URL = (
    "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
)


@dataclass(frozen=True)
class Fundamentals:
    symbol: str
    float_shares: int | None
    shares_outstanding: int | None
    short_percent: float | None
    source: str
    # Raw short-interest share count (FINRA's authoritative datum); short_percent is derived.
    short_interest_shares: int | None = None


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
        short_interest_shares=_to_int(info.get("sharesShort")),
    )


def from_fmp(rows: Sequence[dict[str, Any]], symbol: str) -> Fundamentals | None:
    """Map an FMP ``/shares_float`` response (list of records) to Fundamentals."""
    if not rows:
        return None
    row = rows[0]
    float_shares = _to_int(row.get("floatShares"))
    shares_outstanding = _to_int(row.get("outstandingShares"))
    if float_shares is None and shares_outstanding is None:
        return None
    return Fundamentals(
        symbol=symbol,
        float_shares=float_shares,
        shares_outstanding=shares_outstanding,
        short_percent=None,
        source="fmp",
    )


def from_finra(record: dict[str, Any], symbol: str) -> Fundamentals | None:
    """Map a FINRA consolidated short-interest record to Fundamentals (raw share count)."""
    short_interest = _to_int(record.get("currentShortPositionQuantity"))
    if short_interest is None:
        return None
    return Fundamentals(
        symbol=symbol,
        float_shares=None,
        shares_outstanding=None,
        short_percent=None,
        source="finra",
        short_interest_shares=short_interest,
    )


def merge_fundamentals(parts: Sequence[Fundamentals | None]) -> Fundamentals | None:
    """Merge a priority-ordered list (highest first) per field, recording provenance.

    For each field the first non-null value wins. ``short_percent`` is taken directly when a
    source provides it, otherwise derived from that source's raw short interest and the best
    merged float (so FINRA's authoritative share count drives the percent). ``source`` lists the
    sources that actually contributed, in priority order (e.g. ``"fmp+finra"``).
    """
    present = [p for p in parts if p is not None]
    if not present:
        return None
    used: set[str] = set()

    def pick(attr: str) -> Any:
        for p in present:
            v = getattr(p, attr)
            if v is not None:
                used.add(p.source)
                return v
        return None

    float_shares = pick("float_shares")
    shares_outstanding = pick("shares_outstanding")
    short_interest_shares = pick("short_interest_shares")

    short_percent: float | None = None
    for p in present:
        cand = p.short_percent
        if cand is None and p.short_interest_shares is not None and float_shares:
            cand = p.short_interest_shares / float_shares
        if cand is not None:
            short_percent = cand
            used.add(p.source)
            break

    order = list(dict.fromkeys(p.source for p in present))
    contributors = [s for s in order if s in used]
    source = "+".join(contributors) if contributors else present[0].source
    return Fundamentals(
        symbol=present[0].symbol,
        float_shares=float_shares,
        shares_outstanding=shares_outstanding,
        short_percent=short_percent,
        source=source,
        short_interest_shares=short_interest_shares,
    )


def fundamentals_record(oid: str, f: Fundamentals, ts: datetime) -> dict[str, Any]:
    return {
        "opportunity_id": oid,
        "symbol": f.symbol,
        "ts_utc": ts.astimezone(UTC),
        "float_shares": f.float_shares,
        "shares_outstanding": f.shares_outstanding,
        "short_percent": f.short_percent,
        "short_interest_shares": f.short_interest_shares,
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


class FMPFundamentals:
    """Float / shares outstanding via Financial Modeling Prep (live glue, run off-thread)."""

    def __init__(self, api_key: str, *, timeout: float = _HTTP_TIMEOUT_SEC) -> None:
        self._api_key = api_key
        self._timeout = timeout

    async def fetch(self, candidate: Candidate) -> Fundamentals | None:
        if not self._api_key:
            return None
        try:
            rows = await asyncio.to_thread(self._get, candidate.symbol)
        except Exception:  # noqa: BLE001 — best-effort; never break capture on a data hiccup
            return None
        return from_fmp(rows, candidate.symbol)

    def _get(self, symbol: str) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"symbol": symbol, "apikey": self._api_key})
        with urllib.request.urlopen(  # noqa: S310 — fixed https endpoint
            f"{_FMP_SHARES_FLOAT_URL}?{query}", timeout=self._timeout
        ) as resp:
            data = json.loads(resp.read().decode())
        return data if isinstance(data, list) else []


class FINRAShortInterest:
    """Authoritative consolidated short interest via the FINRA Query API (live glue).

    Uses OAuth2 client-credentials to obtain a bearer token, then queries the latest
    consolidated short-interest record for the symbol. Returns the raw short-interest share
    count; the short-percent-of-float is derived downstream by ``merge_fundamentals``.
    """

    def __init__(
        self, client_id: str, client_secret: str, *, timeout: float = _HTTP_TIMEOUT_SEC
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout

    async def fetch(self, candidate: Candidate) -> Fundamentals | None:
        if not (self._client_id and self._client_secret):
            return None
        try:
            record = await asyncio.to_thread(self._latest, candidate.symbol)
        except Exception:  # noqa: BLE001 — best-effort; never break capture on a data hiccup
            return None
        if not record:
            return None
        return from_finra(record, candidate.symbol)

    def _token(self) -> str:
        cred = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        req = urllib.request.Request(
            _FINRA_TOKEN_URL, method="POST", headers={"Authorization": f"Basic {cred}"}
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
            token: str = json.loads(resp.read().decode())["access_token"]
        return token

    def _latest(self, symbol: str) -> dict[str, Any] | None:
        body = json.dumps(
            {
                "compareFilters": [
                    {
                        "compareType": "EQUAL",
                        "fieldName": "issueSymbolIdentifier",
                        "fieldValue": symbol,
                    }
                ],
                "sortFields": ["-settlementDate"],
                "limit": 1,
            }
        ).encode()
        req = urllib.request.Request(
            _FINRA_SHORT_INTEREST_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())
        rows = data if isinstance(data, list) else data.get("data") or []
        return rows[0] if rows else None


@dataclass(frozen=True)
class ChainedFundamentals:
    """Fetch from several sources concurrently and merge them by priority (highest first)."""

    sources: Sequence[FundamentalsSource]

    async def fetch(self, candidate: Candidate) -> Fundamentals | None:
        results = await asyncio.gather(
            *(s.fetch(candidate) for s in self.sources), return_exceptions=True
        )
        parts = [r for r in results if isinstance(r, Fundamentals)]
        return merge_fundamentals(parts)


def build_fundamentals(settings: Settings) -> FundamentalsSource:
    """Assemble the fundamentals source from config: FMP > FINRA > yfinance (always-on fallback).

    FMP supplies float, FINRA supplies authoritative short interest, and yfinance backfills any
    field the keyed sources are missing. With no API keys configured this is plain yfinance.
    """
    sources: list[FundamentalsSource] = []
    if settings.fmp_api_key:
        sources.append(FMPFundamentals(settings.fmp_api_key))
    if settings.finra_client_id and settings.finra_client_secret:
        sources.append(FINRAShortInterest(settings.finra_client_id, settings.finra_client_secret))
    sources.append(YFinanceFundamentals())
    if len(sources) == 1:
        return sources[0]
    return ChainedFundamentals(sources)
