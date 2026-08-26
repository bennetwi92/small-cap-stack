"""The specification surface — vary every choice the claim makes and re-measure.

Sections map 1:1 onto the brief:

1. `running_alternatives()`  — other ways to say "already running"
2. `small_alternatives()`    — other ways to say "small"
3. `rvol_settled()`          — is rvol_pole load-bearing or decorative
4. `gradients()`             — continuous rank response vs a step at one cut
5. `combination_logic()`     — AND-of-3 vs 2-of-3 vs pairs vs singles vs an additive score
6. `outcome_definitions()`   — other targets, gross vs net, P(50% move), max_r
7. `population_definitions()`— passed off, other pre-market cuts, 1/3 per day, no cons_has_range

**Matched selectivity, not matched threshold.** Comparing `runup >= 0.15` against
`ext_at_trigger >= 0.15` would compare two different amounts of filtering. So for every
alternative, the threshold is chosen so it admits the SAME NUMBER of SHIPPED rows as the clause it
replaces. That removes "how much did you cut" as an explanation and leaves only "did you cut the
right rows".

⚠️ HOLDOUT is contaminated. It is computed and reported per split for completeness; nothing here
treats it as out-of-sample evidence.
"""

from __future__ import annotations

import json
from typing import Any

import features
import numpy as np
import polars as pl
import speclab as S
from speclab import C

RUNUP, RVOL, SHARES = 0.15, 2.0, 50e6

#: Alternative operationalisations of "the stock is already running" (higher = more running).
RUNNING_ALTS: dict[str, str] = {
    "runup_pre_appearance": "ORIGINAL: max high before the scanner first saw it, vs 04:00 open",
    "ext_at_trigger": "extension of the entry fill vs the 04:00 open",
    "ext_at_peak": "extension of the pole peak vs the 04:00 open",
    "ext_at_base": "extension when the pole started, vs the 04:00 open",
    "hi_ext_pre_trigger": "running session high at the trigger bar, vs the 04:00 open",
    "runup_to_pole": "running high before the pole started, vs the 04:00 open",
    "range_pre_trigger_pct": "session high-low range to the trigger, over the 04:00 open",
    "range_before_pole_pct": "high-low range before the pole, over the 04:00 open",
    "ret_last6_to_trigger": "return over the 6 bars before the trigger",
    "ret_last12_to_trigger": "return over the 12 bars before the trigger",
    "ret_last24_to_trigger": "return over the 24 bars before the trigger",
    "ret_last12_to_pole": "return over the 12 bars before the pole base",
    "pole_pct": "the pole move itself",
    "pole_gain_calc": "pole base low to the running high at the trigger",
    "ext_at_first_hit": "extension when the scanner first saw it",
    "move_since_appearance": "move from the scanner appearance to the entry fill",
    "vwap_ext_at_trigger": "entry fill vs the session VWAP to the trigger",
    "atr_pct_pre_trigger": "mean bar range / close to the trigger (volatility, not direction)",
}

#: Alternative operationalisations of "small" (LOWER = smaller, so these are `<=` clauses).
SMALL_ALTS: dict[str, str] = {
    "shares_outstanding": "ORIGINAL: share count",
    "mktcap": "shares_outstanding x entry_fill",
    "mktcap_open": "shares_outstanding x the 04:00 open",
    "price": "the entry fill price on its own",
    "float_shares": "float share count (LIVE ROWS ONLY - 2989/3639 rows are null)",
    "float_cap": "float_shares x entry_fill (LIVE ROWS ONLY)",
    "cum_dollar_vol_to_trigger": "dollar volume traded up to the trigger",
    "planned_risk": "dollars of risk per share",
}


# ---------------------------------------------------------------------------------------------
# Selector plumbing
# ---------------------------------------------------------------------------------------------
def clause(col: str, op: str, val: float) -> pl.Expr:
    e = pl.col(col)
    return (e >= val) if op == "ge" else (e <= val)


def apply_clauses(df: pl.DataFrame, cls: list[tuple[str, str, float]]) -> pl.DataFrame:
    out = df
    for col, cmp_op, val in cls:
        out = out.filter(clause(col, cmp_op, val))
    return out


