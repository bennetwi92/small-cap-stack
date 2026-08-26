"""Step 0 — re-derive every number in CLAIM.md from the raw panel, before anything else.

Nothing in CLAIM.md is taken on trust. This script recomputes, independently of the script that
produced it: the three headline books, the per-period split, the intermediate 50%-move rates, the
quintile monotonicity tables, and the rvol_pole variation-vs-removal contradiction.

Run: .venv/bin/python spikes/engine_lab/validate/specification/step0_rederive.py
"""

from __future__ import annotations

import json

import numpy as np
import polars as pl
import speclab as S
from speclab import C

RUNUP, RVOL, SHARES = 0.15, 2.0, 50e6


def in_play(
    df: pl.DataFrame, runup: float = RUNUP, rvol: float = RVOL, shares: float = SHARES
) -> pl.DataFrame:
    return df.filter(
        (pl.col("runup_pre_appearance") >= runup)
        & (pl.col("rvol_pole") >= rvol)
        & (pl.col("shares_outstanding") <= shares)
    )


def main() -> None:
    df = S.panel(2.0)
    n_sessions = df["dt"].n_unique()
    print(f"panel: {df.height} rows, {n_sessions} sessions")

    # --- the harness itself -------------------------------------------------------------------
    C.assert_no_lookahead(["runup_pre_appearance", "rvol_pole", "shares_outstanding"])
    eq = S.check_net_r_matches_score(df)
    print(f"per-row net_r vs score(): {eq}")

    # --- null coverage of the three features --------------------------------------------------
    nulls = {
        c: int(df[c].null_count()) for c in ("runup_pre_appearance", "rvol_pole", "float_shares")
    }
    nulls["shares_outstanding_zero_or_null"] = int(
        df.filter(
            pl.col("shares_outstanding").is_null() | (pl.col("shares_outstanding") <= 0)
        ).height
    )
    print("nulls:", nulls)
    by_src = (
        df.group_by("source")
        .agg(
            pl.len(),
            pl.col("runup_pre_appearance").null_count().alias("runup_null"),
            pl.col("rvol_pole").null_count().alias("rvol_null"),
            pl.col("float_shares").null_count().alias("float_null"),
            pl.col("shares_outstanding").null_count().alias("so_null"),
        )
        .sort("source")
    )
    print(by_src)

    # --- the three headline books -------------------------------------------------------------
    books = {
        "shipped_only": C.SHIPPED(df),
        "in_play_only": in_play(df),
        "shipped_plus_in_play": in_play(C.SHIPPED(df)),
    }
    headline = {}
    for name, sel in books.items():
        res = C.score(C.build_book(sel, max_per_day=2), sessions=n_sessions, by=("split", "source"))
        headline[name] = S.flat(res)
        print(f"\n{name}: {C.brief(res)}")
        print("   ", C.summarise(res, name))

    # --- the error bar on the claimed book ----------------------------------------------------
    res = C.score(
        C.build_book(books["shipped_plus_in_play"], max_per_day=2),
        sessions=n_sessions,
        by=("split",),
    )
    nr = res["_trades"]["net_r"].to_numpy()
    m, se = S.mean_ci(nr)
    print(f"\nclaimed book net R/trade = {m:+.3f} +/- {se:.3f} (1 s.e., n={len(nr)})")
    headline["net_rpt_mean"] = round(m, 4)
    headline["net_rpt_se"] = round(se, 4)

    # --- intermediate signal: rate of 50%+ moves ----------------------------------------------
    inter = {}
    base = C.SHIPPED(df) if False else df
    for label, pop in (("all_rows", base), ("shipped_rows", C.SHIPPED(df))):
        for split in ("dev", "val", "holdout"):
            p = pop.filter(pl.col("split") == split)
            ip = in_play(p)
            inter[f"{label}|{split}"] = {
                "n_all": p.height,
                "rate_all": round(float((p["max_gain_pct"] >= 0.5).mean() or 0.0), 4),
                "n_inplay": ip.height,
                "rate_inplay": round(
                    float((ip["max_gain_pct"] >= 0.5).mean() or 0.0) if ip.height else 0.0, 4
                ),
            }
    print("\n50%+ move rate (max_gain_pct >= 0.5):")
    for k, v in inter.items():
        print(
            f"  {k:<24} {v['n_all']:>5} rows {v['rate_all'] * 100:5.1f}%  ->  "
            f"{v['n_inplay']:>4} in-play {v['rate_inplay'] * 100:5.1f}%"
        )

    # --- quintile monotonicity ---------------------------------------------------------------
    quint = {}
    for col in ("runup_pre_appearance", "shares_outstanding", "rvol_pole"):
        d = df.filter(pl.col(col).is_not_null())
        v = d[col].cast(pl.Float64).to_numpy()
        edges = np.unique(np.quantile(v, np.linspace(0, 1, 6)))
        rows = []
        for i, (a, b) in enumerate(zip(edges[:-1], edges[1:], strict=False)):
            last = i == len(edges) - 2
            m2 = (pl.col(col) >= a) & (pl.col(col) <= b if last else pl.col(col) < b)
            g = d.filter(m2)
            if g.is_empty():
                continue
            rows.append(
                {
                    "band": f"[{a:.4g},{b:.4g}]",
                    "n": g.height,
                    "rate_50pct": round(float((g["max_gain_pct"] >= 0.5).mean()), 4),
                    "net_rpt": round(float(g["net_r"].mean()), 4),
                    "gross_rpt": round(float(g["r"].mean()), 4),
                }
            )
        quint[col] = rows
        print(f"\nquintiles of {col}:")
        for r in rows:
            print(
                f"  {r['band']:<24} n={r['n']:>5} 50%+={r['rate_50pct'] * 100:5.1f}% "
                f"net={r['net_rpt']:+.3f}"
            )

    # --- the rvol contradiction ---------------------------------------------------------------
    rvol_probe = {}
    for rv in (0.0, 1.0, 1.5, 2.0, 3.0, 5.0):
        sel = in_play(C.SHIPPED(df), rvol=rv)
        r = C.score(C.build_book(sel, max_per_day=2), sessions=n_sessions, by=("split",))
        rvol_probe[f"rvol>={rv}"] = S.flat(r)
        print(
            f"rvol>={rv:<4} {C.brief(r)}  dev {S.split_block(r, 'dev')['net_r']:+.1f} "
            f"val {S.split_block(r, 'val')['net_r']:+.1f} "
            f"hold {S.split_block(r, 'holdout')['net_r']:+.1f}"
        )
    # removal = no rvol clause at all (not even a null-drop)
    sel_norvol = C.SHIPPED(df).filter(
        (pl.col("runup_pre_appearance") >= RUNUP) & (pl.col("shares_outstanding") <= SHARES)
    )
    r = C.score(C.build_book(sel_norvol, max_per_day=2), sessions=n_sessions, by=("split",))
    rvol_probe["removed"] = S.flat(r)
    print(
        f"rvol REMOVED  {C.brief(r)}  dev {S.split_block(r, 'dev')['net_r']:+.1f} "
        f"val {S.split_block(r, 'val')['net_r']:+.1f} "
        f"hold {S.split_block(r, 'holdout')['net_r']:+.1f}"
    )
    # and: removal but keeping rows where rvol is non-null (isolates the null-drop effect)
    sel_nn = C.SHIPPED(df).filter(
        (pl.col("runup_pre_appearance") >= RUNUP)
        & (pl.col("shares_outstanding") <= SHARES)
        & pl.col("rvol_pole").is_not_null()
    )
    r = C.score(C.build_book(sel_nn, max_per_day=2), sessions=n_sessions, by=("split",))
    rvol_probe["removed_but_rvol_notnull"] = S.flat(r)
    print(f"rvol removed, non-null only  {C.brief(r)}")

    out = {
        "population": {"rows": df.height, "sessions": n_sessions},
        "net_r_equivalence": eq,
        "nulls": nulls,
        "headline": headline,
        "intermediate_50pct": inter,
        "quintiles": quint,
        "rvol_probe": rvol_probe,
    }
    (S.OUT / "step0_rederive.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {S.OUT / 'step0_rederive.json'}")


if __name__ == "__main__":
    main()
