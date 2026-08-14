"""Step 8 — is there anything left once the search is disciplined?

Step 6 killed the free 4-clause greedy: its walk-forward is negative at every clause budget, and
on shuffled outcomes it invents +0.08R/trade in-sample. But step 7A found that DEV-positive
conjunctions are *rare* (0 of 334 random 3-clause draws) and that DEV and VAL net R correlate at
+0.44, which is not what a pure-noise surface looks like.

So the question narrows: pick the feature set **by prior, once**, then let only the thresholds be
refit, and walk-forward that. A procedure with 2 free numbers instead of 8 free choices is the
only kind this sample size can support.

Priors, taken from step 1 (all measured before the search, all with a trading story):
- `hits_before_trigger` LOW — take the break the first time the name is surfaced, not after it
  has been grinding on the scanner. (This is the causal cousin of the leaky `n_scanner_hits`.)
- `planned_risk` HIGH — risk per share in cents. On a $500 account a 3-cent stop costs >50% of R
  in commission; this is as much a cost rule as a setup rule.
- `cons_len` LOW — the one shape gate besides `pole_height` with positive value in step 2.
"""

from __future__ import annotations

import json

import lab
import numpy as np
import polars as pl
import search
from lab import C

FIXED = {
    "hits<=2": [search.Clause("hits_before_trigger", "le", 2.0)],
    "risk>=0.19": [search.Clause("planned_risk", "ge", 0.19)],
    "hits<=2 & risk>=0.19": [
        search.Clause("hits_before_trigger", "le", 2.0),
        search.Clause("planned_risk", "ge", 0.19),
    ],
    "hits<=2 & risk>=0.19 & cons_len<=3": [
        search.Clause("hits_before_trigger", "le", 2.0),
        search.Clause("planned_risk", "ge", 0.19),
        search.Clause("cons_len", "le", 3.0),
    ],
    "hits<=2 & risk>=0.19 & cons<=3 & rvol>=0.47": [
        search.Clause("hits_before_trigger", "le", 2.0),
        search.Clause("planned_risk", "ge", 0.19),
        search.Clause("cons_len", "le", 3.0),
        search.Clause("rvol_pole", "ge", 0.4737),
    ],
}


def blocks(p: pl.DataFrame, clauses: list[search.Clause], n: int = 6) -> list[dict]:
    dates = sorted(p["dt"].unique().to_list())
    edges = np.linspace(0, len(dates), n + 1).astype(int)
    out = []
    for a, b in zip(edges[:-1], edges[1:], strict=False):
        d = p.filter(pl.col("dt").is_in(dates[a:b]))
        bk = C.build_book(search.selector(clauses)(d), max_per_day=2)
        out.append(
            {
                "from": str(dates[a]),
                "to": str(dates[b - 1]),
                "sessions": d["dt"].n_unique(),
                "trades": bk.height,
                "net_r": round(float(bk["net_r"].sum()), 2) if bk.height else 0.0,
                "net_per_trade": round(float(bk["net_r"].mean()), 3) if bk.height else 0.0,
            }
        )
    return out


def restricted_fit(features: list[tuple[str, str]], *, min_tps: float = 0.35):
    """A procedure with only thresholds free: fixed features/directions, quantile cuts refit."""

    def fit(train: pl.DataFrame):
        best, best_cl = -np.inf, []
        grids = []
        for col, op in features:
            x = train[col].drop_nulls().cast(pl.Float64).to_numpy()
            qs = (
                (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
                if op == "le"
                else (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
            )
            grids.append([search.Clause(col, op, round(float(np.quantile(x, q)), 5)) for q in qs])
        import itertools

        for combo in itertools.product(*grids):
            v, _ = search.objective(train, list(combo), min_tps=min_tps, max_per_day=2)
            if v > best:
                best, best_cl = v, list(combo)
        return search.selector(best_cl)

    return fit


def main() -> None:
    p = lab.no_holdout(lab.panel())
    res: dict = {}

    print("=== fixed rules: DEV / VAL / six calendar blocks over DEV+VAL")
    for name, cl in FIXED.items():
        row: dict = {"clauses": [str(c) for c in cl]}
        print(f"\n-- {name}")
        for label, d in (("DEV", lab.dev(p)), ("VAL", lab.val(p))):
            bk = C.build_book(search.selector(cl)(d), max_per_day=2)
            s = C.score(bk, sessions=d["dt"].n_unique())
            print(f"   {label}  " + C.brief(s))
            row[label.lower()] = {
                k: s[k]
                for k in (
                    "trades",
                    "net_r",
                    "net_r_per_trade",
                    "gross_r",
                    "win_rate",
                    "trades_per_session",
                )
            }
        bl = blocks(p, cl)
        row["blocks"] = bl
        pos = sum(1 for b in bl if b["net_r"] > 0)
        print(
            f"   blocks: {pos}/{len(bl)} positive  "
            + "  ".join(f"{b['net_r']:+.1f}({b['trades']})" for b in bl)
        )
        row["blocks_positive"] = pos
        res[name] = row

    print("\n\n=== RESTRICTED PROCEDURE walk-forward (features fixed by prior, thresholds refit)")
    for feats, label in (
        ([("hits_before_trigger", "le"), ("planned_risk", "ge")], "hits + risk"),
        (
            [("hits_before_trigger", "le"), ("planned_risk", "ge"), ("cons_len", "le")],
            "hits + risk + cons_len",
        ),
    ):
        wf = C.walk_forward(p, restricted_fit(feats), n_blocks=6)
        print(
            f"\n {label}: {wf['total_trades']} trades, net {wf['total_net_r']:+.1f}R "
            f"({wf['net_r_per_trade']:+.4f}/trade), "
            f"{wf['blocks_positive']}/{wf['n_blocks']} blocks +ve"
        )
        for b in wf["blocks"]:
            print(
                f"    {b['from']} .. {b['to']}  n={b['trades']:>3}  "
                f"net {b['net_r']:+7.2f}R  ({b['net_r_per_trade']:+.3f})"
            )
        res[f"wf_restricted[{label}]"] = wf

    print(
        "\n=== FIXED-THRESHOLD walk-forward (no refit at all — the thresholds below are hand-set)"
    )
    for name, cl in FIXED.items():
        wf = C.walk_forward(p, lambda _t, _cl=cl: search.selector(_cl), n_blocks=6)
        print(
            f"  {name:<46} {wf['total_trades']:>3} trades  net {wf['total_net_r']:+7.1f}R "
            f"({wf['net_r_per_trade']:+.4f})  {wf['blocks_positive']}/{wf['n_blocks']} +ve"
        )
        res[name]["wf_fixed"] = wf

    print("\n=== NULL: the restricted procedure on shuffled outcomes (in-sample DEV)")
    rng = np.random.default_rng(23)
    import step6_null

    vals = []
    for _ in range(15):
        sd = step6_null.shuffle_within_day(lab.dev(p), rng)
        sel = restricted_fit(
            [("hits_before_trigger", "le"), ("planned_risk", "ge"), ("cons_len", "le")]
        )(sd)
        bk = C.build_book(sel(sd), max_per_day=2)
        vals.append(float(bk["net_r"].mean()) if bk.height else 0.0)
    a = np.array(vals)
    print(
        f"  null in-sample: mean {a.mean():+.3f}  "
        f"90th pct {np.quantile(a, 0.9):+.3f}  max {a.max():+.3f}"
    )
    res["null_restricted_dev"] = vals

    lab.OUT.mkdir(parents=True, exist_ok=True)
    (lab.OUT / "step8_restricted.json").write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
