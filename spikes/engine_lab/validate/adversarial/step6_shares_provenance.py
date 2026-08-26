"""Step 6 — the residue. Everything the rule does reduces to `shares_outstanding`.

Steps 1/5 showed runup does nothing and rvol subtracts. So the only load-bearing clause is
`shares_outstanding <= 50e6`, plus the fact that the field is populated at all. Two questions:

  A. PROVENANCE. Is that number knowable at trigger time? `shares_as_of` says when it was
     measured. If it post-dates the session, the rule is reading the future — a reverse split or
     an offering between the trade and the measurement changes the share count by orders of
     magnitude, and this population is exactly the kind of company that does both.
  B. Does the one-clause rule survive the null and the walk-forward on its own?
"""

from __future__ import annotations

import lab as L
import numpy as np
import polars as pl
import search as S
from lab import C
from step3b_wf_null import wf_edges


def main() -> None:
    df = L.load_panel_checked()
    out: dict = {}

    # ------------------------------------------------------------------------- A. provenance
    L.hr("6a. Where does shares_outstanding come from, and when was it measured?")
    print(df.group_by("shares_source").agg(pl.len()).sort("len", descending=True))
    out["shares_source"] = (
        df.group_by("shares_source").agg(pl.len()).sort("len", descending=True).to_dicts()
    )

    if "shares_as_of" in df.columns:
        d = df.filter(pl.col("shares_as_of").is_not_null())
        print(f"\n  rows with shares_as_of: {d.height}/{df.height}")
        if d.height:
            lag = (
                d.select(
                    (pl.col("shares_as_of").cast(pl.Date) - pl.col("dt"))
                    .dt.total_days()
                    .alias("days")
                )["days"]
                .to_numpy()
                .astype(float)
            )
            print(
                f"  shares_as_of MINUS session date (days): "
                f"min={np.nanmin(lag):.0f} p25={np.nanpercentile(lag, 25):.0f} "
                f"median={np.nanmedian(lag):.0f} p75={np.nanpercentile(lag, 75):.0f} "
                f"max={np.nanmax(lag):.0f}"
            )
            after = float((lag > 0).mean())
            print(f"  fraction measured AFTER the session: {after:.1%}")
            print(f"  fraction measured more than 30 days after: {float((lag > 30).mean()):.1%}")
            print(f"  fraction measured more than 180 days after: {float((lag > 180).mean()):.1%}")
            out["shares_as_of_lag_days"] = {
                "n": int(len(lag)),
                "min": float(np.nanmin(lag)),
                "median": float(np.nanmedian(lag)),
                "max": float(np.nanmax(lag)),
                "frac_after_session": round(after, 4),
                "frac_gt_30d_after": round(float((lag > 30).mean()), 4),
                "frac_gt_180d_after": round(float((lag > 180).mean()), 4),
            }
            # by source, because recon and live were enriched by different code paths
            print("\n  by source:")
            bysrc = (
                d.with_columns(
                    (pl.col("shares_as_of").cast(pl.Date) - pl.col("dt"))
                    .dt.total_days()
                    .alias("lag")
                )
                .group_by("source")
                .agg(
                    pl.len(),
                    pl.col("lag").median().alias("median_lag_days"),
                    (pl.col("lag") > 0).mean().alias("frac_after"),
                )
            )
            print(bysrc)
            out["shares_as_of_by_source"] = bysrc.to_dicts()
    else:
        print("  no shares_as_of column in the panel — provenance NOT measurable with this data")
        out["shares_as_of_lag_days"] = "column absent"

    # ------------------------------------------------------ B. the one-clause rule, on its own
    L.hr("6b. The one-clause residue: SHIPPED + shares_outstanding <= 50e6")
    pop = S.Pop(df)
    base = S.shipped_mask(df)
    sh_x = pop.feat["shares_outstanding"]
    one = base & np.where(np.isnan(sh_x), False, sh_x <= 50e6)
    v, n, tot = pop.stats(one)
    print(f"  {n} trades  {tot:+.2f}R  {v:+.4f}/trade  ({n / df['dt'].n_unique():.2f}/session)")
    out["one_clause"] = {"trades": n, "net_r": round(tot, 2), "net_r_per_trade": round(v, 4)}

    # null: the same one-clause search over a shares-only decile menu
    L.hr("6c. One-clause null — search only `shares_outstanding <= q` over deciles")
    x = sh_x[base]
    x = x[~np.isnan(x)]
    menu = [S.Clause("shares_outstanding", "le", float(np.quantile(x, q))) for q in S.DECILES]
    cls, obs, on = S.greedy(pop, base, menu=menu, max_clauses=1, min_trades=25)
    print(f"  best on real data: {[str(c) for c in cls]} -> {on} trades {obs:+.4f}/trade")
    rng = np.random.default_rng(2024)
    idx = np.flatnonzero(base)
    vals, fixed = [], []
    for _ in range(4000):
        mr = pop.max_r.copy()
        mr[idx] = pop.max_r[rng.permutation(idx)]
        _c, bv, _bn = S.greedy(pop, base, menu=menu, max_clauses=1, min_trades=25, max_r=mr)
        vals.append(bv)
        fv, _n, _t = pop.stats(one, mr)
        fixed.append(fv)
    va, fa = np.array(vals), np.array(fixed)
    p_search = (int((va >= obs).sum()) + 1) / (len(va) + 1)
    p_fixed = (int((fa >= v).sum()) + 1) / (len(fa) + 1)
    print(
        f"  searched:  obs {obs:+.4f}  null median {np.median(va):+.4f} "
        f"p90 {np.quantile(va, 0.9):+.4f}  p={p_search:.4f}"
    )
    print(
        f"  as a FIXED rule (50M chosen in advance): obs {v:+.4f}  null median "
        f"{np.median(fa):+.4f} p90 {np.quantile(fa, 0.9):+.4f}  p={p_fixed:.4f}"
    )
    out["one_clause_null"] = {
        "searched": {
            "observed": round(obs, 4),
            "trades": on,
            "null_median": round(float(np.median(va)), 4),
            "null_p90": round(float(np.quantile(va, 0.9)), 4),
            "p_value": round(p_search, 4),
        },
        "fixed": {
            "observed": round(v, 4),
            "null_median": round(float(np.median(fa)), 4),
            "null_p90": round(float(np.quantile(fa, 0.9)), 4),
            "p_value": round(p_fixed, 4),
        },
    }

    # ------------------------------------------------------- 6d. one-clause walk-forward vs null
    L.hr("6d. One-clause walk-forward (refit the cut each window) vs its null")
    edges = wf_edges(pop.day_idx)
    cuts = [10e6, 20e6, 35e6, 50e6, 75e6, 100e6, 200e6]
    cmasks = [(c, base & np.where(np.isnan(sh_x), False, sh_x <= c)) for c in cuts]

    def wf(mr, refit: bool):
        blocks, chose = [], []
        for a, b in edges:
            train, test = pop.day_idx < a, (pop.day_idx >= a) & (pop.day_idx < b)
            if refit:
                best, bm, bc = -np.inf, None, None
                for c, m in cmasks:
                    vv, nn, _ = pop.stats(m & train, mr)
                    if nn >= 15 and vv > best:
                        best, bm, bc = vv, m, c
                if bm is None:
                    bm, bc = cmasks[3][1], 50e6
            else:
                bm, bc = cmasks[3][1], 50e6
            vv, nn, tt = pop.stats(bm & test, mr)
            blocks.append((nn, tt))
            chose.append(bc)
        tr_ = sum(n for n, _ in blocks)
        tt_ = sum(t for _, t in blocks)
        return (tt_ / tr_ if tr_ else 0.0), tt_, sum(1 for _, t in blocks if t > 0), tr_, chose

    for refit, tag in ((False, "FIXED 50M"), (True, "REFIT cut")):
        ov, ot, opos, otr, chose = wf(pop.max_r, refit)
        rng2 = np.random.default_rng(606)
        nv = []
        for _ in range(800):
            mr = pop.max_r.copy()
            mr[idx] = pop.max_r[rng2.permutation(idx)]
            nv.append(wf(mr, refit)[0])
        na = np.array(nv)
        p = (int((na >= ov).sum()) + 1) / (len(na) + 1)
        print(
            f"  {tag:<10} {otr:>3} trades  {ot:+7.2f}R  {ov:+.4f}/trade  {opos}/6 blocks+  "
            f"null median {np.median(na):+.4f}  p={p:.3f}   cuts chosen "
            f"{[f'{c / 1e6:.0f}M' for c in chose]}"
        )
        out.setdefault("one_clause_walkforward", {})[tag] = {
            "trades": otr,
            "net_r": round(ot, 2),
            "net_r_per_trade": round(ov, 4),
            "blocks_positive": opos,
            "null_median": round(float(np.median(na)), 4),
            "p_value": round(p, 4),
            "cuts_chosen": [float(c) for c in chose],
        }

    # ------------------------------------------------- 6e. the same clause on the WHOLE panel
    L.hr("6e. Does 'small company' work outside the shipped pool? (whole panel, row level)")
    for cut in (20e6, 50e6, 100e6, 1e12):
        d = df.filter(
            pl.col("shares_outstanding").is_not_null() & (pl.col("shares_outstanding") <= cut)
        )
        r = C.fixed_target_r(d)["r"].to_numpy()
        print(f"  shares<= {cut / 1e6:>6.0f}M  rows={len(r):>5}  gross R/row {r.mean():+.4f}")
    d = df.filter(pl.col("shares_outstanding").is_null())
    print(
        f"  shares MISSING       rows={d.height:>5}  gross R/row "
        f"{C.fixed_target_r(d)['r'].mean():+.4f}"
    )
    print(
        f"  whole panel          rows={df.height:>5}  gross R/row "
        f"{C.fixed_target_r(df)['r'].mean():+.4f}"
    )

    L.write("step6_shares_provenance.json", out)
    print("\nwrote step6_shares_provenance.json")


if __name__ == "__main__":
    main()