def matched_threshold(pop: pl.DataFrame, col: str, op: str, target_n: int) -> float:
    """Threshold on `col` that admits as close to `target_n` rows of `pop` as possible.

    Nulls never pass a comparison in polars, so a column with nulls cannot reach `target_n` if it
    has fewer non-null rows; the returned threshold then admits all of them and the shortfall is
    reported alongside.
    """
    v = pop[col].cast(pl.Float64).drop_nulls().to_numpy()
    if len(v) == 0:
        return float("nan")
    v = np.sort(v)
    k = min(target_n, len(v))
    if op == "ge":
        return float(v[len(v) - k]) if k > 0 else float(v[-1] + 1)
    return float(v[k - 1]) if k > 0 else float(v[0] - 1)


def evaluate(
    df: pl.DataFrame,
    cls: list[tuple[str, str, float]],
    *,
    sessions: int,
    max_per_day: int = 2,
) -> dict[str, Any]:
    sel = apply_clauses(df, cls)
    res = C.score(
        C.build_book(sel, max_per_day=max_per_day), sessions=sessions, by=("split", "source")
    )
    out = S.flat(res)
    out["rows_selected"] = sel.height
    out["row_net_rpt"] = round(float(sel["net_r"].mean()), 4) if sel.height else 0.0
    out["row_rate50"] = round(float((sel["max_gain_pct"] >= 0.5).mean()), 4) if sel.height else 0.0
    if res.get("trades"):
        nr = res["_trades"]["net_r"].to_numpy()
        m, se = S.mean_ci(nr)
        out["net_rpt_se"] = round(se, 4)
    else:
        out["net_rpt_se"] = 0.0
    return out


def base() -> tuple[pl.DataFrame, pl.DataFrame, int]:
    df = features.attach(S.panel(2.0))
    return df, C.SHIPPED(df), df["dt"].n_unique()


ORIG = [
    ("runup_pre_appearance", "ge", RUNUP),
    ("rvol_pole", "ge", RVOL),
    ("shares_outstanding", "le", SHARES),
]


# ---------------------------------------------------------------------------------------------
# 1 / 2. Alternative operationalisations
# ---------------------------------------------------------------------------------------------
def _swap_study(
    shipped: pl.DataFrame,
    sessions: int,
    alts: dict[str, str],
    slot: int,
    op: str,
) -> list[dict[str, Any]]:
    """Replace clause `slot` of ORIG with each alternative at matched selectivity."""
    orig_col, orig_op, orig_val = ORIG[slot]
    target_n = apply_clauses(shipped, [ORIG[slot]]).height
    rows = []
    for col, desc in alts.items():
        th = matched_threshold(shipped, col, op, target_n)
        if not np.isfinite(th):
            continue
        cls = [c if i != slot else (col, op, th) for i, c in enumerate(ORIG)]
        r = evaluate(shipped, cls, sessions=sessions)
        nn = int(shipped[col].is_not_null().sum())
        rows.append(
            {
                "feature": col,
                "desc": desc,
                "op": op,
                "threshold": round(th, 6),
                "target_n": target_n,
                "admitted_alone": apply_clauses(shipped, [(col, op, th)]).height,
                "non_null_in_shipped": nn,
                "is_original": col == orig_col,
                **r,
            }
        )
    return sorted(rows, key=lambda r: -r["net_r"])


def running_alternatives(shipped: pl.DataFrame, sessions: int) -> list[dict[str, Any]]:
    return _swap_study(shipped, sessions, RUNNING_ALTS, 0, "ge")


def small_alternatives(shipped: pl.DataFrame, sessions: int) -> list[dict[str, Any]]:
    return _swap_study(shipped, sessions, SMALL_ALTS, 2, "le")


