"""Analyse the reconstructed pre-market sessions in the recon store (#431 harvest output).

Replays the live engine (detect_day -> compute_r_metrics) over every harvested session and reports
the funnel: opportunities -> setups found -> triggered -> takeable, plus the R distribution of the
trades the paper book would actually have taken.

Run:  .venv/bin/python spikes/harvest_analysis.py
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

STORE = Store(Path("data/recon"))
S = Settings()


def sessions() -> list:
    dts = sorted(p.name.split("=")[1] for p in Path("data/recon/opportunities").glob("dt=*"))
    return [pl.Series([d]).str.to_date().item() for d in dts]


def main() -> None:
    days = sessions()
    print(f"recon store: {len(days)} sessions, {days[0]} -> {days[-1]}\n")

    rows: list[dict] = []
    trades: list = []
    gate_fails: Counter = Counter()
    per_day: list[dict] = []

    for d in days:
        opps = day_opportunities(STORE, d)
        bars_df = STORE.read("bars", dt=d)
        scans = STORE.read("scanner_hits", dt=d)
        n_runs = n_setup = n_trig = n_take = 0
        for row in opps.iter_rows(named=True):
            day_bars = day_chart_bars(bars_df, row["opportunity_id"], S)
            if not day_bars:
                continue
            for run in symbol_runs(row, bars_df, scans, S):
                rm = compute_r_metrics(day_bars, S, first_hit=run.first_hit)
                n_runs += 1
                n_setup += rm.setup_found
                n_trig += rm.triggered
                n_take += rm.takeable
                if rm.triggered and not rm.takeable:
                    for g in rm.failing_gates:
                        gate_fails[g] += 1
                    if rm.exhausted:
                        gate_fails["(exhausted)"] += 1
                rows.append(
                    {
                        "dt": d,
                        "symbol": row["symbol"],
                        "run": run.idx,
                        "setup": rm.setup_found,
                        "triggered": rm.triggered,
                        "takeable": rm.takeable,
                        "max_r": rm.max_r,
                        "mae_r": rm.mae_r,
                        "max_gain_pct": rm.max_gain_pct,
                        "stopped": rm.stopped_out,
                        "cycle": rm.cycle_num,
                        "pole_len": rm.pole_len,
                        "flag_len": rm.flag_len,
                        "retrace": rm.retracement,
                        "risk": rm.initial_risk,
                        "entry": rm.entry_price,
                    }
                )
        t = extract_day_trades(STORE, S, d, source="recon")
        trades.extend(t)
        per_day.append(
            {
                "dt": d,
                "opps": opps.height,
                "runs": n_runs,
                "setups": n_setup,
                "trig": n_trig,
                "takeable": n_take,
                "book": len(t),
            }
        )

    df = pl.DataFrame(rows)
    pd_df = pl.DataFrame(per_day)
    df.write_parquet("data/spikes/harvest_runs.parquet")

    print("=== FUNNEL (all runs, all sessions) ===")
    tot = pd_df.sum()
    print(
        f"opportunities {tot['opps'][0]}  runs {tot['runs'][0]}  setup-found {tot['setups'][0]}  "
        f"triggered {tot['trig'][0]}  takeable {tot['takeable'][0]}  "
        f"book-qualified {tot['book'][0]}"
    )
    print(f"\nper session: {pd_df.select(pl.exclude('dt')).mean().to_dicts()[0]}")
    print("\n=== PER SESSION ===")
    print(pd_df)

    print("\n=== GATE REJECTIONS (triggered but not takeable) ===")
    for g, n in gate_fails.most_common():
        print(f"  {g:28s} {n}")

    take = df.filter(pl.col("takeable") & pl.col("max_r").is_not_null())
    mr = sorted(take["max_r"].to_list())
    print(f"\n=== TAKEABLE MAX-R (n={len(mr)}) ===")
    if mr:
        print(f"  mean {statistics.mean(mr):.2f}R  median {statistics.median(mr):.2f}R")
        for q in (0.1, 0.25, 0.5, 0.75, 0.9, 0.95):
            print(f"  p{int(q * 100):<3d} {mr[min(int(q * len(mr)), len(mr) - 1)]:.2f}R")
        for thr in (0.5, 1.0, 1.5, 2.0, 3.0, 5.0):
            hit = sum(1 for v in mr if v >= thr)
            print(f"  >= {thr}R : {hit:4d} ({hit / len(mr):5.1%})")
        print(f"  stopped out: {take['stopped'].sum()} / {len(mr)}")

    print("\n=== BOOK TRADES (portfolio-qualified) ===")
    print(f"  n={len(trades)} over {len(days)} sessions ({len(trades) / len(days):.1f}/session)")
    if trades:
        bmr = sorted(t.max_r for t in trades if t.max_r is not None)
        print(f"  Max R: mean {statistics.mean(bmr):.2f}  median {statistics.median(bmr):.2f}")
        for thr in (1.0, 2.0, 3.0):
            hit = sum(1 for v in bmr if v >= thr)
            print(f"    >= {thr}R : {hit:3d} ({hit / len(bmr):5.1%})")
        sym = Counter(t.symbol for t in trades)
        print(f"  distinct symbols {len(sym)}; most frequent: {sym.most_common(8)}")


if __name__ == "__main__":
    Path("data/spikes").mkdir(parents=True, exist_ok=True)
    main()
