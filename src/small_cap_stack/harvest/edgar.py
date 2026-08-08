"""Point-in-time share counts from SEC EDGAR, for reconstructed sessions (#563).

The harvest (#431) rebuilds pre-market days from vendor minute bars and writes no fundamentals at
all, so every ``recon`` trade in ``books_all`` carries ``float_shares: None``. This is the source
that fills the gap — and the reason it is EDGAR rather than the obvious vendor is worth recording:

- **FMP's historical shares-float endpoint is closed to us.** ``api/v4/historical/shares_float``
  answers 403 "*only available for legacy users who have valid subscriptions prior August 31,
  2025*". Not a plan tier more money fixes; the account is on the wrong side of a cutoff.
- **``stable/shares-float`` is a snapshot, not a series.** One row, stamped *now*. Using it for a
  2024 session is exactly the "current float stamped backwards" this module exists to avoid.
- **EDGAR needs no API key.** It needs a ``User-Agent`` identifying the requester and a rate under
  10 req/s (SEC's fair-access policy). A missing UA is a 403, which is why
  :attr:`EdgarFundamentals.user_agent` has no default worth guessing.

## What it returns, and what it deliberately does not

``dei:EntityCommonStockSharesOutstanding`` is the cover-page count every 10-Q/10-K carries: shares
**outstanding**, filed quarterly, back years. It is not free float. See :class:`.AsOfShares` for
why that is kept in its own column instead of being written into ``float_shares``.

## The rule that makes it point-in-time

Each observation carries two dates and they are not interchangeable::

    {"end": "2026-08-04", "val": 256817073, "form": "10-Q", "filed": "2026-08-06"}

``end`` is what the number describes; ``filed`` is when anyone could know it. Selecting on ``end``
looks right and leaks: on 2026-08-05 that row's ``end`` already qualifies, but the filing was two
days away. The repo's no-lookahead discipline (``research/decisions.md``) applies to enrichment for
the same reason it applies to selection — a backtest that quietly knows Thursday's filing on Tuesday
is not measuring the strategy. So: **filter on ``filed <= session``, then take the newest ``end``.**
"""

from __future__ import annotations

import json
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..fundamentals import AsOfShares
from ..logging import get_logger
from .source import HarvestError

log = get_logger(__name__)

#: SEC's JSON API host (the XBRL frames/concepts) and its static-file host (the ticker->CIK map).
#: Overridable so the tests never touch the network, in the shape :mod:`.source` already uses.
DEFAULT_DATA_URL = "https://data.sec.gov"
DEFAULT_WWW_URL = "https://www.sec.gov"

#: The cover-page tag. ``dei`` (Document and Entity Information) rather than ``us-gaap`` on purpose:
#: ``us-gaap:CommonStockSharesOutstanding`` is a balance-sheet figure per class and per period end,
#: which for a multi-class issuer is several series that have to be summed. The ``dei`` tag is the
#: single number on the front of the filing — one series, every filer, no reassembly.
SHARES_TAG = "dei/EntityCommonStockSharesOutstanding"

#: What this source stamps on every row it writes. Not in ``report._FLOAT_PRIORITY``, and it does
#: not need to be: that ranking is over ``float_shares``, which these rows never set.
EDGAR_SOURCE = "edgar"


class EdgarError(HarvestError):
    """EDGAR could not be reached or answered with something unusable.

    A :class:`~.source.HarvestError` subclass so the CLI's existing "operator error, not a crash"
    handling covers it — and so a wall of these aborts the pass through the same failure-ratio
    guard the minute-bar harvest uses. **Not** raised for "this company has no such data": that is
    a legitimate answer and comes back as ``None``.
    """


@dataclass(frozen=True)
class SharesRow:
    """One observation from the concept series — the two dates, the count, the filing."""

    end: date
    filed: date
    val: int
    form: str


