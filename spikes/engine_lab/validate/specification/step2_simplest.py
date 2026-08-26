"""Step 2 — the simplest specification that captures the effect, and whether it survives.

`sweeps.combination_logic()` showed that of the three IN-PLAY clauses only
`shares_outstanding <= 50e6` moves anything: on the SHIPPED population it alone books 60 trades for
+25.2R, while the full AND-of-3 books 35 for +16.7R. So the candidate simple rule is

    SHIPPED  AND  shares_outstanding <= 50e6

This step attacks that rule the way the lab's protocol requires: decompose the shares clause into
its filter and its null-drop, sweep the threshold, walk it forward, permute it, and split it by
period and by source. Then compare it head-to-head with the three-clause original.
"""

from __future__ import annotations

import json
from typing import Any

import features
import numpy as np
import polars as pl
import speclab as S
import sweeps as W
from speclab import C

SHARES = 50e6


def sel_simple(df: pl.DataFrame, th: float = SHARES) -> pl.DataFrame:
    return df.filter(pl.col("shares_outstanding") <= th)


def main() -> None:
    df = features.attach(S.panel(2.0))
    sessions = df["dt"].n_unique()
    sh = C.SHIPPED(df)
    out: dict[str, Any] = {}

    # --- 1. decompose the shares clause: filter vs null-drop ---------------------------------
    grp = {
        "<=50e6": sh.filter(pl.col("shares_outstanding") <= SHARES),
        ">50e6": sh.filter(pl.col("shares_outstanding") > SHARES),
        "null": sh.filter(pl.col("shares_outstanding").is_null()),
    }
    dec = {}
    for k, g in grp.items():
        dec[k] = {
            "rows": g.height,
            "row_net_rpt": round(float(g["net_r"].mean()), 4) if g.height else None,
            "row_gross_rpt": round(float(g["r"].mean()), 4) if g.height else None,
            "rate50": round(float((g["max_gain_pct"] >= 0.5).mean()), 4) if g.height else None,
            "mean_max_r": round(float(g["max_r"].mean()), 4) if g.height else None,
        }
    # keeping the nulls (treat "unknown size" as passing) isolates the null-drop's contribution
    dec["book_filter_only"] = W.evaluate(
        sh, [("shares_outstanding", "le", SHARES)], sessions=sessions
    )
    keep_null = sh.filter(
        (pl.col("shares_outstanding") <= SHARES) | pl.col("shares_outstanding").is_null()
    )
    dec["book_filter_or_null"] = W.evaluate(keep_null, [], sessions=sessions)
    out["shares_decomposition"] = dec
    print("shares clause decomposition:")
    for k, v in dec.items():
        print(f"  {k:<22} {json.dumps(v)[:150]}")

    # --- 2. threshold sweep, shares clause ALONE on SHIPPED ----------------------------------
    swp = {}
    for th in (2e6, 5e6, 1e7, 2e7, 3e7, 4e7, 5e7, 6e7, 7.5e7, 1e8, 1.5e8, 2e8, 5e8, 1e12):
        swp[f"{th:.3g}"] = W.evaluate(sh, [("shares_outstanding", "le", th)], sessions=sessions)
    out["shares_threshold_sweep"] = swp
    print("\nshares_outstanding <= X, on SHIPPED, alone:")
    for k, v in swp.items():
        print(
            f"  {k:>9}  {v['trades']:>4} tr ({v['trades_per_session']:.2f}/sess)  "
            f"net {v['net_r']:+7.1f}R ({v['net_r_per_trade']:+.3f} +/-{v['net_rpt_se']:.3f})  "
            f"dev {v['dev_net_r']:+6.1f} val {v['val_net_r']:+6.1f} "
            f"hold {v['holdout_net_r']:+6.1f}"
            f"  recon {v['recon_net_r']:+6.1f} live {v['live_net_r']:+6.1f}"
        )

    # --- 3. head-to-head, simple vs the three-clause original --------------------------------
    rules = {
        "shipped_only": [],
        "simple: shares<=50e6": [("shares_outstanding", "le", SHARES)],
        "orig AND-of-3": W.ORIG,
        "pair: runup+shares": [W.ORIG[0], W.ORIG[2]],
        "pair: rvol+shares": [W.ORIG[1], W.ORIG[2]],
    }
    h2h = {k: W.evaluate(sh, v, sessions=sessions) for k, v in rules.items()}
    out["head_to_head"] = h2h
    print("\nhead to head:")
    for k, v in h2h.items():
        print(
            f"  {k:<24} {v['trades']:>4} tr ({v['trades_per_session']:.2f}/s) "
            f"net {v['net_r']:+7.1f}R ({v['net_r_per_trade']:+.3f} +/-{v['net_rpt_se']:.3f}) "
            f"win {v['win_rate'] * 100:4.1f}% dd {v['max_dd_net_r']:+.1f} | "
            f"dev {v['dev_net_r']:+6.1f}/{v['dev_trades']:<3} "
            f"val {v['val_net_r']:+6.1f}/{v['val_trades']:<3} "
            f"hold {v['holdout_net_r']:+6.1f}/{v['holdout_trades']}"
        )

    # --- 4. walk-forward, with the threshold REFIT in every block ----------------------------
    # A fixed 50e6 walked forward is not a test of the procedure, only of the number. So the fit
    # function re-picks the threshold from the training data each time, from a coarse grid.
    grid = [5e6, 1e7, 2e7, 3e7, 5e7, 7.5e7, 1e8, 2e8]

    def fit_shares(train: pl.DataFrame):  # noqa: ANN202
        tr = C.SHIPPED(train)
        best, best_th = -1e9, SHARES
        for th in grid:
            s = C.score(
                C.build_book(C.fixed_target_r(sel_simple(tr, th)), max_per_day=2),
                sessions=max(1, tr["dt"].n_unique()),
            )
            if s["trades"] >= 5 and s["net_r"] > best:
                best, best_th = s["net_r"], th
        return lambda d: sel_simple(C.SHIPPED(d), best_th)

    def fit_fixed(_train: pl.DataFrame):  # noqa: ANN202
        return lambda d: sel_simple(C.SHIPPED(d), SHARES)

    def fit_orig(_train: pl.DataFrame):  # noqa: ANN202
        return lambda d: W.apply_clauses(C.SHIPPED(d), W.ORIG)

    def fit_shipped(_train: pl.DataFrame):  # noqa: ANN202
        return C.SHIPPED

    wf = {}
    for name, fn in (
        ("shipped_only", fit_shipped),
        ("simple_fixed_50e6", fit_fixed),
        ("simple_refit_each_block", fit_shares),
        ("orig_and3_fixed", fit_orig),
    ):
        wf[name] = C.walk_forward(df, fn, n_blocks=6, min_train_sessions=60)
        print(
            f"\nwalk-forward {name}: {wf[name]['blocks_positive']}/{wf[name]['n_blocks']} blocks "
            f"positive, {wf[name]['total_trades']} trades, "
            f"{wf[name]['total_net_r']:+.1f}R ({wf[name]['net_r_per_trade']:+.3f}/trade)"
        )
        for b in wf[name]["blocks"]:
            print(
                f"    {b['from']} .. {b['to']}  {b['trades']:>3} tr  "
                f"net {b['net_r']:+6.1f}R ({b['net_r_per_trade']:+.3f})"
            )
    out["walk_forward"] = wf

    # --- 5. permutation ----------------------------------------------------------------------
    perm = {}
    for name, s in (
        ("simple", sel_simple(sh)),
        ("orig_and3", W.apply_clauses(sh, W.ORIG)),
        ("shipped_only", sh),
    ):
        # random rows drawn from the SAME DAYS, from the SHIPPED pool, same count per day
        perm[name + "|pool=shipped"] = round(C.permutation_pvalue(sh, s, n=500, seed=11), 4)
        perm[name + "|pool=all"] = round(C.permutation_pvalue(df, s, n=500, seed=11), 4)
    out["permutation"] = perm
    print("\npermutation p-values (same trade count, same days, random rows):")
    for k, v in perm.items():
        print(f"  {k:<34} p = {v}")

    # --- 6. calendar concentration -----------------------------------------------------------
    bk = C.build_book(sel_simple(sh), max_per_day=2)
    res = C.score(bk, sessions=sessions, by=("split", "source"))
    t = res["_trades"]
    by_m = (
        t.with_columns(pl.col("dt").dt.strftime("%Y-%m").alias("mo"))
        .group_by("mo")
        .agg(pl.len().alias("n"), pl.col("net_r").sum().round(2).alias("net_r"))
        .sort("mo")
    )
    out["monthly_simple"] = by_m.to_dicts()
    print("\nmonthly, simple rule:")
    print(by_m)
    nr = np.sort(t["net_r"].to_numpy())[::-1]
    out["top_trade_concentration"] = {
        "total_net_r": round(float(nr.sum()), 2),
        "top1": round(float(nr[:1].sum()), 2),
        "top3": round(float(nr[:3].sum()), 2),
        "top5": round(float(nr[:5].sum()), 2),
        "n": len(nr),
    }
    print(
        f"concentration: total {nr.sum():+.1f}R, top1 {nr[:1].sum():+.1f}, "
        f"top3 {nr[:3].sum():+.1f}, top5 {nr[:5].sum():+.1f} of {len(nr)} trades"
    )
    bk3 = C.build_book(W.apply_clauses(sh, W.ORIG), max_per_day=2)
    t3 = C.score(bk3, sessions=sessions)["_trades"]
    n3 = np.sort(t3["net_r"].to_numpy())[::-1]
    out["top_trade_concentration_and3"] = {
        "total_net_r": round(float(n3.sum()), 2),
        "top1": round(float(n3[:1].sum()), 2),
        "top3": round(float(n3[:3].sum()), 2),
        "top5": round(float(n3[:5].sum()), 2),
        "n": len(n3),
    }
    print(
        f"concentration and3: total {n3.sum():+.1f}R, top1 {n3[:1].sum():+.1f}, "
        f"top3 {n3[:3].sum():+.1f}, top5 {n3[:5].sum():+.1f} of {len(n3)} trades"
    )

    # --- 7. does the simple rule survive the other targets? ----------------------------------
    tgt = {}
    for target in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        d = S.attach_net_r(
            C.fixed_target_r(df.drop(["r", "net_r", "qty", "sized_by", "cost_r"]), target)
        )
        s2 = C.SHIPPED(d)
        tgt[str(target)] = {
            "simple": W.evaluate(s2, [("shares_outstanding", "le", SHARES)], sessions=sessions),
            "and3": W.evaluate(s2, W.ORIG, sessions=sessions),
            "shipped": W.evaluate(s2, [], sessions=sessions),
        }
    out["targets"] = tgt
    print("\nby target (net R / net R per trade):")
    for k, v in tgt.items():
        print(
            f"  {k:>4}R  simple {v['simple']['net_r']:+7.1f} "
            f"({v['simple']['net_r_per_trade']:+.3f})"
            f"   and3 {v['and3']['net_r']:+7.1f} ({v['and3']['net_r_per_trade']:+.3f})"
            f"   shipped {v['shipped']['net_r']:+7.1f} "
            f"({v['shipped']['net_r_per_trade']:+.3f})"
        )

    (S.OUT / "step2_simplest.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {S.OUT / 'step2_simplest.json'}")


if __name__ == "__main__":
    main()
