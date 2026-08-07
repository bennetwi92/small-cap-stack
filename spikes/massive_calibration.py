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
    implied_prev_close,
    load_dashboard_cases,
    load_export_cases,
    load_fixture_cases,
    reconstruct_hit,
    rolling_window_volume,
    trim_window,
)

from small_cap_stack.capture import Bar, bar_interval  # noqa: E402
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
    """Pull grouped-daily previous closes + per-symbol minute bars into ``cache``.

    **Resumable.** At 5 calls/min the free tier turns a few hundred symbol-days into a couple of
    hours of wall clock, so anything already in the cache is skipped and every symbol is flushed to
    disk as it lands. A dropped connection then costs one symbol, not the whole harvest — the same
    reason the fetch is split from the analysis in the first place.
    """
    cache.mkdir(parents=True, exist_ok=True)
    grouped_path, bars_path = cache / "grouped.json", cache / "minute_bars.json"
    grouped: dict[str, dict[str, float]] = (
        json.loads(grouped_path.read_text()) if grouped_path.exists() else {}
    )
    bars: dict[str, list[list[float]]] = (
        json.loads(bars_path.read_text()) if bars_path.exists() else {}
    )
    client = MassiveClient.from_env(rate_sleep_sec=rate_sleep)
    dates = sorted({c.trading_date for c in cases})

    for prev in sorted({prev_trading_day(d) for d in dates}):
        if prev.isoformat() in grouped:
            continue
        rows = client.grouped_daily(prev)
        grouped[prev.isoformat()] = {str(r["T"]): float(r["c"]) for r in rows if r.get("c")}
        print(f"grouped {prev}: {len(rows)} symbols", flush=True)
        grouped_path.write_text(json.dumps(grouped))

    todo = [c for c in cases if f"{c.symbol}_{c.trading_date.isoformat()}" not in bars]
    print(f"minute bars: {len(cases) - len(todo)} cached, {len(todo)} to fetch", flush=True)
    for i, c in enumerate(todo, 1):
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
        print(f"[{i}/{len(todo)}] {key}: {len(rows)} 1-min bars", flush=True)
        bars_path.write_text(json.dumps(bars))
    print(f"api_calls={client.calls}", flush=True)


