"""Validator C — mechanism and execution audit of the in-play claim.

Two questions, neither of them statistical:

1. **Does the rule work for the reason claimed?** The story is "small companies that are already
   moving are the ones that run, and the flag is the entry timing". A rule can be real and work for
   a different reason, in which case it breaks the moment conditions shift.
2. **Would it survive being traded?** Modelling conventions (the same-bar stop, the conservative
   fill), the $500 account's sizing cap, and — most important — whether the share count that gates
   the rule was actually knowable on the trading day.

Everything here is computed from the raw panel. Nothing in CLAIM.md is taken on trust.

    .venv/bin/python spikes/engine_lab/validate/mechanism/mechanism.py

Writes data/spikes/engine-lab/validate/mechanism/result.json.
"""

from __future__ import annotations

import glob
import json
from collections import Counter
from datetime import date
from typing import Any

import numpy as np
import polars as pl
import spikes.engine_lab.common as C

OUT = C.REPO / "data/spikes/engine-lab/validate/mechanism"

# The claim's three in-play thresholds, as separable pieces.
RUNUP_MIN = 0.15
RVOL_MIN = 2.0
SHARES_MAX = 50e6

IN_PLAY_PARTS: dict[str, pl.Expr] = {
    "runup": pl.col("runup_pre_appearance") >= RUNUP_MIN,
    "rvol": pl.col("rvol_pole") >= RVOL_MIN,
    "small": pl.col("shares_outstanding") <= SHARES_MAX,
}
IN_PLAY = IN_PLAY_PARTS["runup"] & IN_PLAY_PARTS["rvol"] & IN_PLAY_PARTS["small"]

#: The shipped rule set minus `passed` — i.e. the selection tier without the shape gate.
SHIPPED_NO_SHAPE = (
    (pl.col("cycle_num") <= 2)
    & (pl.col("staleness_delay_min") <= 30)
    & pl.col("entry_fill").is_between(3.0, 50.0)
    & (pl.col("stop_pct") >= 0.025)
    & pl.col("trigger_et_min").is_between(240.0, 555.0)
)

#: The five shape gates that survive §D-44, as they appear in `failing_gates`.
GATES = ("cons_retracement", "peak_green", "vol_peak_gt_cons", "cons_len", "pole_height")

TARGET_R = 2.0


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
def book_of(sel: pl.DataFrame, *, max_per_day: int = 2) -> pl.DataFrame:
    return C.build_book(C.fixed_target_r(sel, TARGET_R), max_per_day=max_per_day)


def scored(sel: pl.DataFrame, sessions: int, **kw: Any) -> dict[str, Any]:
    return C.score(book_of(sel), sessions=sessions, **kw)


def slim(res: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "trades",
        "sessions_traded",
        "gross_r",
        "net_r",
        "r_per_trade",
        "net_r_per_trade",
        "win_rate",
        "net_usd",
        "cap_bound",
        "max_dd_net_r",
        "trades_per_session",
        "unaffordable",
        "cost_r_per_trade",
        "mean_qty",
    )
    out = {k: res[k] for k in keep if k in res}
    if isinstance(res.get("split"), dict):
        out["split"] = {
            k: {kk: v[kk] for kk in ("trades", "net_r", "net_r_per_trade", "win_rate") if kk in v}
            for k, v in res["split"].items()
        }
    if isinstance(res.get("source"), dict):
        out["source"] = {
            k: {kk: v[kk] for kk in ("trades", "net_r", "net_r_per_trade", "win_rate") if kk in v}
            for k, v in res["source"].items()
        }
    return out


def _p(label: str, res: dict[str, Any]) -> None:
    print(f"  {label:<34} " + C.brief(res))


# ---------------------------------------------------------------------------------------------
# T0 — re-derive the claim
# ---------------------------------------------------------------------------------------------
def t0_rederive(df: pl.DataFrame) -> dict[str, Any]:
    C.assert_no_lookahead(
        [
            "runup_pre_appearance",
            "rvol_pole",
            "shares_outstanding",
            "passed",
            "cycle_num",
            "staleness_delay_min",
            "entry_fill",
            "stop_pct",
            "trigger_et_min",
        ]
    )
    n = df["dt"].n_unique()
    variants = {
        "shipped_only": C.SHIPPED(df),
        "in_play_alone_no_other_rule": df.filter(IN_PLAY),
        "in_play_plus_shipped_minus_passed": df.filter(IN_PLAY & SHIPPED_NO_SHAPE),
        "shipped_plus_in_play": C.SHIPPED(df).filter(IN_PLAY),
    }
    out: dict[str, Any] = {"claimed": {}, "derived": {}}
    print("== T0 re-derivation of CLAIM.md")
    for k, sel in variants.items():
        res = scored(sel, n, by=("split", "source"))
        out["derived"][k] = slim(res)
        _p(k, res)
    out["claimed"] = {
        "shipped_only": {"trades": 122, "net_r": 7.1, "net_r_per_trade": 0.058},
        "in_play_only_no_shape_gates": {
            "trades": 242,
            "net_r": -20.3,
            "net_r_per_trade": -0.084,
        },
        "shipped_plus_in_play": {"trades": 35, "net_r": 16.7, "net_r_per_trade": 0.478},
    }
    d = out["derived"]
    out["agreement"] = {
        "shipped_only_matches": d["shipped_only"]["trades"] == 122,
        "book_matches": d["shipped_plus_in_play"]["trades"] == 35,
        "middle_row_label_wrong": (
            d["in_play_plus_shipped_minus_passed"]["trades"] == 242
            and d["in_play_alone_no_other_rule"]["trades"] != 242
        ),
        "in_play_alone_really_is": d["in_play_alone_no_other_rule"],
    }
    return out


