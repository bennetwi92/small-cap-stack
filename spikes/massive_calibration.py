"""Spike #428 (Stage 2): does Massive data alone recreate the opportunities we actually saw?

``scanner_reconstruct`` asks whether the *method* works when the bars are ours. This asks the
question that decides the purchase: run the whole chain off **vendor** bars — Massive minute data
in, appearance out, ``detect_day`` over it, R out — and compare against the live tracker's own
record for the same symbol-day. Three sources of divergence are separated rather than pooled:

1. **Method** — reconstructing an appearance from bars instead of reading IBKR's scanner.
2. **Data** — Massive's consolidated tape vs the IBKR feed the tracker recorded.
3. **The change gate** — now decidable, because grouped-daily supplies the previous close.

Restricted to the **pre-market session** (04:00–09:30 ET). That is not a convenience: the paper
book's takeable window is pre-market, so this is the session the strategy actually trades, and
mixing in regular-hours behaviour would measure a strategy nobody runs.

Ground truth is ``tests/fixtures/review_cases`` — 25 trader-reviewed symbol-days carrying both the
IBKR 5-min bars and the appearance time the live scanner logged.

    # fetch once (free tier is 5 calls/min), then replay offline as often as you like
    MASSIVE_API_KEY=… python spikes/massive_calibration.py --fetch --cache data/spikes/massive
    python spikes/massive_calibration.py --cache data/spikes/massive --json out.json

The cache split is deliberate: the vendor call budget is the scarce resource, so the analysis must
be re-runnable without spending it again.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from massive_replay import MassiveClient, MassiveError, aggregate  # noqa: E402
from scanner_reconstruct import (  # noqa: E402
    PREMARKET,
    Case,
    load_fixture_cases,
    reconstruct_hit,
    trim_window,
)

from small_cap_stack.capture import Bar  # noqa: E402
from small_cap_stack.clock import ET  # noqa: E402
from small_cap_stack.config import Settings  # noqa: E402
from small_cap_stack.market_calendar import is_trading_day  # noqa: E402
from small_cap_stack.rmetrics import RMetrics, compute_r_metrics  # noqa: E402


def prev_trading_day(d: date) -> date:
    p = d - timedelta(days=1)
    while not is_trading_day(p):
        p -= timedelta(days=1)
    return p


# ------------------------------------------------------------------------------------------------
# Fetch (vendor calls) — kept separate from the analysis so the call budget is spent once
# ------------------------------------------------------------------------------------------------


def fetch_cache(cases: Sequence[Case], cache: Path, *, rate_sleep: float) -> None:
    """Pull grouped-daily previous closes + per-symbol minute bars into ``cache``."""
    cache.mkdir(parents=True, exist_ok=True)
    client = MassiveClient.from_env(rate_sleep_sec=rate_sleep)
    dates = sorted({c.trading_date for c in cases})

    grouped: dict[str, dict[str, float]] = {}
    for prev in sorted({prev_trading_day(d) for d in dates}):
        rows = client.grouped_daily(prev)
        grouped[prev.isoformat()] = {str(r["T"]): float(r["c"]) for r in rows if r.get("c")}
        print(f"grouped {prev}: {len(rows)} symbols", flush=True)
    (cache / "grouped.json").write_text(json.dumps(grouped))

    bars: dict[str, list[list[float]]] = {}
    for i, c in enumerate(cases, 1):
        key = f"{c.symbol}_{c.trading_date.isoformat()}"
        rows = client.aggregates(c.symbol, start=c.trading_date, end=c.trading_date)
        bars[key] = [
            [
                int(r["t"]),
                float(r["o"]),
                float(r["h"]),
                float(r["l"]),
                float(r["c"]),
                float(r.get("v") or 0.0),
            ]
            for r in rows
        ]
        print(f"[{i}/{len(cases)}] {key}: {len(rows)} 1-min bars", flush=True)
        (cache / "minute_bars.json").write_text(json.dumps(bars))
    print(f"api_calls={client.calls}", flush=True)


def load_cache(cache: Path) -> tuple[dict[str, dict[str, float]], dict[str, list[Bar]]]:
    grouped = json.loads((cache / "grouped.json").read_text())
    raw = json.loads((cache / "minute_bars.json").read_text())
    bars = {
        key: [
            Bar(
                start=datetime.fromtimestamp(r[0] / 1000, tz=UTC),
                open=r[1],
                high=r[2],
                low=r[3],
                close=r[4],
                volume=r[5],
            )
            for r in rows
        ]
        for key, rows in raw.items()
    }
    return grouped, bars


# ------------------------------------------------------------------------------------------------
# Analysis
# ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class VendorRow:
    """One symbol-day, three ways: what we saw, what our bars say, what Massive's bars say."""

    symbol: str
    trading_date: date
    prev_close: float | None
    actual_hit: datetime | None  # the live appearance, if it happened pre-market
    ibkr_hit: datetime | None  # reconstructed from OUR bars, change gate live
    vendor_hit: datetime | None  # reconstructed from MASSIVE bars, change gate live
    ibkr_bars: int
    vendor_bars: int
    volume_ratio: float | None  # Massive / IBKR, over the matched pre-market 5-min bars
    matched_bars: int
    ohlc_max_diff: float | None  # largest |close| disagreement on a matched bar
    actual: RMetrics  # our bars, the live appearance
    vendor: RMetrics  # Massive bars, the reconstructed appearance
    ibkr_recon: RMetrics  # our bars, the reconstructed appearance (isolates data from method)

    @property
    def delta_vendor_min(self) -> float | None:
        if self.vendor_hit is None or self.actual_hit is None:
            return None
        return round((self.vendor_hit - self.actual_hit).total_seconds() / 60.0, 2)

    @property
    def delta_ibkr_min(self) -> float | None:
        if self.ibkr_hit is None or self.actual_hit is None:
            return None
        return round((self.ibkr_hit - self.actual_hit).total_seconds() / 60.0, 2)

    @staticmethod
    def _same(a: RMetrics, b: RMetrics) -> bool:
        return (
            a.takeable == b.takeable
            and a.triggered == b.triggered
            and a.entry_index == b.entry_index
            and round(a.stop or 0, 2) == round(b.stop or 0, 2)
        )

    @property
    def same_trade(self) -> bool:
        """End to end: Massive bars + reconstructed appearance reproduce the live trade."""
        return self._same(self.actual, self.vendor)

    @property
    def same_decision(self) -> bool:
        """The weaker, and for a backtest more important, claim: same take/no-take verdict."""
        return self.actual.takeable == self.vendor.takeable