def live_window_cases(cases: Sequence[Case], window: tuple[time, time]) -> list[Case]:
    """Cases whose LIVE appearance falls inside the analysis window — the ones actually scored.

    Every headline metric (appearance delta, same-trade, same-decision) is conditioned on a live
    appearance in-window, so the out-of-window cases cost a vendor call each and feed exactly one
    number: ``vendor_hits_without_live_premarket_hit``. On a free tier metered at 5 calls/min that
    is the difference between a 7-minute harvest and a 90-minute one, so it is a flag rather than a
    default — drop it when the gate-passing-universe-vs-surfaced-universe gap is the question.
    """
    lo, hi = window
    return [c for c in cases if lo <= c.first_hit.astimezone(ET).time() < hi]


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
    # Entry bar START times, resolved against each source's OWN bar list. Comparing `entry_index`
    # across sources would be meaningless: Massive omits minutes with no trades while the IBKR
    # series is dense, so the same clock time sits at a different index in each. The wall clock is
    # the only shared coordinate.
    actual_entry_at: datetime | None
    vendor_entry_at: datetime | None
    # How precisely the LIVE appearance is known (see `Case.hit_quantum_sec`). Zero when it came
    # from raw `scanner_hits`; 300 when it came from a bar-floored dashboard marker, in which case
    # every appearance delta below is an interval rather than a point.
    hit_quantum_sec: int = 0
    # Diagnostics the divergence classifier reads — see `classify_divergence`.
    vendor_price_at_recon: float | None = None
    vendor_price_at_live: float | None = None
    vendor_vol5m_at_live: float | None = None
    ibkr_vol5m_at_live: float | None = None
    vendor_first_bar_at: datetime | None = None
    ibkr_first_bar_at: datetime | None = None
    implied_low: float | None = None
    implied_high: float | None = None
    implied_feasible: bool = False

    def _delta(self, recon: datetime | None, offset_sec: float) -> float | None:
        if recon is None or self.actual_hit is None:
            return None
        truth = self.actual_hit + timedelta(seconds=offset_sec)
        return round((recon - truth).total_seconds() / 60.0, 2)

    @property
    def delta_vendor_min(self) -> float | None:
        """Point estimate: reconstruction minus the MIDDLE of the appearance's uncertainty band.

        With an exact ground truth (``hit_quantum_sec == 0``) this is the plain difference. With a
        bar-floored one it is the minimum-bias estimator under a uniform prior over where inside the
        bar the appearance fell — and that prior is not an assumption, it is measured: on
        2026-08-03, where both the raw and the floored appearance are published, the 61 offsets run
        58–298s with a median of 178s against the 150s a uniform prior predicts.
        """
        return self._delta(self.vendor_hit, self.hit_quantum_sec / 2.0)

    @property
    def delta_vendor_bounds(self) -> tuple[float, float] | None:
        """Hard bounds on the appearance delta: no distributional assumption, just the quantum."""
        lo = self._delta(self.vendor_hit, self.hit_quantum_sec)
        hi = self._delta(self.vendor_hit, 0.0)
        return None if lo is None or hi is None else (lo, hi)

    @property
    def within_5min(self) -> bool | None:
        """``True``/``False`` only when the quantum cannot flip the verdict, else ``None``."""
        b = self.delta_vendor_bounds
        if b is None:
            return None
        lo, hi = b
        if abs(lo) <= 5 and abs(hi) <= 5:
            return True
        if min(abs(lo), abs(hi)) > 5 and lo * hi > 0:
            return False
        return None

    @property
    def delta_ibkr_min(self) -> float | None:
        return self._delta(self.ibkr_hit, self.hit_quantum_sec / 2.0)

    @property
    def same_trade(self) -> bool:
        """End to end: Massive bars + reconstructed appearance reproduce the live trade.

        Same decision, same entry *bar time*, same stop to the cent — the three things that decide
        whether a backtested trade is the trade we actually had.
        """
        a, b = self.actual, self.vendor
        return (
            a.takeable == b.takeable
            and a.triggered == b.triggered
            and self.actual_entry_at == self.vendor_entry_at
            and round(a.stop or 0, 2) == round(b.stop or 0, 2)
        )

    @property
    def same_decision(self) -> bool:
        """The weaker, and for a backtest more important, claim: same take/no-take verdict."""
        return self.actual.takeable == self.vendor.takeable


#: Divergence mechanisms, in the order :func:`classify_divergence` tests them. The point of the
#: whole exercise is the size of ``unexplained-*``: everything else is a *known* difference between
#: a reconstruction and the live scanner, which a backtest can correct for or at least bound, while
#: an unexplained divergence is an error term with no model behind it.
MECHANISMS = (
    "matched",
    "rank-cap",
    "change-reference",
    "volume-basis",
    "coverage",
    "no-vendor-hit",
    "unexplained-early",
    "unexplained-late",
)


