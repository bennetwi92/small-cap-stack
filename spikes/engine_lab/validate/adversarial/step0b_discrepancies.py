"""Step 0b — chase the two CLAIM.md numbers that did NOT re-derive.

1. "in play only, no shape gates" = 242 trades / -20.3R. I get 366 / -157.3R.
2. The intermediate-signal denominators (433 / 263 / 230). SHIPPED gives 63 / 37 / 25.

Also: nullity. `shares_outstanding <= 50e6` silently drops every row where the field is null,
so part of the filter may be data availability rather than company size.
"""

from __future__ import annotations

import lab as L
import polars as pl
from lab import C


def main() -> None:
    df = L.load_panel_checked()
    out: dict = {}

    # ------------------------------------------------------------------ nullity of the features
    L.hr("NULLITY of the three in-play features")
    nulls = {}
    for col in ("runup_pre_appearance", "rvol_pole", "shares_outstanding"):
        tot = int(df[col].is_null().sum())
        by = (
            df.group_by(["split", "source"])
            .agg(pl.col(col).is_null().mean().alias("null_rate"), pl.len().alias("n"))
            .sort(["split", "source"])
        )
        print(f"\n{col}: {tot}/{df.height} null ({tot / df.height:.1%})")
        print(by)
        nulls[col] = {"n_null": tot, "rate": round(tot / df.height, 4), "by": by.to_dicts()}
    # combined: how many rows does nullity alone remove?
    notnull = df.filter(
        pl.col("runup_pre_appearance").is_not_null()
        & pl.col("rvol_pole").is_not_null()
        & pl.col("shares_outstanding").is_not_null()
    )
    print(f"\nrows with all three non-null: {notnull.height}/{df.height}")
    nulls["all_three_notnull"] = notnull.height
    # within SHIPPED
    sh = C.SHIPPED(df)
    shnn = sh.filter(
        pl.col("runup_pre_appearance").is_not_null()
        & pl.col("rvol_pole").is_not_null()
        & pl.col("shares_outstanding").is_not_null()
    )
    print(f"SHIPPED rows: {sh.height}; with all three non-null: {shnn.height}")
    nulls["shipped"] = {"rows": sh.height, "all_three_notnull": shnn.height}
    out["nullity"] = nulls

    # -------------------------------------- what population gives the claimed 242 / 433-263-230?
    L.hr("Which population reproduces the claimed counts?")
    cands = {
        "raw panel": df,
        "SHIPPED": C.SHIPPED(df),
        "passed only": df.filter(pl.col("passed")),
        "SHIPPED minus passed": df.filter(
            (pl.col("cycle_num") <= 2)
            & (pl.col("staleness_delay_min") <= 30)
            & pl.col("entry_fill").is_between(3.0, 50.0)
            & (pl.col("stop_pct") >= 0.025)
            & pl.col("trigger_et_min").is_between(240.0, 555.0)
        ),
        "price band + trigger window only": df.filter(
            pl.col("entry_fill").is_between(3.0, 50.0)
            & pl.col("trigger_et_min").is_between(240.0, 555.0)
        ),
        "all three features non-null": notnull,
    }
    rows = []
    for name, d in cands.items():
        per_split = {s: d.filter(pl.col("split") == s).height for s in ("dev", "val", "holdout")}
        ip = L.in_play(d)
        bk = L.book_of(ip)
        sc = (
            C.score(bk, sessions=df["dt"].n_unique())
            if bk.height
            else {"trades": 0, "net_r": 0.0, "net_r_per_trade": 0.0}
        )
        print(
            f"  {name:<34} rows={d.height:>5} split={per_split}  "
            f"in-play book trades={sc['trades']:>4} netR={sc['net_r']:+8.1f} "
            f"per={sc['net_r_per_trade']:+.3f}"
        )
        rows.append(
            {
                "population": name,
                "rows": d.height,
                "per_split_rows": per_split,
                "in_play_book_trades": sc["trades"],
                "in_play_book_net_r": sc["net_r"],
                "in_play_book_net_r_per_trade": sc["net_r_per_trade"],
            }
        )
    print("\n  CLAIM.md says: in-play-only book = 242 trades, -20.3R, -0.084/trade")
    print("  CLAIM.md intermediate denominators: dev 433, val 263, holdout 230")
    out["population_search"] = rows

    # ------------------------------------------ intermediate signal on the population that fits
    L.hr("Intermediate signal (>=50% move) on each candidate population")
    inter = []
    for name, d in cands.items():
        r = {"population": name}
        for s in ("dev", "val", "holdout"):
            ds = d.filter(pl.col("split") == s)
            ip = L.in_play(ds)
            r[s] = {
                "n": ds.height,
                "base": round(float((ds["max_gain_pct"] >= 0.50).mean()), 4) if ds.height else None,
                "in_play_n": ip.height,
                "in_play": round(float((ip["max_gain_pct"] >= 0.50).mean()), 4)
                if ip.height
                else None,
            }
        print(
            f"  {name:<34} "
            + "  ".join(
                f"{s}: n={r[s]['n']:>4} {r[s]['base']}->{r[s]['in_play']}"
                for s in ("dev", "val", "holdout")
            )
        )
        inter.append(r)
    out["intermediate_by_population"] = inter

    L.write("step0b_discrepancies.json", out)
    print("\nwrote step0b_discrepancies.json")


if __name__ == "__main__":
    main()
