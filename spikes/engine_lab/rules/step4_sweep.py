"""Step 4 — univariate threshold sweep, fitted on DEV, then read on VAL.

For every candidate feature, every decile cut in both directions, booked at 2/day. We are looking
for a *plateau*: several adjacent thresholds all positive, in both splits. A single good cell is
noise and is reported as such.
"""

from __future__ import annotations

import json

import lab
import numpy as np
import polars as pl
from lab import C
from rules_def import BOOL_FEATURES, CANDIDATE_FEATURES, SHAPE_GATES


def booked(p: pl.DataFrame, mask: pl.Expr) -> dict:
    sel = p.filter(mask)
    if sel.is_empty():
        return {"n": 0, "net": 0.0, "gross": 0.0, "net_total": 0.0, "tps": 0.0}
    book = C.build_book(sel, max_per_day=2)
    return {
        "n": book.height,
        "net": round(float(book["net_r"].mean()), 4),
        "gross": round(float(book["r"].mean()), 4),
        "net_total": round(float(book["net_r"].sum()), 2),
        "tps": round(book.height / p["dt"].n_unique(), 3),
    }


def sweep_feature(d: pl.DataFrame, v: pl.DataFrame, col: str) -> list[dict]:
    x = d[col].drop_nulls().cast(pl.Float64).to_numpy()
    if len(x) < 300:
        return []
    cuts = sorted({float(q) for q in np.quantile(x, np.arange(0.1, 0.95, 0.1))})
    rows = []
    for c in cuts:
        for op, expr in (("ge", pl.col(col) >= c), ("le", pl.col(col) <= c)):
            bd, bv = booked(d, expr), booked(v, expr)
            if bd["n"] < 40:
                continue
            rows.append(
                {
                    "col": col,
                    "op": op,
                    "cut": round(c, 5),
                    "dev_n": bd["n"],
                    "dev_net": bd["net"],
                    "dev_gross": bd["gross"],
                    "val_n": bv["n"],
                    "val_net": bv["net"],
                    "val_gross": bv["gross"],
                }
            )
    return rows


def main() -> None:
    p = lab.no_holdout(lab.panel())
    d, v = lab.dev(p), lab.val(p)
    print("DEV  all rows booked 2/day: ", booked(d, pl.lit(True)))
    print("VAL  all rows booked 2/day: ", booked(v, pl.lit(True)))

    out = []
    for col in CANDIDATE_FEATURES:
        out += sweep_feature(d, v, col)
    for col in BOOL_FEATURES:
        for flag in (True, False):
            bd, bv = booked(d, pl.col(col) == flag), booked(v, pl.col(col) == flag)
            if bd["n"] >= 40:
                out.append(
                    {
                        "col": col,
                        "op": "eq",
                        "cut": float(flag),
                        "dev_n": bd["n"],
                        "dev_net": bd["net"],
                        "dev_gross": bd["gross"],
                        "val_n": bv["n"],
                        "val_net": bv["net"],
                        "val_gross": bv["gross"],
                    }
                )
    for g in SHAPE_GATES:
        e = ~pl.col("failing_gates").str.contains(g, literal=True)
        bd, bv = booked(d, e), booked(v, e)
        out.append(
            {
                "col": f"gate:{g}",
                "op": "pass",
                "cut": 1.0,
                "dev_n": bd["n"],
                "dev_net": bd["net"],
                "dev_gross": bd["gross"],
                "val_n": bv["n"],
                "val_net": bv["net"],
                "val_gross": bv["gross"],
            }
        )

    t = pl.DataFrame(out)
    lab.OUT.mkdir(parents=True, exist_ok=True)
    (lab.OUT / "step4_sweep.json").write_text(json.dumps(out, indent=1))

    with pl.Config(tbl_rows=45, tbl_width_chars=200):
        print("\n=== best 30 by DEV net/trade (dev_n>=60) — read val before believing any of it")
        print(t.filter(pl.col("dev_n") >= 60).sort("dev_net", descending=True).head(30))
        print("\n=== cuts positive in BOTH splits (dev_n>=50, val_n>=25)")
        print(
            t.filter(
                (pl.col("dev_net") > 0)
                & (pl.col("val_net") > 0)
                & (pl.col("dev_n") >= 50)
                & (pl.col("val_n") >= 25)
            ).sort("dev_net", descending=True)
        )


if __name__ == "__main__":
    main()
