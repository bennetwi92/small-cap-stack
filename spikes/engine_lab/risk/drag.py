"""Agent B — cost-drag anatomy: where the 10-24% of R goes, and which trades can never pay for
their own commission.

Algebra first, so the empirics have something to confirm. At $500 / 5% / 50%, with rps = entry-stop:

    risk_qty = floor(25 / rps)          cap_qty = floor(250 / entry)
    cap binds  <=>  250/entry < 25/rps  <=>  rps/entry < 0.10   (i.e. stop_pct < 10%)

so a cap-bound trade deploys risk_usd ~= 250 * stop_pct, not the intended $25.

Round-trip cost, for qty < 100 (which is nearly all of them):

    cost_usd ~= 0.70                (commission minimum, 2 x $0.35 -- FIXED, size-independent)
              + qty * 0.0064        (exchange + clearing, both sides)
              + qty * 0.02          (2-tick stop slippage, LOSERS only)

    cost_R  = cost_usd / (qty * rps)
            = 0.70 / risk_usd  +  0.0064/rps  [+ 0.02/rps on a loser]

Two independent drags, and they are cured by two different rules:

  A. the FIXED $0.70. Pure function of deployed dollar risk. 0.70/$25 = 2.8%; 0.70/$6 = 12%.
     Cured only by deploying more dollars -> a MIN DOLLAR RISK rule (or a bigger account).
  B. the 2-tick SLIPPAGE. Pure function of the per-share stop distance. rps=$0.16 (the pool
     median) -> 12.5% of R on every loser. Cured only by a wider dollar stop -> a MIN STOP
     DISTANCE rule. A bigger account does NOT fix this one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sim as S  # noqa: E402


def annotate(x: pl.DataFrame) -> pl.DataFrame:
    """Split each trade's cost into the fixed part and the per-share part."""
    return x.with_columns(
        (0.70 / pl.col("risk_usd")).alias("fixed_r"),
        ((pl.col("cost_usd") - 0.70) / pl.col("risk_usd")).alias("var_r"),
        (pl.col("risk_usd") / pl.col("qty")).alias("rps"),
    )


def band_table(x: pl.DataFrame, col: str, edges: list[float]) -> pl.DataFrame:
    lab = pl.col(col)
    expr = pl.when(lab < edges[0]).then(pl.lit(f"<{edges[0]:g}"))
    for a, b in zip(edges[:-1], edges[1:], strict=False):
        expr = expr.when(lab < b).then(pl.lit(f"{a:g}-{b:g}"))
    expr = expr.otherwise(pl.lit(f">={edges[-1]:g}"))
    return (
        x.with_columns(expr.alias("band"))
        .group_by("band")
        .agg(
            pl.len().alias("n"),
            pl.col("r").mean().round(3).alias("gross_r"),
            pl.col("net_r").mean().round(3).alias("net_r"),
            pl.col("cost_r").mean().round(3).alias("drag"),
            pl.col("fixed_r").mean().round(3).alias("drag_fixed"),
            pl.col("var_r").mean().round(3).alias("drag_var"),
            pl.col("net_usd").mean().round(2).alias("net_usd_tr"),
            pl.col("net_usd").sum().round(0).alias("net_usd"),
            pl.col("risk_usd").mean().round(1).alias("risk_usd"),
            pl.col("qty").mean().round(0).alias("qty"),
            (pl.col("sized_by") == "cap").mean().round(2).alias("cap"),
        )
        .sort("risk_usd")
    )


def main() -> None:
    work = S.load_work()
    print(f"# DEV+VAL: {work.height} rows, {work['dt'].n_unique()} sessions\n")

    for name in ("pool", "shipped"):
        cfg = S.RiskConfig(max_per_day=99 if name == "pool" else 2)
        x = annotate(S.simulate(S.SELECTIONS[name](work), cfg))
        cap = x.filter(pl.col("sized_by") == "cap")
        rk = x.filter(pl.col("sized_by") == "risk")
        print(f"## {name} (max/day={cfg.max_per_day}) — {x.height} trades")
        print(
            f"   cap-bound {cap.height} ({cap.height / x.height:.0%})  "
            f"drag {cap['cost_r'].mean():.1%} vs risk-bound {rk['cost_r'].mean():.1%}   "
            f"mean risk ${cap['risk_usd'].mean():.2f} vs ${rk['risk_usd'].mean():.2f}"
        )
        print(
            f"   drag split: fixed $0.70 = {x['fixed_r'].mean():.1%} of R, "
            f"per-share = {x['var_r'].mean():.1%} of R"
        )
        print("\n   by DEPLOYED DOLLAR RISK (cures the fixed $0.70):")
        print(band_table(x, "risk_usd", [4, 8, 12, 18, 25]))
        print("\n   by PER-SHARE STOP DISTANCE $ (cures the 2-tick slippage):")
        print(band_table(x, "rps", [0.10, 0.20, 0.35, 0.60, 1.00]))
        print("\n   by ENTRY PRICE:")
        print(band_table(x, "entry_fill", [2, 3, 5, 10, 20]))
        print("\n   by STOP %:")
        print(band_table(x, "stop_pct", [0.03, 0.05, 0.08, 0.12, 0.20]))
        print()

    # Breakeven: what gross R/trade does a trade need to cover its own cost?
    print("## Breakeven gross R by deployed dollar risk (cost ~ $0.70 + qty*0.0264 on a loser)")
    x = annotate(S.simulate(S.SELECTIONS["pool"](work), S.RiskConfig(max_per_day=99)))
    print(
        x.group_by(
            pl.when(pl.col("risk_usd") < 6)
            .then(pl.lit("<6"))
            .when(pl.col("risk_usd") < 12)
            .then(pl.lit("6-12"))
            .when(pl.col("risk_usd") < 20)
            .then(pl.lit("12-20"))
            .otherwise(pl.lit(">=20"))
            .alias("risk_band")
        )
        .agg(
            pl.len().alias("n"),
            pl.col("cost_usd").mean().round(2).alias("cost_usd"),
            (pl.col("cost_usd") / pl.col("risk_usd")).mean().round(3).alias("breakeven_gross_r"),
            pl.col("r").mean().round(3).alias("actual_gross_r"),
        )
        .sort("n", descending=True)
    )


if __name__ == "__main__":
    main()
