"""Spike #428 (Stage 1–2 core): reconstruct a scanner appearance from OHLCV bars alone.

Nobody sells historical scanner output — IBKR doesn't archive it. But the three hard scan gates are
all price-derived (`scan_min_price`/`scan_max_price`, `scan_change_pct`, `scan_min_5m_volume`;
float and news are *collected*, not gated), so an appearance is reconstructible from bars. This
module is that reconstruction, kept **deliberately vendor-agnostic**: it takes a list of
:class:`~small_cap_stack.capture.Bar` and knows nothing about where they came from. The Massive
(ex-Polygon) adapter lives next door in ``massive_replay.py`` and calls into here; the calibration
below runs on bars we already own, so it needs no vendor and no API key at all.

**The question this answers.** "Can I recreate the seen opportunities?" — i.e. if I had only bars,
would I have surfaced the same symbol at (near enough) the same time, and would the engine then
have found the same trade? The answer decides whether a multi-year Massive backtest measures the
same thing the live tracker measures. Run it against the 25 committed review cases:

    python spikes/scanner_reconstruct.py --fixtures
    python spikes/scanner_reconstruct.py --fixtures --json data/spikes/recon-fixtures.json

...or against a live store slice on the box / Mac (never the cloud — no store there):

    python spikes/scanner_reconstruct.py --store /data --date 2026-07-02

## The modelling, and where it is knowingly wrong

1. **Appearance time = the bar's END, not its start.** The scanner can only surface a symbol once
   the trailing-5-min volume actually clears 100k, and a bar's volume is only known when it closes.
   ``hit_time = bar.start + interval``.
2. **The live scanner's window is ROLLING; ours is bar-aligned.** IBKR's ``stVolume5minAbove`` is a
   continuously-updated trailing 5 minutes, so the true crossing can happen mid-bar. Reconstructing
   off 5-min bars is therefore **late by 0–5 min**; off 1-min bars (the vendor path — that is why
   ``massive_replay`` pulls 1-min and aggregates *afterwards*) it is late by 0–1 min.
   :func:`rolling_window_volume` handles both: it sums whatever bars fall inside the trailing
   window, so on a 5-min grid it degenerates to the bar's own volume.
3. **The live side is late too**, by up to one ``tick_interval_sec`` (60s) of scan cadence, plus
   the IBKR 50-row cap on a busy morning. Those push the *actual* ``first_hit`` later. So the two
   biases fight, and the sign of the measured delta is not knowable a priori — which is the whole
   reason to measure it rather than assume it.
4. **The change gate needs the previous daily close**, which bars for a single day do not carry.
   Where it is unknown the gate abstains rather than fails, and the reconstruction records which
   gate was **binding** — the last one to come true. Measured over the 25 review cases, the missing
   change gate is *not* a detail: without it the reconstruction fires a median 18 min early, and on
   6 of 25 it fires on the very first bar of the day. :func:`implied_prev_close` therefore inverts
   the gate — solving for the previous close that would reproduce the observed appearance — so the
   one number we lack is falsifiable rather than merely absent. Supplying it is cheap: the vendor's
   grouped-daily endpoint carries it for every symbol, which is why the harvest pulls that first.
5. **Price = the bar's close** — the scanner filters on last price, which at a bar boundary is the
   close.

The reconstruction is a pure function of bars + Settings, so it is replayable and the methodology
can change retroactively — the same store-raw/compute-on-read contract the rest of the repo keeps.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from small_cap_stack.capture import Bar, bar_interval
from small_cap_stack.clock import ET, within_window
from small_cap_stack.config import Settings
from small_cap_stack.rmetrics import RMetrics, compute_r_metrics

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "review_cases"

# The gates the scanner itself applies, in the order they are reported. `float` and `news` are
# deliberately absent: they are collected downstream and never filter the IBKR scan (see #428).
SCAN_GATES = ("price", "change_pct", "volume_5m", "trading_window")

# How close a bars-only appearance has to land to count as "already right". One 5-min bar plus the
# 60s live scan cadence is the irreducible grid error (module doc, points 2 and 3), so anything
# inside it is noise rather than a divergence worth explaining.
BLIND_TOLERANCE_MIN = 5.0


# ------------------------------------------------------------------------------------------------
# Reconstruction
# ------------------------------------------------------------------------------------------------


def rolling_window_volume(bars: Sequence[Bar], minutes: int = 5) -> list[float]:
    """Trailing ``minutes`` of volume as at each bar's END, one value per bar.

    Generic over the bar grid on purpose (see the module doc, point 2): on 5-min bars each value is
    just that bar's volume; on 1-min bars it is a true 5-bar rolling sum, which is the closest a
    replay gets to IBKR's continuously-updated ``stVolume5minAbove``. A bar contributes when its
    whole span lies inside the window ending at the current bar's close, so a gap in the tape
    shrinks the window's contents rather than reaching further back in time for them.
    """
    window = timedelta(minutes=minutes)
    interval = bar_interval(bars)
    out: list[float] = []
    start = 0
    for i, bar in enumerate(bars):
        end = bar.start + interval
        while start < i and bars[start].start < end - window:
            start += 1
        out.append(sum(b.volume for b in bars[start : i + 1]))
    return out


@dataclass(frozen=True)
class GateTrace:
    """One bar's worth of scan-gate evaluation — the audit trail behind an appearance time."""

    idx: int
    hit_time: datetime  # the bar's END: the earliest instant this bar's volume is knowable
    price: float  # bar close
    change_pct: float | None  # None when the previous daily close is unknown
    volume_5m: float
    price_ok: bool
    change_ok: bool | None
    volume_ok: bool
    window_ok: bool

    @property
    def all_ok(self) -> bool:
        """Every *decidable* gate passes. An undecidable change gate abstains, it does not fail."""
        return self.price_ok and self.volume_ok and self.window_ok and self.change_ok is not False


