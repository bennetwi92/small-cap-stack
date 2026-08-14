"""Step 3 — where the cost drag lives, and how much of "net" is really a sizing decision.

On a $500 account the notional cap binds below a ~10% stop, so a row's dollar risk — and hence
its cost as a fraction of R — is almost entirely a function of `stop_pct`. This quantifies it so
the selection study can separate "this setup wins more often" from "this setup is cheaper to
trade". Both are legitimate selection levers; conflating them is not.
"""

from __future__ import annotations

import lab
import polars as pl


def main() -> None:
    p = lab.no_holdout(lab.panel())
    b = p.with_columns(
        pl.col("stop_pct")
        .cut([0.02, 0.04, 0.06, 0.08, 0.10, 0.13, 0.16, 0.22], left_closed=True)
        .alias("sb")
    )
    with pl.Config(tbl_rows=30, tbl_width_chars=200):
        print(
            b.group_by("sb")
            .agg(
                pl.len().alias("n"),
                (pl.col("sized_by") == "cap").mean().round(3).alias("cap_bound"),
                pl.col("qty").median().alias("med_qty"),
                (pl.col("qty") * (pl.col("entry_fill") - pl.col("stop")))
                .median()
                .round(2)
                .alias("med_risk_usd"),
                pl.col("cost_r").mean().round(3).alias("cost_r"),
                pl.col("r").mean().round(3).alias("gross"),
                pl.col("net_r").mean().round(3).alias("net"),
            )
            .sort("sb")
        )

    print("\ncost_r vs entry price, within stop_pct >= 0.10:")
    q = p.filter(pl.col("stop_pct") >= 0.10).with_columns(
        pl.col("entry_fill").cut([1.0, 2.0, 3.0, 5.0, 10.0], left_closed=True).alias("pb")
    )
    with pl.Config(tbl_rows=30, tbl_width_chars=200):
        print(
            q.group_by("pb")
            .agg(
                pl.len().alias("n"),
                pl.col("cost_r").mean().round(3).alias("cost_r"),
                pl.col("r").mean().round(3).alias("gross"),
                pl.col("net_r").mean().round(3).alias("net"),
            )
            .sort("pb")
        )

    print("\nunaffordable rows (qty<1):", int((p["qty"] < 1).sum()))
    print("\ncorrelation of the 'already moved' family (spearman, dev+val):")
    cols = [
        "pole_pct",
        "ext_at_peak",
        "runup_pre_appearance",
        "ext_at_trigger",
        "stop_pct",
        "planned_risk",
        "entry_fill",
    ]
    d = p.select(cols).drop_nulls()
    rk = d.select([pl.col(c).rank().alias(c) for c in cols])
    print(rk.corr().with_columns(pl.Series("feature", cols)).select(["feature", *cols]))


if __name__ == "__main__":
    main()
