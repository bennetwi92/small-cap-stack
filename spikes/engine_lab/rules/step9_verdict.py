"""Step 9 — the verdict on the one surviving candidate.

Step 8 left exactly one rule standing: `hits_before_trigger <= 2 AND planned_risk >= 0.19 AND
cons_len <= 3`. It is positive on DEV, on VAL and in all six calendar blocks, and the restricted
search that produced it barely inflates (shuffled-outcome null maxed at +0.03R/trade). But its
*procedure* walk-forwards negative, which means the threshold values themselves may be fitted.

This step decides between "small real edge, badly identified thresholds" and "attractive noise":

1. 100-draw shuffled-outcome null of the restricted search — a proper p-value for the search.
2. What thresholds does the walk-forward actually pick, fold by fold? Stable = identified.
3. Bootstrap confidence interval on net R per trade, by session (not by trade — trades on the
   same day are not independent).
4. A fine threshold surface around the chosen point, so a plateau can be seen rather than asserted.
"""

from __future__ import annotations

import itertools
import json

import lab
import numpy as np
import polars as pl
import search
import step6_null
import step8_restricted as s8
from lab import C

RULE = [
    search.Clause("hits_before_trigger", "le", 2.0),
    search.Clause("planned_risk", "ge", 0.19),
    search.Clause("cons_len", "le", 3.0),
]
FEATS = [("hits_before_trigger", "le"), ("planned_risk", "ge"), ("cons_len", "le")]


def null_distribution(d: pl.DataFrame, n: int = 100) -> np.ndarray:
    rng = np.random.default_rng(101)
    vals = []
    for _ in range(n):
        sd = step6_null.shuffle_within_day(d, rng)
        sel = s8.restricted_fit(FEATS)(sd)
        bk = C.build_book(sel(sd), max_per_day=2)
        vals.append(float(bk["net_r"].mean()) if bk.height else 0.0)
    return np.array(vals)