# ---------------------------------------------------------------------------------------------
# T1 — where does the money come from?
# ---------------------------------------------------------------------------------------------
def t1_attribution(df: pl.DataFrame) -> dict[str, Any]:
    n = df["dt"].n_unique()
    ship = scored(C.SHIPPED(df), n)
    play = scored(C.SHIPPED(df).filter(IN_PLAY), n)
    st, pt = ship["_trades"], play["_trades"]

    def parts(t: pl.DataFrame) -> dict[str, Any]:
        r = t["r"].to_numpy()
        return {
            "trades": int(len(r)),
            "win_rate": round(float((r > 0).mean()), 4),
            "mean_win_r": round(float(r[r > 0].mean()), 4) if (r > 0).any() else 0.0,
            "mean_loss_r": round(float(r[r <= 0].mean()), 4) if (r <= 0).any() else 0.0,
            "gross_r_per_trade": round(float(r.mean()), 4),
            # every trade is exactly +2R or -1R, so gross/trade == 3*p - 1 by construction
            "implied_by_win_rate": round(3 * float((r > 0).mean()) - 1, 4),
            "net_r_per_trade": round(float(t["net_r"].mean()), 4),
            "cost_drag_r_per_trade": round(
                float(t["r"].mean() - t["net_r"].mean()),
                4,
            ),
            "mean_max_r": round(float(t["max_r"].mean()), 3),
            "median_max_r": round(float(t["max_r"].median()), 3),
            "pct_max_r_ge_2": round(float((t["max_r"] >= 2).mean()), 4),
            "pct_max_r_ge_5": round(float((t["max_r"] >= 5).mean()), 4),
            "pct_stopped_out": round(float(t["stopped_out"].mean()), 4),
            "pct_same_bar_stop": round(float(t["same_bar_stop"].mean()), 4),
        }

    res = {"shipped": parts(st), "shipped_in_play": parts(pt)}
    # capacity effect: how many *days* each book trades, and R per session
    res["per_session"] = {
        "shipped_sessions_traded": int(st["dt"].n_unique()),
        "in_play_sessions_traded": int(pt["dt"].n_unique()),
        "shipped_net_r_per_session": round(float(st["net_r"].sum()) / n, 4),
        "in_play_net_r_per_session": round(float(pt["net_r"].sum()) / n, 4),
    }
    print("\n== T1 attribution")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return res


# ---------------------------------------------------------------------------------------------
# T2 — does the filter change WHICH trades, or just HOW MANY?
# ---------------------------------------------------------------------------------------------
def t2_substitution(df: pl.DataFrame) -> dict[str, Any]:
    n = df["dt"].n_unique()
    ship = scored(C.SHIPPED(df), n)["_trades"]
    play = scored(C.SHIPPED(df).filter(IN_PLAY), n)["_trades"]
    sk, pk = set(ship["key"]), set(play["key"])

    retained = ship.filter(pl.col("key").is_in(list(pk)))
    dropped = ship.filter(~pl.col("key").is_in(list(pk)))
    promoted = play.filter(~pl.col("key").is_in(list(sk)))

    def blk(t: pl.DataFrame, label: str) -> dict[str, Any]:
        if t.is_empty():
            return {"label": label, "trades": 0, "net_r": 0.0}
        return {
            "label": label,
            "trades": t.height,
            "gross_r": round(float(t["r"].sum()), 2),
            "net_r": round(float(t["net_r"].sum()), 2),
            "net_r_per_trade": round(float(t["net_r"].mean()), 4),
            "win_rate": round(float((t["r"] > 0).mean()), 4),
        }

    res = {
        "retained": blk(retained, "in both books"),
        "dropped": blk(dropped, "shipped took, in-play filtered out"),
        "promoted": blk(promoted, "in-play took, shipped never saw (freed slot)"),
    }
    res["identity_check"] = {
        "shipped_net_r": round(float(ship["net_r"].sum()), 2),
        "in_play_net_r": round(float(play["net_r"].sum()), 2),
        "delta": round(float(play["net_r"].sum() - ship["net_r"].sum()), 2),
        "promoted_minus_dropped": round(
            float(promoted["net_r"].sum() if promoted.height else 0.0)
            - float(dropped["net_r"].sum() if dropped.height else 0.0),
            2,
        ),
    }
    # how often does a day's in-play trade sit *later* in the day than the shipped book's cut?
    later = 0
    for row in play.iter_rows(named=True):
        same_day = ship.filter(pl.col("dt") == row["dt"])
        if same_day.is_empty() or row["trigger_et_min"] > float(same_day["trigger_et_min"].max()):
            later += 1
    res["in_play_trades_later_than_shipped_cut"] = later
    res["in_play_trades"] = play.height
    # substitution is only possible when the cap actually binds. Does it?
    per_day = C.SHIPPED(df).group_by("dt").agg(pl.len().alias("k"))
    res["cap_binding"] = {
        "shipped_rows_available": int(C.SHIPPED(df).height),
        "shipped_rows_taken": ship.height,
        "sessions_with_more_than_2_shipped_rows": int((per_day["k"] > 2).sum()),
        "sessions_with_any_shipped_row": per_day.height,
        "rows_lost_to_the_cap": int(C.SHIPPED(df).height - ship.height),
    }
    print("\n== T2 substitution")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return res