# ---------------------------------------------------------------------------------------------
# 3. Is rvol_pole load-bearing or decorative?
# ---------------------------------------------------------------------------------------------
def rvol_settled(shipped: pl.DataFrame, sessions: int) -> dict[str, Any]:
    """Separate the THRESHOLD's effect from the NULL-DROP's effect.

    `rvol_pole >= x` does two things at once: it drops rows below x, and it drops rows where
    rvol_pole is null (no pre-pole bars to build a baseline from). Varying x moves only the first;
    removing the clause moves both. So the two measurements in CLAIM.md are not measuring the same
    quantity, which is why they appear to contradict each other.
    """
    others = [ORIG[0], ORIG[2]]
    out: dict[str, Any] = {"rows_shipped": shipped.height}
    out["rvol_null_in_shipped"] = int(shipped["rvol_pole"].is_null().sum())
    after_others = apply_clauses(shipped, others)
    out["rvol_null_after_other_two"] = int(after_others["rvol_pole"].is_null().sum())
    out["rows_after_other_two"] = after_others.height

    grid = {}
    for x in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 5.0, 10.0, 25.0):
        grid[f"ge_{x}"] = evaluate(shipped, [*others, ("rvol_pole", "ge", x)], sessions=sessions)
    grid["removed"] = evaluate(shipped, others, sessions=sessions)
    grid["removed_notnull_only"] = evaluate(
        shipped,
        [*others, ("rvol_pole", "ge", float(shipped["rvol_pole"].min() or 0.0) - 1)],
        sessions=sessions,
    )
    # the pure null-drop, expressed as a clause that keeps every non-null row
    nn = after_others.filter(pl.col("rvol_pole").is_not_null())
    dropped = after_others.filter(pl.col("rvol_pole").is_null())
    out["dropped_rows_row_net_rpt"] = (
        round(float(dropped["net_r"].mean()), 4) if dropped.height else None
    )
    out["kept_rows_row_net_rpt"] = round(float(nn["net_r"].mean()), 4) if nn.height else None
    out["n_dropped"] = dropped.height
    out["grid"] = grid
    return out


# ---------------------------------------------------------------------------------------------
# 4. Continuous vs threshold
# ---------------------------------------------------------------------------------------------
def gradients(df: pl.DataFrame, shipped: pl.DataFrame, sessions: int) -> dict[str, Any]:
    """Decile response of each feature, at row level and as a book at every top-k% cut.

    A real effect shows as a gradient across the range. A step at exactly one cut point is a
    fitted artefact.
    """
    out: dict[str, Any] = {}
    for col in (
        "runup_pre_appearance",
        "rvol_pole",
        "shares_outstanding",
        "mktcap",
        "price",
        "ext_at_trigger",
    ):
        for popname, pop in (("all", df), ("shipped", shipped)):
            d = pop.filter(pl.col(col).is_not_null())
            if d.height < 40:
                continue
            v = d[col].cast(pl.Float64).to_numpy()
            n = 10 if popname == "all" else 5
            edges = np.unique(np.quantile(v, np.linspace(0, 1, n + 1)))
            bands = []
            for i in range(len(edges) - 1):
                a, b = edges[i], edges[i + 1]
                last = i == len(edges) - 2
                m = (pl.col(col) >= a) & (pl.col(col) <= b if last else pl.col(col) < b)
                g = d.filter(m)
                if g.is_empty():
                    continue
                bands.append(
                    {
                        "lo": round(float(a), 6),
                        "hi": round(float(b), 6),
                        "n": g.height,
                        "row_net_rpt": round(float(g["net_r"].mean()), 4),
                        "row_gross_rpt": round(float(g["r"].mean()), 4),
                        "rate50": round(float((g["max_gain_pct"] >= 0.5).mean()), 4),
                        "mean_max_r": round(float(g["max_r"].mean()), 4),
                    }
                )
            out[f"{col}|{popname}"] = bands
            # Spearman rank correlation of the feature against the row outcome
            r = d["net_r"].to_numpy()
            out[f"{col}|{popname}|spearman_net"] = round(_spearman(v, r), 4)
            out[f"{col}|{popname}|spearman_rate50"] = round(
                _spearman(v, (d["max_gain_pct"] >= 0.5).cast(pl.Float64).to_numpy()), 4
            )
    # book response as the cut is swept, one feature at a time, others held at ORIG
    sweep: dict[str, Any] = {}
    for slot, (col, op, _th) in enumerate(ORIG):  # noqa: B007
        d = shipped.filter(pl.col(col).is_not_null())
        v = np.sort(d[col].cast(pl.Float64).to_numpy())
        qs = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        rows = []
        for q in qs:
            th = float(np.quantile(v, 1 - q)) if op == "ge" else float(np.quantile(v, q))
            cls = [c if i != slot else (col, op, th) for i, c in enumerate(ORIG)]
            rows.append(
                {
                    "keep_frac": q,
                    "threshold": round(th, 6),
                    **evaluate(shipped, cls, sessions=sessions),
                }
            )
        sweep[col] = rows
    out["book_sweep"] = sweep
    return out


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3:
        return 0.0
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    sa, sb = ra.std(), rb.std()
    return float(((ra - ra.mean()) * (rb - rb.mean())).mean() / (sa * sb)) if sa and sb else 0.0


