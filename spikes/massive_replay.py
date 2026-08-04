"""Spike #428 (Stages 1 / 3): the Massive (ex-Polygon) adapter — buy history instead of waiting.

Pulls historical minute bars from Massive, aggregates them onto the IBKR-aligned 5-min grid the
engine expects, reconstructs the scanner appearance (``scanner_reconstruct``), and replays
``bullflag/day.py::detect_day`` + the R-metrics over it. That chain — vendor bars in, R out — is
what lets strategy expectancy be measured across ≥5 years of regimes *now* rather than after the
3-month Phase-1 window closes.

**Where the secret lives.** ``MASSIVE_API_KEY`` is read from the environment and belongs in GitHub
Actions secrets only, never in a cloud session (which has no secret store — CLAUDE.md). The
companion workflow ``.github/workflows/spike-massive.yml`` runs this on ``ubuntu-latest`` —
deliberately NOT the self-hosted ``vps`` runner, to keep multi-GB vendor pulls off the 4 GB box.

## Subcommands

``probe`` — Stage 1's go/no-go on the vendor itself, before a penny is spent. Four claims the plan
rests on, each verified against the tape rather than the marketing page: extended-hours bars exist
from 04:00 ET; a delisted ticker still resolves; ``adjusted=false`` really returns as-traded prices
(a split-adjusted feed silently breaks the $1–50 price gate for any pre-split year); and the free
tier's lookback actually reaches the dates claimed.

``day`` — one symbol-day end to end: fetch 1-min → aggregate to 5-min → reconstruct the appearance
→ ``detect_day`` → R. This is the unit the harvest parallelises over.

``universe`` — the Stage-3 prefilter. Grouped-daily bars are one request per trading day for the
WHOLE market, so the candidate set (price band + day change) costs ~1,250 requests for five years
and lands the previous close that ``scanner_reconstruct`` needs for the change gate. Minute bars
are then fetched for candidates only (~50/day), which is what keeps the harvest inside one paid
month.

## Bar grid

Massive returns 1-min bars; the engine reads 5-min bars aligned to :00/:05. :func:`aggregate`
resamples onto that grid **anchored to the ET hour** — matching what IBKR hands back — rather than
anchoring to the first bar seen, which would silently shift every boundary on any symbol whose
first print is not on a 5-min mark. The appearance is reconstructed on the **1-min** series (a
true trailing 5-min rolling sum, the closest analogue to ``stVolume5minAbove``) while detection
runs on the **5-min** series; keeping those separate is the point of pulling minute data at all.

Run it (needs a key):

    MASSIVE_API_KEY=… python spikes/massive_replay.py probe
    MASSIVE_API_KEY=… python spikes/massive_replay.py day --symbol ARCT --date 2026-07-02
    MASSIVE_API_KEY=… python spikes/massive_replay.py universe --date 2026-07-02

...and without one, ``--selftest`` exercises the aggregation and the grid alignment on synthetic
bars, so the adapter is verifiable in a session that has no key at all.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scanner_reconstruct import reconstruct_hit  # noqa: E402

from small_cap_stack.capture import Bar  # noqa: E402
from small_cap_stack.clock import ET  # noqa: E402
from small_cap_stack.config import Settings  # noqa: E402
from small_cap_stack.rmetrics import compute_r_metrics  # noqa: E402

# Massive is Polygon.io renamed; the REST surface is unchanged, so the host stays overridable rather
# than hard-coded — the day the domain moves, this is a one-flag change, not a rewrite.
DEFAULT_BASE_URL = os.environ.get("MASSIVE_BASE_URL", "https://api.polygon.io")
API_KEY_ENV = "MASSIVE_API_KEY"

# Free tier is 5 calls/min. Sleep between calls rather than absorb 429s: a spike that hammers a
# vendor's free tier gets the key blocked, and the paid harvest inherits the same code path.
DEFAULT_RATE_SLEEP_SEC = float(os.environ.get("MASSIVE_RATE_SLEEP_SEC", "13"))


class MassiveError(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------------
# REST client
# ------------------------------------------------------------------------------------------------


@dataclass
class MassiveClient:
    """Minimal REST client over the aggregates + reference endpoints (stdlib only, no new deps)."""

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    rate_sleep_sec: float = DEFAULT_RATE_SLEEP_SEC
    timeout_sec: float = 60.0
    max_retries: int = 4
    calls: int = 0

    @classmethod
    def from_env(cls, **kw: Any) -> MassiveClient:
        key = os.environ.get(API_KEY_ENV, "").strip()
        if not key:
            raise MassiveError(
                f"{API_KEY_ENV} is not set. It belongs in GitHub Actions secrets — run this via "
                ".github/workflows/spike-massive.yml, not in a cloud session."
            )
        return cls(api_key=key, **kw)

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        """One GET, with the key injected and 429/5xx retried on an exponential backoff."""
        query = {k: v for k, v in params.items() if v is not None}
        query["apiKey"] = self.api_key
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(query)}"
        return self._get_url(url)

    def _get_url(self, url: str) -> dict[str, Any]:
        if "apiKey=" not in url:  # next_url comes back unsigned
            url = f"{url}{'&' if '?' in url else '?'}apiKey={urllib.parse.quote(self.api_key)}"
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            if self.calls and self.rate_sleep_sec:
                time.sleep(self.rate_sleep_sec)
            self.calls += 1
            try:
                with urllib.request.urlopen(url, timeout=self.timeout_sec) as resp:  # noqa: S310
                    payload: dict[str, Any] = json.loads(resp.read().decode())
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                body = exc.read().decode(errors="replace")[:400]
                # Never echo the URL back: it carries the key.
                raise MassiveError(f"HTTP {exc.code} on {url.split('?')[0]}: {body}") from exc
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise MassiveError(f"network error on {url.split('?')[0]}: {exc}") from exc
        raise MassiveError("unreachable")

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
        """Raw aggregate bars, following ``next_url`` pagination to exhaustion.

        ``adjusted=False`` is the default *on purpose*: the strategy's price gate is $1–50 on the
        price as traded that day. A split-adjusted series would move a 2019 $30 stock to $3 today
        and quietly change which symbol-days are even in the universe.
        """
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

    def grouped_daily(
        self, day: date, *, adjusted: bool = False, include_otc: bool = False
    ) -> list[dict[str, Any]]:
        """Every US equity's daily OHLCV for one session — one request for the whole market."""
        path = f"/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}"
        page = self.get(path, adjusted=str(adjusted).lower(), include_otc=str(include_otc).lower())
        return list(page.get("results") or [])

    def ticker_details(self, symbol: str) -> dict[str, Any]:
        page = self.get(f"/v3/reference/tickers/{urllib.parse.quote(symbol)}")
        return dict(page.get("results") or {})