def _five_min_vendor(minute: Sequence[Bar], window: tuple[time, time]) -> list[Bar]:
    """Trim to the session FIRST, then fold — so a bucket never straddles the window edge."""
    return aggregate(trim_window(minute, window), minutes=5)


def _volume_comparison(
    ibkr: Sequence[Bar], vendor: Sequence[Bar]
) -> tuple[float | None, int, float | None]:
    """Massive-vs-IBKR volume ratio and worst close disagreement over bars they share."""
    by_start = {b.start: b for b in vendor}
    pairs = [(b, by_start[b.start]) for b in ibkr if b.start in by_start]
    if not pairs:
        return None, 0, None
    ibkr_vol = sum(a.volume for a, _ in pairs)
    vendor_vol = sum(b.volume for _, b in pairs)
    ratio = None if ibkr_vol <= 0 else round(vendor_vol / ibkr_vol, 4)
    max_diff = max(abs(a.close - b.close) for a, b in pairs)
    return ratio, len(pairs), round(max_diff, 4)


def analyse(
    cases: Sequence[Case],
    grouped: dict[str, dict[str, float]],
    vendor_minute: dict[str, list[Bar]],
    settings: Settings,
    *,
    window: tuple[time, time] = PREMARKET,
) -> list[VendorRow]:
    rows: list[VendorRow] = []
    for case in cases:
        key = f"{case.symbol}_{case.trading_date.isoformat()}"
        prev_day = prev_trading_day(case.trading_date).isoformat()
        prev_close = grouped.get(prev_day, {}).get(case.symbol)

        ibkr_bars = trim_window(case.bars, window)
        minute = vendor_minute.get(key, [])
        vendor_bars = _five_min_vendor(minute, window)

        # The live appearance only counts if it happened in the session under analysis.
        lo, hi = window
        hit_et = case.first_hit.astimezone(ET).time()
        actual_hit = case.first_hit if lo <= hit_et < hi else None

        ibkr_recon = reconstruct_hit(
            ibkr_bars, settings, symbol=case.symbol, prev_close=prev_close, window_minutes=5
        )
        # The vendor appearance is reconstructed on the MINUTE series — a true trailing 5-min
        # rolling sum, the closest analogue to IBKR's continuously-updated stVolume5minAbove.
        vendor_recon = reconstruct_hit(
            trim_window(minute, window),
            settings,
            symbol=case.symbol,
            prev_close=prev_close,
            window_minutes=5,
        )
        ratio, matched, max_diff = _volume_comparison(ibkr_bars, vendor_bars)
        rows.append(
            VendorRow(
                symbol=case.symbol,
                trading_date=case.trading_date,
                prev_close=prev_close,
                actual_hit=actual_hit,
                ibkr_hit=ibkr_recon.hit_time,
                vendor_hit=vendor_recon.hit_time,
                ibkr_bars=len(ibkr_bars),
                vendor_bars=len(vendor_bars),
                volume_ratio=ratio,
                matched_bars=matched,
                ohlc_max_diff=max_diff,
                actual=compute_r_metrics(ibkr_bars, settings, first_hit=actual_hit),
                vendor=compute_r_metrics(vendor_bars, settings, first_hit=vendor_recon.hit_time),
                ibkr_recon=compute_r_metrics(ibkr_bars, settings, first_hit=ibkr_recon.hit_time),
            )
        )
    return rows