# ---------------------------------------------------------------------------------------------
# T3 — concentration
# ---------------------------------------------------------------------------------------------
def t3_concentration(df: pl.DataFrame) -> dict[str, Any]:
    n = df["dt"].n_unique()
    t = scored(C.SHIPPED(df).filter(IN_PLAY), n)["_trades"]
    t = t.with_columns(
        pl.col("dt").dt.strftime("%Y-%m").alias("month"),
        pl.col("dt").dt.truncate("1w").alias("week"),
    )
    total = float(t["net_r"].sum())
    res: dict[str, Any] = {
        "trades": t.height,
        "distinct_symbols": int(t["symbol"].n_unique()),
        "distinct_sessions": int(t["dt"].n_unique()),
        "distinct_weeks": int(t["week"].n_unique()),
        "distinct_months": int(t["month"].n_unique()),
        "total_net_r": round(total, 2),
    }
    for dim in ("symbol", "dt", "week", "month"):
        agg = (
            t.group_by(dim)
            .agg(pl.len().alias("n"), pl.col("net_r").sum().alias("net_r"))
            .sort("net_r", descending=True)
        )
        top = agg.head(5).to_dicts()
        res[f"top5_by_{dim}"] = [
            {
                str(dim): str(r[dim]),
                "n": r["n"],
                "net_r": round(r["net_r"], 2),
                "share_of_total": round(r["net_r"] / total, 3) if total else None,
            }
            for r in top
        ]
        res[f"net_r_excl_top1_{dim}"] = round(total - float(agg["net_r"][0]), 2)
        res[f"net_r_excl_top3_{dim}"] = round(total - float(agg["net_r"].head(3).sum()), 2)
    # how many winners carry it
    wins = t.filter(pl.col("r") > 0)
    res["winners"] = wins.height
    res["losers"] = t.height - wins.height
    res["net_r_from_winners"] = round(float(wins["net_r"].sum()), 2)
    print("\n== T3 concentration")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return res


# ---------------------------------------------------------------------------------------------
# T4 — interaction with the five shape gates
# ---------------------------------------------------------------------------------------------
def _fail_set(s: str | None) -> set[str]:
    if not s:
        return set()
    return {x.strip() for x in str(s).split(",") if x.strip()}


def t4_shape_gates(df: pl.DataFrame) -> dict[str, Any]:
    n = df["dt"].n_unique()
    pool = df.filter(IN_PLAY & SHIPPED_NO_SHAPE)  # the in-play subpopulation, shape gate off
    fails = [_fail_set(s) for s in pool["failing_gates"].to_list()]
    pool = pool.with_columns(pl.Series("_fails", [sorted(f) for f in fails]))

    res: dict[str, Any] = {}
    res["pool_rows"] = pool.height
    res["pool_all_gates_on"] = scored(pool.filter(pl.col("passed")), n)
    res["pool_all_gates_off"] = scored(pool, n)
    res = {k: (slim(v) if isinstance(v, dict) else v) for k, v in res.items()}

    print("\n== T4 shape gates on the in-play subpopulation")
    print(f"  pool rows (in-play + shipped-minus-passed): {pool.height}")

    # gate prevalence within the pool
    cnt = Counter()
    for f in fails:
        for g in f:
            cnt[g] += 1
    res["gate_failure_counts_in_pool"] = dict(cnt)

    # leave-one-out: drop gate g from the shape requirement
    loo = {}
    for g in GATES:
        keep = pl.Series([f <= {g} for f in fails])
        s = scored(pool.filter(keep), n)
        loo[g] = slim(s)
        _p(f"all gates except {g}", s)
    res["leave_one_out"] = loo

    # single gate only: g is the ONLY gate applied
    solo = {}
    for g in GATES:
        keep = pl.Series([g not in f for f in fails])
        s = scored(pool.filter(keep), n)
        solo[g] = slim(s)
        _p(f"only gate {g}", s)
    res["single_gate_only"] = solo

    # the reverse interaction: which of the three in-play pieces does the work, on top of SHIPPED
    ship = C.SHIPPED(df)
    feat: dict[str, Any] = {}
    for name, expr in IN_PLAY_PARTS.items():
        s = scored(ship.filter(expr), n)
        feat[f"only_{name}"] = slim(s)
        _p(f"SHIPPED + {name} alone", s)
    for a, b in (("runup", "rvol"), ("runup", "small"), ("rvol", "small")):
        s = scored(ship.filter(IN_PLAY_PARTS[a] & IN_PLAY_PARTS[b]), n)
        feat[f"{a}+{b}"] = slim(s)
        _p(f"SHIPPED + {a}+{b}", s)
    res["in_play_feature_ablation"] = feat
    return res


