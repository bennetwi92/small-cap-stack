"""Step 4 — the same decomposition on the LARGE sample, where the book's 35 trades cannot hide.

The book is 35 trades. Every row of the population is 3,639. The 2-a-day cap and the
earliest-first rule mean a filter changes *which* rows are booked as well as *whether* they are
good, so a book result mixes selection quality with book composition. Row-level means separate the
two: they answer "are the rows this clause keeps better rows", with no capacity effect at all.

Reported per split and per source, because a clause that only improves rows in one half of the
data is a measurement artefact of that half.
"""

from __future__ import annotations

import json
from typing import Any

import features
import polars as pl
import speclab as S
import sweeps as W
from speclab import C

CLAUSES: dict[str, list[tuple[str, str, float]]] = {
    "(none)": [],
    "runup>=0.15": [W.ORIG[0]],
    "rvol>=2.0": [W.ORIG[1]],
    "shares<=50e6": [W.ORIG[2]],
    "shares NOT NULL": [("shares_outstanding", "le", 1e15)],
    "runup+rvol": [W.ORIG[0], W.ORIG[1]],
    "runup+shares": [W.ORIG[0], W.ORIG[2]],
    "rvol+shares": [W.ORIG[1], W.ORIG[2]],
    "AND-of-3": W.ORIG,
    "mktcap<=475e6": [("mktcap", "le", 4.747e8)],
    "ext_at_trigger>=0.15": [("ext_at_trigger", "ge", 0.15)],
}


def stats(g: pl.DataFrame) -> dict[str, Any]:
    if g.is_empty():
        return {"n": 0}
    return {
        "n": g.height,
        "row_net_rpt": round(float(g["net_r"].mean()), 4),
        "row_gross_rpt": round(float(g["r"].mean()), 4),
        "rate_2r": round(float((g["max_r"] >= 2.0).mean()), 4),
        "rate50": round(float((g["max_gain_pct"] >= 0.5).mean()), 4),
        "mean_max_r": round(float(g["max_r"].mean()), 4),
        "median_max_r": round(float(g["max_r"].median()), 4),
    }


def main() -> None:
    df = features.attach(S.panel(2.0))
    sh = C.SHIPPED(df)
    out: dict[str, Any] = {}
    for popname, pop in (("all_rows", df), ("shipped_rows", sh)):
        table: dict[str, Any] = {}
        for label, cls in CLAUSES.items():
            sel = W.apply_clauses(pop, cls)
            row: dict[str, Any] = {"all": stats(sel)}
            for split in ("dev", "val", "holdout"):
                row[split] = stats(sel.filter(pl.col("split") == split))
            for src in ("recon", "live"):
                row[src] = stats(sel.filter(pl.col("source") == src))
            table[label] = row
        out[popname] = table
        print(f"\n=== {popname} — row-level mean net R (n) ===")
        print(
            f"  {'clause':<22}{'all':>16}{'dev':>16}{'val':>16}{'holdout':>16}"
            f"{'recon':>16}{'live':>16}"
        )
        for label, row in table.items():
            cells = []
            for k in ("all", "dev", "val", "holdout", "recon", "live"):
                s = row[k]
                cells.append(f"{s.get('row_net_rpt', 0):+.3f}({s['n']})" if s["n"] else "-")
            print(f"  {label:<22}" + "".join(f"{c:>16}" for c in cells))
        print(f"\n=== {popname} — rate of a 2R+ excursion (n) ===")
        for label, row in table.items():
            cells = []
            for k in ("all", "dev", "val", "holdout", "recon", "live"):
                s = row[k]
                cells.append(f"{s.get('rate_2r', 0) * 100:.1f}%({s['n']})" if s["n"] else "-")
            print(f"  {label:<22}" + "".join(f"{c:>16}" for c in cells))

    # how many of the three splits x two sources does each clause improve on its own baseline?
    cons: dict[str, Any] = {}
    for popname, table in out.items():
        base = table["(none)"]
        for label, row in table.items():
            if label == "(none)":
                continue
            wins = 0
            cells = {}
            for k in ("dev", "val", "holdout", "recon", "live"):
                if row[k]["n"] >= 5 and base[k]["n"]:
                    d = row[k]["row_net_rpt"] - base[k]["row_net_rpt"]
                    cells[k] = round(d, 4)
                    wins += int(d > 0)
            cons[f"{popname}|{label}"] = {"improves": wins, "of": len(cells), "deltas": cells}
    out["consistency"] = cons
    print("\n=== consistency: does the clause improve row net R in every slice? ===")
    for k, v in cons.items():
        print(f"  {k:<40} {v['improves']}/{v['of']}  {json.dumps(v['deltas'])}")

    (S.OUT / "step4_rowlevel.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {S.OUT / 'step4_rowlevel.json'}")


if __name__ == "__main__":
    main()