def summarise(rows: Sequence[VendorRow]) -> dict[str, Any]:
    seen = [r for r in rows if r.actual_hit is not None]  # live pre-market appearances
    dv = [r.delta_vendor_min for r in seen if r.delta_vendor_min is not None]
    di = [r.delta_ibkr_min for r in seen if r.delta_ibkr_min is not None]
    ratios = [r.volume_ratio for r in rows if r.volume_ratio]
    diffs = [r.ohlc_max_diff for r in rows if r.ohlc_max_diff is not None]

    def stats(xs: list[float]) -> dict[str, Any]:
        if not xs:
            return {"n": 0}
        return {
            "n": len(xs),
            "median": round(statistics.median(xs), 3),
            "mean": round(statistics.fmean(xs), 3),
            "min": round(min(xs), 3),
            "max": round(max(xs), 3),
        }

    return {
        "cases": len(rows),
        "live_premarket_appearances": len(seen),
        "vendor_reconstructed": sum(1 for r in rows if r.vendor_hit),
        "vendor_reconstructed_of_seen": sum(1 for r in seen if r.vendor_hit),
        "delta_vendor_min": {**stats(dv), "within_5min": sum(1 for d in dv if abs(d) <= 5)},
        "delta_ibkr_min": {**stats(di), "within_5min": sum(1 for d in di if abs(d) <= 5)},
        "volume_ratio_massive_over_ibkr": stats([float(r) for r in ratios]),
        "close_max_diff_usd": stats([float(d) for d in diffs]),
        "same_trade_of_seen": sum(1 for r in seen if r.same_trade),
        "same_decision_of_seen": sum(1 for r in seen if r.same_decision),
        "takeable_actual": sum(1 for r in seen if r.actual.takeable),
        "takeable_vendor": sum(1 for r in seen if r.vendor.takeable),
        "max_r_actual_sum": round(sum(r.actual.max_r or 0.0 for r in seen), 3),
        "max_r_vendor_sum": round(sum(r.vendor.max_r or 0.0 for r in seen), 3),
        # A vendor-only harvest surfaces symbol-days the live tracker never showed us. Counting
        # them is the "gate-passing universe vs scanner-surfaced universe" gap the issue names.
        "vendor_hits_without_live_premarket_hit": sum(
            1 for r in rows if r.vendor_hit and r.actual_hit is None
        ),
    }


