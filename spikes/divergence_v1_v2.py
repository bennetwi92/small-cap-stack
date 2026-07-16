"""Spike #181 — engine v1 (legacy) vs v2 (detect_setup) divergence over stored history.

Quantifies what the engine-v2 cut-over (#180) will change *before* we flip it: per run, replay the
notional trade with the **legacy** detector (current production) and with **v2** (`detect_setup`,
locked params: caps 4/4, `min_pole_pct` 2%, entry trigger +1 tick, `eps` 1 tick), and diff setups /
entries / stops / Max R. An extra "v2 without the 2% floor" pass isolates how many setups the
`min_pole_pct` floor alone removes — the number `engine-v2.md §10` says to eyeball before flipping.

Store-raw / compute-on-read: reuses `symbol_runs` + `day_opportunities` (the production run
segmentation) and `compute_r_metrics` (the legacy baseline), so nothing here can drift from the
real analysis path. Reads only — writes CSV + a summary JSON to `data/spikes/` (gitignored).

**Runs on the box only** (Mac or VPS): needs the `/data` store. Not runnable from Claude web/mobile.

    python spikes/divergence_v1_v2.py --data-dir /data
    python spikes/divergence_v1_v2.py --data-dir /data --start 2026-06-15 --end 2026-06-30
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from small_cap_stack.bullflag import detect_setup
from small_cap_stack.capture import Bar, bar_interval
from small_cap_stack.config import Settings
from small_cap_stack.report import day_opportunities, symbol_runs
from small_cap_stack.rmetrics import (
    RMetrics,
    _first_trigger,
    _measure,
    compute_r_metrics,
)
from small_cap_stack.storage import Store


def _v2_detect_params(settings: Settings, *, min_pole_pct: float) -> dict[str, object]:
    """Locked v2 detection params (bull-flag.md §6): 4/4 caps, +1 tick trigger, 1-tick eps."""
    tick = settings.tick_size
    return {
        "min_pole": settings.bull_flag_min_pole,
        "max_pole": 4,
        "max_cons": 4,
        "max_retracement": settings.bull_flag_max_retracement,
        "max_peak_wick": settings.bull_flag_max_peak_wick,
        "min_pole_pct": min_pole_pct,
        "atr_window": 14,
        "entry_offset": settings.bull_flag_trigger_offset_ticks * tick,
        "eps": 1 * tick,
        "gate_window": False,
    }


def v2_r_metrics(
    bars: list[Bar], settings: Settings, *, first_hit: datetime | None, params: dict[str, object]
) -> RMetrics:
    """R-metrics via the v2 engine — mirrors compute_r_metrics but swaps in detect_setup.

    Identical appearance/staleness gating and stop-first measurement as the legacy path (reuses its
    internals), so any difference is purely the detector, not the measurement.
    """
    interval = bar_interval(bars)
    staleness = timedelta(minutes=settings.entry_staleness_min)
    first_valid = None
    for i in range(1, len(bars)):
        setup = detect_setup(bars[: i + 1], **params)  # type: ignore[arg-type]
        if setup is None or not setup.passed:
            continue
        bf = setup.as_bullflag()
        risk = round(bf.entry_trigger - bf.stop, 6)
        if risk <= 0:
            continue
        if first_valid is None:
            first_valid = (bf, risk)
        trig_j = _first_trigger(bars, i, bf.entry_trigger)
        if trig_j is None:
            continue
        if first_hit is not None and bars[trig_j].start + interval <= first_hit:
            continue
        if first_hit is not None and bars[trig_j].start >= first_hit + staleness:
            continue
        return _measure(bars, bf, risk, trig_j)
    if first_valid is None:
        return RMetrics(setup_found=False)
    bf, risk = first_valid
    return RMetrics(
        setup_found=True,
        triggered=False,
        entry_trigger=bf.entry_trigger,
        stop=bf.stop,
        initial_risk=risk,
        flag_len=bf.flag_len,
        retracement=bf.retracement,
        pole_len=bf.pole_len,
        cons_vol_reducing=bf.cons_vol_reducing,
        pole_has_big_green=bf.pole_has_big_green,
    )


def _row(
    trading_date: date,
    seg_id: str,
    symbol: str,
    bars: list[Bar],
    first_hit: datetime | None,
    legacy: RMetrics,
    v2: RMetrics,
    v2_no_floor: RMetrics,
) -> dict[str, object]:
    def d(a: float | None, b: float | None) -> float | None:
        return None if a is None or b is None else round(b - a, 6)

    return {
        "trading_date": trading_date,
        "seg_id": seg_id,
        "symbol": symbol,
        "first_hit": first_hit.isoformat() if first_hit else None,
        "n_bars": len(bars),
        "legacy_found": legacy.setup_found,
        "legacy_triggered": legacy.triggered,
        "legacy_entry": legacy.entry_trigger,
        "legacy_stop": legacy.stop,
        "legacy_max_r": legacy.max_r,
        "legacy_pole_len": legacy.pole_len,
        "legacy_flag_len": legacy.flag_len,
        "legacy_retracement": legacy.retracement,
        "v2_found": v2.setup_found,
        "v2_triggered": v2.triggered,
        "v2_entry": v2.entry_trigger,
        "v2_stop": v2.stop,
        "v2_max_r": v2.max_r,
        "v2_pole_len": v2.pole_len,
        "v2_flag_len": v2.flag_len,
        "v2_retracement": v2.retracement,
        # deltas (v2 - legacy)
        "found_changed": legacy.setup_found != v2.setup_found,
        "triggered_changed": legacy.triggered != v2.triggered,
        "added": v2.setup_found and not legacy.setup_found,
        "removed": legacy.setup_found and not v2.setup_found,
        "entry_delta": d(legacy.entry_trigger, v2.entry_trigger),
        "stop_delta": d(legacy.stop, v2.stop),
        "max_r_delta": d(legacy.max_r, v2.max_r),
        # min_pole_pct floor ablation: found without the floor but not with it
        "floor_removed": v2_no_floor.setup_found and not v2.setup_found,
    }


def _summary(rows: list[dict[str, object]]) -> dict[str, object]:
    def count(key: str) -> int:
        return sum(1 for r in rows if r[key])

    def triggered_max_r(key: str) -> list[float]:
        return [r[key] for r in rows if isinstance(r[key], (int, float))]  # type: ignore[misc]

    max_r_deltas = [r["max_r_delta"] for r in rows if isinstance(r["max_r_delta"], (int, float))]
    legacy_r = triggered_max_r("legacy_max_r")
    v2_r = triggered_max_r("v2_max_r")
    return {
        "runs": len(rows),
        "legacy_setups": count("legacy_found"),
        "v2_setups": count("v2_found"),
        "legacy_triggered": count("legacy_triggered"),
        "v2_triggered": count("v2_triggered"),
        "added_setups": count("added"),
        "removed_setups": count("removed"),
        "found_changed": count("found_changed"),
        "triggered_changed": count("triggered_changed"),
        "floor_removed_setups": count("floor_removed"),  # <- the min_pole_pct 2% impact
        "legacy_mean_max_r": round(statistics.mean(legacy_r), 4) if legacy_r else None,
        "v2_mean_max_r": round(statistics.mean(v2_r), 4) if v2_r else None,
        "mean_max_r_delta": round(statistics.mean(max_r_deltas), 4) if max_r_deltas else None,
        "runs_with_entry_shift": sum(1 for r in rows if r["entry_delta"] not in (None, 0)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="/data", help="the Store root (the box's /data)")
    ap.add_argument("--out", default="data/spikes", help="output dir (gitignored)")
    ap.add_argument("--start", type=date.fromisoformat, default=None, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", type=date.fromisoformat, default=None, help="YYYY-MM-DD (inclusive)")
    args = ap.parse_args()

    settings = Settings()
    store = Store(Path(args.data_dir))
    opps = store.read("opportunities")
    if opps.is_empty():
        print("no opportunities in the store — nothing to compare")
        return
    bars_df = store.read("bars")
    scans = store.read("scanner_hits")

    dates = sorted(set(opps["trading_date"].to_list()))
    if args.start:
        dates = [d for d in dates if d >= args.start]
    if args.end:
        dates = [d for d in dates if d <= args.end]

    v2_params = _v2_detect_params(settings, min_pole_pct=0.02)
    v2_no_floor_params = _v2_detect_params(settings, min_pole_pct=0.0)

    rows: list[dict[str, object]] = []
    for d in dates:
        for orow in day_opportunities(store, d).iter_rows(named=True):
            for run in symbol_runs(orow, bars_df, scans, settings):
                if not run.bars:
                    continue
                legacy = compute_r_metrics(run.bars, settings, first_hit=run.first_hit)
                v2 = v2_r_metrics(run.bars, settings, first_hit=run.first_hit, params=v2_params)
                v2nf = v2_r_metrics(
                    run.bars, settings, first_hit=run.first_hit, params=v2_no_floor_params
                )
                rows.append(
                    _row(d, run.seg_id, run.symbol, run.bars, run.first_hit, legacy, v2, v2nf)
                )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = f"{dates[0]}_{dates[-1]}" if dates else "empty"
    csv_path = out / f"divergence_{stamp}.csv"
    json_path = out / f"divergence_{stamp}.summary.json"
    pl.DataFrame(rows).write_csv(csv_path)
    summary = _summary(rows)
    json_path.write_text(json.dumps(summary, indent=2, default=str))

    print(f"\n=== v1 vs v2 divergence ({stamp}, {len(rows)} runs) ===")
    for k, v in summary.items():
        print(f"  {k:24} {v}")
    print(f"\nper-run CSV : {csv_path}\nsummary JSON: {json_path}")


if __name__ == "__main__":
    main()