def parse_shares_series(payload: object) -> list[SharesRow]:
    """The ``units.shares`` array of a ``companyconcept`` response, as rows. Tolerant by design.

    Unparseable entries are skipped rather than raising: the series spans a decade of filings from
    thousands of filers, and one malformed historical row must not cost the whole symbol. An empty
    list is a legitimate answer (a filer with no cover-page count under this tag).
    """
    if not isinstance(payload, dict):
        return []
    units = payload.get("units")
    if not isinstance(units, dict):
        return []
    out: list[SharesRow] = []
    for raw in units.get("shares") or []:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(
                SharesRow(
                    end=date.fromisoformat(str(raw["end"])),
                    filed=date.fromisoformat(str(raw["filed"])),
                    val=int(raw["val"]),
                    form=str(raw.get("form") or ""),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def shares_asof(rows: list[SharesRow], on: date) -> SharesRow | None:
    """The share count knowable on ``on`` — **the correctness rule of #563**, kept pure.

    Two things, in order:

    1. **``filed <= on``.** Not ``end <= on``. A 10-Q with a 2026-08-04 cover date was filed on
       2026-08-06, so on the 5th its ``end`` qualifies and the number did not yet exist. Selecting
       on ``end`` is the lookahead this whole module exists to prevent.
    2. **Newest ``end`` wins**, ``filed`` breaking the tie. An amended filing (``10-Q/A``) restates
       an *older* cover date months later, so "latest filed" would hand back a stale count on the
       strength of a recent correction. Ordering on ``end`` first asks the right question — which
       count describes the most recent date? — and the ``filed`` tiebreak then prefers the
       restatement over the original for the same cover date, which is what an amendment is for.

    ``None`` when nothing had been filed yet: a company that IPO'd after the session has no
    knowable share count on it, and inventing one from its first filing would be the same bug.
    """
    known = [r for r in rows if r.filed <= on]
    if not known:
        return None
    return max(known, key=lambda r: (r.end, r.filed))


def parse_cik_map(payload: object) -> dict[str, str]:
    """``company_tickers.json`` as ``{TICKER: zero-padded 10-digit CIK}``.

    SEC serves it as an object keyed by row index, not as an array, and ``cik_str`` is a bare int
    despite the name — the concept URL wants it zero-padded to 10 digits.
    """
    if not isinstance(payload, dict):
        return {}
    out: dict[str, str] = {}
    for raw in payload.values():
        if not isinstance(raw, dict):
            continue
        ticker = str(raw.get("ticker") or "").strip().upper()
        cik = raw.get("cik_str")
        if not ticker or cik is None:
            continue
        try:
            out[ticker] = f"{int(cik):010d}"
        except (TypeError, ValueError):
            continue
    return out


def cik_candidates(symbol: str) -> list[str]:
    """The forms of ``symbol`` worth looking up, most likely first.

    The vendor and SEC punctuate share classes differently — Polygon says ``BRK.B``, EDGAR says
    ``BRK-B`` — and a class ticker that misses is a symbol silently carrying no share count for the
    whole harvest. Cheap to try both; nothing else about the name is guessed.
    """
    upper = symbol.strip().upper()
    forms = [upper]
    for alt in (upper.replace(".", "-"), upper.replace("-", "."), upper.replace(".", "")):
        if alt and alt not in forms:
            forms.append(alt)
    return forms


@dataclass
class EdgarFundamentals:
    """SEC EDGAR as a :class:`~..fundamentals.PointInTimeFundamentals`, stdlib only.

    Free, so unlike :class:`~.source.MassiveSource` the budget is not what shapes this — the
    fair-access rate is (10 req/s; :attr:`min_interval_sec` sits under it). What shapes it is
    **reuse**: a harvest of ~500 sessions asks about the same few thousand symbols over and over,
    and one company's whole filing history arrives in a single response. So the series is memoised
    per symbol for the life of the object and a full backfill costs roughly one call per distinct
    symbol, not one per symbol-day.

    The memo is per-run, never on disk. A cached series goes stale the moment the company files
    again, and a harvest that is *adding* recent sessions is exactly when that matters.
    """

    #: Required by SEC's fair-access policy: a real contact string. Without it every request is a
    #: 403 — which is why there is no default; a guessed one is a working harvest that writes nulls.
    user_agent: str
    data_url: str = DEFAULT_DATA_URL
    www_url: str = DEFAULT_WWW_URL
    min_interval_sec: float = 0.15  # ~6.7 req/s against SEC's 10 req/s ceiling
    timeout_sec: float = 30.0
    max_retries: int = 3
    calls: int = 0
    #: Injected so the tests run instantly, exactly as :class:`~.source.MassiveSource` does.
    sleep: Callable[[float], None] = field(default=_time.sleep, repr=False)
    _ciks: dict[str, str] | None = field(default=None, init=False, repr=False)
    #: symbol -> its series, or None for "asked, EDGAR has nothing". Both are worth remembering:
    #: re-asking for a name with no filings would cost a call per session it appears in.
    _series: dict[str, list[SharesRow] | None] = field(default_factory=dict, init=False, repr=False)

    # -- transport ------------------------------------------------------------------------------

    def _get(self, url: str, *, missing_ok: bool = False) -> Any:
        """One GET, rate-limited and retried. ``None`` on 404 when ``missing_ok``."""
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            if self.calls and self.min_interval_sec:
                self.sleep(self.min_interval_sec)
            self.calls += 1
            req = urllib.request.Request(
                url, headers={"User-Agent": self.user_agent, "Accept": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:  # noqa: S310
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and missing_ok:
                    return None
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    self.sleep(delay)
                    delay *= 2
                    continue
                body = exc.read().decode(errors="replace")[:200]
                hint = ""
                if exc.code == 403:
                    # The one failure a reader will otherwise misdiagnose: SEC serves an HTML block
                    # page, not a JSON error, and it looks nothing like a rate limit or an outage.
                    hint = (
                        " — SEC blocks requests without an identifying User-Agent; "
                        "set HARVEST_EDGAR_USER_AGENT to 'name email'"
                    )
                raise EdgarError(f"HTTP {exc.code} on {url}: {body}{hint}") from exc
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
                if attempt < self.max_retries:
                    self.sleep(delay)
                    delay *= 2
                    continue
                raise EdgarError(f"EDGAR request failed for {url}: {exc}") from exc
        raise EdgarError("unreachable")

    # -- lookups --------------------------------------------------------------------------------

    def cik_for(self, symbol: str) -> str | None:
        """``symbol``'s zero-padded CIK, fetching the map once. ``None`` when SEC does not list it.

        The map holds ~10.4k tickers, so a thin name in the harvested universe can genuinely be
        absent — a foreign private issuer, an OTC name, a ticker recycled since. That is a null
        share count, not a failure, and it must not abort the pass.
        """
        if self._ciks is None:
            payload = self._get(f"{self.www_url}/files/company_tickers.json")
            self._ciks = parse_cik_map(payload)
            log.info("edgar.cik_map", tickers=len(self._ciks))
        for form in cik_candidates(symbol):
            cik = self._ciks.get(form)
            if cik is not None:
                return cik
        return None

    def series_for(self, symbol: str) -> list[SharesRow] | None:
        """``symbol``'s whole cover-page share-count history, memoised. ``None`` if it has none."""
        if symbol in self._series:
            return self._series[symbol]
        rows: list[SharesRow] | None = None
        cik = self.cik_for(symbol)
        if cik is not None:
            payload = self._get(
                f"{self.data_url}/api/xbrl/companyconcept/CIK{cik}/{SHARES_TAG}.json",
                # A filer with no observations under this tag answers 404, which is data ("nothing
                # here"), not a transport failure. Treating it as an error would abandon the whole
                # session over one shell company.
                missing_ok=True,
            )
            parsed = parse_shares_series(payload)
            rows = parsed or None
        self._series[symbol] = rows
        return rows

    # -- PointInTimeFundamentals ----------------------------------------------------------------

    def shares_asof(self, symbol: str, on: date) -> AsOfShares | None:
        """The share count knowable for ``symbol`` on ``on``, or ``None`` if there is none."""
        rows = self.series_for(symbol)
        if not rows:
            return None
        row = shares_asof(rows, on)
        if row is None:
            return None
        return AsOfShares(
            symbol=symbol,
            # Outstanding, never float — see AsOfShares. The two columns mean different things and
            # this source can only honestly fill one of them.
            float_shares=None,
            shares_outstanding=row.val,
            source=EDGAR_SOURCE,
            as_of=row.end,
            filed=row.filed,
            form=row.form,
        )