# ---------------------------------------------------------------------------------------------
# 5. Combination logic
# ---------------------------------------------------------------------------------------------
def combination_logic(shipped: pl.DataFrame, sessions: int) -> dict[str, Any]:
    names = ["runup", "rvol", "shares"]
    out: dict[str, Any] = {}
    out["none"] = evaluate(shipped, [], sessions=sessions)
    for i, nm in enumerate(names):
        out[f"single:{nm}"] = evaluate(shipped, [ORIG[i]], sessions=sessions)
    for i in range(3):
        for j in range(i + 1, 3):
            out[f"pair:{names[i]}+{names[j]}"] = evaluate(
                shipped, [ORIG[i], ORIG[j]], sessions=sessions
            )
    out["and3"] = evaluate(shipped, ORIG, sessions=sessions)

    # 2-of-3 and an additive score: build a count column, then threshold it
    d = shipped.with_columns(
        (
            (pl.col("runup_pre_appearance") >= RUNUP).fill_null(False).cast(pl.Int32)
            + (pl.col("rvol_pole") >= RVOL).fill_null(False).cast(pl.Int32)
            + (pl.col("shares_outstanding") <= SHARES).fill_null(False).cast(pl.Int32)
        ).alias("n_pass")
    )
    for k in (1, 2, 3):
        out[f"atleast{k}of3"] = evaluate(d, [("n_pass", "ge", float(k))], sessions=sessions)

    # additive z-score of the three features (rank-normalised, direction-corrected)
    z = _rank_score(
        shipped,
        [("runup_pre_appearance", 1), ("rvol_pole", 1), ("shares_outstanding", -1)],
    )
    d2 = shipped.with_columns(pl.Series("zscore", z))
    target = out["and3"]["rows_selected"]
    th = matched_threshold(d2, "zscore", "ge", target)
    out["additive_rankscore_matched"] = {
        "threshold": round(th, 4),
        **evaluate(d2, [("zscore", "ge", th)], sessions=sessions),
    }
    for q in (0.05, 0.1, 0.2, 0.3, 0.5):
        thq = float(np.quantile(z, 1 - q))
        out[f"additive_rankscore_top{int(q * 100)}pct"] = evaluate(
            d2, [("zscore", "ge", thq)], sessions=sessions
        )
    # each single feature at MATCHED selectivity to the AND-of-3, so "how much does one do" is fair
    for i, nm in enumerate(names):
        col, op, _ = ORIG[i]
        th2 = matched_threshold(shipped, col, op, target)
        out[f"single_matched:{nm}"] = {
            "threshold": round(th2, 6),
            **evaluate(shipped, [(col, op, th2)], sessions=sessions),
        }
    return out


def _rank_score(df: pl.DataFrame, spec: list[tuple[str, int]]) -> np.ndarray:
    tot = np.zeros(df.height)
    for col, sign in spec:
        v = df[col].cast(pl.Float64).to_numpy().astype(float)
        ok = ~np.isnan(v)
        r = np.full(len(v), 0.5)
        if ok.sum() > 1:
            rr = np.argsort(np.argsort(v[ok])).astype(float) / (ok.sum() - 1)
            r[ok] = rr
        tot += sign * r
    return tot


