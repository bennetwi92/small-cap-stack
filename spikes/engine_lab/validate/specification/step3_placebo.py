"""Step 3 — how surprising is +16.7R, and how many reasonable specifications find it?

Two questions the swap study cannot answer on its own.

**A. The reference distribution.** The SHIPPED population is 125 rows. A rule that keeps 35 of them
books ~35 trades. What does an ARBITRARY 35-of-125 selection score? Two placebos:

- *random subsets* — draw k rows at random, 5,000 times, and score the book.
- *arbitrary features* — take every trigger-time-safe numeric column in the panel, cut it at the
  same selectivity in BOTH directions, and score. This is the honest denominator for "we tried
  three features and found +16.7R": it says how often any feature, cut anywhere, does that well.

**B. The specification family.** If "the stock is in play" is a real idea, then the cross product of
{a running measure} x {a size measure} x {rvol on/off} should mostly be positive. Building the whole
grid and counting the positives is the direct test of the brief's question.
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

#: Trigger-time-safe numeric columns that are NOT one of the three claimed features. Placebos.
PLACEBO_COLS = [
    "entry_fill",
    "stop_pct",
    "planned_risk",
    "pole_len",
    "cons_len",
    "retracement",
    "cycle_num",
    "untraded_cons_bars",
    "trigger_et_min",
    "staleness_delay_min",
    "first_hit_et_min",
    "pole_pct",
    "pole_volume",
    "vol_share_pole",
    "range_before_pole_pct",
    "cum_volume_to_trigger",
    "cum_dollar_vol_to_trigger",
    "hits_before_trigger",
    "bars_before_pole",
    "short_percent",
    "ext_at_trigger",
    "ext_at_peak",
    "ext_at_base",
    "hi_ext_pre_trigger",
    "runup_to_pole",
    "range_pre_trigger_pct",
    "ret_last6_to_trigger",
    "ret_last12_to_trigger",
    "ret_last24_to_trigger",
    "ret_last12_to_pole",
    "atr_pct_pre_trigger",
    "atr_pct_pre_pole",
    "up_bar_frac_pre_pole",
    "vwap_ext_at_trigger",
    "bars_to_trigger",
    "move_since_appearance",
    "mktcap",
    "price",
    "pole_gain_calc",
    "ext_at_first_hit",
]


def random_subset_null(
    sh: pl.DataFrame, k: int, *, sessions: int, n: int = 5000, seed: int = 3
) -> dict[str, Any]:
    """Book net R for `n` random k-row subsets of the SHIPPED population."""
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        idx = rng.choice(sh.height, size=min(k, sh.height), replace=False)
        s = C.score(C.build_book(sh[idx.tolist()], max_per_day=2), sessions=sessions)
        vals.append(s["net_r"])
    a = np.array(vals)
    return {
        "k": k,
        "n_draws": n,
        "mean": round(float(a.mean()), 3),
        "sd": round(float(a.std(ddof=1)), 3),
        "p05": round(float(np.quantile(a, 0.05)), 2),
        "p50": round(float(np.quantile(a, 0.5)), 2),
        "p95": round(float(np.quantile(a, 0.95)), 2),
        "max": round(float(a.max()), 2),
        "_samples": a,
    }


def placebo_features(sh: pl.DataFrame, *, sessions: int, target_n: int) -> list[dict[str, Any]]:
    """Every placebo column, cut both ways at the same selectivity as the claim's rule."""
    rows = []
    for col in PLACEBO_COLS:
        if col not in sh.columns:
            continue
        for op in ("ge", "le"):
            th = W.matched_threshold(sh, col, op, target_n)
            if not np.isfinite(th):
                continue
            r = W.evaluate(sh, [(col, op, th)], sessions=sessions)
            rows.append({"feature": col, "op": op, "threshold": round(float(th), 6), **r})
    return sorted(rows, key=lambda r: -r["net_r"])


def spec_family(sh: pl.DataFrame, *, sessions: int) -> list[dict[str, Any]]:
    """{running} x {size} x {rvol on/off}, every threshold at matched selectivity."""
    run_target = W.apply_clauses(sh, [W.ORIG[0]]).height
    size_target = W.apply_clauses(sh, [W.ORIG[2]]).height
    runs = [c for c in W.RUNNING_ALTS if c in sh.columns]
    sizes = [c for c in W.SMALL_ALTS if c in sh.columns]
    rows = []
    for rc in runs:
        rth = W.matched_threshold(sh, rc, "ge", run_target)
        for sc in sizes:
            sth = W.matched_threshold(sh, sc, "le", size_target)
            if not (np.isfinite(rth) and np.isfinite(sth)):
                continue
            for rvol_on in (False, True):
                cls = [(rc, "ge", rth), (sc, "le", sth)]
                if rvol_on:
                    cls.append(W.ORIG[1])
                r = W.evaluate(sh, cls, sessions=sessions)
                rows.append(
                    {
                        "running": rc,
                        "size": sc,
                        "rvol": rvol_on,
                        "is_original": rc == "runup_pre_appearance"
                        and sc == "shares_outstanding"
                        and rvol_on,
                        **r,
                    }
                )
    return rows


