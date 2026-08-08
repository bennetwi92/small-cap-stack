"""Apples-to-apples: recon vs live restricted to the PRE-MARKET population.

The harvest reconstructs the scanner only for 04:00-09:30 ET (reconstruct.PREMARKET), by design --
the paper book's window (05:30-09:15) sits inside it. The live tracker's scanner runs to 11:59 ET,
so a raw cross-store funnel compares 5.5 hours of live scanning against 5.5 hours of pre-market
reconstruction and makes recon look thin. This restricts BOTH sides to runs whose scanner
appearance is before 09:30 ET, which is the only comparison that means anything.

Run:  .venv/bin/python spikes/harvest_premarket.py
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import time
from pathlib import Path

import polars as pl

from small_cap_stack.clock import ET
from small_cap_stack.config import Settings
from small_cap_stack.portfolio.extract import extract_day_trades
from small_cap_stack.report import day_chart_bars, day_opportunities, symbol_runs
from small_cap_stack.rmetrics import compute_r_metrics
from small_cap_stack.storage import Store

S = Settings()
BELL = time(9, 30)


def dates_for(root: str) -> list:
    dts = sorted(p.name.split("=")[1] for p in Path(f"{root}/opportunities").glob("dt=*"))
    return [pl.Series([d]).str.to_date().item() for d in dts]


def funnel(root: str, label: str) -> dict:
    store = Store(Path(root))
    days = dates_for(root)
    gates: Counter = Counter()
    n_opps = n_runs = n_trig = n_take = 0
    take_mr: list[float] = []
    trades = []
    for d in days:
        opps = day_opportunities(store, d)
        bars_df = store.read("bars", dt=d)
        scans = store.read("scanner_hits", dt=d)
        for row in opps.iter_rows(named=True):
            day_bars = day_chart_bars(bars_df, row["opportunity_id"], S)
            if not day_bars:
                continue
            counted_opp = False
            for run in symbol_runs(row, bars_df, scans, S):
                if run.first_hit is None or run.first_hit.astimezone(ET).time() >= BELL:
                    continue  # regular-session appearance: recon has none, so exclude from both
                if not counted_opp:
                    n_opps += 1
                    counted_opp = True
                rm = compute_r_metrics(day_bars, S, first_hit=run.first_hit)
                n_runs += 1
                if not rm.triggered:
                    continue
                n_trig += 1
                if rm.takeable:
                    n_take += 1
                    if rm.max_r is not None:
                        take_mr.append(rm.max_r)
                else:
                    for g in rm.failing_gates:
                        gates[g] += 1
                    if rm.exhausted:
                        gates["(exhausted)"] += 1
        trades.extend(extract_day_trades(store, S, d, source=label))
    return {
        "label": label,
        "days": len(days),
        "span": (days[0], days[-1]),
        "opps": n_opps,
        "runs": n_runs,
        "trig": n_trig,
        "take": n_take,
        "book": len(trades),
        "gates": gates,
        "take_mr": take_mr,
        "trades": trades,
    }


def main() -> None:
    recon, live = funnel("data/recon", "recon"), funnel("data/live", "live")
    print("PRE-MARKET-ONLY FUNNEL (scanner appearance before 09:30 ET)\n")
    print(f"{'metric':30s} {'recon':>12s} {'live':>12s} {'ratio':>8s}")
    print(f"{'span':30s} {str(recon['span'][0]):>12s} {str(live['span'][0]):>12s}")
    print(f"{'':30s} {str(recon['span'][1]):>12s} {str(live['span'][1]):>12s}")
    print(f"{'sessions':30s} {recon['days']:12d} {live['days']:12d}")
    for k in ("opps", "runs", "trig", "take", "book"):
        a, b = recon[k] / recon["days"], live[k] / live["days"]
        print(f"{k + '/session':30s} {a:12.2f} {b:12.2f} {a / b if b else 0:8.2f}")
    print(
        f"{'trigger rate (of runs)':30s} {recon['trig'] / max(recon['runs'], 1):11.1%} "
        f"{live['trig'] / max(live['runs'], 1):11.1%}"
    )
    print(
        f"{'takeable rate (of triggered)':30s} {recon['take'] / max(recon['trig'], 1):11.1%} "
        f"{live['take'] / max(live['trig'], 1):11.1%}"
    )
    print(
        f"{'book rate (of takeable)':30s} {recon['book'] / max(recon['take'], 1):11.1%} "
        f"{live['book'] / max(live['take'], 1):11.1%}"
    )

    print("\nGATE MIX (share of gate failures)")
    rt, lt = sum(recon["gates"].values()), sum(live["gates"].values())
    print(f"{'gate':26s} {'recon':>9s} {'live':>9s}")
    for g in sorted(set(recon["gates"]) | set(live["gates"])):
        print(f"{g:26s} {recon['gates'][g] / max(rt, 1):9.1%} {live['gates'][g] / max(lt, 1):9.1%}")

    print("\nTAKEABLE MAX-R")
    for d in (recon, live):
        a = sorted(d["take_mr"])
        if a:
            print(
                f"  {d['label']:6s} n={len(a):4d}  mean {statistics.mean(a):5.2f}R  "
                f"median {statistics.median(a):5.2f}R  "
                f">=1R {sum(v >= 1 for v in a) / len(a):5.1%}  "
                f">=2R {sum(v >= 2 for v in a) / len(a):5.1%}  "
                f">=3R {sum(v >= 3 for v in a) / len(a):5.1%}"
            )

    print("\nBOOK TRADES (what the paper book would actually take)")
    for d in (recon, live):
        mr = sorted(t.max_r for t in d["trades"] if t.max_r is not None)
        if mr:
            print(
                f"  {d['label']:6s} n={len(mr):3d} ({len(mr) / d['days']:.2f}/session)  "
                f"mean Max R {statistics.mean(mr):5.2f}  median {statistics.median(mr):5.2f}  "
                f">=1R {sum(v >= 1 for v in mr) / len(mr):5.1%}  "
                f">=2R {sum(v >= 2 for v in mr) / len(mr):5.1%}"
            )


if __name__ == "__main__":
    main()