def classify_divergence(r: VendorRow, settings: Settings) -> tuple[str, str]:
    """Attribute an appearance divergence >5 min to a mechanism, with the evidence for it.

    Each branch is a *falsifiable* test against the bars, not a label of convenience:

    - **rank-cap** (#432) — the reconstruction fired EARLY at a price at or above the price at the
      live appearance. That ordering is the proof: the change gate is monotone in price, so **no**
      previous-close reference, however chosen, can rank the later/lower bar ahead of the earlier/
      higher one. Only a capacity limit — ``TOP_PERC_GAIN`` returning 50 rows, the symbol waiting
      for the field to thin — puts the live appearance after a bar that already passed every gate.
    - **change-reference** (#433) — the divergence is exactly what a different previous close
      would fix, and the implied interval says so: LATE and our close sits at/above the interval
      (a lower reference, which is what IBKR is measured to use, fires earlier), or EARLY and our
      close sits below it (a higher reference fires later).
    - **volume-basis** — at the live appearance the vendor's trailing 5-min volume had not yet
      cleared ``scan_min_5m_volume`` while ours had. The two tapes disagree about when 100k
      printed, so the appearance moves for reasons of tape composition, not method.
    - **coverage** — Massive's pre-market tape simply starts after the live appearance, so the
      reconstruction could not have fired on time whatever the gates said.
    - **unexplained-early / unexplained-late** — none of the above fits. This is the honest error
      bar on a multi-year backtest, and the number worth quoting.
    """
    if r.actual_hit is None:
        return "matched", "not scored (no live in-window appearance)"
    if r.vendor_hit is None:
        return "no-vendor-hit", "vendor bars never passed every gate in-window"
    d = r.delta_vendor_min
    if d is None:
        return "no-vendor-hit", "no delta"
    if r.within_5min is not False:
        return "matched", f"Δ={d:+.1f} min within the bar-grid tolerance"

    if d < 0:  # the reconstruction surfaced it before the live scanner did
        pr, pl_ = r.vendor_price_at_recon, r.vendor_price_at_live
        if pr is not None and pl_ is not None and pr >= pl_:
            return (
                "rank-cap",
                f"gates passed at ${pr:.2f} {abs(d):.0f} min before the live appearance at "
                f"${pl_:.2f} — a higher price earlier, so no change reference reorders it",
            )
        if r.implied_feasible and r.implied_low and r.prev_close and r.implied_low > r.prev_close:
            gap = r.implied_low / r.prev_close - 1.0
            return (
                "change-reference",
                f"a prev close ≥${r.implied_low:.2f} ({gap:+.2%} vs Massive's "
                f"${r.prev_close:.2f}) delays the reconstruction onto the live bar",
            )
        return "unexplained-early", f"Δ={d:+.1f} min early; no price ordering or reference explains"

    # LATE: the reconstruction surfaced it after the live scanner did
    if (
        r.vendor_first_bar_at is not None
        and r.actual_hit is not None
        and r.vendor_first_bar_at > r.actual_hit
    ):
        return (
            "coverage",
            f"Massive's first pre-market bar is {r.vendor_first_bar_at.astimezone(ET):%H:%M}, "
            f"after the live appearance — the tape cannot fire on time",
        )
    if r.implied_feasible and r.implied_high and r.prev_close and r.prev_close >= r.implied_high:
        gap = 1.0 - r.implied_high / r.prev_close
        return (
            "change-reference",
            f"a prev close <${r.implied_high:.2f} ({gap:.2%} below Massive's "
            f"${r.prev_close:.2f}) fires on the live bar; #433 measures IBKR's reference low",
        )
    if (
        r.vendor_vol5m_at_live is not None
        and r.vendor_vol5m_at_live <= settings.scan_min_5m_volume
        and (r.ibkr_vol5m_at_live or 0.0) > settings.scan_min_5m_volume
    ):
        return (
            "volume-basis",
            f"at the live appearance Massive's trailing 5-min volume was "
            f"{r.vendor_vol5m_at_live:,.0f} vs our {r.ibkr_vol5m_at_live:,.0f} — "
            f"the 100k gate had not cleared on the vendor tape",
        )
    return "unexplained-late", f"Δ={d:+.1f} min late; no coverage, reference or volume gap explains"


def _entry_at(bars: Sequence[Bar], m: RMetrics) -> datetime | None:
    """The entry bar's start time in ``bars`` — the cross-source-comparable coordinate."""
    if m.entry_index is None or m.entry_index >= len(bars):
        return None
    return bars[m.entry_index].start


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


def _bar_at(bars: Sequence[Bar], moment: datetime | None) -> int | None:
    """Index of the first bar whose CLOSE is at/after ``moment`` — where a scan could first see it.

    Joined on wall-clock, never on index: Massive omits minutes with no trades where the IBKR series
    is dense, so the same instant sits at different offsets in the two lists.
    """
    if moment is None or not bars:
        return None
    interval = bar_interval(bars)
    return next((i for i, b in enumerate(bars) if b.start + interval >= moment), None)


