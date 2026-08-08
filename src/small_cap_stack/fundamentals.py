"""Fundamentals: float / shares outstanding / short interest (issue #17).

IBKR (Reuters) fundamentals require a paid entitlement (paper returns error 10358), so the
free Phase-1 source is **yfinance** (no API key; what tradepilot used). Values are captured raw at
flag time and recomputed on read, so we can swap in a more reliable source (FMP float / FINRA short
interest) later without re-collecting.

⚠️ ``float_shares`` is **context, not a filter.** Its only consumer is ``gates.py::float_gate``,
whose only caller is the EOD report's ``float_ok`` count — nothing in the selection path or the
paper book reads it (#551, ``research/strategy.md`` §4). Short interest has no source wired at all.

Two protocols live here, and the difference between them is the whole of #563:

- :class:`FundamentalsSource` answers *what is it now* — the live capture path, called at flag time.
- :class:`PointInTimeFundamentals` answers *what was it on this session* — the reconstructed-history
  path (:mod:`.harvest.edgar`). A recon day is months or years old, and for a serially-diluting
  small cap today's answer is not a rounding error on it: CLSK went 37M shares (2011) to 257M
  (2026). Anything implementing it must be decidable **at the session date**, which is a stronger
  requirement than "dated before it" — see :func:`.harvest.edgar.shares_asof`.
"""

from __future__ import annotations

import asyncio
import json
import math
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

from .logging import get_logger
from .scanner import Candidate

log = get_logger(__name__)


@dataclass(frozen=True)
class Fundamentals:
    symbol: str
    float_shares: int | None
    shares_outstanding: int | None
    short_percent: float | None
    source: str


class FundamentalsSource(Protocol):
    async def fetch(self, candidate: Candidate) -> Fundamentals | None: ...


@dataclass(frozen=True)
class AsOfShares:
    """A share count as it stood on one session, carrying the filing that established it (#563).

    ``as_of`` and ``filed`` are **not** interchangeable and keeping both is the point. ``as_of`` is
    what the number describes (a 10-Q's cover date); ``filed`` is when it became public, and it is
    the one a point-in-time query must select on — see :func:`.harvest.edgar.shares_asof`. Storing
    them means a row can be audited afterwards rather than taken on trust.

    ⚠️ ``float_shares`` is **None** for every SEC-derived row and that is deliberate, not a gap.
    EDGAR publishes ``dei:EntityCommonStockSharesOutstanding`` — shares *outstanding*, which is a
    ceiling on free float, not free float. Copying it into ``float_shares`` would make a recon day
    quote a float number no filing ever stated, and would do so in the very column the live tracker
    fills from a real float source. A source that sells historical float proper sets both.
    """

    symbol: str
    float_shares: int | None
    shares_outstanding: int | None
    source: str
    #: The cover date the count describes.
    as_of: date
    #: When the filing carrying it was published — what makes it knowable on the session.
    filed: date
    #: The filing type (``10-Q``, ``10-K``, ``10-Q/A``…), kept so a restatement is legible.
    form: str


class PointInTimeFundamentals(Protocol):
    """What a reconstructed session needs: the share count *as of* a past date.

    Deliberately not :class:`FundamentalsSource` with a date bolted on. That one takes a
    :class:`~.scanner.Candidate` (an IBKR contract) and is async because it runs on the capture
    loop; this one is called from the overnight harvest, off any loop, and a symbol is all it has —
    a reconstructed candidate carries ``con_id=0``.
    """

    @property
    def calls(self) -> int:
        """Requests issued so far — the same cost meter :class:`~.harvest.source.HarvestSource`
        exposes, so a pass can report what it spent without knowing which source it holds."""

    def shares_asof(self, symbol: str, on: date) -> AsOfShares | None: ...


class NullFundamentals:
    """Default no-op source (used in tests / when fundamentals are disabled)."""

    async def fetch(self, candidate: Candidate) -> Fundamentals | None:  # noqa: ARG002 — FundamentalsSource
        return None


def _to_float(v: object) -> float | None:
    # yfinance's .info returns NaN (and occasionally inf) for missing/unknown fields — reject any
    # non-finite value so it maps to None rather than propagating garbage or crashing _to_int.
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, str):
        try:
            f = float(v)
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