# ---------------------------------------------------------------------------------------------
# T5 — is it a warm-day proxy?
# ---------------------------------------------------------------------------------------------
def t5_warm_days(df: pl.DataFrame) -> dict[str, Any]:
    n = df["dt"].n_unique()
    # day heat = the best max_r available that day. Lookahead for day t, but we only ever read it
    # for day t-1, which is history by the time day t trades.
    heat = (
        df.group_by("dt")
        .agg(pl.col("max_r").max().alias("day_best_max_r"))
        .sort("dt")
        .with_columns(pl.col("day_best_max_r").shift(1).alias("prev_day_best_max_r"))
    )
    hot = heat.with_columns((pl.col("prev_day_best_max_r") >= 10.0).alias("after_hot"))

    ship = scored(C.SHIPPED(df), n)["_trades"].join(hot, on="dt", how="left")
    play = scored(C.SHIPPED(df).filter(IN_PLAY), n)["_trades"].join(hot, on="dt", how="left")

    def by_hot(t: pl.DataFrame, label: str) -> dict[str, Any]:
        out = {}
        for flag in (True, False):
            g = t.filter(pl.col("after_hot") == flag)
            out["after_hot" if flag else "after_cold"] = {
                "trades": g.height,
                "net_r": round(float(g["net_r"].sum()), 2) if g.height else 0.0,
                "net_r_per_trade": round(float(g["net_r"].mean()), 4) if g.height else 0.0,
                "win_rate": round(float((g["r"] > 0).mean()), 4) if g.height else 0.0,
            }
        out["label"] = label
        return out

    res: dict[str, Any] = {
        "sessions_after_hot": int(hot["after_hot"].sum()),
        "sessions_total": hot.height,
        "shipped": by_hot(ship, "shipped"),
        "in_play": by_hot(play, "shipped+in_play"),
    }
    # does the in-play book *concentrate* on hot-follow days?
    res["in_play_share_of_trades_after_hot"] = round(
        float(play["after_hot"].fill_null(False).mean()), 4
    )
    res["shipped_share_of_trades_after_hot"] = round(
        float(ship["after_hot"].fill_null(False).mean()), 4
    )

    # THE paired test: on days the in-play book trades, how did the shipped book do?
    play_days = play["dt"].unique().to_list()
    ship_same_days = ship.filter(pl.col("dt").is_in(play_days))
    res["paired_same_day"] = {
        "days": len(play_days),
        "in_play_trades": play.height,
        "in_play_net_r_per_trade": round(float(play["net_r"].mean()), 4),
        "shipped_trades_on_those_days": ship_same_days.height,
        "shipped_net_r_per_trade_on_those_days": round(float(ship_same_days["net_r"].mean()), 4)
        if ship_same_days.height
        else 0.0,
        "shipped_net_r_per_trade_other_days": round(
            float(ship.filter(~pl.col("dt").is_in(play_days))["net_r"].mean()), 4
        ),
    }
    # and: the whole pool's outcome on in-play days vs other days (is the day just good?)
    pool = C.fixed_target_r(df, TARGET_R)
    res["pool_r_per_trade_on_in_play_days"] = round(
        float(pool.filter(pl.col("dt").is_in(play_days))["r"].mean()), 4
    )
    res["pool_r_per_trade_other_days"] = round(
        float(pool.filter(~pl.col("dt").is_in(play_days))["r"].mean()), 4
    )
    print("\n== T5 warm/cold days")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return res


# ---------------------------------------------------------------------------------------------
# T6 — execution exposure
# ---------------------------------------------------------------------------------------------
def _replay_optimistic(
    path: np.ndarray, entry_fill: float, stop: float, target_r: float
) -> dict[str, Any]:
    """Same as `replay_bracket` but the TARGET is checked before the stop on the entry bar.

    The shipped convention books an entry bar containing both as a loss. #583 re-resolved those at
    1-min granularity and found the conservative reading wrong 38% of the time. This is the other
    extreme, not a measurement — the truth is between the two.
    """
    o, h, lo = path[:, 0], path[:, 1], path[:, 2]
    cl = path[:, 3]
    entry = max(entry_fill, float(o[0]))
    risk = entry - stop
    if risk <= 0:
        return {"r": 0.0, "valid": False}
    tgt = entry + target_r * risk
    for k in range(len(path)):
        hit_t = h[k] >= tgt
        hit_s = lo[k] <= stop
        if k == 0 and hit_t and hit_s:
            return {"r": target_r, "valid": True}
        if hit_s:
            return {"r": -1.0, "valid": True}
        if hit_t:
            return {"r": target_r, "valid": True}
    return {"r": (float(cl[-1]) - entry) / risk, "valid": True}


def t6_execution(df: pl.DataFrame) -> dict[str, Any]:
    n = df["dt"].n_unique()
    play = scored(C.SHIPPED(df).filter(IN_PLAY), n)
    t = play["_trades"]
    paths = C.load_paths(df)

    res: dict[str, Any] = {
        "trades": t.height,
        "net_r_baseline": round(float(t["net_r"].sum()), 2),
        "same_bar_stop_trades": int(t["same_bar_stop"].sum()),
        "fill_above_entry_bar_high_trades": int(t["fill_above_entry_bar_high"].sum()),
    }
    # (a) how much R sits in trades flagged same_bar_stop / fill-above-high
    for flag in ("same_bar_stop", "fill_above_entry_bar_high"):
        g = t.filter(pl.col(flag))
        res[f"net_r_in_{flag}_trades"] = round(float(g["net_r"].sum()), 2) if g.height else 0.0
        res[f"net_r_excl_{flag}_trades"] = round(float(t.filter(~pl.col(flag))["net_r"].sum()), 2)

    # (b) re-book the whole in-play book under the optimistic same-bar convention
    rows = []
    missing = 0
    for row in C.SHIPPED(df).filter(IN_PLAY).iter_rows(named=True):
        arr = paths.get(row["key"])
        if arr is None:
            missing += 1
            continue
        rows.append(
            {
                **row,
                "r_opt": _replay_optimistic(arr, row["entry_fill"], row["stop"], TARGET_R)["r"],
            }
        )
    alt = pl.DataFrame(rows, infer_schema_length=None)
    res["paths_missing"] = missing
    if not alt.is_empty():
        s = C.score(C.build_book(alt, max_per_day=2), r_col="r_opt", sessions=n)
        res["optimistic_same_bar"] = slim(s)
        _p("optimistic same-bar convention", s)

    # (b2) upper bound on the same-bar exposure: if the 5-min stop reading were wrong and the
    #      trade had survived the entry bar, would it ever have reached +2R?
    sb = t.filter(pl.col("same_bar_stop"))
    would_have = 0
    for row in sb.iter_rows(named=True):
        arr = paths.get(row["key"])
        if arr is None:
            continue
        entry = max(float(row["entry_fill"]), float(arr[0, 0]))
        risk = entry - float(row["stop"])
        if risk > 0 and float(arr[:, 1].max() - entry) / risk >= TARGET_R:
            would_have += 1
    res["same_bar_stops_that_would_reach_2r_if_not_stopped"] = would_have
    res["same_bar_stop_max_upside_r"] = round(would_have * 3.0, 2)

    # (c) fill assumption: use the trigger price rather than the +3-tick conservative fill.
    #     Risk shrinks, so R is re-derived from the bars, not from the panel's max_r.
    rows2 = []
    for row in C.SHIPPED(df).filter(IN_PLAY).iter_rows(named=True):
        arr = paths.get(row["key"])
        if arr is None:
            continue
        rr = C.replay_bracket(arr, row["entry_trigger"], row["stop"], target_r=TARGET_R)
        rows2.append({**row, "r_trig": rr["r"], "entry_fill": row["entry_trigger"]})
    trig = pl.DataFrame(rows2, infer_schema_length=None)
    if not trig.is_empty():
        s = C.score(C.build_book(trig, max_per_day=2), r_col="r_trig", sessions=n)
        res["fill_at_trigger_not_plus3ticks"] = slim(s)
        _p("fill at trigger price", s)

    # (d) slippage sensitivity
    for ticks in (0.0, 2.0, 4.0, 8.0):
        s = C.score(
            book_of(C.SHIPPED(df).filter(IN_PLAY)),
            sessions=n,
            costs=C.Costs(stop_slip_ticks=ticks),
        )
        res[f"slip_{int(ticks)}_ticks_net_r"] = s["net_r"]
        res[f"slip_{int(ticks)}_ticks_net_r_per_trade"] = s["net_r_per_trade"]
    print("\n== T6 execution exposure")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return res