def _price_at(bars: Sequence[Bar], moment: datetime | None) -> float | None:
    i = _bar_at(bars, moment)
    return None if i is None else bars[i].close


def _vol5m_at(bars: Sequence[Bar], moment: datetime | None) -> float | None:
    i = _bar_at(bars, moment)
    return None if i is None else rolling_window_volume(bars, minutes=5)[i]


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
        in_window = lo <= hit_et < hi
        actual_hit = case.first_hit if in_window else None
        # `first_hit` is the FLOOR of the appearance when the source quantised it, and the R-metrics
        # must be gated on an instant strictly inside that band, never on the floor itself — see
        # `Case.gating_hit`. Reported deltas still use the floor plus an explicit offset.
        gating_hit = case.gating_hit if in_window else None

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
        actual_m = compute_r_metrics(ibkr_bars, settings, first_hit=gating_hit)
        vendor_m = compute_r_metrics(vendor_bars, settings, first_hit=vendor_recon.hit_time)
        vendor_pm = trim_window(minute, window)
        implied = (
            implied_prev_close(ibkr_bars, settings, gating_hit, window_minutes=5)
            if gating_hit is not None
            else None
        )
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
                actual=actual_m,
                vendor=vendor_m,
                ibkr_recon=compute_r_metrics(ibkr_bars, settings, first_hit=ibkr_recon.hit_time),
                actual_entry_at=_entry_at(ibkr_bars, actual_m),
                vendor_entry_at=_entry_at(vendor_bars, vendor_m),
                hit_quantum_sec=case.hit_quantum_sec,
                vendor_price_at_recon=_price_at(vendor_pm, vendor_recon.hit_time),
                vendor_price_at_live=_price_at(vendor_pm, gating_hit),
                vendor_vol5m_at_live=_vol5m_at(vendor_pm, gating_hit),
                ibkr_vol5m_at_live=_vol5m_at(ibkr_bars, gating_hit),
                vendor_first_bar_at=vendor_pm[0].start if vendor_pm else None,
                ibkr_first_bar_at=ibkr_bars[0].start if ibkr_bars else None,
                implied_low=None if implied is None else implied.low,
                implied_high=None if implied is None else implied.high,
                implied_feasible=bool(implied and implied.feasible),
            )
        )
    return rows