def score_implied_intervals(
    cases: Sequence[Case],
    grouped: dict[str, dict[str, float]],
    settings: Settings,
    *,
    window: tuple[time, time] = PREMARKET,
) -> dict[str, Any]:
    """Score ``implied_prev_close``'s predictions against the previous closes Massive supplies.

    ``scanner_reconstruct`` inverts the change gate to bound the previous close that would explain
    an observed appearance. That was a *prediction* made with no access to the number. Buying the
    grouped-daily data makes it checkable, which is the rare case of a spike getting to mark its own
    homework out of sample: an interval containing the true close is evidence the reconstruction
    models the live scanner correctly, and a miss localises where it does not.
    """
    from scanner_reconstruct import implied_prev_close

    hits, misses, undecided = [], [], []
    for case in cases:
        prev_day = prev_trading_day(case.trading_date).isoformat()
        truth = grouped.get(prev_day, {}).get(case.symbol)
        lo, hi = window
        if truth is None or not (lo <= case.first_hit.astimezone(ET).time() < hi):
            continue
        bars = trim_window(case.bars, window)
        implied = implied_prev_close(bars, settings, case.first_hit, window_minutes=5)
        entry = {
            "symbol": case.symbol,
            "date": case.trading_date.isoformat(),
            "truth": round(truth, 4),
            "low": None if implied.low is None else round(implied.low, 4),
            "high": None if implied.high is None else round(implied.high, 4),
        }
        if not implied.feasible or implied.high is None:
            undecided.append(entry)
        elif (implied.low or 0.0) <= truth < implied.high:
            hits.append(entry)
        else:
            misses.append(entry)
    scored = len(hits) + len(misses)
    return {
        "scored": scored,
        "contained": len(hits),
        "missed": len(misses),
        "undecided": len(undecided),
        "hit_rate": None if not scored else round(len(hits) / scored, 3),
        "hits": hits,
        "misses": misses,
        "undecided_rows": undecided,
    }


def _et(ts: datetime | None) -> str:
    return "-" if ts is None else ts.astimezone(ET).strftime("%H:%M")


def _print_table(rows: Sequence[VendorRow]) -> None:
    head = (
        f"{'symbol':<7}{'date':<12}{'prevC':>8}{'seen':>7}{'ibkr':>7}{'mssv':>7}"
        f"{'Δv':>7}{'vol×':>7}{'Δclose':>8}{'bars i/v':>10}"
        f"{'maxR(a)':>9}{'maxR(v)':>9}  same"
    )
    print(head)
    print("-" * len(head))
    for r in rows:
        print(
            f"{r.symbol:<7}{r.trading_date.isoformat():<12}"
            f"{'-' if r.prev_close is None else f'{r.prev_close:.2f}':>8}"
            f"{_et(r.actual_hit):>7}{_et(r.ibkr_hit):>7}{_et(r.vendor_hit):>7}"
            f"{'-' if r.delta_vendor_min is None else f'{r.delta_vendor_min:+.0f}':>7}"
            f"{'-' if r.volume_ratio is None else f'{r.volume_ratio:.2f}':>7}"
            f"{'-' if r.ohlc_max_diff is None else f'{r.ohlc_max_diff:.2f}':>8}"
            f"{f'{r.ibkr_bars}/{r.vendor_bars}':>10}"
            f"{'-' if r.actual.max_r is None else f'{r.actual.max_r:.2f}':>9}"
            f"{'-' if r.vendor.max_r is None else f'{r.vendor.max_r:.2f}':>9}"
            f"  {'yes' if r.same_trade else ('n/a' if r.actual_hit is None else 'NO')}"
        )