@dataclass(frozen=True)
class Reconstruction:
    """What a bars-only scanner would have surfaced for one symbol-day."""

    symbol: str
    trading_date: date
    hit_idx: int | None
    hit_time: datetime | None
    binding_gate: str | None  # the last gate to come true — what actually delayed the appearance
    first_pass: dict[str, int | None]  # per-gate, the first bar index at which it passes
    change_decidable: bool
    n_bars: int
    trace: tuple[GateTrace, ...]

    @property
    def found(self) -> bool:
        return self.hit_idx is not None


def _gate_trace(
    bars: Sequence[Bar], settings: Settings, *, prev_close: float | None, window_minutes: int
) -> list[GateTrace]:
    vols = rolling_window_volume(bars, minutes=window_minutes)
    interval = bar_interval(bars)
    traces: list[GateTrace] = []
    for i, bar in enumerate(bars):
        end = bar.start + interval
        change = None if prev_close in (None, 0) else (bar.close / float(prev_close) - 1.0) * 100.0
        traces.append(
            GateTrace(
                idx=i,
                hit_time=end,
                price=bar.close,
                change_pct=change,
                volume_5m=vols[i],
                price_ok=settings.scan_min_price <= bar.close <= settings.scan_max_price,
                change_ok=None if change is None else change > settings.scan_change_pct,
                volume_ok=vols[i] > settings.scan_min_5m_volume,
                # The window is tested at the appearance instant, matching `trading_window_gate`.
                window_ok=within_window(end.astimezone(ET), settings.scan_start, settings.scan_end),
            )
        )
    return traces


def reconstruct_hit(
    bars: Sequence[Bar],
    settings: Settings,
    *,
    symbol: str = "",
    trading_date: date | None = None,
    prev_close: float | None = None,
    window_minutes: int = 5,
) -> Reconstruction:
    """The first bar at which every decidable scan gate passes — the reconstructed appearance.

    ``prev_close`` is the previous session's daily close; without it the change gate abstains (see
    the module doc, point 4). ``window_minutes`` is the trailing volume window (5, matching
    ``stVolume5minAbove``) — it is a parameter only so the sensitivity can be swept, not tuned.
    """
    trace = _gate_trace(bars, settings, prev_close=prev_close, window_minutes=window_minutes)
    hit = next((t for t in trace if t.all_ok), None)
    flags = {
        "price": [t.price_ok for t in trace],
        "change_pct": [t.change_ok is not False for t in trace],
        "volume_5m": [t.volume_ok for t in trace],
        "trading_window": [t.window_ok for t in trace],
    }
    first_pass: dict[str, int | None] = {
        name: next((i for i, ok in enumerate(oks) if ok), None) for name, oks in flags.items()
    }
    # The binding gate is the one whose first *sustained-to-the-hit* pass lands latest: with the hit
    # bar in hand, ask which gates were still false on the bar before it. That is the honest answer
    # to "what delayed the appearance", and it is what decides whether the missing change gate
    # matters (module doc, point 4).
    binding: str | None = None
    if hit is not None:
        prior = trace[hit.idx - 1] if hit.idx > 0 else None
        if prior is None:
            binding = "none"  # gates were already true at the first bar we can see
        else:
            still_false = [
                name
                for name, ok in (
                    ("price", prior.price_ok),
                    ("change_pct", prior.change_ok is not False),
                    ("volume_5m", prior.volume_ok),
                    ("trading_window", prior.window_ok),
                )
                if not ok
            ]
            binding = "+".join(still_false) if still_false else "none"
    return Reconstruction(
        symbol=symbol,
        trading_date=trading_date or (bars[0].start.astimezone(ET).date() if bars else date.min),
        hit_idx=hit.idx if hit else None,
        hit_time=hit.hit_time if hit else None,
        binding_gate=binding,
        first_pass=first_pass,
        change_decidable=prev_close is not None,
        n_bars=len(bars),
        trace=tuple(trace),
    )


