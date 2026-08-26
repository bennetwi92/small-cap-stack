"""Step 4 — leave-one-out at every level, block bootstrap by session, sub-sample stability.

Nothing here is a null test; these ask "how few things have to be different for the number to
disappear?" A +0.478R/trade that dies when you drop one month, one symbol or three trades is a
description of those trades.
"""

from __future__ import annotations

import lab as L
import numpy as np
import polars as pl
from lab import C


def book_stats(df: pl.DataFrame) -> tuple[int, float, float]:
    bk = L.book_of(L.claim_selector(df))
    if bk.is_empty():
        return 0, 0.0, 0.0
    v = L.fast_net_r(bk["entry_fill"].to_numpy(), bk["stop"].to_numpy(), bk["r"].to_numpy())
    v = v[~np.isnan(v)]
    return len(v), float(v.sum()), float(v.mean()) if len(v) else 0.0


def main() -> None:
    df = L.load_panel_checked()
    out: dict = {}
    n0, tot0, per0 = book_stats(df)
    print(f"baseline: {n0} trades, {tot0:+.2f}R, {per0:+.4f}/trade")
    out["baseline"] = {"trades": n0, "net_r": round(tot0, 2), "net_r_per_trade": round(per0, 4)}

    book = L.book_of(L.claim_selector(df))
    res = C.score(book, sessions=df["dt"].n_unique())
    tr = res["_trades"]
    v = tr["net_r"].to_numpy()

    # ------------------------------------------------------------------------ leave-one-period
    L.hr("4a. Leave one period out")
    rows = []
    for sp in ("dev", "val", "holdout"):
        d = df.filter(pl.col("split") != sp)
        n, t, p = book_stats(d)
        print(f"  drop {sp:<8} -> {n:>3} trades  {t:+7.2f}R  {p:+.4f}/trade")
        rows.append({"dropped": sp, "trades": n, "net_r": round(t, 2), "per": round(p, 4)})
    out["leave_one_period"] = rows

    # ------------------------------------------------------------------------- leave-one-month
    L.hr("4b. Leave one calendar month out")
    tr2 = tr.with_columns(pl.col("dt").dt.strftime("%Y-%m").alias("mon"))
    months = sorted(df.select(pl.col("dt").dt.strftime("%Y-%m")).to_series().unique().to_list())
    rows = []
    for m in months:
        d = df.filter(pl.col("dt").dt.strftime("%Y-%m") != m)
        n, t, p = book_stats(d)
        rows.append({"dropped": m, "trades": n, "net_r": round(t, 2), "per": round(p, 4)})
    for r in sorted(rows, key=lambda r: r["per"]):
        flag = "  <-- kills it" if r["per"] <= 0 else ""
        print(
            f"  drop {r['dropped']} -> {r['trades']:>3} trades  {r['net_r']:+7.2f}R  "
            f"{r['per']:+.4f}/trade{flag}"
        )
    out["leave_one_month"] = rows
    print(f"  worst month to lose: {min(rows, key=lambda r: r['per'])['dropped']}")
    print(
        f"  months whose removal turns it negative: {[r['dropped'] for r in rows if r['per'] <= 0]}"
    )
    print("\n  per-month contribution:")
    per_mon = (
        tr2.group_by("mon")
        .agg(pl.len().alias("n"), pl.col("net_r").sum().round(2).alias("net_r"))
        .sort("mon")
    )
    print(per_mon)
    out["per_month_contribution"] = per_mon.to_dicts()

    # ------------------------------------------------------------------------ drop the top N
    L.hr("4c. Drop the top N trades")
    srt = np.sort(v)[::-1]
    rows = []
    for k in range(0, 9):
        rest = srt[k:]
        print(
            f"  drop top {k:>2}: {len(rest):>3} trades  {rest.sum():+7.2f}R  "
            f"{rest.mean():+.4f}/trade"
        )
        rows.append(
            {
                "dropped_top": k,
                "trades": len(rest),
                "net_r": round(float(rest.sum()), 2),
                "per": round(float(rest.mean()), 4),
            }
        )
    first_neg = next((r["dropped_top"] for r in rows if r["net_r"] <= 0), None)
    print(f"  -> total R goes non-positive after dropping the top {first_neg} trades")
    out["drop_top_n"] = {"rows": rows, "n_to_kill_total": first_neg}

    # ----------------------------------------------------------------------- leave-one-symbol
    L.hr("4d. Leave one symbol out (33 symbols across 35 trades)")
    rows = []
    for s in sorted(set(tr["symbol"].to_list())):
        d = df.filter(pl.col("symbol") != s)
        n, t, p = book_stats(d)
        rows.append({"dropped": s, "trades": n, "net_r": round(t, 2), "per": round(p, 4)})
    worst = sorted(rows, key=lambda r: r["per"])[:5]
    for r in worst:
        print(
            f"  drop {r['dropped']:<6} -> {r['trades']:>3} trades  {r['net_r']:+7.2f}R  "
            f"{r['per']:+.4f}/trade"
        )
    kills = [r["dropped"] for r in rows if r["per"] <= 0]
    print(f"  symbols whose removal turns it negative: {kills if kills else 'none'}")
    out["leave_one_symbol"] = {"rows": rows, "kills": kills}

    # --------------------------------------------------------------- block bootstrap by session
    L.hr("4e. Block bootstrap by SESSION (trades within a day are not independent)")
    sess = tr.group_by("dt").agg(pl.col("net_r").sum().alias("s"), pl.len().alias("k")).sort("dt")
    S_ = sess["s"].to_numpy()
    K_ = sess["k"].to_numpy()
    rng = np.random.default_rng(99)
    B = 20000
    idx = rng.integers(0, len(S_), size=(B, len(S_)))
    boot_per = S_[idx].sum(axis=1) / K_[idx].sum(axis=1)
    boot_tot = S_[idx].sum(axis=1)
    lo, hi = np.quantile(boot_per, [0.025, 0.975])
    print(
        f"  {len(S_)} traded sessions.  net R/trade  point {per0:+.4f}  "
        f"95% CI [{lo:+.4f}, {hi:+.4f}]"
    )
    print(f"  P(net R/trade <= 0) under the bootstrap = {(boot_per <= 0).mean():.4f}")
    print(
        f"  total net R      point {tot0:+.2f}  95% CI "
        f"[{np.quantile(boot_tot, 0.025):+.2f}, {np.quantile(boot_tot, 0.975):+.2f}]"
    )
    out["block_bootstrap"] = {
        "n_sessions": int(len(S_)),
        "point_per_trade": round(per0, 4),
        "ci95_per_trade": [round(float(lo), 4), round(float(hi), 4)],
        "p_le_zero": round(float((boot_per <= 0).mean()), 4),
        "ci95_total_r": [
            round(float(np.quantile(boot_tot, 0.025)), 2),
            round(float(np.quantile(boot_tot, 0.975)), 2),
        ],
        "b": B,
    }

    # ------------------------------------------------------------------- sub-sample stability
    L.hr("4f. Sub-sample stability")
    dates = sorted(df["dt"].unique().to_list())
    rank = {d: i for i, d in enumerate(dates)}
    tr3 = tr.with_columns(
        pl.col("dt").map_elements(lambda d: rank[d] % 2, return_dtype=pl.Int64).alias("parity"),
        pl.col("dt")
        .map_elements(lambda d: "H1" if rank[d] < len(dates) / 2 else "H2", return_dtype=pl.Utf8)
        .alias("half"),
        pl.when(pl.col("entry_fill") < 6.0)
        .then(pl.lit("<$6"))
        .otherwise(pl.lit(">=$6"))
        .alias("price_band"),
        pl.when(pl.col("stop_pct") < 0.06)
        .then(pl.lit("tight"))
        .otherwise(pl.lit("wide"))
        .alias("stop_band"),
    )
    subs = {}
    for col in ("parity", "half", "price_band", "stop_band", "source", "split", "sized_by"):
        g = (
            tr3.group_by(col)
            .agg(
                pl.len().alias("n"),
                pl.col("net_r").sum().round(2).alias("net_r"),
                pl.col("net_r").mean().round(4).alias("per"),
                (pl.col("r") > 0).mean().round(3).alias("win"),
            )
            .sort(col)
        )
        print(f"\n  by {col}:")
        print(g)
        subs[col] = g.to_dicts()
    n_pos = sum(1 for c, rows_ in subs.items() for r in rows_ if r["per"] > 0)
    n_tot = sum(len(rows_) for rows_ in subs.values())
    print(f"\n  sub-samples with positive net R/trade: {n_pos}/{n_tot}")
    out["subsamples"] = subs
    out["subsample_positive"] = {"positive": n_pos, "total": n_tot}

    # ---------------------------------------------------------------- matched-trade-count random
    L.hr("4g. Is it just taking fewer trades? Random rules with the SAME daily trade count")
    sel = L.claim_selector(df)
    for pool_name, pool in (
        ("SHIPPED pool", C.SHIPPED(df)),
        (
            "SHIPPED + all 3 features present",
            C.SHIPPED(df).filter(
                pl.col("runup_pre_appearance").is_not_null()
                & pl.col("rvol_pole").is_not_null()
                & pl.col("shares_outstanding").is_not_null()
            ),
        ),
        ("whole panel", df),
    ):
        p = C.permutation_pvalue(pool, sel, n=4000, seed=13)
        print(f"  {pool_name:<34} p={p:.4f}")
        out.setdefault("matched_count_permutation", {})[pool_name] = round(p, 4)

    L.write("step4_robustness.json", out)
    print("\nwrote step4_robustness.json")


if __name__ == "__main__":
    main()
