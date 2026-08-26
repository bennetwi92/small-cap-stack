"""Step 3c — decompose the one test that survived (walk-forward vs null, p~0.05).

Step 1 showed the biggest single jump in the whole chain is NOT a threshold: it is
`shares_outstanding is not null` (+0.058 -> +0.192 net R/trade, 125 -> 96 rows). Every one of the
150 grid combos silently contains that clause, because `x <= 50e6` is False for a null.

So: rerun the walk-forward null with the base population already restricted to rows where all
three features are present. If the effect survives, the thresholds are doing the work. If it
collapses, the "in-play rule" is largely a fundamentals-lookup-succeeded filter.

Also runs the WIDE search's walk-forward against its own null, which is the only fully honest
version (the 150-point grid was itself chosen after seeing all the data).
"""

from __future__ import annotations

import lab as L
import numpy as np
import search as S
from step3b_wf_null import grid_masks, run_wf, wf_edges


def null_p(pop, base, edges, masks, obs, *, refit, n_iter, seed, min_train_trades=15):
    rng = np.random.default_rng(seed)
    idx = np.flatnonzero(base)
    per, pos, tot = [], [], []
    for _ in range(n_iter):
        mr = pop.max_r.copy()
        mr[idx] = pop.max_r[rng.permutation(idx)]
        r = run_wf(pop, base, edges, masks, mr, refit=refit, min_train_trades=min_train_trades)
        per.append(r["net_r_per_trade"])
        pos.append(r["blocks_positive"])
        tot.append(r["total_net_r"])
    per, pos, tot = np.array(per), np.array(pos), np.array(tot)
    return {
        "null_median_per_trade": round(float(np.median(per)), 4),
        "null_p90_per_trade": round(float(np.quantile(per, 0.9)), 4),
        "p_per_trade": round((int((per >= obs["net_r_per_trade"]).sum()) + 1) / (n_iter + 1), 4),
        "p_total_r": round((int((tot >= obs["total_net_r"]).sum()) + 1) / (n_iter + 1), 4),
        "null_mean_blocks_positive": round(float(pos.mean()), 3),
        "p_blocks_positive": round(
            (int((pos >= obs["blocks_positive"]).sum()) + 1) / (n_iter + 1), 4
        ),
    }


