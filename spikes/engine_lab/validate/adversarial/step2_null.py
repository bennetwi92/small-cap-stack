"""Step 2 — THE test. Run the same search on scrambled outcomes, many times.

If a greedy 3-clause search over decile cuts invents rules that look like +0.478R/trade when the
outcomes have been shuffled, then the observed rule is inside its own null and means nothing.

Two nulls:
  A. permute `max_r` among the rows the search operates on (destroys feature->outcome, keeps the
     outcome distribution and every feature exactly).
  B. permute `max_r` in whole-session blocks (keeps within-day clustering of outcomes, which #690
     said might exist).

Also: the multiple-comparisons accounting — how many clause combinations the search evaluates.
"""

from __future__ import annotations

import time

import lab as L
import numpy as np
import polars as pl
import search as S
from lab import C


def check_pop_matches_harness(pop: S.Pop, base: np.ndarray, df: pl.DataFrame) -> dict:
    """Prove the numpy book/scorer reproduces `common.score` for the claim's rule."""
    keys = set(L.claim_selector(df)["key"].to_list())
    mask = base & np.array([k in keys for k in pop.df["key"].to_list()])
    v, n, tot = pop.stats(mask)
    res = L.score_sel(df, L.claim_selector(df))
    return {
        "numpy": {"trades": n, "net_r_per_trade": round(v, 6), "net_r": round(tot, 4)},
        "harness": {
            "trades": res["trades"],
            "net_r_per_trade": res["net_r_per_trade"],
            "net_r": res["net_r"],
        },
        "agree": n == res["trades"] and abs(tot - res["net_r"]) < 0.01,
    }


def run_null(
    pop: S.Pop,
    base: np.ndarray,
    menu: list[S.Clause],
    *,
    n_iter: int,
    seed: int,
    block: bool,
    min_trades: int,
    max_clauses: int,
) -> list[float]:
    rng = np.random.default_rng(seed)
    idx_base = np.flatnonzero(base)
    vals = []
    for _ in range(n_iter):
        mr = pop.max_r.copy()
        if block:
            days = pop.day_idx[idx_base]
            uniq = np.unique(days)
            perm = rng.permutation(len(uniq))
            remap = dict(zip(uniq, uniq[perm], strict=True))
            # move each day's outcome block onto another day's rows, positionally
            byday = {d: idx_base[days == d] for d in uniq}
            for d in uniq:
                src, dst = byday[remap[d]], byday[d]
                k = min(len(src), len(dst))
                mr[dst[:k]] = pop.max_r[src[:k]]
        else:
            mr[idx_base] = pop.max_r[rng.permutation(idx_base)]
        _cl, v, _n = S.greedy(
            pop, base, menu=menu, max_clauses=max_clauses, min_trades=min_trades, max_r=mr
        )
        vals.append(v if np.isfinite(v) else -np.inf)
    return vals


def summarise_null(obs: float, vals: list[float], label: str) -> dict:
    a = np.array([v for v in vals if np.isfinite(v)])
    n_bad = len(vals) - len(a)
    ge = int((a >= obs).sum())
    p = (ge + 1) / (len(a) + 1) if len(a) else float("nan")
    q = (
        {k: round(float(np.quantile(a, k / 100)), 4) for k in (50, 75, 90, 95, 99)}
        if len(a)
        else {}
    )
    print(
        f"  {label:<34} obs={obs:+.4f}  null mean={a.mean():+.4f} "
        f"p50={q.get(50)} p90={q.get(90)} p95={q.get(95)} p99={q.get(99)} "
        f"max={a.max():+.4f}  >=obs: {ge}/{len(a)}  p={p:.4f}"
    )
    return {
        "label": label,
        "observed": round(obs, 4),
        "n_iter": len(vals),
        "n_degenerate": n_bad,
        "null_mean": round(float(a.mean()), 4) if len(a) else None,
        "null_quantiles": q,
        "null_max": round(float(a.max()), 4) if len(a) else None,
        "n_ge_obs": ge,
        "p_value": round(p, 4),
    }


