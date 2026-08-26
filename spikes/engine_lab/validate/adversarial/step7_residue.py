"""Step 7 — pin down the residue: what is left, how big is it really, and can it be trusted?

Three things:
  A. `shares_as_of` exists only for `edgar` rows. `fmp` and `yfinance` have no as-of date, and
     yfinance in particular serves *today's* share count. Any book trade sourced that way is
     reading a number that did not exist on the session date. How exposed is the book?
  B. The `shares <= 50M` effect is ~10x larger inside the 125-row SHIPPED pool than in the
     3,639-row population. Is that interaction real, or is it the small pool talking?
  C. The one-clause rule, dev+val only (no holdout), so there is a number that is not contaminated.
"""

from __future__ import annotations

import lab as L
import numpy as np
import polars as pl
import search as S
from lab import C


def main() -> None:
    df = L.load_panel_checked()
    out: dict = {}

    # ------------------------------------------------------------ A. shares_source composition
    L.hr("7a. shares_source by data source — where does the number come from?")
    x = (
        df.group_by(["source", "shares_source"])
        .agg(pl.len().alias("n"))
        .sort(["source", "n"], descending=[False, True])
    )
    print(x)
    out["shares_source_by_source"] = x.to_dicts()

    L.hr("7b. shares_source of the 35 booked trades")
    bk = L.book_of(L.claim_selector(df))
    comp = bk.group_by("shares_source").agg(pl.len().alias("n")).sort("n", descending=True)
    print(comp)
    dated = int(bk.filter(pl.col("shares_as_of").is_not_null()).height)
    print(f"  trades whose share count carries an as-of date: {dated}/{bk.height}")
    print(
        f"  trades whose share count has NO as-of date (fmp/yfinance = as-served, "
        f"possibly today's): {bk.height - dated}/{bk.height}"
    )
    out["book_shares_source"] = {
        "composition": comp.to_dicts(),
        "with_as_of": dated,
        "without_as_of": bk.height - dated,
        "trades": bk.height,
    }
    # and the one-clause book
    sh = C.SHIPPED(df)
    one = sh.filter(
        pl.col("shares_outstanding").is_not_null() & (pl.col("shares_outstanding") <= 50e6)
    )
    b1 = L.book_of(one)
    d1 = int(b1.filter(pl.col("shares_as_of").is_not_null()).height)
    print(f"  (one-clause book: {d1}/{b1.height} dated)")
    out["one_clause_book_dated"] = {"dated": d1, "trades": b1.height}

    # sensitivity: drop the undated rows entirely
    L.hr("7c. Re-run with every undated share count treated as unknown")
    dated_only = sh.filter(pl.col("shares_as_of").is_not_null())
    for name, d in (
        ("CLAIM, dated shares only", L.in_play(dated_only)),
        ("one-clause, dated shares only", dated_only.filter(pl.col("shares_outstanding") <= 50e6)),
    ):
        b = L.book_of(d)
        v = L.fast_net_r(b["entry_fill"].to_numpy(), b["stop"].to_numpy(), b["r"].to_numpy())
        v = v[~np.isnan(v)]
        print(
            f"  {name:<34} {len(v):>3} trades  {v.sum():+7.2f}R  "
            f"{v.mean() if len(v) else 0:+.4f}/trade"
        )
        out.setdefault("dated_only", {})[name] = {
            "trades": int(len(v)),
            "net_r": round(float(v.sum()), 2),
            "net_r_per_trade": round(float(v.mean()) if len(v) else 0.0, 4),
        }

    # ------------------------------------------------------------------- B. the interaction
    L.hr("7d. Is 'small company helps' a shipped-pool effect or a population effect?")
    rows = []
    for pool_name, pool in (
        ("SHIPPED (125 rows)", sh),
        ("NOT shipped (3,514 rows)", df.join(sh.select("key"), on="key", how="anti")),
        ("passed only", df.filter(pl.col("passed"))),
        ("whole panel", df),
    ):
        p = pool.filter(pl.col("shares_outstanding").is_not_null())
        small = p.filter(pl.col("shares_outstanding") <= 50e6)
        big = p.filter(pl.col("shares_outstanding") > 50e6)
        rs = C.fixed_target_r(small)["r"].to_numpy()
        rb = C.fixed_target_r(big)["r"].to_numpy()
        diff = (rs.mean() - rb.mean()) if len(rs) and len(rb) else np.nan
        se = (
            np.sqrt(rs.var(ddof=1) / len(rs) + rb.var(ddof=1) / len(rb))
            if len(rs) > 1 and len(rb) > 1
            else np.nan
        )
        print(
            f"  {pool_name:<26} small n={len(rs):>4} R/row {rs.mean():+.4f} | "
            f"big n={len(rb):>4} R/row {rb.mean():+.4f} | diff {diff:+.4f} +/- {se:.4f}"
        )
        rows.append(
            {
                "pool": pool_name,
                "small_n": int(len(rs)),
                "small_r": round(float(rs.mean()), 4),
                "big_n": int(len(rb)),
                "big_r": round(float(rb.mean()), 4),
                "diff": round(float(diff), 4),
                "se": round(float(se), 4),
            }
        )
    print("\n  If the size effect were a property of small-caps, the whole-panel row would show it")
    print("  at the same magnitude. It does not — it is ~10x smaller outside the shipped pool.")
    out["interaction"] = rows

    # -------------------------------------------------- C. dev+val only (holdout left alone)
    L.hr("7e. The residue on DEV+VAL only — a figure that owes nothing to the spent holdout")
    nohold = df.filter(pl.col("split") != "holdout")
    pop = S.Pop(nohold)
    base = S.shipped_mask(nohold)
    sx = pop.feat["shares_outstanding"]
    for cut, tag in ((50e6, "shares <= 50M"), (35e6, "shares <= 35M"), (np.inf, "SHIPPED only")):
        m = base if np.isinf(cut) else base & np.where(np.isnan(sx), False, sx <= cut)
        v, n, tot = pop.stats(m)
        print(
            f"  {tag:<16} {n:>3} trades ({n / nohold['dt'].n_unique():.2f}/sess)  "
            f"{tot:+7.2f}R  {v:+.4f}/trade"
        )
        out.setdefault("dev_val_only", {})[tag] = {
            "trades": n,
            "net_r": round(tot, 2),
            "net_r_per_trade": round(v, 4),
        }
    mclaim = base & np.array(
        [k in set(L.claim_selector(nohold)["key"].to_list()) for k in pop.df["key"].to_list()]
    )
    v, n, tot = pop.stats(mclaim)
    print(
        f"  {'CLAIM rule':<16} {n:>3} trades ({n / nohold['dt'].n_unique():.2f}/sess)  "
        f"{tot:+7.2f}R  {v:+.4f}/trade"
    )
    out["dev_val_only"]["CLAIM rule"] = {
        "trades": n,
        "net_r": round(tot, 2),
        "net_r_per_trade": round(v, 4),
    }

    # null on dev+val for the one-clause rule
    one_m = base & np.where(np.isnan(sx), False, sx <= 50e6)
    ov, on_, _ = pop.stats(one_m)
    rng = np.random.default_rng(808)
    idx = np.flatnonzero(base)
    vals = []
    for _ in range(5000):
        mr = pop.max_r.copy()
        mr[idx] = pop.max_r[rng.permutation(idx)]
        vv, _n, _t = pop.stats(one_m, mr)
        vals.append(vv)
    va = np.array(vals)
    p = (int((va >= ov).sum()) + 1) / (len(va) + 1)
    print(
        f"\n  dev+val, shares<=50M as a FIXED rule: obs {ov:+.4f}, null median "
        f"{np.median(va):+.4f}, p={p:.4f}"
    )
    out["dev_val_one_clause_null"] = {
        "observed": round(ov, 4),
        "trades": on_,
        "null_median": round(float(np.median(va)), 4),
        "p_value": round(p, 4),
    }

    L.write("step7_residue.json", out)
    print("\nwrote step7_residue.json")


if __name__ == "__main__":
    main()