# ------------------------------------------------------------------------------------------------
# Calibration: reconstructed appearance vs the one the live tracker actually recorded
# ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One symbol-day of ground truth: the bars we saw and the appearance we actually logged."""

    symbol: str
    trading_date: date
    first_hit: datetime
    bars: list[Bar]
    prev_close: float | None = None


def _et(ts: datetime | None) -> str:
    return "-" if ts is None else ts.astimezone(ET).strftime("%H:%M:%S")


@dataclass(frozen=True)
class ImpliedPrevClose:
    """The previous daily close(s) that would make a bars-only scan fire when the live one did.

    The change gate is the one scan filter a single day's bars cannot decide (module doc, point 4),
    and without it the reconstruction fires **early**. Rather than assume that away, invert it: the
    price / volume / window gates are independent of the previous close, so they define a set *S*
    of bars that are eligible at all. The change gate then picks the first bar in *S* whose close
    clears ``prev_close × (1 + scan_change_pct/100)``. Requiring that bar to be the observed one
    pins ``prev_close`` to a half-open interval — and if that interval is EMPTY, no previous close
    explains the observation and something outside this model (the IBKR 50-row cap, the vendor
    volume basis, a mid-bar rolling crossing) is responsible.

    That makes the missing input falsifiable instead of merely absent: a feasible interval means
    the reconstruction is *consistent* with what the tracker saw, pending one number the vendor's
    grouped-daily endpoint supplies for free.
    """

    target_idx: int | None  # the bar the reconstruction would have to fire on
    low: float | None  # inclusive: prev_close must be at least this
    high: float | None  # exclusive: ...and below this
    feasible: bool
    implied_change_pct: float | None  # day change at the target bar, at the interval's low end

    @property
    def label(self) -> str:
        if self.target_idx is None:
            return "no-target"
        if not self.feasible:
            return "INFEASIBLE"
        lo = "0" if not self.low else f"{self.low:.2f}"
        return f"{lo}–{self.high:.2f}" if self.high else lo


def implied_prev_close(
    bars: Sequence[Bar],
    settings: Settings,
    actual_hit: datetime,
    *,
    window_minutes: int = 5,
) -> ImpliedPrevClose:
    """Solve for the previous close consistent with ``actual_hit``.

    See :class:`ImpliedPrevClose` for what the interval means and why it is worth computing.
    """
    trace = _gate_trace(bars, settings, prev_close=None, window_minutes=window_minutes)
    eligible = [t for t in trace if t.price_ok and t.volume_ok and t.window_ok]
    if not eligible:
        return ImpliedPrevClose(None, None, None, False, None)
    # The reconstruction fires at a bar END, so it can never beat the live appearance: the bar the
    # live hit must map to is the first eligible one closing at/after it.
    target = next((t for t in eligible if t.hit_time >= actual_hit), None)
    if target is None:
        return ImpliedPrevClose(None, None, None, False, None)
    mult = 1.0 + settings.scan_change_pct / 100.0
    priors = [t.price for t in eligible if t.idx < target.idx]
    low = max(priors) / mult if priors else None  # every earlier eligible bar must NOT have fired
    high = target.price / mult  # ...and the target one must
    feasible = low is None or low < high
    change = (target.price / low - 1.0) * 100.0 if (feasible and low) else None
    return ImpliedPrevClose(
        target_idx=target.idx,
        low=low,
        high=high,
        feasible=feasible,
        implied_change_pct=round(change, 2) if change is not None else None,
    )


