"""Step 5 — greedy conjunction search on DEV, read on VAL, and the procedure walk-forwarded.

Two questions, in this order:
1. What is the best <=4-clause rule DEV can produce? (in-sample; an upper bound, not a result)
2. Does the *procedure* that produced it make money out of sample? `walk_forward(make_fit(...))`
   re-runs the whole greedy search on each expanding training block and trades the next one.
"""

from __future__ import annotations

import json

import lab
import polars as pl
import search
from lab import C


def report(name: str, clauses: list[search.Clause], p: pl.DataFrame) -> dict:
    sel = search.selector(clauses)
    out: dict = {"name": name, "clauses": [str(c) for c in clauses]}
    print(f"\n== {name}")
    for c in clauses:
        print(f"   {c}")
    for label, d in (("DEV", lab.dev(p)), ("VAL", lab.val(p))):
        book = C.build_book(sel(d), max_per_day=2)
        s = C.score(book, sessions=d["dt"].n_unique())
        print(f"   {label}  " + C.brief(s))
        out[label.lower()] = {
            k: s[k]
            for k in (
                "trades",
                "net_r",
                "net_r_per_trade",
                "gross_r",
                "r_per_trade",
                "win_rate",
                "trades_per_session",
                "max_dd_net_r",
            )
        }
    return out


def main() -> None:
    p = lab.no_holdout(lab.panel())
    d = lab.dev(p)
    results = []

    for min_tps, tag in ((0.5, "tps>=0.5"), (0.35, "tps>=0.35"), (0.25, "tps>=0.25")):
        for r_col in ("net_r", "r"):
            print(f"\n--- greedy on DEV, objective={r_col}, {tag}")
            cl = search.greedy(d, max_clauses=4, min_tps=min_tps, r_col=r_col, verbose=True)
            results.append(report(f"greedy[{r_col},{tag}]", cl, p))

    # Hand-built structural candidates, chosen from step 1-3 mechanics rather than from a search.
    hand = {
        "shape-fixed (pole_height+cons_len gates only)": [
            search.Clause("pole_height", "gate", 1.0),
            search.Clause("cons_len", "gate", 1.0),
        ],
        "cheap-to-trade (stop>=10%)": [search.Clause("stop_pct", "ge", 0.10)],
        "cheap + already-moved": [
            search.Clause("stop_pct", "ge", 0.10),
            search.Clause("ext_at_peak", "ge", 0.25),
        ],
        "cheap + moved + shape": [
            search.Clause("stop_pct", "ge", 0.10),
            search.Clause("ext_at_peak", "ge", 0.25),
            search.Clause("pole_height", "gate", 1.0),
        ],
        "shipped-like without `passed`": [
            search.Clause("entry_fill", "ge", 3.0),
            search.Clause("entry_fill", "le", 50.0),
            search.Clause("stop_pct", "ge", 0.025),
            search.Clause("staleness_delay_min", "le", 30.0),
        ],
    }
    for name, cl in hand.items():
        results.append(report(name, cl, p))

    print("\n\n=== WALK-FORWARD OF THE PROCEDURE (fit greedy on the past, trade the next block)")
    for min_tps, r_col in ((0.5, "net_r"), (0.35, "net_r"), (0.5, "r")):
        wf = C.walk_forward(
            p, search.make_fit(max_clauses=4, min_tps=min_tps, r_col=r_col), n_blocks=6
        )
        print(
            f"\n greedy(min_tps={min_tps}, obj={r_col}):  "
            f"{wf['total_trades']} trades, net {wf['total_net_r']:+.1f}R "
            f"({wf['net_r_per_trade']:+.4f}/trade), "
            f"{wf['blocks_positive']}/{wf['n_blocks']} blocks +ve"
        )
        for b in wf["blocks"]:
            print(
                f"    {b['from']} .. {b['to']}  n={b['trades']:>3}  "
                f"net {b['net_r']:+7.2f}R  ({b['net_r_per_trade']:+.3f})"
            )
        results.append({"name": f"walkforward_greedy_{min_tps}_{r_col}", "walk_forward": wf})

    print("\n=== WALK-FORWARD of SHIPPED (a fixed rule, for reference)")
    wf = C.walk_forward(p, lambda _t: C.SHIPPED, n_blocks=6)
    print(
        f" SHIPPED: {wf['total_trades']} trades, net {wf['total_net_r']:+.1f}R "
        f"({wf['net_r_per_trade']:+.4f}/trade), "
        f"{wf['blocks_positive']}/{wf['n_blocks']} blocks +ve"
    )
    for b in wf["blocks"]:
        print(
            f"    {b['from']} .. {b['to']}  n={b['trades']:>3}  "
            f"net {b['net_r']:+7.2f}R  ({b['net_r_per_trade']:+.3f})"
        )

    lab.OUT.mkdir(parents=True, exist_ok=True)
    (lab.OUT / "step5_greedy.json").write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