def _row_dict(r: VendorRow) -> dict[str, Any]:
    return {
        "symbol": r.symbol,
        "date": r.trading_date.isoformat(),
        "prev_close": r.prev_close,
        "actual_hit_et": _et(r.actual_hit),
        "ibkr_recon_hit_et": _et(r.ibkr_hit),
        "vendor_recon_hit_et": _et(r.vendor_hit),
        "delta_vendor_min": r.delta_vendor_min,
        "delta_ibkr_min": r.delta_ibkr_min,
        "volume_ratio": r.volume_ratio,
        "matched_bars": r.matched_bars,
        "close_max_diff": r.ohlc_max_diff,
        "premarket_bars": {"ibkr": r.ibkr_bars, "vendor": r.vendor_bars},
        "actual": {"takeable": r.actual.takeable, "max_r": r.actual.max_r, "stop": r.actual.stop},
        "vendor": {"takeable": r.vendor.takeable, "max_r": r.vendor.max_r, "stop": r.vendor.stop},
        "ibkr_recon": {"takeable": r.ibkr_recon.takeable, "max_r": r.ibkr_recon.max_r},
        "same_trade": r.same_trade,
        "same_decision": r.same_decision,
    }


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--cache", type=Path, required=True, help="dir for the fetched vendor data")
    p.add_argument("--fetch", action="store_true", help="spend vendor calls to fill the cache")
    p.add_argument("--rate-sleep", type=float, default=13.0, help="free tier is 5 calls/min")
    p.add_argument("--regular-hours", action="store_true", help="analyse 09:30–16:00 instead")
    p.add_argument("--json", type=Path)
    args = p.parse_args(argv)

    settings = Settings()
    cases = load_fixture_cases()
    if args.fetch:
        try:
            fetch_cache(cases, args.cache, rate_sleep=args.rate_sleep)
        except MassiveError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if not (args.cache / "minute_bars.json").exists():
        print(f"error: no cache at {args.cache} — run once with --fetch", file=sys.stderr)
        return 2

    window = (time(9, 30), time(16, 0)) if args.regular_hours else PREMARKET
    grouped, vendor_minute = load_cache(args.cache)
    rows = analyse(cases, grouped, vendor_minute, settings, window=window)
    rows.sort(key=lambda r: (r.trading_date, r.symbol))
    _print_table(rows)
    summary = summarise(rows)
    intervals = score_implied_intervals(cases, grouped, settings, window=window)
    summary["implied_prev_close_check"] = {
        k: v for k, v in intervals.items() if not k.endswith(("hits", "misses", "rows"))
    }
    print(f"\nwindow: {window[0]:%H:%M}-{window[1]:%H:%M} ET\nsummary:")
    print(json.dumps(summary, indent=2))
    print("\nimplied prev-close intervals vs the truth Massive supplies:")
    for entry in intervals["hits"] + intervals["misses"]:
        lo = "0" if entry["low"] is None else f"{entry['low']:.2f}"
        inside = entry in intervals["hits"]
        print(
            f"  {entry['symbol']:<6}{entry['date']}  predicted {lo}–{entry['high']:.2f}"
            f"  actual {entry['truth']:.2f}  {'HIT' if inside else 'miss'}"
        )

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "window": [f"{window[0]:%H:%M}", f"{window[1]:%H:%M}"],
                    "summary": summary,
                    "rows": [_row_dict(r) for r in rows],
                },
                indent=2,
            )
        )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