# ---------------------------------------------------------------------------------------------
# 6. Outcome definitions
# ---------------------------------------------------------------------------------------------
def outcome_definitions(df: pl.DataFrame, sessions: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tgt in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        d = S.attach_net_r(
            C.fixed_target_r(df.drop(["r", "net_r", "qty", "sized_by", "cost_r"]), tgt)
        )
        sh = C.SHIPPED(d)
        out[f"target_{tgt}"] = {
            "in_play": evaluate(sh, ORIG, sessions=sessions),
            "shipped_only": evaluate(sh, [], sessions=sessions),
        }
    # target-free outcome statistics on the SELECTED ROWS (not the book) -- bigger samples
    sh2 = C.SHIPPED(df)
    for label, pop in (("shipped", sh2), ("shipped+in_play", apply_clauses(sh2, ORIG))):
        stats = {}
        for split in ("dev", "val", "holdout", "all"):
            p = pop if split == "all" else pop.filter(pl.col("split") == split)
            if p.is_empty():
                continue
            stats[split] = {
                "n": p.height,
                "rate50": round(float((p["max_gain_pct"] >= 0.5).mean()), 4),
                "rate_2r": round(float((p["max_r"] >= 2.0).mean()), 4),
                "mean_max_r": round(float(p["max_r"].mean()), 4),
                "median_max_r": round(float(p["max_r"].median()), 4),
                "stop_rate": round(float(p["stopped_out"].mean()), 4),
            }
        out[f"rowstats:{label}"] = stats
    # the same on the full population (no shipped gates) -- the largest sample available
    for label, pop in (("all", df), ("all+in_play", apply_clauses(df, ORIG))):
        stats = {}
        for split in ("dev", "val", "holdout", "all"):
            p = pop if split == "all" else pop.filter(pl.col("split") == split)
            if p.is_empty():
                continue
            stats[split] = {
                "n": p.height,
                "rate50": round(float((p["max_gain_pct"] >= 0.5).mean()), 4),
                "rate_2r": round(float((p["max_r"] >= 2.0).mean()), 4),
                "mean_max_r": round(float(p["max_r"].mean()), 4),
                "median_max_r": round(float(p["max_r"].median()), 4),
                "row_net_rpt": round(float(p["net_r"].mean()), 4),
            }
        out[f"rowstats:{label}"] = stats
    return out


# ---------------------------------------------------------------------------------------------
# 7. Population definitions
# ---------------------------------------------------------------------------------------------
def population_definitions() -> dict[str, Any]:
    out: dict[str, Any] = {}
    variants = {
        "default(premkt<570, cons_range)": {"premarket_cut": 570.0, "require_cons_range": True},
        "premkt<555": {"premarket_cut": 555.0, "require_cons_range": True},
        "premkt<540": {"premarket_cut": 540.0, "require_cons_range": True},
        "premkt<600": {"premarket_cut": 600.0, "require_cons_range": True},
        "no_cons_range_filter": {"premarket_cut": 570.0, "require_cons_range": False},
    }
    for label, kw in variants.items():
        d = features.attach(S.panel(2.0, **kw))  # type: ignore[arg-type]
        sess = d["dt"].n_unique()
        sh = C.SHIPPED(d)
        out[label] = {
            "rows": d.height,
            "sessions": sess,
            "shipped_only": evaluate(sh, [], sessions=sess),
            "shipped+in_play": evaluate(sh, ORIG, sessions=sess),
        }
    d = features.attach(S.panel(2.0))
    sess = d["dt"].n_unique()
    # `passed` on / off, and the rest of SHIPPED on / off
    no_passed = d.filter(
        (pl.col("cycle_num") <= 2)
        & (pl.col("staleness_delay_min") <= 30)
        & pl.col("entry_fill").is_between(3.0, 50.0)
        & (pl.col("stop_pct") >= 0.025)
        & pl.col("trigger_et_min").is_between(240.0, 555.0)
    )
    out["passed_OFF (rest of SHIPPED on)"] = {
        "rows": no_passed.height,
        "base": evaluate(no_passed, [], sessions=sess),
        "in_play": evaluate(no_passed, ORIG, sessions=sess),
    }
    out["passed_ONLY"] = {
        "base": evaluate(d.filter(pl.col("passed")), [], sessions=sess),
        "in_play": evaluate(d.filter(pl.col("passed")), ORIG, sessions=sess),
    }
    out["no_gates_at_all"] = {
        "base": evaluate(d, [], sessions=sess),
        "in_play": evaluate(d, ORIG, sessions=sess),
    }
    sh = C.SHIPPED(d)
    for cap in (1, 2, 3, 5):
        out[f"cap_{cap}_per_day"] = {
            "shipped_only": evaluate(sh, [], sessions=sess, max_per_day=cap),
            "shipped+in_play": evaluate(sh, ORIG, sessions=sess, max_per_day=cap),
        }
    return out


# ---------------------------------------------------------------------------------------------
def main() -> None:
    df, shipped, sessions = base()
    print(f"population {df.height} rows / {sessions} sessions; SHIPPED {shipped.height} rows")
    res: dict[str, Any] = {
        "population": {"rows": df.height, "sessions": sessions, "shipped_rows": shipped.height}
    }

    print("\n--- 1. alternative 'already running' ---")
    res["running_alternatives"] = running_alternatives(shipped, sessions)
    _table(res["running_alternatives"])

    print("\n--- 2. alternative 'small' ---")
    res["small_alternatives"] = small_alternatives(shipped, sessions)
    _table(res["small_alternatives"])

    print("\n--- 3. rvol settled ---")
    res["rvol"] = rvol_settled(shipped, sessions)
    print(json.dumps({k: v for k, v in res["rvol"].items() if k != "grid"}, indent=2))
    for k, v in res["rvol"]["grid"].items():
        print(
            f"  {k:<24} {v['trades']:>3} tr  net {v['net_r']:+7.1f}R "
            f"({v['net_r_per_trade']:+.3f})  rows {v['rows_selected']}"
        )

    print("\n--- 4. gradients ---")
    res["gradients"] = gradients(df, shipped, sessions)
    for k, v in res["gradients"].items():
        if k.endswith("|all") or k.endswith("|shipped"):
            print(f"  {k}:")
            for b in v:
                print(
                    f"     [{b['lo']:>12.4g},{b['hi']:>12.4g}] n={b['n']:>5} "
                    f"net={b['row_net_rpt']:+.3f} rate50={b['rate50'] * 100:5.1f}% "
                    f"maxR={b['mean_max_r']:+.2f}"
                )
        elif "spearman" in k:
            print(f"  {k} = {v}")
    print("  book sweep:")
    for col, rows in res["gradients"]["book_sweep"].items():
        print(f"   {col}")
        for r in rows:
            print(
                f"     keep {r['keep_frac']:.0%} th={r['threshold']:>12.4g} "
                f"{r['trades']:>3} tr net {r['net_r']:+7.1f}R ({r['net_r_per_trade']:+.3f})"
            )

    print("\n--- 5. combination logic ---")
    res["combination"] = combination_logic(shipped, sessions)
    for k, v in res["combination"].items():
        print(
            f"  {k:<34} {v['trades']:>3} tr net {v['net_r']:+7.1f}R "
            f"({v['net_r_per_trade']:+.3f}) rows={v['rows_selected']}"
        )

    print("\n--- 6. outcome definitions ---")
    res["outcomes"] = outcome_definitions(df, sessions)
    for k, v in res["outcomes"].items():
        if k.startswith("target_"):
            a, b = v["in_play"], v["shipped_only"]
            print(
                f"  {k:<12} in-play {a['trades']:>3} tr net {a['net_r']:+7.1f}R "
                f"({a['net_r_per_trade']:+.3f})   shipped {b['trades']:>4} tr "
                f"net {b['net_r']:+7.1f}R ({b['net_r_per_trade']:+.3f})"
            )
        else:
            print(f"  {k}: {json.dumps(v)}")

    print("\n--- 7. population definitions ---")
    res["populations"] = population_definitions()
    for k, v in res["populations"].items():
        terse = {
            kk: (
                vv
                if not isinstance(vv, dict)
                else {"trades": vv.get("trades"), "net_r": vv.get("net_r")}
            )
            for kk, vv in v.items()
        }
        print(f"  {k}: {json.dumps(terse)}")

    (S.OUT / "sweeps.json").write_text(json.dumps(res, indent=2, default=str))
    print(f"\nwrote {S.OUT / 'sweeps.json'}")


def _table(rows: list[dict[str, Any]]) -> None:
    print(
        f"  {'feature':<28}{'th':>12}{'rows':>6}{'tr':>5}{'netR':>9}{'/tr':>8}"
        f"{'dev':>7}{'val':>7}{'hold':>7}"
    )
    for r in rows:
        mark = " *" if r["is_original"] else "  "
        print(
            f"{mark}{r['feature']:<28}{r['threshold']:>12.4g}{r['rows_selected']:>6}"
            f"{r['trades']:>5}{r['net_r']:>9.1f}{r['net_r_per_trade']:>8.3f}"
            f"{r['dev_net_r']:>7.1f}{r['val_net_r']:>7.1f}{r['holdout_net_r']:>7.1f}"
        )


if __name__ == "__main__":
    main()