def _entry_time(bars: Sequence[Bar], m: RMetrics) -> str:
    if m.entry_index is None or m.entry_index >= len(bars):
        return "-"
    return _et(bars[m.entry_index].start)[:5]


@dataclass(frozen=True)
class CalibrationRow:
    """Live-vs-reconstructed for one case: the appearance delta, and what it did to the trade."""

    symbol: str
    trading_date: date
    actual_hit: datetime
    recon_hit: datetime | None
    delta_min: float | None  # recon − actual, in minutes; positive = reconstruction is LATE
    binding_gate: str | None
    change_decidable: bool
    actual: RMetrics
    recon: RMetrics
    # The same replay again, but with the appearance pinned to the bar a *known* previous close
    # would have fired on (see :func:`implied_prev_close`). This is the reconstruction the paid
    # vendor path actually gets, since grouped-daily bars carry the previous close.
    pinned: RMetrics
    implied: ImpliedPrevClose
    actual_entry: str
    recon_entry: str
    pinned_entry: str

    @staticmethod
    def _same(a: RMetrics, b: RMetrics) -> bool:
        return (
            a.takeable == b.takeable
            and a.triggered == b.triggered
            and a.entry_index == b.entry_index
            and a.stop == b.stop
            and a.entry_fill == b.entry_fill
        )

    @property
    def same_trade(self) -> bool:
        """The change-gate-blind reconstruction produces the same decision AND geometry."""
        return self._same(self.actual, self.recon)

    @property
    def same_trade_pinned(self) -> bool:
        """The change-gate-aware reconstruction does — the number the go/no-go turns on."""
        return self._same(self.actual, self.pinned)

    @property
    def same_max_r(self) -> bool:
        return self.actual.max_r == self.recon.max_r

    @property
    def same_max_r_pinned(self) -> bool:
        return self.actual.max_r == self.pinned.max_r

    @property
    def verdict(self) -> str:
        """Why this case diverges — the classification the go/no-go actually reads.

        - ``matched-blind``: bars alone already land within one bar-grid of the live appearance, so
          the missing change gate never bound and nothing needs explaining.
        - ``change-gate``: bars alone fire early, and a *feasible* previous close (the one number
          the vendor's grouped-daily endpoint supplies) puts the appearance back on the right bar.
        - ``unexplained``: bars alone fire early and NO previous close explains it. Something
          outside this model moved it — the IBKR 50-row cap on a busy morning, or a volume basis
          that disagrees with ours. These are the cases that bound how far a backtest transfers.
        """
        if self.recon_hit is None or self.delta_min is None:
            return "no-hit"
        if abs(self.delta_min) <= BLIND_TOLERANCE_MIN:
            return "matched-blind"
        return "change-gate" if self.implied.feasible else "unexplained"


def calibrate_case(case: Case, settings: Settings, *, window_minutes: int = 5) -> CalibrationRow:
    """Replay one case twice — once off the logged appearance, once off the reconstructed one."""
    recon = reconstruct_hit(
        case.bars,
        settings,
        symbol=case.symbol,
        trading_date=case.trading_date,
        prev_close=case.prev_close,
        window_minutes=window_minutes,
    )
    implied = implied_prev_close(case.bars, settings, case.first_hit, window_minutes=window_minutes)
    pinned_hit = (
        case.bars[implied.target_idx].start + bar_interval(case.bars)
        if implied.target_idx is not None
        else recon.hit_time
    )
    actual_m = compute_r_metrics(case.bars, settings, first_hit=case.first_hit)
    recon_m = compute_r_metrics(case.bars, settings, first_hit=recon.hit_time)
    pinned_m = compute_r_metrics(case.bars, settings, first_hit=pinned_hit)
    delta = (
        None
        if recon.hit_time is None
        else round((recon.hit_time - case.first_hit).total_seconds() / 60.0, 2)
    )
    return CalibrationRow(
        symbol=case.symbol,
        trading_date=case.trading_date,
        actual_hit=case.first_hit,
        recon_hit=recon.hit_time,
        delta_min=delta,
        binding_gate=recon.binding_gate,
        change_decidable=recon.change_decidable,
        actual=actual_m,
        recon=recon_m,
        pinned=pinned_m,
        implied=implied,
        actual_entry=_entry_time(case.bars, actual_m),
        recon_entry=_entry_time(case.bars, recon_m),
        pinned_entry=_entry_time(case.bars, pinned_m),
    )


