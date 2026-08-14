"""Step 3 — the surface with stop and target genuinely decoupled.

Step 2's grid is misleading in one specific way: `target_r` is denominated in the bracket's own
risk, so moving the stop moves the target *in price* even though the column heading didn't change.
"cons+25%, 1.5R" is a target 1.875 consolidation-ranges above entry — almost exactly where the
shipped 2R target already sat. So that grid cannot tell you whether the gain came from the stop or
from the target.

Here both axes are in the same fixed unit — **C, the shipped consolidation range** (entry minus the
consolidation low) — which does not move when the bracket does:

    stop   = entry - m * C          m = 1.0 is the shipped stop
    target = entry + t * C          t = 2.0 is the shipped 2R target, in price

The realised R multiple of a winner is then simply t/m.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import lab as X
import numpy as np
import polars as pl
from lab import C

M_GRID = [0.6, 0.8, 1.0, 1.15, 1.25, 1.4, 1.5, 1.75, 2.0, 2.5, 3.0]
T_GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 6.0]


def cell(
    df: pl.DataFrame,
    m: float,
    t: float,
    g: X.Geom,
    p: X.Packed,
    *,
    selector: Callable[[pl.DataFrame], pl.DataFrame] | None,
    exit_930: bool = False,
) -> dict[str, Any]:
    b = X.Bracket(buf_frac=m - 1.0, target_r=t / m, exit_at_930=exit_930)
    return X.evaluate(df, b, g, p, selector=selector)


def grid(
    dv: pl.DataFrame,
    g: X.Geom,
    p: X.Packed,
    *,
    selector: Callable[[pl.DataFrame], pl.DataFrame] | None,
    label: str,
) -> list[dict[str, Any]]:
    dev = dv.filter(pl.col("split") == "dev")
    val = dv.filter(pl.col("split") == "val")
    rows = []
    for m in M_GRID:
        for t in T_GRID:
            ra = cell(dv, m, t, g, p, selector=selector)
            rd = cell(dev, m, t, g, p, selector=selector)
            rv = cell(val, m, t, g, p, selector=selector)
            rows.append(
                {
                    "sel": label,
                    "m": m,
                    "t": t,
                    "r_mult": round(t / m, 2),
                    "n": ra["trades"],
                    "net": ra["net_r_per_trade"],
                    "net_total": ra["net_r"],
                    "gross": ra["r_per_trade"],
                    "dev": rd["net_r_per_trade"],
                    "val": rv["net_r_per_trade"],
                    "win": ra["win_rate"],
                    "stopped": ra["pct_stopped"],
                    "same_bar": ra["pct_same_bar"],
                    "open930": ra["pct_open_930"],
                    "cost_r": ra.get("cost_r_per_trade", 0.0),
                    "cap": ra.get("cap_bound", 0),
                }
            )
    return rows


def heat(rows: list[dict[str, Any]], key: str, title: str) -> None:
    print(f"\n### {title} [{key}]   rows = stop m x C, cols = target t x C")
    print("  " + f"{'m':<6}" + "".join(f"{t:>8}" for t in T_GRID))
    for m in M_GRID:
        line = f"  {m:<6}"
        for t in T_GRID:
            v = [r[key] for r in rows if r["m"] == m and r["t"] == t]
            line += f"{v[0]:>8.3f}" if v else f"{'':>8}"
        print(line)


def main() -> None:
    df, p, g, paths, pre = X.load_all()
    dv = df.filter(pl.col("split") != "holdout")
    out = {}
    for label, sel in (("SHIPPED", C.SHIPPED), ("RAW POOL", None)):
        rows = grid(dv, g, p, selector=sel, label=label)
        out[label] = rows
        heat(rows, "net", f"{label} — NET R/trade, DEV+VAL")
        heat(rows, "dev", f"{label} — NET R/trade, DEV only")
        heat(rows, "val", f"{label} — NET R/trade, VAL only")
        heat(rows, "gross", f"{label} — GROSS R/trade, DEV+VAL")
        heat(rows, "win", f"{label} — win rate, DEV+VAL")
        heat(rows, "same_bar", f"{label} — share of trades resolved by the same-bar assumption")
        n = rows[0]["n"]
        print(f"\n  ({label}: {n} trades in DEV+VAL, identical in every cell)")
        pos = [r for r in rows if r["dev"] > 0 and r["val"] > 0]
        print(f"  cells positive on BOTH dev and val: {len(pos)} / {len(rows)}")
        for r in sorted(pos, key=lambda r: -min(r["dev"], r["val"]))[:15]:
            print(
                f"    m={r['m']:<5} t={r['t']:<5} ({r['r_mult']}R)  net {r['net']:+.3f} "
                f"dev {r['dev']:+.3f} val {r['val']:+.3f}  win {r['win']:.1%} "
                f"stop {r['stopped']:.1%} same-bar {r['same_bar']:.1%}"
            )
    X.OUT.mkdir(parents=True, exist_ok=True)
    with (X.OUT / "decoupled.json").open("w") as fh:
        json.dump(out, fh, indent=1, default=float)
    _ = np


if __name__ == "__main__":
    main()