# ---------------------------------------------------------------------------------------------
# T7 — affordability, capacity and dollars
# ---------------------------------------------------------------------------------------------
def t7_dollars(df: pl.DataFrame) -> dict[str, Any]:
    n = df["dt"].n_unique()
    raw = book_of(C.SHIPPED(df).filter(IN_PLAY))
    flat = C.score(raw, sessions=n)
    comp = C.score(raw, sessions=n, sizing=C.Sizing(compound=True))
    t = flat["_trades"]
    ct = comp["_trades"]

    eq = 500.0
    curve = []
    for v in ct["net_usd"].to_list():
        eq += v
        curve.append(eq)
    arr = np.array(curve) if curve else np.array([500.0])
    peak = np.maximum.accumulate(arr)
    dd = arr - peak
    ddpct = dd / peak

    gaps = np.diff(np.sort(np.array([d.toordinal() for d in t["dt"].to_list()])))
    res: dict[str, Any] = {
        "trades_booked": raw.height,
        "trades_affordable": t.height,
        "unaffordable": int(flat["unaffordable"]),
        "cap_bound": int(flat["cap_bound"]),
        "risk_bound": int(t.height - flat["cap_bound"]),
        "mean_qty": round(float(t["qty"].mean()), 1),
        "median_qty": float(t["qty"].median()),
        "min_qty": int(t["qty"].min()),
        "mean_risk_usd": round(float((t["qty"] * (t["entry_fill"] - t["stop"])).mean()), 2),
        "median_risk_usd": round(float((t["qty"] * (t["entry_fill"] - t["stop"])).median()), 2),
        "mean_cost_usd": round(float(t["cost_usd"].mean()), 2),
        "cost_as_pct_of_risk": round(
            float((t["cost_usd"] / (t["qty"] * (t["entry_fill"] - t["stop"]))).mean()), 4
        ),
        "flat_net_usd_total": round(float(t["net_usd"].sum()), 2),
        "flat_net_usd_per_trade": round(float(t["net_usd"].mean()), 2),
        "compound_final_equity": round(float(arr[-1]), 2),
        "compound_return_pct": round(float(arr[-1] / 500.0 - 1) * 100, 1),
        "compound_max_dd_usd": round(float(dd.min()), 2),
        "compound_max_dd_pct": round(float(ddpct.min()) * 100, 1),
        "biggest_single_win_usd": round(float(t["net_usd"].max()), 2),
        "biggest_single_loss_usd": round(float(t["net_usd"].min()), 2),
        "sessions_available": n,
        "sessions_per_trade": round(n / t.height, 2),
        "mean_calendar_days_between_trades": round(float(gaps.mean()), 1) if len(gaps) else None,
        "max_calendar_days_between_trades": int(gaps.max()) if len(gaps) else None,
        "equity_curve_usd": [round(x, 2) for x in curve],
    }
    # months in which the strategy did nothing at all
    months = df.select(pl.col("dt").dt.strftime("%Y-%m").alias("m")).unique()["m"].sort().to_list()
    traded = set(t.select(pl.col("dt").dt.strftime("%Y-%m").alias("m"))["m"].to_list())
    res["months_total"] = len(months)
    res["months_with_no_trade"] = [m for m in months if m not in traded]
    print("\n== T7 dollars")
    for k, v in res.items():
        if k != "equity_curve_usd":
            print(f"  {k}: {v}")
    return res