def summarise(rows: Sequence[VendorRow], settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or Settings()
    seen = [r for r in rows if r.actual_hit is not None]  # live pre-market appearances
    mech: dict[str, int] = dict.fromkeys(MECHANISMS, 0)
    for r in seen:
        mech[classify_divergence(r, settings)[0]] += 1
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
        # `within_5min` is a TRI-state count, not a rate: a bar-floored ground truth leaves some
        # cases genuinely undecidable, and folding those into either bucket would invent precision.
        "delta_vendor_min": {
            **stats(dv),
            "within_5min": sum(1 for r in seen if r.within_5min is True),
            "outside_5min": sum(1 for r in seen if r.within_5min is False),
            "undecidable": sum(1 for r in seen if r.within_5min is None and r.vendor_hit),
            "quantised_truth": sum(1 for r in seen if r.hit_quantum_sec),
        },
        "delta_ibkr_min": {**stats(di), "within_5min": sum(1 for d in di if abs(d) <= 5)},
        "divergence_mechanism": {k: v for k, v in mech.items() if v},
        "unexplained": mech["unexplained-early"] + mech["unexplained-late"],
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
    hits, misses, undecided = [], [], []
    for case in cases:
        prev_day = prev_trading_day(case.trading_date).isoformat()
        truth = grouped.get(prev_day, {}).get(case.symbol)
        lo, hi = window
        if truth is None or not (lo <= case.first_hit.astimezone(ET).time() < hi):
            continue
        bars = trim_window(case.bars, window)
        # `gating_hit`, not `first_hit`: the solver picks the first eligible bar CLOSING at/after
        # the appearance, and bar ends sit on the same grid a floored marker does — so feeding it
        # the floor would target the bar before the right one and shift every interval a bar early.
        implied = implied_prev_close(bars, settings, case.gating_hit, window_minutes=5)
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


def _print_table(rows: Sequence[VendorRow], settings: Settings) -> None:
    head = (
        f"{'symbol':<7}{'date':<12}{'prevC':>8}{'seen':>7}{'ibkr':>7}{'mssv':>7}"
        f"{'Δv':>7}{'vol×':>7}{'Δclose':>8}{'bars i/v':>10}"
        f"{'ent(a)':>8}{'ent(v)':>8}{'maxR(a)':>9}{'maxR(v)':>9}  {'same':<5}{'mechanism':<18}"
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
            f"{_et(r.actual_entry_at):>8}{_et(r.vendor_entry_at):>8}"
            f"{'-' if r.actual.max_r is None else f'{r.actual.max_r:.2f}':>9}"
            f"{'-' if r.vendor.max_r is None else f'{r.vendor.max_r:.2f}':>9}"
            f"  {('yes' if r.same_trade else ('n/a' if r.actual_hit is None else 'NO')):<5}"
            f"{classify_divergence(r, settings)[0]:<18}"
        )


def _row_dict(r: VendorRow, settings: Settings) -> dict[str, Any]:
    mech, why = classify_divergence(r, settings)
    bounds = r.delta_vendor_bounds
    return {
        "mechanism": mech,
        "mechanism_detail": why,
        "hit_quantum_sec": r.hit_quantum_sec,
        "delta_vendor_bounds_min": None if bounds is None else list(bounds),
        "within_5min": r.within_5min,
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
        "actual_entry_et": _et(r.actual_entry_at),
        "vendor_entry_et": _et(r.vendor_entry_at),
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
    p.add_argument(
        "--cases",
        choices=["fixtures", "dashboard", "export"],
        default="fixtures",
        help="ground-truth source (fixtures = the 25 review cases, the regression baseline)",
    )
    p.add_argument(
        "--charts-dir", type=Path, help="dashboard-data charts/ dir, with --cases dashboard"
    )
    p.add_argument(
        "--stats", type=Path, help="dashboard-data stats.json — exact hits for its one date"
    )
    p.add_argument("--export-dir", type=Path, help="data-export parquet dir, with --cases export")
    p.add_argument(
        "--dates",
        type=lambda s: [date.fromisoformat(x) for x in s.split(",")],
        help="comma-separated trading dates to keep",
    )
    p.add_argument(
        "--live-window-only",
        action="store_true",
        help="keep only cases whose live appearance is in-window (see live_window_cases)",
    )
    p.add_argument("--json", type=Path)
    args = p.parse_args(argv)

    window = (time(9, 30), time(16, 0)) if args.regular_hours else PREMARKET
    settings = Settings()
    if args.cases == "dashboard":
        if args.charts_dir is None:
            p.error("--cases dashboard requires --charts-dir")
        cases = load_dashboard_cases(args.charts_dir, dates=args.dates, stats=args.stats)
    elif args.cases == "export":
        if args.export_dir is None:
            p.error("--cases export requires --export-dir")
        cases = load_export_cases(args.export_dir, dates=args.dates)
    else:
        cases = load_fixture_cases()
    if args.live_window_only:
        cases = live_window_cases(cases, window)
    if not cases:
        print("error: no ground-truth cases", file=sys.stderr)
        return 2
    if args.fetch:
        try:
            fetch_cache(cases, args.cache, rate_sleep=args.rate_sleep)
        except MassiveError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if not (args.cache / "minute_bars.json").exists():
        print(f"error: no cache at {args.cache} — run once with --fetch", file=sys.stderr)
        return 2

    grouped, vendor_minute = load_cache(args.cache)
    rows = analyse(cases, grouped, vendor_minute, settings, window=window)
    rows.sort(key=lambda r: (r.trading_date, r.symbol))
    _print_table(rows, settings)
    summary = summarise(rows, settings)
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
                    "rows": [_row_dict(r, settings) for r in rows],
                },
                indent=2,
            )
        )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