def main() -> None:
    df = features.attach(S.panel(2.0))
    sessions = df["dt"].n_unique()
    sh = C.SHIPPED(df)
    out: dict[str, Any] = {"shipped_rows": sh.height, "sessions": sessions}

    and3 = W.evaluate(sh, W.ORIG, sessions=sessions)
    simple = W.evaluate(sh, [("shares_outstanding", "le", 50e6)], sessions=sessions)
    out["and3"] = and3
    out["simple"] = simple

    print("--- A1. random k-row subsets of SHIPPED ---")
    nulls = {}
    for k, obs, label in ((35, and3["net_r"], "and3"), (60, simple["net_r"], "simple")):
        nd = random_subset_null(sh, k, sessions=sessions)
        a = nd.pop("_samples")
        nd["observed"] = obs
        nd["rule"] = label
        nd["p_ge_observed"] = round(float((a >= obs).mean()), 4)
        nulls[label] = nd
        print(
            f"  k={k:<3} ({label}) observed {obs:+.1f}R vs random mean {nd['mean']:+.1f} "
            f"sd {nd['sd']:.1f} p95 {nd['p95']:+.1f} max {nd['max']:+.1f}  "
            f"-> p = {nd['p_ge_observed']}"
        )
    out["random_subset_null"] = nulls

    print("\n--- A2. arbitrary features, cut at the same selectivity ---")
    for target_n, label, obs in ((35, "and3", and3["net_r"]), (60, "simple", simple["net_r"])):
        pf = placebo_features(sh, sessions=sessions, target_n=target_n)
        beat = [p for p in pf if p["net_r"] >= obs]
        out[f"placebo_features_n{target_n}"] = pf
        out[f"placebo_summary_n{target_n}"] = {
            "n_specs": len(pf),
            "n_beating_observed": len(beat),
            "frac_beating": round(len(beat) / len(pf), 4) if pf else 0.0,
            "median_net_r": round(float(np.median([q["net_r"] for q in pf])), 2),
            "p90_net_r": round(float(np.quantile([q["net_r"] for q in pf], 0.9)), 2),
            "max_net_r": round(max(p["net_r"] for p in pf), 2) if pf else 0.0,
            "observed": obs,
        }
        summ = out[f"placebo_summary_n{target_n}"]
        print(
            f"  target_n={target_n} ({label}, observed {obs:+.1f}R): "
            f"{len(pf)} arbitrary specs, median {summ['median_net_r']:+.1f}R, "
            f"p90 {summ['p90_net_r']:+.1f}R, "
            f"{len(beat)} ({len(beat) / len(pf) * 100:.0f}%) beat it"
        )
        for q in pf[:8]:
            print(
                f"     {q['feature']:<26}{q['op']}  {q['trades']:>3} tr net {q['net_r']:+7.1f}R "
                f"({q['net_r_per_trade']:+.3f})"
            )

    print("\n--- B. the specification family: running x size x rvol ---")
    fam = spec_family(sh, sessions=sessions)
    out["spec_family"] = fam
    nr = np.array([f["net_r"] for f in fam])
    rpt = np.array([f["net_r_per_trade"] for f in fam])
    out["spec_family_summary"] = {
        "n_specs": len(fam),
        "frac_net_positive": round(float((nr > 0).mean()), 4),
        "frac_rpt_gt_0.2": round(float((rpt > 0.2).mean()), 4),
        "median_net_r": round(float(np.median(nr)), 2),
        "median_rpt": round(float(np.median(rpt)), 4),
        "original_net_r": and3["net_r"],
        "original_rank_of_n": int((nr > and3["net_r"]).sum() + 1),
    }
    print(
        f"  {len(fam)} specifications: {(nr > 0).mean() * 100:.0f}% net-positive, "
        f"median {np.median(nr):+.1f}R ({np.median(rpt):+.3f}/trade); "
        f"the original ranks {out['spec_family_summary']['original_rank_of_n']} of {len(fam)}"
    )
    # which factor explains the spread?
    for key in ("running", "size", "rvol"):
        g = {}
        for f in fam:
            g.setdefault(f[key], []).append(f["net_r"])
        rows = sorted(((k, float(np.median(v)), len(v)) for k, v in g.items()), key=lambda x: -x[1])
        print(f"  median net R by {key}:")
        for k, m, n in rows:
            print(f"     {str(k):<28} {m:+7.1f}R  (n={n})")
        out[f"spec_family_by_{key}"] = [
            {"value": str(k), "median_net_r": round(m, 2), "n": n} for k, m, n in rows
        ]

    def _enc(o: Any) -> Any:
        return float(o) if isinstance(o, np.floating) else str(o)

    (S.OUT / "step3_placebo.json").write_text(json.dumps(out, indent=2, default=_enc))
    print(f"\nwrote {S.OUT / 'step3_placebo.json'}")


if __name__ == "__main__":
    main()