def summarise(rows: Sequence[CalibrationRow]) -> dict[str, Any]:
    """Aggregate the calibration into the numbers the go/no-go actually turns on."""
    deltas = [r.delta_min for r in rows if r.delta_min is not None]
    binding: dict[str, int] = {}
    for r in rows:
        key = r.binding_gate or "no-hit"
        binding[key] = binding.get(key, 0) + 1
    matched = [r for r in rows if r.same_trade]
    takeable_actual = [r for r in rows if r.actual.takeable]
    takeable_recon = [r for r in rows if r.recon.takeable]
    return {
        "cases": len(rows),
        "reconstructed": len(deltas),
        "missed": len(rows) - len(deltas),
        "delta_min": {
            "median": round(statistics.median(deltas), 2) if deltas else None,
            "mean": round(statistics.fmean(deltas), 2) if deltas else None,
            "min": min(deltas) if deltas else None,
            "max": max(deltas) if deltas else None,
            "within_5min": sum(1 for d in deltas if abs(d) <= 5),
            "early": sum(1 for d in deltas if d < 0),
            "late": sum(1 for d in deltas if d > 0),
        },
        "binding_gate": dict(sorted(binding.items(), key=lambda kv: -kv[1])),
        "verdict": {
            v: sum(1 for r in rows if r.verdict == v)
            for v in ("matched-blind", "change-gate", "unexplained", "no-hit")
        },
        "explainable": sum(1 for r in rows if r.verdict in ("matched-blind", "change-gate")),
        # Change-gate-BLIND reconstruction (no previous close): the free-tier / bars-only ceiling.
        "blind": {
            "same_trade": len(matched),
            "same_max_r": sum(1 for r in rows if r.same_max_r),
            "takeable_recon": len(takeable_recon),
            "takeable_agreement": sum(1 for r in rows if r.actual.takeable == r.recon.takeable),
            "max_r_sum": round(sum(r.recon.max_r or 0.0 for r in rows), 3),
        },
        # Change-gate-AWARE reconstruction (previous close known — what the vendor path gets).
        "pinned": {
            "feasible": sum(1 for r in rows if r.implied.feasible),
            "infeasible": sum(1 for r in rows if not r.implied.feasible),
            "same_trade": sum(1 for r in rows if r.same_trade_pinned),
            "same_max_r": sum(1 for r in rows if r.same_max_r_pinned),
            "takeable_recon": sum(1 for r in rows if r.pinned.takeable),
            "takeable_agreement": sum(1 for r in rows if r.actual.takeable == r.pinned.takeable),
            "max_r_sum": round(sum(r.pinned.max_r or 0.0 for r in rows), 3),
        },
        "takeable_actual": len(takeable_actual),
        "max_r_actual_sum": round(sum(r.actual.max_r or 0.0 for r in rows), 3),
    }


# ------------------------------------------------------------------------------------------------
# Case sources
# ------------------------------------------------------------------------------------------------


def _bars_from_rows(rows: Iterable[Sequence[Any]]) -> list[Bar]:
    return [
        Bar(
            start=datetime.fromisoformat(str(r[0])),
            open=float(r[1]),
            high=float(r[2]),
            low=float(r[3]),
            close=float(r[4]),
            volume=float(r[5]),
        )
        for r in rows
    ]


def load_fixture_cases(directory: Path = FIXTURE_DIR) -> list[Case]:
    """The 25 committed review cases — real bars plus the appearance the tracker actually logged.

    These are trader-reviewed regression inputs (``tests/fixtures/review_cases``, the documented
    exception to never-commit-data), which makes them the one ground-truth set available to a
    session with no store and no vendor key.
    """
    cases: list[Case] = []
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text())
        cases.append(
            Case(
                symbol=str(raw["symbol"]),
                trading_date=date.fromisoformat(str(raw["date"])),
                first_hit=datetime.fromisoformat(str(raw["first_hit"])),
                bars=_bars_from_rows(raw["bars"]),
            )
        )
    return cases


def load_store_cases(data_dir: Path, trading_date: date, settings: Settings) -> list[Case]:
    """Every opportunity on ``trading_date`` from a live Parquet store (box / Mac only)."""
    from small_cap_stack.report import day_chart_bars, day_opportunities  # local: store-only path
    from small_cap_stack.storage import Store

    store = Store(data_dir)
    opps = day_opportunities(store, trading_date)
    if opps.is_empty():
        return []
    bars_df = store.read("bars", dt=trading_date)
    scans = store.read("scanner_hits", dt=trading_date)
    cases: list[Case] = []
    for row in opps.iter_rows(named=True):
        oid = str(row["opportunity_id"])
        bars = day_chart_bars(bars_df, oid, settings)
        if not bars:
            continue
        hits = (
            sorted(scans.filter(scans["opportunity_id"] == oid)["ts_utc"].to_list())
            if not scans.is_empty()
            else []
        )
        first_hit = hits[0] if hits else row["first_seen_utc"]
        cases.append(
            Case(
                symbol=str(row["symbol"]),
                trading_date=trading_date,
                first_hit=first_hit,
                bars=bars,
            )
        )
    return cases