def _to_int(v: object) -> int | None:
    # Via _to_float so NaN/inf -> None instead of `int(nan)` raising ValueError.
    f = _to_float(v)
    return int(f) if f is not None else None


def from_info(info: dict[str, Any], symbol: str) -> Fundamentals:
    """Map a yfinance ``Ticker.info`` dict to Fundamentals (tolerant of missing keys)."""
    return Fundamentals(
        symbol=symbol,
        float_shares=_to_int(info.get("floatShares")),
        shares_outstanding=_to_int(info.get("sharesOutstanding")),
        short_percent=_to_float(info.get("shortPercentOfFloat")),
        source="yfinance",
    )


def from_fmp(row: dict[str, Any], symbol: str) -> Fundamentals:
    """Map an FMP ``/stable/shares-float`` row to Fundamentals (tolerant of missing keys).

    FMP's ``freeFloat`` is a *percent of outstanding*, not short interest, so ``short_percent``
    stays ``None`` here — short interest is a separate source (FINRA, #110).
    """
    return Fundamentals(
        symbol=symbol,
        float_shares=_to_int(row.get("floatShares")),
        shares_outstanding=_to_int(row.get("outstandingShares")),
        short_percent=None,
        source="fmp",
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
    """Free float/short source via yfinance (blocking lib run off-thread, time-bounded)."""

    def __init__(self, timeout_sec: float = 10.0) -> None:
        self.timeout_sec = timeout_sec

    async def fetch(self, candidate: Candidate) -> Fundamentals | None:
        try:
            async with asyncio.timeout(self.timeout_sec):
                info = await asyncio.to_thread(self._info, candidate.symbol)
        except Exception:  # noqa: BLE001 — best-effort; a hang/hiccup must not break capture
            log.warning("fundamentals.yfinance_failed", symbol=candidate.symbol, exc_info=True)
            return None
        if not info:
            return None
        return from_info(info, candidate.symbol)

    @staticmethod
    def _info(symbol: str) -> dict[str, Any]:
        import yfinance

        return dict(yfinance.Ticker(symbol).info)


class FMPFundamentals:
    """Float/shares source via Financial Modeling Prep ``/stable/shares-float``.

    A per-symbol HTTPS GET, run off-thread and time-bounded to mirror ``YFinanceFundamentals``
    (no async HTTP dependency needed). Free tier: 250 req/day, US stocks — covers our micro-cap
    universe (spot-checked on #109). ``short_percent`` is left to FINRA (#110).
    """

    _BASE = "https://financialmodelingprep.com/stable/shares-float"

    def __init__(self, api_key: str, timeout_sec: float = 10.0) -> None:
        self.api_key = api_key
        self.timeout_sec = timeout_sec

    async def fetch(self, candidate: Candidate) -> Fundamentals | None:
        if not self.api_key:
            return None
        try:
            async with asyncio.timeout(self.timeout_sec):
                payload = await asyncio.to_thread(self._get, candidate.symbol)
        except Exception:  # noqa: BLE001 — best-effort; a hang/hiccup must not break capture
            log.warning("fundamentals.fmp_failed", symbol=candidate.symbol, exc_info=True)
            return None
        row = _first_row(payload)
        if row is None:
            return None
        return from_fmp(row, candidate.symbol)

    def _get(self, symbol: str) -> object:
        query = urllib.parse.urlencode({"symbol": symbol, "apikey": self.api_key})
        req = urllib.request.Request(
            f"{self._BASE}?{query}", headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:  # noqa: S310 — fixed https host
            return json.loads(resp.read().decode("utf-8"))


def _first_row(payload: object) -> dict[str, Any] | None:
    """FMP returns a list of rows (or a bare object); take the first, reject error payloads."""
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if not isinstance(payload, dict) or "Error Message" in payload:
        return None
    return payload


class MultiFundamentals:
    """Fan out to several sources concurrently, returning one Fundamentals per source that answers.

    Every source's raw number is kept (store raw); the read side picks per-field by source
    priority. Onboarding another source over time is just appending it to ``sources``.
    """

    def __init__(self, sources: Sequence[FundamentalsSource]) -> None:
        self.sources = tuple(sources)

    async def fetch_all(self, candidate: Candidate) -> list[Fundamentals]:
        results = await asyncio.gather(
            *(s.fetch(candidate) for s in self.sources), return_exceptions=True
        )
        return [r for r in results if isinstance(r, Fundamentals)]