def main() -> None:
    df = L.load_panel_checked()
    pop = S.Pop(df)
    base_shipped = S.shipped_mask(df)
    out: dict = {}

    L.hr("2a. numpy engine agrees with the shared harness")
    chk = check_pop_matches_harness(pop, base_shipped, df)
    print(f"  {chk}")
    out["engine_check"] = chk
    assert chk["agree"], "numpy engine disagrees with common.score"

    # ------------------------------------------------------- what does the search find for real?
    L.hr("2b. The observed search (SHIPPED base, greedy <=3 clauses, min 25 trades)")
    menu_sh = pop.menu(base_shipped)
    print(f"  clause menu on SHIPPED: {len(menu_sh)} clauses")
    t0 = time.time()
    cls, v, n = S.greedy(pop, base_shipped, menu=menu_sh, max_clauses=3, min_trades=25)
    print(f"  found in {time.time() - t0:.1f}s: {[str(c) for c in cls]}")
    print(f"  -> {n} trades, net R/trade {v:+.4f}")
    claim_v, claim_n, claim_tot = pop.stats(
        base_shipped
        & np.array([k in set(L.claim_selector(df)["key"].to_list()) for k in pop.df["key"]])
    )
    print(f"  the CLAIM's rule scores {claim_v:+.4f} over {claim_n} trades")
    out["observed_search"] = {
        "clauses": [str(c) for c in cls],
        "trades": n,
        "net_r_per_trade": round(v, 4),
        "menu_size": len(menu_sh),
        "claim_rule": {"trades": claim_n, "net_r_per_trade": round(claim_v, 4)},
    }

    # ------------------------------------------------------------- multiple-comparisons accounting
    L.hr("2c. Multiple-comparisons accounting")
    m = len(menu_sh)
    evals = m + (m - 1) + (m - 2)  # greedy: three passes over the menu
    exhaustive3 = m * (m - 1) * (m - 2) / 6
    print(f"  clauses in menu                     : {m}")
    print(f"  clause evaluations in one greedy run: {evals}")
    print(f"  distinct 3-clause rules in the space: {exhaustive3:,.0f}")
    print("  (and the claim's authors also chose the base population, the target, the cap,")
    print("   and looked at a quintile table for these same features on this same data)")
    out["multiple_comparisons"] = {
        "menu_clauses": m,
        "greedy_evaluations": int(evals),
        "exhaustive_3_clause_rules": int(exhaustive3),
    }

    # ------------------------------------------------------------------------------- the nulls
    N = 400
    L.hr(f"2d. NULL — same search, scrambled outcomes ({N} iterations each)")
    res_null = []
    t0 = time.time()
    vals_a = run_null(
        pop,
        base_shipped,
        menu_sh,
        n_iter=N,
        seed=11,
        block=False,
        min_trades=25,
        max_clauses=3,
    )
    res_null.append(summarise_null(v, vals_a, "A: row-permuted, search finds"))
    res_null.append(summarise_null(claim_v, vals_a, "A: vs the CLAIM's +0.478"))
    vals_b = run_null(
        pop,
        base_shipped,
        menu_sh,
        n_iter=N,
        seed=23,
        block=True,
        min_trades=25,
        max_clauses=3,
    )
    res_null.append(summarise_null(v, vals_b, "B: day-block-permuted, search"))
    res_null.append(summarise_null(claim_v, vals_b, "B: vs the CLAIM's +0.478"))
    print(f"  ({time.time() - t0:.0f}s)")
    out["nulls"] = res_null

    # ---------------------------------- a fixed-3-clause null: no search, just the claim's own rule
    L.hr("2e. NULL — the CLAIM's exact rule under permuted outcomes (no search at all)")
    rng = np.random.default_rng(5)
    claim_mask = base_shipped & np.array(
        [k in set(L.claim_selector(df)["key"].to_list()) for k in pop.df["key"]]
    )
    idx_base = np.flatnonzero(base_shipped)
    fixed = []
    for _ in range(5000):
        mr = pop.max_r.copy()
        mr[idx_base] = pop.max_r[rng.permutation(idx_base)]
        fv, _n, _t = pop.stats(claim_mask, mr)
        fixed.append(fv)
    fa = np.array(fixed)
    p_fixed = (int((fa >= claim_v).sum()) + 1) / (len(fa) + 1)
    print(
        f"  obs={claim_v:+.4f}  null mean={fa.mean():+.4f} sd={fa.std():.4f} "
        f"p95={np.quantile(fa, 0.95):+.4f}  p={p_fixed:.4f}"
    )
    print("  ^ this is the generous test: it pretends the rule was NOT searched for.")
    out["fixed_rule_null"] = {
        "observed": round(claim_v, 4),
        "null_mean": round(float(fa.mean()), 4),
        "null_sd": round(float(fa.std()), 4),
        "null_p95": round(float(np.quantile(fa, 0.95)), 4),
        "p_value": round(p_fixed, 4),
        "n_iter": len(fa),
    }

    # ------------------------------------------------ harness permutation test, for comparability
    L.hr("2f. common.permutation_pvalue (same trade count, same days, random rows)")
    for pop_name, poolf in (
        ("pool = SHIPPED", C.SHIPPED(df)),
        ("pool = whole panel", df),
    ):
        p = C.permutation_pvalue(poolf, L.claim_selector(df), n=2000, seed=3)
        print(f"  {pop_name:<22} p={p:.4f}")
        out.setdefault("harness_permutation", {})[pop_name] = round(p, 4)

    L.write("step2_null.json", out)
    print("\nwrote step2_null.json")


if __name__ == "__main__":
    main()