# ---------------------------------------------------------------------------------------------
# T8 — timeliness of the share count (the lookahead check)
# ---------------------------------------------------------------------------------------------
def t8_timeliness(df: pl.DataFrame) -> dict[str, Any]:
    res: dict[str, Any] = {}
    prov = (
        df.group_by(["source", "shares_source"])
        .agg(
            pl.len().alias("rows"),
            pl.col("shares_outstanding").null_count().alias("null_shares"),
            pl.col("shares_as_of").min().alias("as_of_min"),
            pl.col("shares_as_of").max().alias("as_of_max"),
        )
        .sort(["source", "shares_source"])
    )
    res["provenance"] = [
        {k: (str(v) if isinstance(v, date) else v) for k, v in r.items()} for r in prov.to_dicts()
    ]
    # recon: the panel records the filing COVER date. Is it ever after the session?
    recon = df.filter(pl.col("source") == "recon", pl.col("shares_as_of").is_not_null())
    res["recon"] = {
        "rows_with_as_of": recon.height,
        "as_of_after_session": int((recon["shares_as_of"] > recon["dt"]).sum()),
        "median_age_days": int(
            recon.select((pl.col("dt") - pl.col("shares_as_of")).dt.total_days().median()).item()
        ),
        "p90_age_days": int(
            recon.select(
                (pl.col("dt") - pl.col("shares_as_of")).dt.total_days().quantile(0.9)
            ).item()
        ),
        "max_age_days": int(
            recon.select((pl.col("dt") - pl.col("shares_as_of")).dt.total_days().max()).item()
        ),
        "note": (
            "harvest/edgar.shares_asof() selects on filed<=session and records row.end as as_of; "
            "the cover date is therefore always <= session by construction and the FILED date, "
            "which is the one that matters, is enforced upstream."
        ),
    }
    # live: fundamentals rows carry ts_utc — the moment the count was fetched.
    files = sorted(glob.glob(str(C.REPO / "data/live/fundamentals/dt=*/*.parquet")))
    fund = (
        pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")
        if files
        else pl.DataFrame()
    )
    if not fund.is_empty():
        fund = fund.with_columns(
            pl.col("ts_utc").dt.convert_time_zone("America/New_York").alias("et")
        ).with_columns(
            pl.col("et").dt.date().alias("fetch_dt"),
            (pl.col("et").dt.hour() * 60 + pl.col("et").dt.minute())
            .cast(pl.Float64)
            .alias("fetch_et_min"),
        )
        live = df.filter(pl.col("source") == "live")
        j = live.join(
            fund.filter(pl.col("shares_outstanding").is_not_null())
            .group_by("opportunity_id")
            .agg(
                pl.col("fetch_dt").min().alias("fetch_dt"),
                pl.col("fetch_et_min").min().alias("fetch_et_min"),
            ),
            on="opportunity_id",
            how="left",
        )
        matched = j.filter(pl.col("fetch_dt").is_not_null())
        res["live"] = {
            "live_rows": live.height,
            "matched_to_a_fundamentals_fetch": matched.height,
            "fetched_on_the_session_day": int((matched["fetch_dt"] == matched["dt"]).sum()),
            "fetched_after_the_session_day": int((matched["fetch_dt"] > matched["dt"]).sum()),
            "known_before_trigger": int(
                (
                    (matched["fetch_dt"] < matched["dt"])
                    | (
                        (matched["fetch_dt"] == matched["dt"])
                        & (matched["fetch_et_min"] <= matched["trigger_et_min"])
                    )
                ).sum()
            ),
            "fetched_after_trigger_same_day": int(
                (
                    (matched["fetch_dt"] == matched["dt"])
                    & (matched["fetch_et_min"] > matched["trigger_et_min"])
                ).sum()
            ),
            "median_minutes_fetch_minus_trigger": round(
                float((matched["fetch_et_min"] - matched["trigger_et_min"]).median()), 1
            ),
            "rows_fetched_after_session_detail": matched.filter(pl.col("fetch_dt") > pl.col("dt"))
            .select(["dt", "symbol", "fetch_dt", "shares_outstanding"])
            .to_dicts(),
            "note": (
                "a live count fetched minutes AFTER the trigger is a same-session snapshot, not a "
                "later filing: shares outstanding does not move intraday, so this is a latency "
                "question (would the number have been on hand in time), not a lookahead one."
            ),
        }
    print("\n== T8 timeliness of shares_outstanding")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return res


# ---------------------------------------------------------------------------------------------
# T9 — null coverage
# ---------------------------------------------------------------------------------------------
def t9_coverage(df: pl.DataFrame) -> dict[str, Any]:
    n = df["dt"].n_unique()
    ship = C.SHIPPED(df)
    res: dict[str, Any] = {
        "panel_rows": df.height,
        "panel_shares_null": int(df["shares_outstanding"].null_count()),
        "shipped_rows": ship.height,
        "shipped_shares_null": int(ship["shares_outstanding"].null_count()),
    }
    res["null_by_source"] = (
        df.group_by("source")
        .agg(
            pl.len().alias("rows"),
            pl.col("shares_outstanding").null_count().alias("null"),
        )
        .to_dicts()
    )
    res["null_by_split"] = (
        df.group_by("split")
        .agg(
            pl.len().alias("rows"),
            pl.col("shares_outstanding").null_count().alias("null"),
        )
        .to_dicts()
    )
    # three null policies
    other = IN_PLAY_PARTS["runup"] & IN_PLAY_PARTS["rvol"]
    policies = {
        "null_excluded_as_claimed": ship.filter(IN_PLAY),
        "null_treated_as_pass": ship.filter(
            other & (IN_PLAY_PARTS["small"] | pl.col("shares_outstanding").is_null())
        ),
        "null_rows_only": ship.filter(other & pl.col("shares_outstanding").is_null()),
        "shares_gate_dropped": ship.filter(other),
    }
    for k, sel in policies.items():
        s = scored(sel, n, by=("split", "source"))
        res[k] = slim(s)
        _p(k, s)
    # is the shares gate doing anything at all *within* the non-null population?
    nn = ship.filter(other & pl.col("shares_outstanding").is_not_null())
    s = scored(nn, n)
    res["non_null_no_shares_gate"] = slim(s)
    _p("non-null, shares gate off", s)

    # the plain shipped book split on null-ness alone — how much of the "small is good" result is
    # really "a missing share count is bad"?
    st = scored(ship, n)["_trades"]
    for label, sub in (
        ("shipped_null_shares", st.filter(pl.col("shares_outstanding").is_null())),
        ("shipped_shares_le_50m", st.filter(pl.col("shares_outstanding") <= SHARES_MAX)),
        (
            "shipped_shares_gt_50m",
            st.filter(pl.col("shares_outstanding") > SHARES_MAX),
        ),
    ):
        res[label] = {
            "trades": sub.height,
            "gross_r": round(float(sub["r"].sum()), 2) if sub.height else 0.0,
            "net_r": round(float(sub["net_r"].sum()), 2) if sub.height else 0.0,
            "net_r_per_trade": round(float(sub["net_r"].mean()), 4) if sub.height else 0.0,
            "win_rate": round(float((sub["r"] > 0).mean()), 4) if sub.height else 0.0,
        }
        print(f"  {label}: {res[label]}")
    print("\n== T9 coverage")
    for k, v in res.items():
        if not isinstance(v, dict):
            print(f"  {k}: {v}")
    return res