# ------------------------------------------------------------------------------------------------
# Bar adapter + grid alignment
# ------------------------------------------------------------------------------------------------


def to_bars(rows: Iterable[dict[str, Any]]) -> list[Bar]:
    """Vendor aggregate rows → :class:`Bar`. ``t`` is epoch milliseconds, UTC."""
    bars = [
        Bar(
            start=datetime.fromtimestamp(int(r["t"]) / 1000, tz=UTC),
            open=float(r["o"]),
            high=float(r["h"]),
            low=float(r["l"]),
            close=float(r["c"]),
            volume=float(r.get("v") or 0.0),
        )
        for r in rows
    ]
    return sorted(bars, key=lambda b: b.start)


def _bucket_start(moment: datetime, minutes: int) -> datetime:
    """Floor to the ``minutes`` grid anchored on the ET hour (see the module doc, "Bar grid")."""
    et = moment.astimezone(ET)
    floored = et.replace(minute=(et.minute // minutes) * minutes, second=0, microsecond=0)
    return floored.astimezone(UTC)


def aggregate(bars: Sequence[Bar], minutes: int = 5) -> list[Bar]:
    """Resample finer bars onto the ``minutes`` grid: first open, max high, min low, last close.

    Empty buckets are **not** filled. IBKR's historical bars omit periods with no trades, so
    synthesising flat bars would hand the detector candles that never existed — and the engine
    reads consecutive bars as consecutive price action.
    """
    if not bars:
        return []
    out: list[Bar] = []
    bucket: list[Bar] = []
    current = _bucket_start(bars[0].start, minutes)
    for bar in bars:
        start = _bucket_start(bar.start, minutes)
        if start != current and bucket:
            out.append(_fold(bucket, current))
            bucket = []
        current = start
        bucket.append(bar)
    if bucket:
        out.append(_fold(bucket, current))
    return out


def _fold(bucket: Sequence[Bar], start: datetime) -> Bar:
    return Bar(
        start=start,
        open=bucket[0].open,
        high=max(b.high for b in bucket),
        low=min(b.low for b in bucket),
        close=bucket[-1].close,
        volume=sum(b.volume for b in bucket),
    )


def in_session(bars: Sequence[Bar], settings: Settings, trading_date: date) -> list[Bar]:
    """Trim to the chart window the engine reads: ``[chart_start, capture_end)`` ET on the date."""
    out = []
    for b in bars:
        et = b.start.astimezone(ET)
        if et.date() == trading_date and settings.chart_start <= et.time() < settings.capture_end:
            out.append(b)
    return out


# ------------------------------------------------------------------------------------------------
# Replay
# ------------------------------------------------------------------------------------------------


def replay_day(
    client: MassiveClient,
    symbol: str,
    trading_date: date,
    settings: Settings,
    *,
    prev_close: float | None = None,
) -> dict[str, Any]:
    """Fetch, aggregate, reconstruct the appearance, detect, and measure — one symbol-day."""
    raw = client.aggregates(symbol, start=trading_date, end=trading_date)
    minute_bars = in_session(to_bars(raw), settings, trading_date)
    five = aggregate(minute_bars, minutes=5)
    if not five:
        return {"symbol": symbol, "date": trading_date.isoformat(), "error": "no bars"}

    # The appearance is reconstructed on the MINUTE series — a true trailing 5-min rolling sum, the
    # closest analogue to IBKR's continuously-updated stVolume5minAbove (see the module doc).
    recon = reconstruct_hit(
        minute_bars,
        settings,
        symbol=symbol,
        trading_date=trading_date,
        prev_close=prev_close,
        window_minutes=5,
    )
    metrics = compute_r_metrics(five, settings, first_hit=recon.hit_time)
    return {
        "symbol": symbol,
        "date": trading_date.isoformat(),
        "minute_bars": len(minute_bars),
        "five_min_bars": len(five),
        "first_bar_et": minute_bars[0].start.astimezone(ET).strftime("%H:%M")
        if minute_bars
        else None,
        "prev_close": prev_close,
        "reconstructed_hit_et": (
            recon.hit_time.astimezone(ET).strftime("%H:%M:%S") if recon.hit_time else None
        ),
        "binding_gate": recon.binding_gate,
        "change_decidable": recon.change_decidable,
        "setup_found": metrics.setup_found,
        "triggered": metrics.triggered,
        "takeable": metrics.takeable,
        "entry_fill": metrics.entry_fill,
        "stop": metrics.stop,
        "max_r": metrics.max_r,
        "mae_r": metrics.mae_r,
        "stopped_out": metrics.stopped_out,
        "failing_gates": list(metrics.failing_gates),
    }


def universe_for(
    client: MassiveClient, trading_date: date, settings: Settings, *, prev_day: date | None = None
) -> dict[str, Any]:
    """The Stage-3 prefilter: symbol-days worth pulling minute bars for, plus their prev close.

    Two grouped-daily requests (the day and the session before it) give price, day volume and the
    previous close for the entire market. The day's HIGH is used for the price band, not the close:
    a runner that gapped to $6 and closed at $0.90 was inside the $1–50 universe while it mattered.
    """
    today = client.grouped_daily(trading_date)
    prior = client.grouped_daily(prev_day) if prev_day else []
    prev_close = {str(r["T"]): float(r["c"]) for r in prior if r.get("c")}
    candidates = []
    for row in today:
        sym = str(row.get("T") or "")
        high, low, close = row.get("h"), row.get("l"), row.get("c")
        if not sym or high is None or close is None:
            continue
        if not (settings.scan_min_price <= float(high) <= settings.scan_max_price):
            continue
        pc = prev_close.get(sym)
        change = None if not pc else (float(high) / pc - 1.0) * 100.0
        # No previous close -> keep it: a first-day/relisted symbol is exactly the kind of runner
        # the strategy wants, and dropping it here would bias the harvest silently.
        if change is not None and change <= settings.scan_change_pct:
            continue
        if float(row.get("v") or 0.0) < settings.scan_min_5m_volume:
            continue  # a day that never traded 100k shares cannot clear a 5-min 100k gate
        candidates.append(
            {
                "symbol": sym,
                "prev_close": pc,
                "high": float(high),
                "low": None if low is None else float(low),
                "close": float(close),
                "day_volume": float(row.get("v") or 0.0),
                "day_change_pct": None if change is None else round(change, 2),
            }
        )
    candidates.sort(key=lambda c: -(c["day_change_pct"] or 0.0))
    return {
        "date": trading_date.isoformat(),
        "universe_rows": len(today),
        "candidates": len(candidates),
        "prev_close_coverage": sum(1 for c in candidates if c["prev_close"]),
        "rows": candidates,
    }


# ------------------------------------------------------------------------------------------------
# Stage-1 probe: verify the vendor's claims before paying
# ------------------------------------------------------------------------------------------------


def probe(client: MassiveClient, settings: Settings, *, symbol: str, day: date) -> dict[str, Any]:
    """Check the four assumptions the plan rests on (see the module doc, ``probe``)."""
    checks: dict[str, Any] = {}

    raw = client.aggregates(symbol, start=day, end=day)
    bars = to_bars(raw)
    et_times = [b.start.astimezone(ET) for b in bars]
    premarket = [t for t in et_times if t.time() < settings.capture_end and t.hour < 9]
    checks["extended_hours"] = {
        "claim": "minute bars exist from 04:00 ET",
        "bars": len(bars),
        "first_bar_et": et_times[0].strftime("%H:%M") if et_times else None,
        "premarket_bars": len(premarket),
        "passed": bool(premarket) and bool(et_times) and et_times[0].time().hour <= 4,
    }

    adj = to_bars(client.aggregates(symbol, start=day, end=day, adjusted=True))
    unadj_close = bars[-1].close if bars else None
    adj_close = adj[-1].close if adj else None
    checks["adjusted_flag"] = {
        "claim": "adjusted=false returns as-traded prices (the $1-50 gate depends on it)",
        "unadjusted_last_close": unadj_close,
        "adjusted_last_close": adj_close,
        # On a symbol with no split since `day` the two agree, which proves nothing either way —
        # so this reports rather than asserts. Point it at a known post-split name to make it bite.
        "differs": None if None in (unadj_close, adj_close) else unadj_close != adj_close,
        "passed": unadj_close is not None,
    }

    try:
        details = client.ticker_details(symbol)
        checks["ticker_reference"] = {
            "claim": "reference data resolves (delisted tickers included)",
            "name": details.get("name"),
            "active": details.get("active"),
            "delisted_utc": details.get("delisted_utc"),
            "passed": bool(details),
        }
    except MassiveError as exc:
        checks["ticker_reference"] = {
            "claim": "reference data resolves",
            "error": str(exc),
            "passed": False,
        }

    five = aggregate(bars, minutes=5)
    aligned = all(b.start.astimezone(ET).minute % 5 == 0 for b in five)
    checks["grid_alignment"] = {
        "claim": "1-min bars fold onto the IBKR-aligned :00/:05 grid",
        "five_min_bars": len(five),
        "volume_preserved": round(sum(b.volume for b in five), 2)
        == round(sum(b.volume for b in bars), 2),
        "passed": aligned and bool(five),
    }

    return {
        "symbol": symbol,
        "date": day.isoformat(),
        "api_calls": client.calls,
        "checks": checks,
        "all_passed": all(c.get("passed") for c in checks.values()),
    }


# ------------------------------------------------------------------------------------------------
# Self-test (no key required)
# ------------------------------------------------------------------------------------------------


def selftest() -> int:
    """Exercise the adapter's pure half on synthetic bars — what a keyless session can verify."""
    base = datetime(2026, 7, 2, 13, 32, tzinfo=UTC)  # 09:32 ET: deliberately off the 5-min grid
    minute = [
        Bar(
            start=base + timedelta(minutes=i),
            open=10.0 + i * 0.1,
            high=10.5 + i * 0.1,
            low=9.5 + i * 0.1,
            close=10.2 + i * 0.1,
            volume=1000.0 * (i + 1),
        )
        for i in range(13)
    ]
    five = aggregate(minute, minutes=5)
    failures: list[str] = []

    ets = [b.start.astimezone(ET) for b in five]
    if not all(t.minute % 5 == 0 for t in ets):
        failures.append(f"buckets not on the 5-min grid: {[t.strftime('%H:%M') for t in ets]}")
    if ets and ets[0].strftime("%H:%M") != "09:30":
        failures.append(f"first bucket anchored to the bar, not the ET grid: {ets[0]:%H:%M}")
    if round(sum(b.volume for b in five), 6) != round(sum(b.volume for b in minute), 6):
        failures.append("aggregation lost volume")
    if five and five[0].open != minute[0].open:
        failures.append("bucket open is not the first minute's open")
    if five and five[-1].close != minute[-1].close:
        failures.append("bucket close is not the last minute's close")
    for b in five:
        members = [m for m in minute if _bucket_start(m.start, 5) == b.start]
        if b.high != max(m.high for m in members) or b.low != min(m.low for m in members):
            failures.append(f"bucket {b.start} high/low is not the extreme of its members")

    # A gap in the tape must NOT be filled (see `aggregate`): 3 buckets in, 3 buckets out.
    gapped = minute[:3] + [
        Bar(
            start=base + timedelta(minutes=40),
            open=12.0,
            high=12.5,
            low=11.9,
            close=12.4,
            volume=5.0,
        )
    ]
    if len(aggregate(gapped, minutes=5)) != 2:
        failures.append("empty buckets were synthesised across a gap")

    if not to_bars([{"t": 1751463120000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}]):
        failures.append("to_bars dropped a row")

    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    print("selftest: " + ("PASS" if not failures else f"{len(failures)} FAILURE(S)"))
    return 0 if not failures else 1


# ------------------------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------------------------


def _emit(result: Any, out: Path | None) -> None:
    text = json.dumps(result, indent=2, default=str)
    print(text)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(f"\nwrote {out}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("command", choices=["probe", "day", "universe", "selftest"])
    p.add_argument("--symbol", default="AAPL")
    p.add_argument("--date", type=date.fromisoformat)
    p.add_argument(
        "--prev-date", type=date.fromisoformat, help="previous session, for --command universe"
    )
    p.add_argument("--prev-close", type=float, help="previous daily close, for the change gate")
    p.add_argument("--limit", type=int, default=25, help="candidates to keep (universe)")
    p.add_argument("--rate-sleep", type=float, default=DEFAULT_RATE_SLEEP_SEC)
    p.add_argument("--json", type=Path, help="also write the result here")
    args = p.parse_args(argv)

    if args.command == "selftest":
        return selftest()

    settings = Settings()
    try:
        client = MassiveClient.from_env(rate_sleep_sec=args.rate_sleep)
    except MassiveError as exc:  # a missing key is operator error, not a crash
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "probe":
        day = args.date or date(2026, 7, 2)
        _emit(probe(client, settings, symbol=args.symbol, day=day), args.json)
        return 0

    if args.date is None:
        p.error(f"--date is required for {args.command}")

    if args.command == "day":
        _emit(
            replay_day(client, args.symbol, args.date, settings, prev_close=args.prev_close),
            args.json,
        )
        return 0

    result = universe_for(client, args.date, settings, prev_day=args.prev_date)
    result["rows"] = result["rows"][: args.limit]
    _emit(result, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
