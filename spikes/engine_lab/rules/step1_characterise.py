"""Step 1 — which trigger-time features carry any signal at all?

Row-level (not booked) mean R at the fixed 2R bracket, by decile/band of every
trigger-time-safe numeric or boolean feature. DEV and VAL reported side by side so a band that
only works in one is visible immediately. HOLDOUT is dropped before anything is computed.
"""

from __future__ import annotations

import json

import lab
import polars as pl

NUMERIC = [
    "trigger_et_min",
    "first_hit_et_min",
    "staleness_delay_min",
    "n_scanner_hits",
    "hits_before_trigger",
    "first_rank",
    "entry_fill",
    "planned_risk",
    "stop_pct",
    "pole_len",
    "cons_len",
    "retracement",
    "score",
    "cycle_num",
    "untraded_cons_bars",
    "pole_pct",
    "pole_volume",
    "day_open",
    "ext_at_peak",
    "ext_at_trigger",
    "bars_before_pole",
    "runup_pre_appearance",
    "rvol_pole",
    "vol_share_pole",
    "range_before_pole_pct",
    "cum_volume_to_trigger",
    "cum_dollar_vol_to_trigger",
    "float_shares",
    "short_percent",
    "shares_outstanding",
]
BOOLEAN = ["cons_vol_reducing", "pole_has_big_green", "halted_consolidation", "passed"]


def spearman(df: pl.DataFrame, col: str, y: str = "net_r") -> float:
    d = df.select([col, y]).drop_nulls()
    if d.height < 50 or d[col].n_unique() < 3:
        return float("nan")
    a = d[col].cast(pl.Float64).rank().to_numpy()
    b = d[y].rank().to_numpy()
    return float(((a - a.mean()) * (b - b.mean())).sum() / (len(a) * a.std() * b.std()))


def main() -> None:
    p = lab.no_holdout(lab.panel())
    print(f"population (dev+val): {p.height} rows, {p['dt'].n_unique()} sessions")
    print(
        f"base rate  gross {p['r'].mean():+.3f}R  net {p['net_r'].mean():+.3f}R  "
        f"win {(p['r'] > 0).mean():.1%}"
    )
    print(
        f"cost drag  {p['cost_r'].mean():.3f}R/trade   "
        f"cap-bound {(p['sized_by'] == 'cap').mean():.1%}\n"
    )

    rows = []
    for col in NUMERIC:
        nn = p[col].drop_nulls().len()
        if nn < 200:
            print(f"-- {col}: only {nn} non-null, skipped")
            continue
        rho = spearman(p, col)
        print(f"\n=== {col}   (rho_net={rho:+.3f}, nulls={p.height - nn})")
        b = lab.bands(p, col, n=10)
        print(b)
        rows.append({"col": col, "rho_net": rho, "bands": b.to_dicts()})

    for col in BOOLEAN:
        print(f"\n=== {col}")
        b = lab.bands(p, col)
        print(b)
        rows.append({"col": col, "rho_net": None, "bands": b.to_dicts()})

    lab.OUT.mkdir(parents=True, exist_ok=True)
    (lab.OUT / "step1_bands.json").write_text(json.dumps(rows, indent=1, default=str))

    print("\n\n=== rank of |rho_net| (dev+val, row level)")
    rr = sorted(
        [r for r in rows if r["rho_net"] == r["rho_net"] and r["rho_net"] is not None],
        key=lambda r: -abs(r["rho_net"]),
    )
    for r in rr:
        print(f"  {r['col']:<28} {r['rho_net']:+.4f}")


if __name__ == "__main__":
    main()