def fold_thresholds(p: pl.DataFrame, n_blocks: int = 6, min_train: int = 60) -> list[dict]:
    dates = sorted(p["dt"].unique().to_list())
    edges = np.linspace(min_train, len(dates), n_blocks + 1).astype(int)
    out = []
    for a, b in zip(edges[:-1], edges[1:], strict=False):
        train = p.filter(pl.col("dt") < dates[a])
        test = p.filter(pl.col("dt").is_in(dates[a:b]))
        best, best_cl = -np.inf, []
        grids = []
        for col, op in FEATS:
            x = train[col].drop_nulls().cast(pl.Float64).to_numpy()
            qs = (
                (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
                if op == "le"
                else (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
            )
            grids.append([search.Clause(col, op, round(float(np.quantile(x, q)), 5)) for q in qs])
        for combo in itertools.product(*grids):
            v, _ = search.objective(train, list(combo), min_tps=0.35, max_per_day=2)
            if v > best:
                best, best_cl = v, list(combo)
        bk = C.build_book(search.selector(best_cl)(test), max_per_day=2)
        out.append(
            {
                "block": f"{dates[a]}..{dates[b - 1]}",
                "chosen": [str(c) for c in best_cl],
                "train_obj": round(best, 3),
                "test_trades": bk.height,
                "test_net": round(float(bk["net_r"].sum()), 2) if bk.height else 0.0,
            }
        )
    return out


def bootstrap_by_session(p: pl.DataFrame, clauses: list[search.Clause], n: int = 4000) -> dict:
    bk = C.build_book(search.selector(clauses)(p), max_per_day=2)
    by_day = {str(k[0]): g["net_r"].to_numpy() for k, g in bk.group_by(["dt"])}
    days = list(by_day)
    rng = np.random.default_rng(5)
    means = []
    for _ in range(n):
        pick = rng.choice(len(days), size=len(days), replace=True)
        vals = np.concatenate([by_day[days[i]] for i in pick])
        means.append(vals.mean())
    a = np.array(means)
    return {
        "trades": bk.height,
        "sessions_traded": len(days),
        "net_per_trade": round(float(bk["net_r"].mean()), 4),
        "ci95": [round(float(np.quantile(a, 0.025)), 4), round(float(np.quantile(a, 0.975)), 4)],
        "p_below_zero": round(float((a <= 0).mean()), 4),
    }


def surface(p: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for h in (1, 2, 3, 4, 6):
        for r in (0.10, 0.15, 0.19, 0.25, 0.35):
            for c in (2, 3, 4, 6):
                cl = [
                    search.Clause("hits_before_trigger", "le", float(h)),
                    search.Clause("planned_risk", "ge", r),
                    search.Clause("cons_len", "le", float(c)),
                ]
                bk = C.build_book(search.selector(cl)(p), max_per_day=2)
                if bk.height < 15:
                    continue
                rows.append(
                    {
                        "hits<=": h,
                        "risk>=": r,
                        "cons<=": c,
                        "n": bk.height,
                        "tps": round(bk.height / p["dt"].n_unique(), 2),
                        "net_per_trade": round(float(bk["net_r"].mean()), 3),
                        "net_total": round(float(bk["net_r"].sum()), 1),
                    }
                )
    return pl.DataFrame(rows)


def main() -> None:
    p = lab.no_holdout(lab.panel())
    d, v = lab.dev(p), lab.val(p)
    res: dict = {}

    obs_dev = float(C.build_book(search.selector(RULE)(d), max_per_day=2)["net_r"].mean())
    print(f"observed DEV net/trade for the rule: {obs_dev:+.4f}")
    print("\n=== 1. 100-draw shuffled-outcome null of the restricted search (DEV)")
    nul = null_distribution(d, n=100)
    pval = float((nul >= obs_dev).mean())
    print(
        f"  null: mean {nul.mean():+.3f}  90th {np.quantile(nul, 0.9):+.3f}  "
        f"99th {np.quantile(nul, 0.99):+.3f}  max {nul.max():+.3f}"
    )
    print(f"  fraction of nulls >= observed: {pval:.3f}")
    res["null"] = {
        "mean": float(nul.mean()),
        "p90": float(np.quantile(nul, 0.9)),
        "max": float(nul.max()),
        "p_value": pval,
        "observed_dev": obs_dev,
    }

    print("\n=== 2. thresholds the walk-forward picks, fold by fold")
    ft = fold_thresholds(p)
    for f in ft:
        print(
            f"  {f['block']}  train_obj {f['train_obj']:+.3f}  test n={f['test_trades']:>3} "
            f"net {f['test_net']:+6.2f}   {f['chosen']}"
        )
    res["fold_thresholds"] = ft

    print("\n=== 3. bootstrap by session (trades on one day are not independent)")
    for label, dd in (("DEV", d), ("VAL", v), ("DEV+VAL", p)):
        b = bootstrap_by_session(dd, RULE)
        print(
            f"  {label:<8} {b['trades']:>3} trades over {b['sessions_traded']:>3} sessions  "
            f"net/trade {b['net_per_trade']:+.3f}  "
            f"95% CI [{b['ci95'][0]:+.3f}, {b['ci95'][1]:+.3f}]  "
            f"P(<=0) {b['p_below_zero']:.3f}"
        )
        res[f"bootstrap_{label}"] = b

    print("\n=== 4. threshold surface on DEV+VAL (is there a plateau?)")
    s = surface(p)
    with pl.Config(tbl_rows=120, tbl_width_chars=140):
        print(s.sort("net_per_trade", descending=True))
    res["surface"] = s.to_dicts()
    print(f"\n  cells positive: {(s['net_per_trade'] > 0).sum()} / {s.height}")
    print(
        f"  cells with tps>=0.35 that are positive: "
        f"{s.filter((pl.col('tps') >= 0.35) & (pl.col('net_per_trade') > 0)).height} / "
        f"{s.filter(pl.col('tps') >= 0.35).height}"
    )

    lab.OUT.mkdir(parents=True, exist_ok=True)
    (lab.OUT / "step9_verdict.json").write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
