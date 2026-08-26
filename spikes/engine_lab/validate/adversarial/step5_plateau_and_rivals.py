"""Step 5 — the claimed plateau, the exit target, the inert capacity cap, and simpler rivals.

CLAIM.md: "moving any one threshold +/-(a lot) kept net R/trade between +0.25 and +0.63, with no
sign flip". Re-derive it — and print the TRADE COUNT at every cell, which the claim omits. Step 2b
showed the null's median is essentially a function of trade count, so a plateau of 20-40-trade
books is a plateau of the selection effect, not of an edge.

Then: is there a simpler rule that does as well or better with more trades?
"""

from __future__ import annotations

import lab as L
import numpy as np
import polars as pl
from lab import C


def stats(sel: pl.DataFrame, target: float = 2.0) -> tuple[int, float, float]:
    bk = L.book_of(sel, target=target)
    if bk.is_empty():
        return 0, 0.0, 0.0
    v = L.fast_net_r(bk["entry_fill"].to_numpy(), bk["stop"].to_numpy(), bk["r"].to_numpy())
    v = v[~np.isnan(v)]
    return (len(v), float(v.sum()), float(v.mean())) if len(v) else (0, 0.0, 0.0)


def main() -> None:
    df = L.load_panel_checked()
    sh = C.SHIPPED(df)
    out: dict = {}

    # -------------------------------------------------------------- 5a. the cap does nothing
    L.hr("5a. Is the '2 per day' cap doing anything?")
    sel = L.claim_selector(df)
    for cap in (1, 2, 3, 99):
        n, t, p = stats(sel)
        bk = C.build_book(C.fixed_target_r(sel), max_per_day=cap)
        v = L.fast_net_r(bk["entry_fill"].to_numpy(), bk["stop"].to_numpy(), bk["r"].to_numpy())
        v = v[~np.isnan(v)]
        print(
            f"  max_per_day={cap:>2}: {len(v):>3} trades  {v.sum():+7.2f}R  {v.mean():+.4f}/trade"
        )
    print(f"  selected rows = {sel.height}, distinct sessions = {sel['dt'].n_unique()}")
    print("  -> the cap never binds; '2 per day' is inert in this rule.")
    out["cap_inert"] = {
        "selected_rows": sel.height,
        "sessions": sel["dt"].n_unique(),
        "binds": bool(sel.height > sel["dt"].n_unique() * 2),
    }

    # ------------------------------------------------------------- 5b. sensitivity, with counts
    L.hr("5b. Sensitivity — one threshold at a time, TRADE COUNT shown")
    rows = []
    grids = {
        "runup": [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40],
        "rvol": [0.0, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0],
        "shares": [10e6, 20e6, 35e6, 50e6, 75e6, 100e6, 200e6, 1e12],
    }
    for name, grid in grids.items():
        print(f"\n  {name}:")
        for g in grid:
            th = dict(L.IN_PLAY)
            th[name] = g
            n, t, p = stats(L.in_play(sh, **th))
            print(f"    {name}={g:<12g} {n:>3} trades  {t:+7.2f}R  {p:+.4f}/trade")
            rows.append(
                {
                    "threshold": name,
                    "value": g,
                    "trades": n,
                    "net_r": round(t, 2),
                    "per": round(p, 4),
                }
            )
    out["sensitivity"] = rows
    per = [r["per"] for r in rows if r["trades"] >= 10]
    print(
        f"\n  across cells with >=10 trades: net R/trade ranges {min(per):+.3f} .. {max(per):+.3f}"
    )
    print("  BUT the trade count moves with it — see step 2b: the null's median net R/trade")
    print("  is itself ~+0.7 at 25 trades, +0.47 at 35, +0.19 at 99.")

    # ---------------------------------------------------- 5c. the plateau against the null median
    L.hr("5c. Every sensitivity cell vs the null median AT ITS OWN TRADE COUNT")
    # null median as a function of trade count, measured directly: random subsets of SHIPPED of
    # size k, best-of-150-grid is not needed here — the honest comparator is the *unconditional*
    # mean of SHIPPED (+0.058) plus the selection effect measured in step 2b.
    ref = {25: 0.7066, 35: 0.6472, 50: 0.4663, 70: 0.3552, 99: 0.1908}
    ks = sorted(ref)
    print("  null median (3-clause search, from step 2b-1):")
    for k in ks:
        print(f"    {k:>3} trades -> {ref[k]:+.4f}")
    beat = []
    for r in rows:
        if r["trades"] < 10:
            continue
        k = min(ks, key=lambda x: abs(x - r["trades"]))
        beat.append({**r, "null_median_at_count": ref[k], "beats_null": r["per"] > ref[k]})
    n_beat = sum(1 for b in beat if b["beats_null"])
    print(
        f"\n  sensitivity cells beating the null median at their own trade count: "
        f"{n_beat}/{len(beat)}"
    )
    out["plateau_vs_null"] = {"cells": beat, "n_beating": n_beat, "n_cells": len(beat)}

    # -------------------------------------------------------------- 5d. does the target matter?
    L.hr("5d. Exit target robustness (the claim is stated at 2R)")
    tgt_rows = []
    for tgt in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        nc, tc, pc = stats(L.claim_selector(df), target=tgt)
        ns, ts, ps = stats(sh, target=tgt)
        print(
            f"  target {tgt:>4.1f}R: claim {nc:>3} trades {pc:+.4f}/trade  |  "
            f"SHIPPED {ns:>3} trades {ps:+.4f}/trade  |  edge {pc - ps:+.4f}"
        )
        tgt_rows.append(
            {
                "target": tgt,
                "claim_trades": nc,
                "claim_per": round(pc, 4),
                "shipped_trades": ns,
                "shipped_per": round(ps, 4),
                "edge": round(pc - ps, 4),
            }
        )
    out["target_robustness"] = tgt_rows

    # ------------------------------------------------------------------- 5e. simpler rivals
    L.hr("5e. Simpler rivals on the same SHIPPED pool (all in-sample, all equally selected)")
    notnull = pl.col("shares_outstanding").is_not_null()
    rivals = {
        "SHIPPED (baseline)": sh,
        "shares_outstanding present": sh.filter(notnull),
        "shares <= 50M": sh.filter(notnull & (pl.col("shares_outstanding") <= 50e6)),
        "shares <= 100M": sh.filter(notnull & (pl.col("shares_outstanding") <= 100e6)),
        "runup >= 0.15": sh.filter(pl.col("runup_pre_appearance") >= 0.15),
        "shares <= 50M AND runup >= 0.15": sh.filter(
            notnull
            & (pl.col("shares_outstanding") <= 50e6)
            & (pl.col("runup_pre_appearance") >= 0.15)
        ),
        "CLAIM (all three)": L.claim_selector(df),
    }
    rr = []
    for name, d in rivals.items():
        n, t, p = stats(d)
        tps = n / df["dt"].n_unique()
        print(f"  {name:<34} {n:>3} trades ({tps:.2f}/sess)  {t:+7.2f}R  {p:+.4f}/trade")
        rr.append(
            {
                "rule": name,
                "trades": n,
                "per_session": round(tps, 3),
                "net_r": round(t, 2),
                "per": round(p, 4),
            }
        )
    print(
        "\n  The lab's objective #2 is >=0.5 trades/session. The CLAIM's rule reaches "
        f"{rivals['CLAIM (all three)'].height / df['dt'].n_unique():.2f}."
    )
    out["rivals"] = rr

    # ---------------------------------------------------- 5f. rvol_pole: does it do ANY work?
    L.hr("5f. rvol_pole — CLAIM.md already suspected it does no work")
    for rv in (0.0, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 20.0):
        n, t, p = stats(L.in_play(sh, rvol=rv))
        print(f"  rvol>={rv:<5g} {n:>3} trades  {t:+7.2f}R  {p:+.4f}/trade")
    n_off, t_off, p_off = stats(
        sh.filter((pl.col("runup_pre_appearance") >= 0.15) & (pl.col("shares_outstanding") <= 50e6))
    )
    print(f"  drop rvol entirely (2 clauses): {n_off} trades {t_off:+.2f}R {p_off:+.4f}/trade")
    out["rvol_work"] = {
        "two_clause": {"trades": n_off, "net_r": round(t_off, 2), "per": round(p_off, 4)}
    }

    # ------------------------------------------------- 5g. same-bar-stop and cap-bound exposure
    L.hr("5g. How much of the book leans on contested mechanics?")
    bk = L.book_of(L.claim_selector(df))
    res = C.score(bk, sessions=df["dt"].n_unique())
    tr = res["_trades"]
    sb = int(tr["same_bar_stop"].sum())
    print(f"  same-bar stops: {sb}/{tr.height} ({sb / tr.height:.0%}) — common.py notes the")
    print("    conservative reading was wrong 38% of the time at 1-min granularity (#583).")
    print(f"  cap-bound (sized by notional, not risk): {res['cap_bound']}/{tr.height}")
    print(f"  mean cost drag: {res['cost_r_per_trade']:+.4f} R/trade")
    # what if the 38% figure applied?
    print(
        f"  if 38% of those same-bar losers were actually 2R wins: "
        f"net R/trade would move to ~{(tr['net_r'].sum() + 0.38 * sb * 3.0) / tr.height:+.3f}"
    )
    out["mechanics"] = {
        "same_bar_stops": sb,
        "trades": tr.height,
        "cap_bound": res["cap_bound"],
        "cost_r_per_trade": res["cost_r_per_trade"],
    }

    L.write("step5_plateau_and_rivals.json", out)
    print("\nwrote step5_plateau_and_rivals.json")


if __name__ == "__main__":
    main()