# ------------------------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------------------------


def _row_dict(r: CalibrationRow) -> dict[str, Any]:
    return {
        "symbol": r.symbol,
        "date": r.trading_date.isoformat(),
        "actual_hit_et": _et(r.actual_hit),
        "recon_hit_et": _et(r.recon_hit),
        "delta_min": r.delta_min,
        "binding_gate": r.binding_gate,
        "change_decidable": r.change_decidable,
        "implied_prev_close": {
            "low": None if r.implied.low is None else round(r.implied.low, 4),
            "high": None if r.implied.high is None else round(r.implied.high, 4),
            "feasible": r.implied.feasible,
            "implied_change_pct": r.implied.implied_change_pct,
        },
        "actual": {
            "entry_et": r.actual_entry,
            "triggered": r.actual.triggered,
            "takeable": r.actual.takeable,
            "max_r": r.actual.max_r,
            "stop": r.actual.stop,
        },
        "recon": {
            "entry_et": r.recon_entry,
            "triggered": r.recon.triggered,
            "takeable": r.recon.takeable,
            "max_r": r.recon.max_r,
            "stop": r.recon.stop,
        },
        "pinned": {
            "entry_et": r.pinned_entry,
            "triggered": r.pinned.triggered,
            "takeable": r.pinned.takeable,
            "max_r": r.pinned.max_r,
            "stop": r.pinned.stop,
        },
        "same_trade": r.same_trade,
        "same_trade_pinned": r.same_trade_pinned,
        "verdict": r.verdict,
    }


def _print_table(rows: Sequence[CalibrationRow]) -> None:
    head = (
        f"{'symbol':<7}{'date':<12}{'seen':<7}{'blind':<7}{'Δmin':>8}  "
        f"{'implied prevC':<15}{'verdict':<15}{'entry(a)':<9}{'entry(p)':<9}"
        f"{'maxR(a)':>8}{'maxR(p)':>9}  same"
    )
    print(head)
    print("-" * len(head))
    for r in rows:
        print(
            f"{r.symbol:<7}{r.trading_date.isoformat():<12}"
            f"{_et(r.actual_hit)[:5]:<7}{_et(r.recon_hit)[:5]:<7}"
            f"{'-' if r.delta_min is None else f'{r.delta_min:+.1f}':>8}  "
            f"{r.implied.label:<15}{r.verdict:<15}"
            f"{r.actual_entry:<9}{r.pinned_entry:<9}"
            f"{'-' if r.actual.max_r is None else f'{r.actual.max_r:.2f}':>8}"
            f"{'-' if r.pinned.max_r is None else f'{r.pinned.max_r:.2f}':>9}"
            f"  {'yes' if r.same_trade_pinned else 'NO'}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--fixtures", action="store_true", help="calibrate over the 25 review cases")
    src.add_argument("--store", type=Path, help="Parquet store dir (box/Mac only), e.g. /data")
    p.add_argument("--date", type=date.fromisoformat, help="trading date, with --store")
    p.add_argument(
        "--window-minutes", type=int, default=5, help="trailing volume window (default 5)"
    )
    p.add_argument("--json", type=Path, help="write the full result to this path")
    args = p.parse_args(argv)

    settings = Settings()
    if args.fixtures:
        cases = load_fixture_cases()
        source = f"review fixtures ({FIXTURE_DIR})"
    else:
        if args.date is None:
            p.error("--store requires --date")
        cases = load_store_cases(args.store, args.date, settings)
        source = f"store {args.store} dt={args.date}"
    if not cases:
        print(f"no cases from {source}", file=sys.stderr)
        return 1

    rows = [calibrate_case(c, settings, window_minutes=args.window_minutes) for c in cases]
    rows.sort(key=lambda r: (r.trading_date, r.symbol))
    _print_table(rows)
    summary = summarise(rows)
    print("\nsummary:")
    print(json.dumps(summary, indent=2))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "source": source,
                    "window_minutes": args.window_minutes,
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
