"""Step 0 — re-derive every number in CLAIM.md from the raw panel. Trust nothing.

Runs: `.venv/bin/python spikes/engine_lab/validate/adversarial/step0_rederive.py`
"""

from __future__ import annotations

import lab as L
import numpy as np
import polars as pl
from lab import C


def main() -> None:
    df = L.load_panel_checked()
    L.hr("STEP 0 — population")
    print(f"rows={df.height}  sessions={df['dt'].n_unique()}")
    print(
        df.group_by("split")
        .agg(pl.len().alias("rows"), pl.col("dt").n_unique().alias("sessions"))
        .sort("split")
    )
    print(df.group_by("source").agg(pl.len(), pl.col("dt").n_unique().alias("sessions")))

    # --- lookahead guard on the claim's own columns -------------------------------------------
    C.assert_no_lookahead(L.CLAIM_COLS)
    print("\nassert_no_lookahead(CLAIM_COLS): OK")

    out: dict = {"population": {"rows": df.height, "sessions": df["dt"].n_unique()}}

    # --- the three headline rows ---------------------------------------------------------------
    L.hr("STEP 0 — the CLAIM.md table, re-derived")
    variants = {
        "shipped_only": C.SHIPPED(df),
        "in_play_only": L.in_play(df),
        "shipped_and_in_play": L.claim_selector(df),
    }
    claimed = {
        "shipped_only": (122, 0.62, 7.1, 0.058),
        "in_play_only": (242, 1.23, -20.3, -0.084),
        "shipped_and_in_play": (35, 0.18, 16.7, 0.478),
    }
    table = {}
    for name, sel in variants.items():
        res = L.score_sel(df, sel)
        got = (
            res["trades"],
            res["trades_per_session"],
            res["net_r"],
            res["net_r_per_trade"],
        )
        cl = claimed[name]
        print(
            f"{name:<22} claimed trades={cl[0]:>4} /sess={cl[1]:.2f} netR={cl[2]:+7.1f} "
            f"per={cl[3]:+.3f}"
        )
        print(
            f"{'':<22} GOT     trades={got[0]:>4} /sess={got[1]:.2f} netR={got[2]:+7.1f} "
            f"per={got[3]:+.3f}   grossR={res['gross_r']:+.1f} win={res['win_rate']:.3f}"
        )
        match = got[0] == cl[0] and abs(got[2] - cl[2]) < 0.15 and abs(got[3] - cl[3]) < 0.005
        print(f"{'':<22} MATCH={match}")
        table[name] = {
            "claimed": {
                "trades": cl[0],
                "per_session": cl[1],
                "net_r": cl[2],
                "net_r_per_trade": cl[3],
            },
            "derived": {
                "trades": got[0],
                "per_session": got[1],
                "net_r": got[2],
                "net_r_per_trade": got[3],
                "gross_r": res["gross_r"],
                "win_rate": res["win_rate"],
                "max_dd_net_r": res["max_dd_net_r"],
                "cap_bound": res["cap_bound"],
            },
            "match": bool(match),
        }
        if "split" in res:
            table[name]["by_split"] = {
                k: {kk: v[kk] for kk in ("trades", "net_r", "net_r_per_trade", "win_rate")}
                for k, v in res["split"].items()
            }
        if "source" in res:
            table[name]["by_source"] = {
                k: {kk: v[kk] for kk in ("trades", "net_r", "net_r_per_trade", "win_rate")}
                for k, v in res["source"].items()
            }
    out["headline_table"] = table

    # --- per-period claim: dev +4.8, val +10.6, holdout +1.3 -----------------------------------
    L.hr("STEP 0 — per-period net R of the claimed book")
    print("claimed: dev +4.8  val +10.6  holdout +1.3")
    bysplit = table["shipped_and_in_play"].get("by_split", {})
    for k in ("dev", "val", "holdout"):
        b = bysplit.get(k, {})
        print(
            f"  {k:<8} trades={b.get('trades', 0):>3} netR={b.get('net_r', 0.0):+6.2f} "
            f"per={b.get('net_r_per_trade', 0.0):+.3f} win={b.get('win_rate', 0.0):.3f}"
        )

    # --- the claimed error bar: +0.50 +/- 0.43 --------------------------------------------------
    L.hr("STEP 0 — error bar on net R/trade (naive trade-level SE)")
    book = L.book_of(L.claim_selector(df))
    res = C.score(book, sessions=df["dt"].n_unique())
    tr = res["_trades"]
    x = tr["net_r"].to_numpy()
    se = float(x.std(ddof=1) / np.sqrt(len(x)))
    print(
        f"n={len(x)} mean={x.mean():+.4f} sd={x.std(ddof=1):.4f} naive SE={se:.4f} "
        f"(claim quoted +/-{0.43:.2f})"
    )
    # gross too, since the claim's +0.50 looks like a gross figure
    g = tr["r"].to_numpy()
    seg = float(g.std(ddof=1) / np.sqrt(len(g)))
    print(f"gross mean={g.mean():+.4f} sd={g.std(ddof=1):.4f} SE={seg:.4f}")
    out["error_bar"] = {
        "n": len(x),
        "net_mean": round(float(x.mean()), 4),
        "net_sd": round(float(x.std(ddof=1)), 4),
        "net_se": round(se, 4),
        "gross_mean": round(float(g.mean()), 4),
        "gross_se": round(seg, 4),
        "claimed": "+0.50 +/- 0.43",
    }

    # --- intermediate signal: rate of 50%+ moves after entry -----------------------------------
    L.hr("STEP 0 — intermediate signal (max_gain_pct >= 0.50), claimed to roughly double")
    claim_rates = {
        "dev": (433, 0.045, 0.081),
        "val": (263, 0.094, 0.141),
        "holdout": (230, 0.052, 0.078),
    }
    inter = {}
    for sp, (n_cl, base_cl, ip_cl) in claim_rates.items():
        d = C.SHIPPED(df.filter(pl.col("split") == sp))
        ip = L.in_play(d)
        b = float((d["max_gain_pct"] >= 0.50).mean()) if d.height else 0.0
        i = float((ip["max_gain_pct"] >= 0.50).mean()) if ip.height else 0.0
        print(
            f"  {sp:<8} claimed n={n_cl:>4} {base_cl:.3f}->{ip_cl:.3f} | "
            f"GOT n={d.height:>4} {b:.3f}->{i:.3f} (in-play n={ip.height})"
        )
        inter[sp] = {
            "claimed": {"n": n_cl, "base": base_cl, "in_play": ip_cl},
            "derived": {
                "n": d.height,
                "base": round(b, 4),
                "in_play": round(i, 4),
                "in_play_n": ip.height,
            },
        }
    out["intermediate"] = inter

    # --- monotone quintiles ---------------------------------------------------------------------
    L.hr("STEP 0 — quintile monotonicity vs a 50%+ move (whole population)")
    quint = {}
    for col in ("runup_pre_appearance", "shares_outstanding", "rvol_pole"):
        d = df.filter(pl.col(col).is_not_null())
        q = d.with_columns((pl.col(col).rank("ordinal") * 5 // (d.height + 1)).alias("_q"))
        rows = (
            q.group_by("_q")
            .agg(pl.len().alias("n"), (pl.col("max_gain_pct") >= 0.50).mean().alias("rate"))
            .sort("_q")
        )
        print(
            f"  {col}: "
            + "  ".join(f"{r['rate']:.3f}(n={r['n']})" for r in rows.iter_rows(named=True))
        )
        quint[col] = rows.to_dicts()
    out["quintiles"] = quint

    L.write("step0_rederive.json", out)
    print("\nwrote step0_rederive.json")


if __name__ == "__main__":
    main()
