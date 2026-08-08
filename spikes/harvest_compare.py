"""Compare the reconstructed (recon) funnel against the live-captured one.

Recon covers 2026-05-15..06-30, live 2026-07-01..08-07 — adjacent, non-overlapping windows. If the
harvest is sound, the two funnels should be broadly similar in shape (runs/session, trigger rate,
gate-rejection mix). A big divergence is either a real regime change or a reconstruction artefact.

Run:  .venv/bin/python spikes/harvest_compare.py
"""

from __future__ import annotations

import statistics
from collections import Counter
from pathlib import Path

import polars as pl

from small_cap_stack.config import Settings
from small_cap_stack.portfolio.extract import extract_day_trades
from small_cap_stack.report import day_chart_bars, day_opportunities, symbol_runs
from small_cap_stack.rmetrics import compute_r_metrics
from small_cap_stack.storage import Store

S = Settings()


def dates_for(root: str) -> list:
    dts = sorted(p.name.split("=")[1] for p in Path(f"{root}/opportunities").glob("dt=*"))
    return [pl.Series([d]).str.to_date().item() for d in dts]


def funnel(root: str, label: str) -> dict:
    store = Store(Path(root))
    days = dates_for(root)
    gate_fails: Counter = Counter()
    n_opps = n_runs = n_trig = n_take = 0
    take_mr: list[float] = []
    trig_mr: list[float] = []
    trades = []
    price_at_entry: list[float] = []
    for d in days:
        opps = day_opportunities(store, d)
        bars_df = store.read("bars", dt=d)
        scans = store.read("scanner_hits", dt=d)
        n_opps += opps.height
        for row in opps.iter_rows(named=True):
            day_bars = day_chart_bars(bars_df, row["opportunity_id"], S)
            if not day_bars:
                continue
            for run in symbol_runs(row, bars_df, scans, S):
                rm = compute_r_metrics(day_bars, S, first_hit=run.first_hit)
                n_runs += 1
                if not rm.triggered:
                    continue
                n_trig += 1
                if rm.max_r is not None:
                    trig_mr.append(rm.max_r)
                if rm.entry_price:
                    price_at_entry.append(rm.entry_price)
                if rm.takeable:
                    n_take += 1
                    if rm.max_r is not None:
                        take_mr.append(rm.max_r)
                else:
                    for g in rm.failing_gates:
                        gate_fails[g] += 1
                    if rm.exhausted:
                        gate_fails["(exhausted)"] += 1
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
        "gates": gate_fails,
        "take_mr": take_mr,
        "trig_mr": trig_mr,
        "price": price_at_entry,
        "trades": trades,
    }


def show(r: dict) -> None:
    d = r["days"]
    print(f"\n=== {r['label'].upper()}  {r['span'][0]} -> {r['span'][1]}  ({d} sessions) ===")
    print(
        f"  opps {r['opps']:5d} ({r['opps'] / d:5.1f}/day)   "
        f"runs {r['runs']:5d} ({r['runs'] / d:5.1f}/day)"
    )
    print(f"  triggered {r['trig']:5d} ({r['trig'] / max(r['runs'], 1):5.1%} of runs)")
    print(f"  takeable  {r['take']:5d} ({r['take'] / max(r['trig'], 1):5.1%} of triggered)")
    print(f"  book      {r['book']:5d} ({r['book'] / d:5.2f}/day)")
    tot = sum(r["gates"].values())
    print("  gate rejections (share of all gate failures):")
    for g, n in r["gates"].most_common(9):
        print(f"    {g:24s} {n:5d}  {n / max(tot, 1):5.1%}")
    for name, arr in (("takeable Max R", r["take_mr"]), ("all-triggered Max R", r["trig_mr"])):
        if arr:
            a = sorted(arr)
            ge1 = sum(1 for v in a if v >= 1.0) / len(a)
            ge2 = sum(1 for v in a if v >= 2.0) / len(a)
            print(
                f"  {name:20s} n={len(a):4d}  mean {statistics.mean(a):5.2f}R  "
                f"median {statistics.median(a):5.2f}R  >=1R {ge1:5.1%}  >=2R {ge2:5.1%}"
            )
    if r["price"]:
        p = sorted(r["price"])
        print(
            f"  entry price          median ${statistics.median(p):.2f}  "
            f"p10 ${p[len(p) // 10]:.2f}  p90 ${p[9 * len(p) // 10]:.2f}"
        )


def main() -> None:
    recon = funnel("data/recon", "recon")
    live = funnel("data/live", "live")
    show(recon)
    show(live)

    print("\n\n=== SIDE BY SIDE (per session) ===")
    print(f"{'metric':28s} {'recon':>10s} {'live':>10s} {'ratio':>8s}")
    for k in ("opps", "runs", "trig", "take", "book"):
        a, b = recon[k] / recon["days"], live[k] / live["days"]
        print(f"{k:28s} {a:10.2f} {b:10.2f} {(a / b if b else float('nan')):8.2f}")
    for k, lbl in (("trig", "trigger rate (of runs)"), ("take", "takeable rate (of trig)")):
        den = "runs" if k == "trig" else "trig"
        a, b = recon[k] / max(recon[den], 1), live[k] / max(live[den], 1)
        print(f"{lbl:28s} {a:10.1%} {b:10.1%} {(a / b if b else float('nan')):8.2f}")

    print("\n=== GATE MIX (share of gate failures) ===")
    keys = sorted(set(recon["gates"]) | set(live["gates"]))
    rt, lt = sum(recon["gates"].values()), sum(live["gates"].values())
    print(f"{'gate':26s} {'recon':>8s} {'live':>8s}")
    for g in keys:
        print(f"{g:26s} {recon['gates'][g] / max(rt, 1):8.1%} {live['gates'][g] / max(lt, 1):8.1%}")


if __name__ == "__main__":
    main()
