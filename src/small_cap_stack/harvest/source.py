"""Where the harvest's bars come from — one narrow protocol, one vendor implementation (#431).

#430 chose the free tier over the $29 Starter month, so the whole harvest is priced by a **5
calls/min** rate limit rather than by bytes or lookback. Two consequences shape this module:

- **Rate limiting is the design, not a courtesy.** Every call sleeps first (:attr:`rate_sleep_sec`,
  13 s ≈ 4.6/min). Absorbing 429s instead would get the key blocked, and there is no second key.
  The sleep is injectable so the tests exercise the retry/pagination logic without waiting.
- **Everything above this module is ingest-agnostic.** :class:`HarvestSource` is the entire surface
  the runner uses — two methods. #430's escape hatch (buy the Starter month, or switch to flat
  files) stays a one-class change rather than a rewrite, which is what the issue asked for.

``MASSIVE_API_KEY`` is read from the environment and belongs in the box's runtime env / GitHub
Actions secrets only — never in a cloud session, which has no secret store (CLAUDE.md). Massive is
Polygon.io renamed and the REST surface is unchanged, so the host stays overridable.
"""

from __future__ import annotations

import json
import os
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

DEFAULT_BASE_URL = os.environ.get("MASSIVE_BASE_URL", "https://api.polygon.io")
API_KEY_ENV = "MASSIVE_API_KEY"


class HarvestError(RuntimeError):
    """A vendor/transport failure the harvest cannot recover from within one call."""


class HarvestEntitlementError(HarvestError):
    """The vendor served the request but will not sell data that far back (#440).

    Kept apart from every other failure because it means something completely different: not "this
    call went wrong" but "the window you planned is longer than the one you bought". Everything else
    is worth aborting a night over; this is worth *trimming the plan* and carrying on.

    Matched on the message text rather than on the status code alone. A 403 from this vendor is also
    what a bad or revoked key looks like, and a key problem misread as an entitlement edge would
    trim the harvest's window to nothing every night while reporting a clean run — the one failure
    mode worse than the crash this class exists to prevent.
    """


#: Substrings that identify the entitlement refusal in the vendor's 403 body. Deliberately narrow:
#: anything not matched here stays a plain :class:`HarvestError` and still stops the night.
_ENTITLEMENT_MARKERS = ("historical entitlement", "past historical")


class HarvestSource(Protocol):
    """The two reads the harvest needs. Anything satisfying this can feed it.

    Rows are the vendor's own aggregate dicts (``T``/``t``/``o``/``h``/``l``/``c``/``v``) rather
    than a parsed type, so the adapter stays a transport and the parsing lives in one place
    (:func:`.reconstruct.to_bars`, :func:`.prefilter.universe_rows`).
    """

    @property
    def calls(self) -> int:
        """Requests issued so far — the harvest's only cost meter."""

    def grouped_daily(self, day: date) -> list[dict[str, Any]]:
        """Every US equity's daily OHLCV for one session — one request for the whole market."""

    def minute_bars(self, symbol: str, day: date) -> list[dict[str, Any]]:
        """One symbol's 1-minute bars for one session, extended hours included."""


@dataclass
class MassiveSource:
    """Massive (ex-Polygon) REST, stdlib only — no new dependency for a 45-night job.

    ``adjusted=False`` is not a default to skim past: the strategy's price gate is $1–50 **as
    traded that day**. A split-adjusted series would move a 2019 $30 stock to $3 today and quietly
    change which symbol-days are even in the universe — two years into a harvest, invisibly.
    """

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    rate_sleep_sec: float = 13.0  # free tier is 5 calls/min; 13 s ≈ 4.6/min with headroom
    timeout_sec: float = 60.0
    max_retries: int = 4
    calls: int = 0
    #: Injected so tests run instantly and the runner can make a long sleep interruptible.
    sleep: Callable[[float], None] = field(default=_time.sleep, repr=False)

    @classmethod
    def from_env(cls, **kw: Any) -> MassiveSource:
        key = os.environ.get(API_KEY_ENV, "").strip()
        if not key:
            raise HarvestError(
                f"{API_KEY_ENV} is not set. It lives in the box's runtime env or Actions secrets — "
                "never in a cloud session, which has no secret store."
            )
        return cls(api_key=key, **kw)

    # -- transport --------------------------------------------------------------------------

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        """One GET against the vendor, with the key injected and the rate limit applied."""
        query = {k: v for k, v in params.items() if v is not None}
        query["apiKey"] = self.api_key
        return self._get_url(f"{self.base_url}{path}?{urllib.parse.urlencode(query)}")

    def _get_url(self, url: str) -> dict[str, Any]:
        if "apiKey=" not in url:  # next_url comes back unsigned
            url = f"{url}{'&' if '?' in url else '?'}apiKey={urllib.parse.quote(self.api_key)}"
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            if self.calls and self.rate_sleep_sec:
                self.sleep(self.rate_sleep_sec)
            self.calls += 1
            try:
                with urllib.request.urlopen(url, timeout=self.timeout_sec) as resp:  # noqa: S310
                    payload: dict[str, Any] = json.loads(resp.read().decode())
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    self.sleep(delay)
                    delay *= 2
                    continue
                body = exc.read().decode(errors="replace")[:400]
                # Never echo the URL back: it carries the key.
                message = f"HTTP {exc.code} on {url.split('?')[0]}: {body}"
                lowered = body.lower()
                if any(marker in lowered for marker in _ENTITLEMENT_MARKERS):
                    raise HarvestEntitlementError(message) from exc
                raise HarvestError(message) from exc
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    self.sleep(delay)
                    delay *= 2
                    continue
                raise HarvestError(f"network error on {url.split('?')[0]}: {exc}") from exc
        raise HarvestError("unreachable")

    # -- HarvestSource ----------------------------------------------------------------------

    def grouped_daily(
        self, day: date, *, adjusted: bool = False, include_otc: bool = False
    ) -> list[dict[str, Any]]:
        path = f"/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}"
        page = self.get(path, adjusted=str(adjusted).lower(), include_otc=str(include_otc).lower())
        return list(page.get("results") or [])

    def aggregates(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
        multiplier: int = 1,
        timespan: str = "minute",
        adjusted: bool = False,
        limit: int = 50_000,
    ) -> list[dict[str, Any]]:
        """Raw aggregate bars, following ``next_url`` pagination to exhaustion."""
        path = (
            f"/v2/aggs/ticker/{urllib.parse.quote(symbol)}/range/{multiplier}/{timespan}/"
            f"{start.isoformat()}/{end.isoformat()}"
        )
        page = self.get(path, adjusted=str(adjusted).lower(), sort="asc", limit=limit)
        rows: list[dict[str, Any]] = list(page.get("results") or [])
        while page.get("next_url"):
            page = self._get_url(str(page["next_url"]))
            rows.extend(page.get("results") or [])
        return rows

    def minute_bars(self, symbol: str, day: date) -> list[dict[str, Any]]:
        """The session's minute bars.

        One request returns the **whole day**, not just pre-market — which is why the harvest
        stores full-session 5-min bars (see :mod:`.runner`): trimming the window saves storage, not
        API budget, and the budget is the scarce thing.
        """
        return self.aggregates(symbol, start=day, end=day)