# ---------------------------------------------------------------------------------------------
# T10 — is the gross edge real, or is the whole gain avoided commission?
# ---------------------------------------------------------------------------------------------
def t10_gross_vs_cost(df: pl.DataFrame) -> dict[str, Any]:
    n = df["dt"].n_unique()
    ship = scored(C.SHIPPED(df), n)
    play = scored(C.SHIPPED(df).filter(IN_PLAY), n)
    small = scored(C.SHIPPED(df).filter(IN_PLAY_PARTS["small"]), n, by=("split", "source"))
    st, pt = ship["_trades"], play["_trades"]
    dropped = st.filter(~pl.col("key").is_in(pt["key"].to_list()))
    res: dict[str, Any] = {
        "shipped_gross_r": round(float(st["r"].sum()), 2),
        "in_play_gross_r": round(float(pt["r"].sum()), 2),
        "gross_r_added_by_in_play_filter": round(float(pt["r"].sum() - st["r"].sum()), 2),
        "dropped_trades": dropped.height,
        "dropped_gross_r": round(float(dropped["r"].sum()), 2),
        "dropped_net_r": round(float(dropped["net_r"].sum()), 2),
        "dropped_cost_usd": round(float(dropped["cost_usd"].sum()), 2),
        "dropped_win_rate": round(float((dropped["r"] > 0).mean()), 4),
        "shipped_net_r": round(float(st["net_r"].sum()), 2),
        "in_play_net_r": round(float(pt["net_r"].sum()), 2),
        "net_r_gain": round(float(pt["net_r"].sum() - st["net_r"].sum()), 2),
        # net gain decomposes exactly: (gross R the filter adds) + (cost R it avoids)
        "net_gain_from_gross_edge": round(float(pt["r"].sum() - st["r"].sum()), 2),
        "net_gain_from_avoided_cost": round(
            float((pt["net_r"].sum() - st["net_r"].sum()) - (pt["r"].sum() - st["r"].sum())),
            2,
        ),
        "small_only_variant": slim(small),
    }
    # threshold sweep on the one feature that survives ablation
    sweep = []
    for cap in (10e6, 20e6, 30e6, 50e6, 75e6, 100e6, 200e6, 500e6):
        s = scored(C.SHIPPED(df).filter(pl.col("shares_outstanding") <= cap), n)
        sweep.append(
            {
                "shares_max_m": cap / 1e6,
                "trades": s["trades"],
                "gross_r": s["gross_r"],
                "net_r": s["net_r"],
                "net_r_per_trade": s["net_r_per_trade"],
                "win_rate": s["win_rate"],
            }
        )
    res["shares_cap_sweep"] = sweep
    # and the runup sweep on top of small-only, to show what "already moving" costs
    rsweep = []
    for mn in (0.0, 0.05, 0.10, 0.15, 0.25, 0.40):
        s = scored(
            C.SHIPPED(df).filter(IN_PLAY_PARTS["small"] & (pl.col("runup_pre_appearance") >= mn)),
            n,
        )
        rsweep.append(
            {
                "runup_min": mn,
                "trades": s["trades"],
                "gross_r": s["gross_r"],
                "net_r": s["net_r"],
                "net_r_per_trade": s["net_r_per_trade"],
            }
        )
    res["runup_sweep_on_small_only"] = rsweep
    print("\n== T10 gross vs cost")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return res


# ---------------------------------------------------------------------------------------------
# T11 — day selection vs stock selection
# ---------------------------------------------------------------------------------------------
def t11_day_vs_stock(df: pl.DataFrame) -> dict[str, Any]:
    n = df["dt"].n_unique()
    ship = scored(C.SHIPPED(df), n)["_trades"]
    play = scored(C.SHIPPED(df).filter(IN_PLAY), n)["_trades"]
    days = play["dt"].unique().to_list()
    on = ship.filter(pl.col("dt").is_in(days))
    off = ship.filter(~pl.col("dt").is_in(days))
    inp = set(play["key"])
    on_other = on.filter(~pl.col("key").is_in(list(inp)))
    base = float(ship["net_r"].mean())
    res = {
        "in_play_days": len(days),
        "shipped_trades_on_in_play_days": on.height,
        "shipped_net_r_per_trade_on_in_play_days": round(float(on["net_r"].mean()), 4),
        "shipped_net_r_per_trade_off_days": round(float(off["net_r"].mean()), 4)
        if off.height
        else 0.0,
        "non_in_play_shipped_trades_on_those_days": on_other.height,
        "non_in_play_net_r_per_trade_on_those_days": round(float(on_other["net_r"].mean()), 4)
        if on_other.height
        else 0.0,
        "in_play_net_r_per_trade": round(float(play["net_r"].mean()), 4),
        "shipped_baseline_net_r_per_trade": round(base, 4),
    }
    lift = res["in_play_net_r_per_trade"] - base
    day_part = res["shipped_net_r_per_trade_on_in_play_days"] - base
    res["total_lift_per_trade"] = round(lift, 4)
    res["explained_by_day_choice"] = round(day_part, 4)
    res["explained_by_stock_choice_within_day"] = round(lift - day_part, 4)
    res["day_share_of_lift"] = round(day_part / lift, 3) if lift else None
    print("\n== T11 day vs stock selection")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return res


