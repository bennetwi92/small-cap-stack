"""Step 2b — calibrate the null so "you searched too hard" cannot be a defence.

Three questions:
  1. How does the null's search strength depend on the trade floor and the clause budget?
     At what trade floor does noise stop beating +0.478?
  2. A DELIBERATELY NARROW null: only the claim's own three features, decile cuts, pick up to 3.
     This is the closest reconstruction of what CLAIM.md says happened ("the thresholds were
     chosen after looking at the quintile table for these same features").
  3. The narrowest of all: the three features fixed, only the three *cut values* searched over a
     round-number grid. If even that invents +0.478 out of noise, the rule is a coin flip.
"""

from __future__ import annotations

import itertools

import lab as L
import numpy as np
import search as S
from step2_null import run_null, summarise_null


def claim_mask_of(pop: S.Pop, df) -> np.ndarray:
    keys = set(L.claim_selector(df)["key"].to_list())
    return np.array([k in keys for k in pop.df["key"].to_list()])


def main() -> None:
    df = L.load_panel_checked()
    pop = S.Pop(df)
    base = S.shipped_mask(df)
    cm = claim_mask_of(pop, df)
    claim_v, claim_n, _ = pop.stats(base & cm)
    out: dict = {"claim": {"net_r_per_trade": round(claim_v, 4), "trades": claim_n}}

    full_menu = pop.menu(base)

    # ------------------------------------------------------------- 1. trade floor / clause budget
    L.hr("2b-1. Null strength vs trade floor and clause budget (200 iters each)")
    grid = []
    for max_clauses in (1, 2, 3):
        for min_trades in (25, 35, 50, 70, 99):
            vals = run_null(
                pop,
                base,
                full_menu,
                n_iter=200,
                seed=101 + min_trades,
                block=False,
                min_trades=min_trades,
                max_clauses=max_clauses,
            )
            a = np.array([v for v in vals if np.isfinite(v)])
            if not len(a):
                print(f"  clauses={max_clauses} floor={min_trades:>3}: no feasible rule")
                continue
            p = (int((a >= claim_v).sum()) + 1) / (len(a) + 1)
            print(
                f"  clauses={max_clauses} floor={min_trades:>3}: null median={np.median(a):+.4f} "
                f"p90={np.quantile(a, 0.9):+.4f}  P(null >= claim's {claim_v:+.3f})={p:.3f}"
            )
            grid.append(
                {
                    "max_clauses": max_clauses,
                    "min_trades": min_trades,
                    "feasible": int(len(a)),
                    "null_median": round(float(np.median(a)), 4),
                    "null_p90": round(float(np.quantile(a, 0.9)), 4),
                    "p_vs_claim": round(p, 4),
                }
            )
    out["floor_grid"] = grid

    # ---------------------------------------------------------------- 2. the narrow, faithful null
    L.hr("2b-2. Narrow null — only the claim's OWN three features, decile cuts")
    narrow: list[S.Clause] = []
    for col, op in (
        ("runup_pre_appearance", "ge"),
        ("rvol_pole", "ge"),
        ("shares_outstanding", "le"),
    ):
        x = pop.feat[col][base]
        x = x[~np.isnan(x)]
        for q in S.DECILES:
            narrow.append(S.Clause(col, op, float(np.quantile(x, q))))
    print(f"  menu = {len(narrow)} clauses (3 features x 9 deciles, prior-directed)")
    _cl, obs_narrow, obs_n = S.greedy(pop, base, menu=narrow, max_clauses=3, min_trades=25)
    print(
        f"  on REAL outcomes this search finds {[str(c) for c in _cl]} -> "
        f"{obs_n} trades {obs_narrow:+.4f}"
    )
    vals = run_null(
        pop, base, narrow, n_iter=1500, seed=77, block=False, min_trades=25, max_clauses=3
    )
    r1 = summarise_null(obs_narrow, vals, "narrow search vs its own best")
    r2 = summarise_null(claim_v, vals, "narrow search vs claim +0.478")
    out["narrow_null"] = {
        "menu": len(narrow),
        "observed_best": {
            "clauses": [str(c) for c in _cl],
            "trades": obs_n,
            "net_r_per_trade": round(obs_narrow, 4),
        },
        "vs_best": r1,
        "vs_claim": r2,
    }

    # --------------------------------------------------- 3. narrowest: round-number grid, 3 fixed
    L.hr("2b-3. Narrowest null — 3 fixed features, round-number cut grid only")
    runups = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
    rvols = [1.0, 1.5, 2.0, 3.0, 5.0]
    shares = [20e6, 50e6, 100e6, 200e6, 500e6]
    combos = list(itertools.product(runups, rvols, shares))
    print(f"  grid = {len(combos)} threshold triples")

    def best_of_grid(mr: np.ndarray | None) -> tuple[float, int]:
        bv, bn = -np.inf, 0
        for ru, rv, sh in combos:
            m = (
                base
                & (pop.feat["runup_pre_appearance"] >= ru)
                & (pop.feat["rvol_pole"] >= rv)
                & (pop.feat["shares_outstanding"] <= sh)
            )
            m = np.where(
                np.isnan(pop.feat["rvol_pole"]) | np.isnan(pop.feat["shares_outstanding"]),
                False,
                m,
            )
            v, n, _ = pop.stats(m, mr)
            if n >= 25 and v > bv:
                bv, bn = v, n
        return bv, bn

    ov, on = best_of_grid(None)
    print(f"  best on real outcomes: {ov:+.4f} over {on} trades (claim = {claim_v:+.4f}/{claim_n})")
    rng = np.random.default_rng(909)
    idx_base = np.flatnonzero(base)
    gv = []
    for _ in range(600):
        mr = pop.max_r.copy()
        mr[idx_base] = pop.max_r[rng.permutation(idx_base)]
        v, _n = best_of_grid(mr)
        gv.append(v)
    r3 = summarise_null(ov, gv, "grid best vs its own best")
    r4 = summarise_null(claim_v, gv, "grid best vs claim +0.478")
    out["grid_null"] = {
        "n_combos": len(combos),
        "observed_best": {"net_r_per_trade": round(ov, 4), "trades": on},
        "vs_best": r3,
        "vs_claim": r4,
    }

    L.write("step2b_null_calibration.json", out)
    print("\nwrote step2b_null_calibration.json")


if __name__ == "__main__":
    main()
