"""Agent B — put the pieces together, and the two extra diagnostics the sweeps raised.

1. slot quality: is the day's Nth trigger worse than its first? (the capacity answer's cause)
2. the irreducible drag floor: the 2-tick stop slippage is proportional to size, so a bigger
   account never removes it. What is left when the account is infinite?
3. the combined proposal, its DEV/VAL split, and the equity curve.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sim as S  # noqa: E402
import study as T  # noqa: E402


def slot_quality(work: pl.DataFrame) -> None:
    T.hdr("A. SLOT QUALITY — the day's Nth pre-market trigger, capacity off")
    print("   This is why capacity matters: it is not 'more trades = more cost', it is that the")
    print("   later triggers of a day are worse setups. Ordering is by time, never by rank.")
    for sel in ("shipped", "pool"):
        x = S.simulate(S.SELECTIONS[sel](work), replace(T.BASE, max_per_day=99))
        x = x.with_columns((pl.int_range(pl.len()).over("dt") + 1).alias("slot"))
        print(f"\n  -- {sel} --")
        print(
            x.with_columns(pl.min_horizontal(pl.col("slot"), pl.lit(5)).alias("slot_c"))
            .group_by("slot_c")
            .agg(
                pl.len().alias("n"),
                pl.col("r").mean().round(3).alias("gross_r"),
                (pl.col("r") > 0).mean().round(3).alias("win"),
                pl.col("net_r").mean().round(3).alias("net_r"),
                pl.col("cost_r").mean().round(3).alias("drag"),
                pl.col("trigger_et_min").median().alias("med_trig_et"),
            )
            .sort("slot_c")
        )


def drag_floor(work: pl.DataFrame) -> None:
    T.hdr("B. THE IRREDUCIBLE DRAG FLOOR — what a bigger account cannot fix")
    print("   commission is fixed per trade (dies with size); the 2-tick stop slip is per share")
    print("   (never dies). At infinite equity, drag -> 2 ticks / (entry - stop) on every loser.")
    for sel in ("shipped", "pool"):
        big = S.simulate(S.SELECTIONS[sel](work), replace(T.BASE, equity=1e7))
        small = S.simulate(S.SELECTIONS[sel](work), T.BASE)
        print(
            f"  {sel:<8} drag at $500 = {small['cost_r'].mean():.1%}   "
            f"floor at infinite equity = {big['cost_r'].mean():.1%}   "
            f"gross edge = {big['r'].mean():+.3f}R/trade   "
            f"=> best possible net = {big['net_r'].mean():+.3f}R/trade"
        )


def combo(work: pl.DataFrame) -> dict[str, Any]:
    T.hdr("C. COMBINED — capacity x notional cap x cost exclusion (settled-cash legal only)")
    cands: list[tuple[str, S.RiskConfig]] = [
        ("shipped default: 2/day cap50", T.BASE),
        ("2/day cap50 + cost<=10%", replace(T.BASE, max_cost_r=0.10)),
        ("1/day cap100", replace(T.BASE, max_per_day=1, position_fraction=1.0)),
        (
            "1/day cap100 + cost<=10%",
            replace(T.BASE, max_per_day=1, position_fraction=1.0, max_cost_r=0.10),
        ),
        (
            "1/day cap100 + cost<=12%",
            replace(T.BASE, max_per_day=1, position_fraction=1.0, max_cost_r=0.12),
        ),
        (
            "1/day cap100 + cost<=8%",
            replace(T.BASE, max_per_day=1, position_fraction=1.0, max_cost_r=0.08),
        ),
        (
            "1/day cap100 risk8% + cost<=10%",
            replace(
                T.BASE, max_per_day=1, position_fraction=1.0, risk_fraction=0.08, max_cost_r=0.10
            ),
        ),
        (
            "1/day cap100 + cost<=10% + stop$>=0.35",
            replace(
                T.BASE, max_per_day=1, position_fraction=1.0, max_cost_r=0.10, min_stop_usd=0.35
            ),
        ),
        ("2/day cap50 + stop$>=0.35 only", replace(T.BASE, min_stop_usd=0.35)),
        (
            "1/day cap100 + stop$>=0.35 only",
            replace(T.BASE, max_per_day=1, position_fraction=1.0, min_stop_usd=0.35),
        ),
    ]
    out = {}
    for sel in ("shipped", "pool"):
        print(f"\n  -- selection = {sel} --")
        for nm, cfg in cands:
            r = T.show(work, cfg, nm, sel)
            if sel == "shipped":
                for sp, b in r["splits"].items():
                    if b["trades"]:
                        print(f"      {sp:<30} " + S.line(b))
                out[nm] = r
    return out


def equity_curve(work: pl.DataFrame, cfg: S.RiskConfig) -> list[dict[str, Any]]:
    T.hdr("D. MINIMUM ACCOUNT SIZE for the proposed config")
    rows = []
    for sel in ("shipped", "pool"):
        print(f"\n  -- selection = {sel} --")
        for eq in (250, 500, 750, 1000, 1500, 2000, 3000, 5000, 10000, 100000):
            r = T.show(work, replace(cfg, equity=float(eq)), f"equity ${eq:,}", sel)
            rows.append(
                {
                    "selection": sel,
                    "equity": eq,
                    **{
                        k: r[k]
                        for k in (
                            "trades",
                            "gross_r",
                            "net_r",
                            "net_r_per_trade",
                            "net_usd",
                            "cost_r_per_trade",
                            "cap_bound_pct",
                            "mean_risk_usd",
                            "max_dd_net_usd",
                            "max_dd_net_r",
                        )
                    },
                }
            )
    return rows


def compounding(work: pl.DataFrame, cfg: S.RiskConfig) -> None:
    T.hdr("E. COMPOUNDING — day-open equity, so no intraday lookahead")
    for sel in ("shipped", "pool"):
        x = S.simulate(S.SELECTIONS[sel](work), replace(cfg, compound=True))
        if x.is_empty():
            continue
        final = float(x["equity_before"][-1] + x["net_usd"][-1])
        eq = x["equity_before"].to_numpy()
        print(
            f"  {sel:<8} $500 -> ${final:,.2f} over {x.height} trades   "
            f"peak ${eq.max():,.2f}  trough ${eq.min():,.2f}"
        )


def main() -> None:
    work = S.load_work()
    slot_quality(work)
    drag_floor(work)
    combo(work)
    prop = replace(T.BASE, max_per_day=1, position_fraction=1.0, max_cost_r=0.10)
    rows = equity_curve(work, prop)
    compounding(work, prop)
    S.OUT.mkdir(parents=True, exist_ok=True)
    (S.OUT / "equity_curve.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
