"""Step 6 — how much does the search itself inflate an in-sample number?

Step 5 found 4-clause rules worth +0.27R/trade on DEV *and* +0.77R/trade on VAL, whose procedure
nevertheless walk-forwards to -0.13R/trade. So DEV/VAL agreement is not evidence here. This
measures the inflation directly:

- **shuffled-outcome null** — permute `r` within each session (so the day structure, the trade
  count and the capacity cap are all preserved, and only the pairing of setup to outcome is
  destroyed) and re-run the identical greedy search. Whatever it finds is pure search inflation.
- **clause-budget walk-forward** — the same procedure at 1, 2, 3 and 4 clauses. If the honest
  out-of-sample number falls as the budget rises, the budget is buying overfit, not edge.
"""

from __future__ import annotations

import json

import lab
import numpy as np
import polars as pl
import search
from lab import C


def shuffle_within_day(p: pl.DataFrame, rng: np.random.Generator) -> pl.DataFrame:
    """Permute the outcome columns within each session. Keeps calendar and pool, kills the link."""
    parts = []
    for _k, g in p.group_by(["dt"], maintain_order=True):
        idx = rng.permutation(g.height)
        parts.append(
            g.with_columns(
                pl.Series("r", g["r"].to_numpy()[idx]),
                pl.Series("net_r", g["net_r"].to_numpy()[idx]),
            )
        )
    return pl.concat(parts)


def main() -> None:
    p = lab.no_holdout(lab.panel())
    d = lab.dev(p)
    out: dict = {}

    print("=== NULL TEST: greedy on DEV with outcomes shuffled within each session")
    print("    (any positive number here is the search inventing an edge from nothing)")
    rng = np.random.default_rng(11)
    nulls = []
    for i in range(20):
        sd = shuffle_within_day(d, rng)
        cl = search.greedy(sd, max_clauses=4, min_tps=0.35, r_col="net_r")
        book = C.build_book(search.selector(cl)(sd), max_per_day=2)
        v = float(book["net_r"].mean()) if book.height else 0.0
        nulls.append(v)
        print(
            f"  null draw {i + 1:>2}: {book.height:>3} trades, net {v:+.3f}/trade   "
            f"[{', '.join(str(c) for c in cl)}]"
        )
    nulls_a = np.array(nulls)
    print(
        f"\n  null in-sample greedy: mean {nulls_a.mean():+.3f}, median {np.median(nulls_a):+.3f}, "
        f"90th pct {np.quantile(nulls_a, 0.9):+.3f}, max {nulls_a.max():+.3f}"
    )
    print("  observed real greedy on DEV (min_tps=0.35, obj=net_r): +0.274/trade")
    out["null_dev_greedy"] = nulls

    print("\n=== NULL TEST: does the shuffled rule also 'confirm' on VAL?")
    hits = 0
    for _i in range(20):
        sd = shuffle_within_day(d, rng)
        sv = shuffle_within_day(lab.val(p), rng)
        cl = search.greedy(sd, max_clauses=4, min_tps=0.35, r_col="net_r")
        bv = C.build_book(search.selector(cl)(sv), max_per_day=2)
        v = float(bv["net_r"].mean()) if bv.height else 0.0
        hits += v > 0
    print(f"  {hits}/20 shuffled rules were ALSO positive on a shuffled VAL")
    out["null_val_confirm"] = hits

    print("\n=== CLAUSE BUDGET: walk-forward of the same procedure at 1..4 clauses")
    for k in (1, 2, 3, 4):
        wf = C.walk_forward(
            p, search.make_fit(max_clauses=k, min_tps=0.35, r_col="net_r"), n_blocks=6
        )
        print(
            f"  {k} clause(s): {wf['total_trades']:>3} trades  net {wf['total_net_r']:+7.1f}R "
            f"({wf['net_r_per_trade']:+.4f}/trade)  "
            f"{wf['blocks_positive']}/{wf['n_blocks']} blocks +ve"
        )
        out[f"wf_{k}_clause"] = wf

    lab.OUT.mkdir(parents=True, exist_ok=True)
    (lab.OUT / "step6_null.json").write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