def main() -> None:
    df = L.load_panel_checked()
    pop = S.Pop(df)
    edges = wf_edges(pop.day_idx)
    out: dict = {}
    N = 800

    shipped = S.shipped_mask(df)
    present = (
        ~np.isnan(pop.feat["runup_pre_appearance"])
        & ~np.isnan(pop.feat["rvol_pole"])
        & ~np.isnan(pop.feat["shares_outstanding"])
    )
    bases = {
        "SHIPPED (nullity free to help)": shipped,
        "SHIPPED & all 3 features present": shipped & present,
    }

    for label, base in bases.items():
        L.hr(f"3c. Walk-forward vs null — base = {label}  ({int(base.sum())} rows)")
        masks = grid_masks(pop, base)
        for refit, tag in ((False, "FIXED"), (True, "GRID")):
            obs = run_wf(pop, base, edges, masks, pop.max_r, refit=refit)
            o = {
                "trades": obs["total_trades"],
                "net_r_per_trade": obs["net_r_per_trade"],
                "total_net_r": obs["total_net_r"],
                "blocks_positive": obs["blocks_positive"],
            }
            res = null_p(pop, base, edges, masks, o, refit=refit, n_iter=N, seed=17)
            print(
                f"  {tag:<6} obs {o['trades']:>3} trades {o['net_r_per_trade']:+.4f}/trade "
                f"({o['total_net_r']:+6.2f}R, {o['blocks_positive']}/6 blocks+)  ->  "
                f"null median {res['null_median_per_trade']:+.4f}  p_per={res['p_per_trade']:.3f} "
                f"p_tot={res['p_total_r']:.3f} p_blocks={res['p_blocks_positive']:.3f}"
            )
            out[f"{label} | {tag}"] = {
                "observed": {k: round(v, 4) if isinstance(v, float) else v for k, v in o.items()},
                **res,
            }

    # --------------------------------------------- the nullity clause on its own, walk-forwarded
    L.hr("3c-2. Walk-forward of 'shares_outstanding is present' ALONE — no thresholds at all")
    blocks = []
    for a, b in edges:
        m = (
            shipped
            & ~np.isnan(pop.feat["shares_outstanding"])
            & (pop.day_idx >= a)
            & (pop.day_idx < b)
        )
        v, n, tot = pop.stats(m)
        blocks.append({"trades": n, "net_r": tot, "per": 0.0 if not n else tot / n})
    tr = sum(b["trades"] for b in blocks)
    tot = sum(b["net_r"] for b in blocks)
    print(
        f"  'shares present' only: {tr} trades, net {tot:+.2f}R ({tot / tr:+.4f}/trade), "
        f"{sum(1 for b in blocks if b['net_r'] > 0)}/{len(blocks)} blocks positive"
    )
    print("  (the claim's rule managed 30 trades / +16.25R over the same blocks)")
    out["shares_present_only_walkforward"] = {
        "trades": tr,
        "net_r": round(tot, 2),
        "net_r_per_trade": round(tot / tr, 4),
        "blocks_positive": sum(1 for b in blocks if b["net_r"] > 0),
        "n_blocks": len(blocks),
        "blocks": [
            {k: round(v, 4) if isinstance(v, float) else v for k, v in b.items()} for b in blocks
        ],
    }

    # ------------------------------------------------------------------- the WIDE search vs null
    L.hr("3c-3. WIDE greedy search walk-forward vs its own null (the only unconstrained version)")
    menu = pop.menu(shipped)

    def wide_wf(mr):
        blocks = []
        for a, b in edges:
            train, test = pop.day_idx < a, (pop.day_idx >= a) & (pop.day_idx < b)
            cls, _v, _n = S.greedy(
                pop, shipped & train, menu=menu, max_clauses=3, min_trades=15, max_r=mr
            )
            m = shipped.copy()
            for cl in cls:
                m &= pop.clause_mask(cl)
            v, n, t = pop.stats(m & test, mr)
            blocks.append((n, t))
        tr = sum(n for n, _ in blocks)
        tt = sum(t for _, t in blocks)
        return (tt / tr if tr else 0.0), tt, sum(1 for _, t in blocks if t > 0), tr

    ov, ot, opos, otr = wide_wf(None)
    print(f"  observed: {otr} trades  {ov:+.4f}/trade  {ot:+.2f}R  {opos}/6 blocks+")
    rng = np.random.default_rng(31)
    idx = np.flatnonzero(shipped)
    vals = []
    for _ in range(200):
        mr = pop.max_r.copy()
        mr[idx] = pop.max_r[rng.permutation(idx)]
        vals.append(wide_wf(mr)[:3])
    va = np.array(vals)
    p = (int((va[:, 0] >= ov).sum()) + 1) / (len(va) + 1)
    print(
        f"  null median {np.median(va[:, 0]):+.4f}/trade, p90 {np.quantile(va[:, 0], 0.9):+.4f} "
        f"-> p={p:.3f}"
    )
    out["wide_walkforward"] = {
        "observed_per_trade": round(ov, 4),
        "observed_total_r": round(ot, 2),
        "observed_trades": otr,
        "observed_blocks_positive": opos,
        "null_median": round(float(np.median(va[:, 0])), 4),
        "null_p90": round(float(np.quantile(va[:, 0], 0.9)), 4),
        "p_value": round(p, 4),
        "n_iter": len(va),
    }

    L.write("step3c_wf_decompose.json", out)
    print("\nwrote step3c_wf_decompose.json")


if __name__ == "__main__":
    main()
