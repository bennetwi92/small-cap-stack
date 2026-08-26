"""Step 1 — anatomy of the 35 trades, and where the filter's selectivity actually comes from.

Two questions the claim never answers:
  a) how much of the in-play filter is *nullity* (the field is missing) rather than a threshold?
  b) is the in-play filter positive on its own at row level, or only in the 35-row intersection?
"""

from __future__ import annotations

import lab as L
import numpy as np
import polars as pl
from lab import C


def rowlevel(d: pl.DataFrame, target: float = 2.0) -> tuple[int, float]:
    if d.is_empty():
        return 0, 0.0
    r = C.fixed_target_r(d, target)["r"].to_numpy()
    return len(r), float(r.mean())


def main() -> None:
    df = L.load_panel_checked()
    out: dict = {}

    # ------------------------------------------------------------------- nullity decomposition
    L.hr("1a. What does the in-play filter actually remove from the SHIPPED pool?")
    sh = C.SHIPPED(df)
    steps = [
        ("SHIPPED", sh),
        ("+ runup notnull", sh.filter(pl.col("runup_pre_appearance").is_not_null())),
        ("+ rvol notnull", sh.filter(pl.col("rvol_pole").is_not_null())),
        ("+ shares notnull", sh.filter(pl.col("shares_outstanding").is_not_null())),
        (
            "+ all three notnull",
            sh.filter(
                pl.col("runup_pre_appearance").is_not_null()
                & pl.col("rvol_pole").is_not_null()
                & pl.col("shares_outstanding").is_not_null()
            ),
        ),
    ]
    nn = steps[-1][1]
    steps += [
        ("  then runup>=0.15", nn.filter(pl.col("runup_pre_appearance") >= 0.15)),
        (
            "  then +rvol>=2.0",
            nn.filter((pl.col("runup_pre_appearance") >= 0.15) & (pl.col("rvol_pole") >= 2.0)),
        ),
        ("  then +shares<=50M (= IN PLAY)", L.in_play(sh)),
    ]
    tbl = []
    for name, d in steps:
        n, rpt = rowlevel(d)
        bk = L.book_of(d)
        sc = C.score(bk, sessions=df["dt"].n_unique()) if bk.height else None
        print(
            f"  {name:<32} rows={n:>4} rowR={rpt:+.3f}  book={sc['trades'] if sc else 0:>3} "
            f"netR={sc['net_r'] if sc else 0:+7.2f} per={sc['net_r_per_trade'] if sc else 0:+.3f}"
        )
        tbl.append(
            {
                "step": name,
                "rows": n,
                "row_r_per_row": round(rpt, 4),
                "book_trades": sc["trades"] if sc else 0,
                "book_net_r": sc["net_r"] if sc else 0.0,
                "book_net_r_per_trade": sc["net_r_per_trade"] if sc else 0.0,
            }
        )
    out["decomposition"] = tbl

    # nullity as a standalone filter on SHIPPED
    L.hr("1b. Is 'field is present' by itself a profitable filter?")
    for name, d in (
        ("SHIPPED all", sh),
        ("SHIPPED, shares present", sh.filter(pl.col("shares_outstanding").is_not_null())),
        ("SHIPPED, shares MISSING", sh.filter(pl.col("shares_outstanding").is_null())),
    ):
        bk = L.book_of(d)
        sc = C.score(bk, sessions=df["dt"].n_unique()) if bk.height else None
        n, rpt = rowlevel(d)
        print(
            f"  {name:<28} rows={n:>4} rowR={rpt:+.3f}  book={sc['trades'] if sc else 0:>3} "
            f"netR={sc['net_r'] if sc else 0:+7.2f} per={sc['net_r_per_trade'] if sc else 0:+.3f}"
        )

    # -------------------------------------------------- in-play at ROW level, whole population
    L.hr("1c. In-play at ROW level (no capacity cap) — does it beat the pool it came from?")
    rl = []
    for pop_name, pop in (("raw panel", df), ("passed only", df.filter(pl.col("passed")))):
        for lab_, d in ((f"{pop_name}: all", pop), (f"{pop_name}: in play", L.in_play(pop))):
            n, rpt = rowlevel(d)
            hit = float((d["max_gain_pct"] >= 0.50).mean()) if d.height else 0.0
            print(f"  {lab_:<28} rows={n:>5} rowR@2R={rpt:+.4f}  P(+50% move)={hit:.4f}")
            rl.append({"pop": lab_, "rows": n, "row_r": round(rpt, 4), "p50": round(hit, 4)})
    out["row_level"] = rl

    # ------------------------------------------------------------------ the 35 trades themselves
    L.hr("1d. The 35 trades")
    book = L.book_of(L.claim_selector(df))
    res = C.score(book, sessions=df["dt"].n_unique())
    tr = res["_trades"].sort("net_r", descending=True)
    cols = [
        "dt",
        "symbol",
        "source",
        "split",
        "entry_fill",
        "stop_pct",
        "r",
        "net_r",
        "qty",
        "sized_by",
        "same_bar_stop",
        "max_r",
    ]
    print(tr.select([c for c in cols if c in tr.columns]))
    x = tr["net_r"].to_numpy()
    print(f"\n  net R total {x.sum():+.2f}; winners {(x > 0).sum()}, losers {(x <= 0).sum()}")
    print(f"  top 1 contributes {x.max():+.2f} ({x.max() / x.sum():.0%} of total)")
    print(
        f"  top 3 contribute {np.sort(x)[-3:].sum():+.2f} ({np.sort(x)[-3:].sum() / x.sum():.0%})"
    )
    print(f"  same_bar_stop trades: {int(tr['same_bar_stop'].sum())} of {tr.height}")
    print(f"  cap-bound trades: {res['cap_bound']} of {tr.height}")
    print(
        f"  distinct symbols: {tr['symbol'].n_unique()}; distinct sessions: {tr['dt'].n_unique()}"
    )
    print("\n  symbol counts:")
    print(
        tr.group_by("symbol")
        .agg(pl.len().alias("n"), pl.col("net_r").sum().round(2))
        .sort("net_r", descending=True)
    )
    out["trades"] = tr.select([c for c in cols if c in tr.columns]).to_dicts()
    out["concentration"] = {
        "total_net_r": round(float(x.sum()), 2),
        "winners": int((x > 0).sum()),
        "losers": int((x <= 0).sum()),
        "top1_net_r": round(float(x.max()), 2),
        "top3_share": round(float(np.sort(x)[-3:].sum() / x.sum()), 4),
        "same_bar_stops": int(tr["same_bar_stop"].sum()),
        "cap_bound": res["cap_bound"],
        "n_symbols": tr["symbol"].n_unique(),
        "n_sessions": tr["dt"].n_unique(),
    }

    L.write("step1_anatomy.json", out)
    print("\nwrote step1_anatomy.json")


if __name__ == "__main__":
    main()