# ---------------------------------------------------------------------------------------------
# T12 — the claim's supporting evidence: the intermediate signal and the quintile tables
# ---------------------------------------------------------------------------------------------
def t12_intermediate(df: pl.DataFrame) -> dict[str, Any]:
    """CLAIM.md leans on 'the rate of 50%+ moves roughly doubles under the in-play filter'.

    `max_gain_pct` is an outcome column, so this is a descriptive check of the claim's own
    evidence, never a selection input.
    """
    res: dict[str, Any] = {}
    big = pl.col("max_gain_pct") >= 0.5
    for label, sub in (
        ("all_rows", df),
        ("in_play", df.filter(IN_PLAY)),
        ("small_only", df.filter(IN_PLAY_PARTS["small"])),
        ("runup_only", df.filter(IN_PLAY_PARTS["runup"])),
        ("rvol_only", df.filter(IN_PLAY_PARTS["rvol"])),
    ):
        res[label] = {
            "rows": sub.height,
            "pct_big_move": round(float(sub.select(big.mean()).item()), 4),
            "by_split": {
                str(k[0]): {
                    "rows": g.height,
                    "pct_big_move": round(float(g.select(big.mean()).item()), 4),
                }
                for k, g in sub.group_by(["split"])
            },
        }
    # quintiles on the SHIPPED population — the one the book actually draws from — rather than on
    # the whole 3,639-row pool, which is where the claim's monotone tables were computed.
    ship = C.fixed_target_r(C.SHIPPED(df), TARGET_R)
    quint: dict[str, Any] = {}
    for feat in ("runup_pre_appearance", "rvol_pole", "shares_outstanding"):
        s = ship.filter(pl.col(feat).is_not_null())
        s = s.with_columns(
            ((pl.col(feat).rank("ordinal") - 1) * 5 // pl.len()).alias("q"),
        )
        quint[feat] = (
            s.group_by("q")
            .agg(
                pl.len().alias("n"),
                pl.col(feat).median().alias("median"),
                pl.col("r").mean().round(3).alias("gross_r_per_trade"),
                (pl.col("max_gain_pct") >= 0.5).mean().round(4).alias("pct_big_move"),
            )
            .sort("q")
            .to_dicts()
        )
    res["quintiles_on_shipped_population"] = quint
    print("\n== T12 intermediate signal and quintiles")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return res


# ---------------------------------------------------------------------------------------------
def main() -> None:
    df = C.load_panel()
    print(f"panel: {df.height} rows, {df['dt'].n_unique()} sessions\n")
    out: dict[str, Any] = {
        "panel_rows": df.height,
        "sessions": df["dt"].n_unique(),
        "t0_rederive": t0_rederive(df),
        "t1_attribution": t1_attribution(df),
        "t2_substitution": t2_substitution(df),
        "t3_concentration": t3_concentration(df),
        "t4_shape_gates": t4_shape_gates(df),
        "t5_warm_days": t5_warm_days(df),
        "t6_execution": t6_execution(df),
        "t7_dollars": t7_dollars(df),
        "t8_timeliness": t8_timeliness(df),
        "t9_coverage": t9_coverage(df),
        "t10_gross_vs_cost": t10_gross_vs_cost(df),
        "t11_day_vs_stock": t11_day_vs_stock(df),
        "t12_intermediate": t12_intermediate(df),
    }
    out["verdict"] = {
        "validator": "C (mechanism + execution)",
        "verdict_on_the_claim_as_stated": "ARTEFACT",
        "verdict_on_the_surviving_sub_rule": "PROMISING",
        "surviving_sub_rule": "SHIPPED and shares_outstanding <= 50e6 (drop runup and rvol)",
        "confidence": "high on the mechanism falsification, moderate on the sub-rule",
        "strongest_evidence_for": (
            "the share-count gate is the entire edge of the shipped book and it is not lookahead: "
            "shipped splits into shares<=50M (60 trades, +30R gross, +25.2R net, 50% win), "
            "shares>50M (34 trades, -1R gross) and shares missing (28 trades, -10R gross); "
            "the EDGAR pass selects on filed<=session and 618/619 live counts were fetched on the "
            "session day before the trigger"
        ),
        "strongest_evidence_against": (
            "the three-part in-play rule adds exactly ZERO gross R over the shipped book "
            "(19.0R both). Its entire +9.6R net gain is commission not paid on 87 removed trades "
            "whose gross R was exactly 0.00. It is a cost-avoidance effect, not stock selection, "
            "and the two features the claim's story rests on (runup, rvol) both subtract gross R"
        ),
        "what_would_change_my_mind": [
            "a fresh period in which runup or rvol adds gross R on top of the shares gate",
            "the shares<=50M bucket staying positive on >=60 trades in a period nobody has queried",
            "the removed set being reliably negative in gross R rather than break-even",
        ],
        "share_count_timing_check": "PASSED",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {OUT / 'result.json'}")


if __name__ == "__main__":
    main()
