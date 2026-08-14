"""Step 6 — is the bracket's optimum a property of the exits, or of the selection it sits behind?

The raw pool wants a much wider stop (m ~ 2-3) than the shipped selection does (m ~ 1.25), so the
two are not independent. Agent A may replace selection entirely, which makes this the single most
important caveat to quantify: a ladder of selections from SHIPPED down to everything, each with its
own m-profile at a fixed target, and its own best cell.

Also tests the one explicit entanglement the brief names: SHIPPED's `stop_pct >= 0.025` floor is
measured against the *shipped* stop, so widening the stop silently raises the effective floor.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import lab as X
import polars as pl
from lab import C

M_GRID = [1.0, 1.1, 1.25, 1.4, 1.5, 1.75, 2.0, 2.5, 3.0]
T_GRID = [1.0, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0]


def sel_no_passed(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(
        (pl.col("cycle_num") <= 2)
        & (pl.col("staleness_delay_min") <= 30)
        & pl.col("entry_fill").is_between(3.0, 50.0)
        & (pl.col("stop_pct") >= 0.025)
        & pl.col("trigger_et_min").is_between(240.0, 555.0)
    )


def sel_price_only(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(
        pl.col("entry_fill").is_between(3.0, 50.0)
        & (pl.col("stop_pct") >= 0.025)
        & pl.col("trigger_et_min").is_between(240.0, 555.0)
    )


def sel_passed_only(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(pl.col("passed"))


def sel_stoppct_only(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(pl.col("stop_pct") >= 0.025)


def sel_shipped_rescaled(df: pl.DataFrame) -> pl.DataFrame:
    """SHIPPED, but with the stop_pct floor divided by 1.25 so the BRACKET's stop still clears
    2.5% of entry. Tests whether the proposal is quietly re-selecting through the floor."""
    return df.filter(
        pl.col("passed")
        & (pl.col("cycle_num") <= 2)
        & (pl.col("staleness_delay_min") <= 30)
        & pl.col("entry_fill").is_between(3.0, 50.0)
        & (pl.col("stop_pct") >= 0.025 / 1.25)
        & pl.col("trigger_et_min").is_between(240.0, 555.0)
    )


SELECTIONS: list[tuple[str, Callable | None]] = [
    ("SHIPPED", C.SHIPPED),
    ("SHIPPED, floor rescaled", sel_shipped_rescaled),
    ("SHIPPED minus `passed`", sel_no_passed),
    ("`passed` only", sel_passed_only),
    ("price/time/stop only", sel_price_only),
    ("stop_pct >= 2.5% only", sel_stoppct_only),
    ("RAW POOL", None),
]


def main() -> None:
    df, p, g, paths, pre = X.load_all()
    dv = df.filter(pl.col("split") != "holdout")
    out: dict[str, Any] = {}
    print("### m-profile at a fixed target of t = 2.0 C  (net R per trade, DEV+VAL)")
    print("  " + f"{'selection':<26}{'n':>5}" + "".join(f"{m:>8}" for m in M_GRID))
    prof = {}
    for name, sel in SELECTIONS:
        vals, n = [], 0
        for m in M_GRID:
            r = X.evaluate(dv, X.Bracket(buf_frac=m - 1.0, target_r=2.0 / m), g, p, selector=sel)
            vals.append(r["net_r_per_trade"])
            n = r["trades"]
        prof[name] = vals
        print(f"  {name:<26}{n:>5}" + "".join(f"{v:>8.3f}" for v in vals))
    out["m_profile_at_t2"] = {"m_grid": M_GRID, "profiles": prof}

    print("\n### the best (m, t) cell for each selection, and how the proposal (1.25, 2.0) does")
    for name, sel in SELECTIONS:
        cells = []
        for m in M_GRID:
            for t in T_GRID:
                r = X.evaluate(dv, X.Bracket(buf_frac=m - 1.0, target_r=t / m), g, p, selector=sel)
                sp = r.get("split", {})
                cells.append(
                    {
                        "m": m,
                        "t": t,
                        "n": r["trades"],
                        "net": r["net_r_per_trade"],
                        "dev": sp.get("dev", {}).get("net_r_per_trade", 0),
                        "val": sp.get("val", {}).get("net_r_per_trade", 0),
                        "net_r": r["net_r"],
                        "cost_r": r.get("cost_r_per_trade", 0),
                        "cap": r.get("cap_bound", 0),
                        "fill_above": r.get("pct_fill_above_high", 0),
                    }
                )
        best = max(cells, key=lambda c: c["net"])
        prop = next(c for c in cells if c["m"] == 1.25 and c["t"] == 2.0)
        ship = next(c for c in cells if c["m"] == 1.0 and c["t"] == 2.0)
        print(
            f"  {name:<26} n={prop['n']:<4} "
            f"best (m={best['m']}, t={best['t']}) net {best['net']:+.3f} | "
            f"proposal net {prop['net']:+.3f} (dev {prop['dev']:+.3f} val {prop['val']:+.3f}) | "
            f"shipped net {ship['net']:+.3f} | cost {prop['cost_r']:.3f}R "
            f"cap {prop['cap']}/{prop['n']} fill>high {prop['fill_above']:.1%}"
        )
        out[name] = {"best": best, "proposal": prop, "shipped": ship, "cells": cells}

    with (X.OUT / "selection_robustness.json").open("w") as fh:
        json.dump(out, fh, indent=1, default=float)
    print(f"\nwrote {X.OUT / 'selection_robustness.json'}")


if __name__ == "__main__":
    main()
