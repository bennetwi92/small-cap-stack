"""Analysis v1 — W4 (#748): the joint pass. Selection × market state on the R pool, then execution.

Plan: `research/analysis-plan-4-joint.md`. Protocol: `research/analysis-protocol.md` incl. §16.
Ledger: #735. This module imports `stage0` (the one implementation of the splits, Filter A, the
exit families, the per-leg cost model and the §6.2 report) and forks nothing.

    .venv/bin/python spikes/analysis_v1/w4.py vix       # build the VIX cache from CBOE's public CSV
    .venv/bin/python spikes/analysis_v1/w4.py free      # W4-0a … W4-0f   (0 trials)
    .venv/bin/python spikes/analysis_v1/w4.py charged   # S4-0g … S4-0k   (8 trials, ledgered first)

Stage 0 only, in this file's first cut. Stage A/B/C are added after the Stage-0 exit gate and its
interpretation (protocol §13.4: measurement and interpretation never share a session).

⚠️ `max_r` here is always the panel's **stop-truncated** value (plan §3.2). Nothing in this module
computes a raw path maximum, and W4-0b proves the walk is stop-armed on a synthetic path rather
than by measuring the forbidden quantity on real ones.
"""

from __future__ import annotations

import json
import math
import re
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import stage0  # noqa: E402

REPO = stage0.REPO
OUT = stage0.OUT / "w4"
SPEC_SHA = REPO / "research/panel-v1.sha256"
SPEC_MD = REPO / "research/panel-v1-spec.md"
VIX_CSV = REPO / "data/spikes/VIX_History.csv"
VIX_PATH = REPO / "data/spikes/vix_daily_w4.parquet"
VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"

SEED = 20260919
B_NULL = 200
TP_BAND = (0.45, 1.0)  # amendment P-2
ALPHA = 0.01  # amendment P-3
POWER = 0.80
LOOKBACK = 5  # sessions, for the state series
R4_TRADES = 20
FIT_SESSIONS = 266

# ---------------------------------------------------------------------------------------------
# §6.3 — the axes, directions declared a priori. Not searched both ways.
# ---------------------------------------------------------------------------------------------
#: A-S1 … A-S5: (column, op). ">=" keeps the upper tail, "<=" the lower tail.
SEL_AXES: dict[str, tuple[str, str]] = {
    "A-S1": ("hits_before_trigger", ">="),
    "A-S2": ("cum_dollar_vol_pre_trigger", ">="),
    "A-S3": ("retracement", "<="),
    "A-S4": ("stop_pct", ">="),
    "A-S5": ("ext_at_trigger", "<="),
}
#: The selection grid: the share of FIT rows a level keeps (plan §6.1). q80 / q90 / q95.
LEVELS: dict[str, float] = {"q80": 0.20, "q90": 0.10, "q95": 0.05}
#: A-R1 … A-R6: (series, side). "low" = stand aside when the series is in its lower tail;
#: "high" = in its upper tail. A-R5/A-R6 are the paid-for reversals of A-R1/A-R3.
STATE_AXES: dict[str, tuple[str, str]] = {
    "A-R1": ("R1_breadth", "low"),
    "A-R2": ("R2_attention", "low"),
    "A-R3": ("R3_vix", "high"),
    "A-R4": ("R4_stream", "low"),
    "A-R5": ("R1_breadth", "high"),
    "A-R6": ("R3_vix", "low"),
}
FRACTIONS = (0.10, 0.20, 0.30)


# ---------------------------------------------------------------------------------------------
# W4-0a — the panel is verified by bytes, 8 / 8, before anything else
# ---------------------------------------------------------------------------------------------
def verify() -> dict[str, str]:
    want = {}
    for line in SPEC_SHA.read_text().splitlines():
        h, name = line.split()
        want[name] = h
    got = {name: stage0.sha256(stage0.PUBLISH / name) for name in want}
    bad = {n: (got[n], want[n]) for n in want if got[n] != want[n]}
    if bad:
        raise SystemExit(
            f"STOP-AND-REPORT (W4-0a): sha256 mismatch vs research/panel-v1.sha256: {bad}"
        )
    return got


def rule_columns() -> list[str]:
    """The frozen 35 `RULE_COLUMNS`, read from `panel-v1-spec.md` §4 — the published authority.
    (`stage0.rule_columns()` reads a gitignored Stage-0 artefact that a cloud session lacks.)"""
    text = SPEC_MD.read_text()
    m = re.search(
        r"\*\*`RULE_COLUMNS` — frozen, (\d+) columns\.\*\*.*?\n\n(`[^\n]+`)\n", text, re.S
    )
    if not m:
        raise SystemExit("STOP-AND-REPORT: RULE_COLUMNS not found in panel-v1-spec.md")
    cols = re.findall(r"`([a-z_0-9]+)`", m.group(2))
    if len(cols) != int(m.group(1)):
        raise SystemExit(
            f"STOP-AND-REPORT: spec says {m.group(1)} RULE_COLUMNS, parsed {len(cols)}"
        )
    return sorted(cols)


def run_rule(df: pl.DataFrame, predicate: Any) -> pl.DataFrame:
    """§5.1 layer 1: the predicate sees `df.select(RULE_COLUMNS)` and nothing else."""
    return df.filter(predicate(df.select(rule_columns())))


def earliest_one(d: pl.DataFrame) -> pl.DataFrame:
    """Capacity N = 1: the earliest trigger per session (ties: symbol, run). Never ranked."""
    d = d.sort(["dt", "trigger_et_min", "symbol", "run"])
    return d.with_columns(pl.int_range(pl.len()).over("dt").alias("_seq")).filter(
        pl.col("_seq") == 0
    )


def dump(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, default=str))
    print(json.dumps(obj, indent=2, default=str))


def _q(s: pl.Series, q: float) -> float:
    return float(s.drop_nulls().quantile(q, interpolation="linear"))


# ---------------------------------------------------------------------------------------------
# Selection: thresholds are FIT quantiles (§5.3); a level keeps the declared share of FIT rows
# ---------------------------------------------------------------------------------------------
def sel_threshold(fit: pl.DataFrame, axis: str, level: str) -> float:
    col, op = SEL_AXES[axis]
    keep = LEVELS[level]
    return _q(fit[col], 1.0 - keep if op == ">=" else keep)


def sel_predicate(thr: dict[str, float]) -> Any:
    def pred(x: pl.DataFrame) -> pl.Series:
        m = pl.Series([True] * x.height)
        for axis, t in thr.items():
            col, op = SEL_AXES[axis]
            m = m & ((x[col] >= t) if op == ">=" else (x[col] <= t)).fill_null(False)
        return m

    return pred


def select(df: pl.DataFrame, thr: dict[str, float]) -> pl.DataFrame:
    return earliest_one(run_rule(df, sel_predicate(thr)))


# ---------------------------------------------------------------------------------------------
# VIX — the one external input (A-R3), from CBOE's public daily history
# ---------------------------------------------------------------------------------------------
def cmd_vix() -> None:
    VIX_CSV.parent.mkdir(parents=True, exist_ok=True)
    if not VIX_CSV.exists():
        urllib.request.urlretrieve(VIX_URL, VIX_CSV)  # noqa: S310
    raw = pl.read_csv(VIX_CSV)
    vix = (
        raw.select(
            pl.col("DATE").str.strptime(pl.Date, "%m/%d/%Y").alias("d"),
            pl.col("CLOSE").cast(pl.Float64).alias("vix_close"),
        )
        .filter(pl.col("d") >= date(2024, 6, 1))
        .sort("d")
    )
    vix.write_parquet(VIX_PATH)
    dump(
        "w4-vix.json",
        {
            "source": VIX_URL,
            "csv_sha256": stage0.sha256(VIX_CSV),
            "rows": vix.height,
            "first": vix["d"].min(),
            "last": vix["d"].max(),
            "note": "VIX is a session-state feature, not an outcome; HOLDOUT features ship intact.",
        },
    )


# ---------------------------------------------------------------------------------------------
# W4-0c — the session-state series, from panel-v1 columns only (plus the VIX cache for A-R3)
# ---------------------------------------------------------------------------------------------
def _roll_prior_mean(values: list[float | None], n: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        window = [v for v in values[max(0, i - n) : i] if v is not None]
        out.append(float(np.mean(window)) if i >= n and len(window) == n else None)
    return out


def vix_features(sessions: list[date]) -> tuple[list[float | None], list[float | None]]:
    """Prior VIX close and its 5-day change: both complete before the session opens."""
    vix = pl.read_parquet(VIX_PATH).sort("d")
    days, closes = vix["d"].to_list(), vix["vix_close"].to_list()
    level: list[float | None] = []
    chg5: list[float | None] = []
    for s in sessions:
        i = int(np.searchsorted(np.array(days, dtype="datetime64[D]"), np.datetime64(s))) - 1
        if i < 0:
            level.append(None)
            chg5.append(None)
            continue
        level.append(float(closes[i]))
        chg5.append(float(closes[i] - closes[i - 5]) if i >= 5 else None)
    return level, chg5


def session_state(panel: pl.DataFrame, sessions: pl.DataFrame) -> pl.DataFrame:
    """One row per session. R1 = prior-session distinct triggered symbols, 5-session mean;
    R2 = prior-session mean `hits_before_trigger`, 5-session mean (W3-A1's construction, carried
    with its ρ = 0.385 caveat); R3 = prior VIX close (+ its 5-day change, reported). A-R4 reads
    outcomes and is added by `add_r4` in the charged stage only."""
    sess = sessions.sort("dt")
    order = sess["dt"].to_list()
    per = (
        panel.group_by("dt")
        .agg(
            pl.col("symbol").n_unique().alias("breadth_raw"),
            pl.len().alias("n_setups"),
            pl.col("hits_before_trigger").mean().alias("attention_raw"),
        )
        .sort("dt")
    )
    m = {r["dt"]: r for r in per.iter_rows(named=True)}
    breadth = [float(m[d]["breadth_raw"]) if d in m else 0.0 for d in order]
    attention = [float(m[d]["attention_raw"]) if d in m else None for d in order]
    level, chg5 = vix_features(order)
    return sess.with_columns(
        pl.Series("breadth_raw", breadth),
        pl.Series("attention_raw", attention, dtype=pl.Float64),
        pl.Series("R1_breadth", _roll_prior_mean(breadth, LOOKBACK), dtype=pl.Float64),
        pl.Series("R2_attention", _roll_prior_mean(attention, LOOKBACK), dtype=pl.Float64),
        pl.Series("R3_vix", level, dtype=pl.Float64),
        pl.Series("R3b_vix_chg5", chg5, dtype=pl.Float64),
    )


def add_r4(state: pl.DataFrame, trades: pl.DataFrame, name: str = "R4_stream") -> pl.DataFrame:
    """A-R4: trailing 20-trade net R of the stream, over trades closed strictly before the
    session. Undefined until 20 have closed. ⚠️ Rebuilt inside every null replicate (plan §6.3)."""
    by_dt = {r["dt"]: float(r["net_r"]) for r in trades.iter_rows(named=True)}
    closed: list[float] = []
    vals: list[float | None] = []
    for d in state.sort("dt")["dt"].to_list():
        vals.append(float(np.mean(closed[-R4_TRADES:])) if len(closed) >= R4_TRADES else None)
        if d in by_dt:
            closed.append(by_dt[d])
    return state.sort("dt").with_columns(pl.Series(name, vals, dtype=pl.Float64))


def state_cut(state: pl.DataFrame, series: str, side: str, frac: float) -> float:
    """The FIT quantile that stands aside on `frac` of FIT sessions from the declared tail."""
    fit = state.filter(pl.col("split") == "FIT")[series]
    return _q(fit, frac if side == "low" else 1.0 - frac)


def keep_mask(state: pl.DataFrame, series: str, side: str, cut: float) -> dict[date, bool]:
    """True = trade. An undefined state trades (the ungated default — W3's convention)."""
    keep: dict[date, bool] = {}
    for r in state.iter_rows(named=True):
        v = r[series]
        keep[r["dt"]] = True if v is None else (v >= cut if side == "low" else v <= cut)
    return keep


# ---------------------------------------------------------------------------------------------
# Correlation helpers (no scipy in the cloud venv)
# ---------------------------------------------------------------------------------------------
def _corr(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    ok = ~(np.isnan(a) | np.isnan(b))
    a, b = a[ok], b[ok]
    if len(a) < 3:
        return float("nan"), float("nan")
    pear = float(np.corrcoef(a, b)[0, 1])
    ra = pl.Series(a).rank().to_numpy().astype(float)
    rb = pl.Series(b).rank().to_numpy().astype(float)
    return pear, float(np.corrcoef(ra, rb)[0, 1])


def corr_table(df: pl.DataFrame, cols: list[str]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for i, a in enumerate(cols):
        for b in cols[i + 1 :]:
            p, s = _corr(
                df[a].cast(pl.Float64).fill_null(np.nan).to_numpy(),
                df[b].cast(pl.Float64).fill_null(np.nan).to_numpy(),
            )
            out[f"{a}|{b}"] = {"pearson": round(p, 3), "spearman": round(s, 3)}
    return out


# ---------------------------------------------------------------------------------------------
# W4-0b — the stop-armed walk, proven on a synthetic path (never by computing R_max on real ones)
# ---------------------------------------------------------------------------------------------
def synthetic_stop_check() -> dict[str, Any]:
    """Entry 10, stop 9 (risk 1). Bar 0 high 10.5; bar 1 low touches the stop; bar 2 rallies to
    14. A stop-armed walk credits 0.5 R and halts; a raw path max would report 4 R."""
    path = np.array(
        [
            [10.0, 10.5, 10.0, 10.2],
            [10.2, 10.3, 9.0, 9.1],
            [9.1, 14.0, 9.0, 13.5],
        ]
    )
    res = stage0.replay_bracket(path, 10.0, 9.0, target_r=None)
    return {
        "max_r": res["max_r"],
        "stopped": res["stopped"],
        "bars_held": res["bars_held"],
        "stop_armed": bool(res["stopped"] and abs(res["max_r"] - 0.5) < 1e-9),
    }


# ---------------------------------------------------------------------------------------------
# Stage 0 — free items
# ---------------------------------------------------------------------------------------------
def load() -> tuple[pl.DataFrame, pl.DataFrame]:
    verify()
    return stage0.load_panel_v1(), stage0.load_sessions_v1()


def _half(df: pl.DataFrame) -> pl.Series:
    return pl.Series(
        [
            f"{s}-{h}" if s == "HOLDOUT" else s
            for s, h in zip(df["split"].to_list(), df["source"].to_list(), strict=True)
        ]
    )


def cmd_free() -> None:
    hashes = verify()
    df, sessions = stage0.load_panel_v1(), stage0.load_sessions_v1()
    fit = df.filter(pl.col("split") == "FIT")
    fit_sess = sorted(sessions.filter(pl.col("split") == "FIT")["dt"].to_list())
    out: dict[str, Any] = {"W4-0a": {"verified_8_of_8": True, "sha256": hashes}}

    # W4-0b — re-run the max_r reproduction on every row that has a path (FIT + CHECK), and
    # prove the walk halts at the stop on a synthetic path.
    pdict = stage0.load_paths_v1()
    fc = df.filter(pl.col("key").is_in(list(pdict)))
    bad = 0
    for r in fc.select("key", "entry_fill", "stop", "max_r").iter_rows(named=True):
        rep = stage0.replay_bracket(pdict[r["key"]], r["entry_fill"], r["stop"], target_r=None)
        if abs(rep["max_r"] - r["max_r"]) > 0.02:
            bad += 1
    out["W4-0b"] = {
        "rows_with_path": fc.height,
        "max_r_reproduction_mismatches": bad,
        "synthetic_path": synthetic_stop_check(),
        "code": "engine_lab.common.replay_bracket: `if lo[k] <= stop:` returns max_r before the "
        "bar's high is folded in — the stop is checked first on every bar, incl. bar 0",
    }

    # W4-0c — the state series over all 508 sessions, coverage per half
    state = session_state(df, sessions)
    cov: dict[str, dict[str, str]] = {}
    for s in ("R1_breadth", "R2_attention", "R3_vix", "R3b_vix_chg5"):
        cov[s] = {}
        for (sp, src), g in state.group_by(["split", "source"]):
            cov[s][f"{sp}-{src}"] = f"{g[s].drop_nulls().len()}/{g.height}"
    out["W4-0c"] = {
        "coverage": cov,
        "R4_note": "A-R4 reads outcomes: built in the charged stage on FIT+CHECK; on HOLDOUT it "
        "is computable by the custodian only (W3's precedent, plan §6.3).",
        "R5_R6_note": "A-R5 / A-R6 are A-R1 / A-R3 with the stand-aside side reversed: same "
        "series, opposite tail, charged as their own hypotheses.",
    }

    # W4-0d — throughput calibration of the whole grid on FIT (no outcome read)
    thr: dict[tuple[str, str], float] = {}
    sel_keep: dict[str, dict[str, Any]] = {}
    sel_sessions: dict[str, set[date]] = {"none": set(fit["dt"].to_list())}
    for axis in SEL_AXES:
        for lvl in LEVELS:
            t = sel_threshold(fit, axis, lvl)
            thr[(axis, lvl)] = t
            rows = run_rule(fit, sel_predicate({axis: t}))
            taken = earliest_one(rows)
            name = f"{axis}@{lvl}"
            sel_sessions[name] = set(taken["dt"].to_list())
            sel_keep[name] = {
                "column": SEL_AXES[axis][0],
                "op": SEL_AXES[axis][1],
                "threshold_fit": t,
                "rows_kept": rows.height,
                "row_share": round(rows.height / fit.height, 4),
                "trades_per_session": round(taken.height / len(fit_sess), 4),
                "in_band": TP_BAND[0] <= taken.height / len(fit_sess) <= TP_BAND[1],
            }
    gates: dict[str, dict[str, Any]] = {}
    gate_keep: dict[str, dict[date, bool]] = {}
    fit_state = state.filter(pl.col("split") == "FIT")
    for axis, (series, side) in STATE_AXES.items():
        if axis == "A-R4":
            continue
        for f in FRACTIONS:
            cut = state_cut(state, series, side, f)
            keep = keep_mask(fit_state, series, side, cut)
            name = f"{axis}@{int(f * 100)}%"
            gate_keep[name] = keep
            gates[name] = {
                "series": series,
                "side": side,
                "cut_fit": round(cut, 4),
                "sessions_stood_aside": sum(1 for v in keep.values() if not v),
                "stand_aside_share": round(sum(1 for v in keep.values() if not v) / len(keep), 4),
            }
    matrix: dict[str, dict[str, float]] = {}
    for sname, sess_with in sel_sessions.items():
        matrix[sname] = {"none": round(len(sess_with) / len(fit_sess), 4)}
        for gname, keep in gate_keep.items():
            n = sum(1 for d in sess_with if keep.get(d, True))
            matrix[sname][gname] = round(n / len(fit_sess), 4)
        for f in FRACTIONS:  # A-R4: nominal — its cut is a quantile of its own series
            matrix[sname][f"A-R4@{int(f * 100)}%(nominal)"] = round(
                len(sess_with) / len(fit_sess) * (1 - f), 4
            )
    joint = [
        (s, g, v) for s, row in matrix.items() if s != "none" for g, v in row.items() if g != "none"
    ]
    scoreable = [(s, g, v) for s, g, v in joint if TP_BAND[0] <= v <= TP_BAND[1]]
    out["W4-0d"] = {
        "band": TP_BAND,
        "selection_levels": sel_keep,
        "state_gates": gates,
        "throughput_matrix": matrix,
        "joint_points_total": len(joint),
        "joint_points_in_band": len(scoreable),
        "joint_points_dropped": [(s, g, v) for s, g, v in joint if (s, g, v) not in scoreable],
        "selection_levels_in_band": [n for n, v in sel_keep.items() if v["in_band"]],
    }

    # W4-0e — feature–feature correlation on FIT
    sel_cols = [c for c, _ in SEL_AXES.values()]
    out["W4-0e"] = {
        "selection_rows_fit": corr_table(fit, sel_cols),
        "state_sessions_fit": corr_table(
            fit_state, ["R1_breadth", "R2_attention", "R3_vix", "R3b_vix_chg5"]
        ),
        "rule": "|rho| > 0.9 -> keep the better-covered one, drop the other, ledger it",
    }

    # W4-0f — per-split feature-distribution drift (features only; HOLDOUT features ship intact)
    drift: dict[str, Any] = {}
    dh = df.with_columns(_half(df).alias("half"))
    for axis, (col, op) in SEL_AXES.items():
        drift[f"{axis}:{col}"] = {}
        for h in ("FIT", "CHECK", "HOLDOUT-recon", "HOLDOUT-live"):
            s = dh.filter(pl.col("half") == h)[col]
            share = {}
            for lvl in LEVELS:
                t = thr[(axis, lvl)]
                kept = (s >= t) if op == ">=" else (s <= t)
                share[lvl] = round(float(kept.fill_null(False).mean()), 4)
            drift[f"{axis}:{col}"][h] = {
                "q10_q50_q90": [round(_q(s, q), 5) for q in (0.1, 0.5, 0.9)],
                "share_kept_by_FIT_threshold": share,
            }
    sh = state.with_columns(_half(state).alias("half"))
    for s in ("R1_breadth", "R2_attention", "R3_vix", "R3b_vix_chg5"):
        drift[s] = {}
        for h in ("FIT", "CHECK", "HOLDOUT-recon", "HOLDOUT-live"):
            v = sh.filter(pl.col("half") == h)[s].drop_nulls()
            drift[s][h] = {
                "mean": round(float(v.mean()), 3) if v.len() else None,
                "q10_q50_q90": [round(_q(v, q), 3) for q in (0.1, 0.5, 0.9)] if v.len() else None,
            }
    out["W4-0f"] = drift
    out["sessions_per_split"] = {
        f"{sp}-{src}": n
        for sp, src, n in sessions.group_by(["split", "source"]).len().sort("split").iter_rows()
    }
    dump("w4-stage0-free.json", out)


# ---------------------------------------------------------------------------------------------
# Stage 0 — the eight charged trials. `pool_i = max(max_r_i, −1) − c_i` (plan §3.1).
# ---------------------------------------------------------------------------------------------
def pool_rows(df: pl.DataFrame, pdict: dict[str, np.ndarray]) -> pl.DataFrame:
    """Per row: the oracle-exit ceiling net of the pinned cost model at $500 full buying power.

    The oracle exit is priced as ONE non-limit leg at `entry + r·risk`, `r = max(max_r, −1)` —
    the cost floor a trailing exit pays (`c_trail` in the S0-F table), i.e. commissions on both
    legs plus 2-tick slippage on the exit. An unaffordable row (qty < 1) contributes 0 and is
    counted. `max_r` is the panel's stop-truncated value; nothing here re-walks the path past a
    stop."""
    rows = []
    for r in df.select("key", "dt", "entry_fill", "stop", "max_r", "stop_pct").iter_rows(
        named=True
    ):
        path = pdict[r["key"]]
        rr = max(float(r["max_r"]), -1.0)
        entry = max(r["entry_fill"], float(path[0, 0]))
        risk = entry - r["stop"]
        legs = [stage0.Leg(1.0, entry + rr * risk, False, rr)] if risk > 0 else []
        pr = stage0.price_trade(r["entry_fill"], r["stop"], float(path[0, 0]), legs)
        rows.append(
            {
                "key": r["key"],
                "dt": r["dt"],
                "max_r": float(r["max_r"]),
                "gross_ceiling": rr,
                "cost_r": pr.get("cost_r"),
                "pool": pr["net_r"],
                "affordable": pr["net_r"] is not None,
                "stop_pct": r["stop_pct"],
            }
        )
    return pl.DataFrame(rows)


def ceiling(pool: pl.DataFrame, n_sess: int) -> dict[str, Any]:
    p = pool.filter(pl.col("affordable"))["pool"].to_numpy()
    g = pool.filter(pl.col("affordable"))["gross_ceiling"].to_numpy()
    per = dict.fromkeys(sorted(set(pool["dt"].to_list())), 0.0)
    for r in pool.filter(pl.col("affordable")).iter_rows(named=True):
        per[r["dt"]] += r["pool"]
    v = np.array(list(per.values()) + [0.0] * (n_sess - len(per)))
    top = np.sort(v)[::-1][: max(1, n_sess // 10)]
    return {
        "sessions": n_sess,
        "trades": int(pool.height),
        "unaffordable": int((~pool["affordable"]).sum()),
        "trades_per_session": round(pool.height / n_sess, 4),
        "C_net_ceiling_per_session": round(float(p.sum()) / n_sess, 4),
        "gross_ceiling_per_session": round(float(g.sum()) / n_sess, 4),
        "cost_per_trade_mean": round(float(np.mean(p - g) * -1), 4),
        "pool_per_trade_mean": round(float(p.mean()), 4),
        "pool_per_trade_sd": round(float(p.std(ddof=1)), 4),
        "pool_per_trade_se": round(float(p.std(ddof=1) / math.sqrt(len(p))), 4),
        "pool_per_trade_deciles": [
            round(float(x), 3) for x in np.quantile(p, np.linspace(0, 1, 11))
        ],
        "session_pool_deciles": [round(float(x), 3) for x in np.quantile(v, np.linspace(0, 1, 11))],
        "share_trades_pool_le_0": round(float((p <= 0).mean()), 4),
        "top_decile_sessions_share_of_positive_pool": round(
            float(top.clip(min=0).sum() / max(v.clip(min=0).sum(), 1e-12)), 4
        ),
        "stop_pct_q10_q50_q90": [round(_q(pool["stop_pct"], q), 4) for q in (0.1, 0.5, 0.9)],
    }


def mde_table(sd: float) -> dict[str, float]:
    z = NormalDist().inv_cdf
    k = z(1 - ALPHA / 2) + z(POWER)
    return {
        f"n={n}": round(k * sd / math.sqrt(n), 4)
        for n in (FIT_SESSIONS, round(0.83 * FIT_SESSIONS), round(0.58 * FIT_SESSIONS), 120)
    }


def cmd_charged() -> None:
    df, sessions = load()
    fit = df.filter(pl.col("split") == "FIT")
    fit_sess = sorted(sessions.filter(pl.col("split") == "FIT")["dt"].to_list())
    n = len(fit_sess)
    pdict = stage0.load_paths_v1()
    out: dict[str, Any] = {"trials": 8, "seed": SEED}

    # S4-0g (1) — the stop-truncated max_r distribution over all FIT rows, and its concentration
    allp = pool_rows(fit, pdict)
    m = allp["max_r"].to_numpy()
    pos = np.clip(np.maximum(m, -1.0), 0, None)
    top_rows = np.sort(pos)[::-1][: max(1, len(pos) // 10)]
    out["S4-0g"] = {
        "rows": int(len(m)),
        "mean": round(float(m.mean()), 4),
        "median": round(float(np.median(m)), 4),
        "deciles": [round(float(x), 3) for x in np.quantile(m, np.linspace(0, 1, 11))],
        "share_max_r_le_0": round(float((m <= 0).mean()), 4),
        "share_max_r_ge_1": round(float((m >= 1).mean()), 4),
        "share_max_r_ge_2": round(float((m >= 2).mean()), 4),
        "top_decile_rows_share_of_positive_excursion": round(float(top_rows.sum() / pos.sum()), 4),
        "uniform_reference": 0.10,
        "all_rows_pool_per_trade_mean": round(
            float(allp.filter(pl.col("affordable"))["pool"].mean()), 4
        ),
        "all_rows_pool_per_trade_sd": round(
            float(allp.filter(pl.col("affordable"))["pool"].std()), 4
        ),
        "all_rows_unaffordable": int((~allp["affordable"]).sum()),
    }

    # S4-0h (1) — C_raw: the unfiltered N = 1 earliest-by-time FIT stream
    raw_taken = earliest_one(fit)
    raw_pool = pool_rows(raw_taken, pdict)
    out["S4-0h"] = {"stream": "unfiltered, N=1 earliest by time", **ceiling(raw_pool, n)}
    c_raw = out["S4-0h"]["C_net_ceiling_per_session"]

    # S4-0i (1) — C_A: the Filter-A reference stream (retired as a population, kept as a baseline)
    k_a = stage0.filter_a_constant(df)
    fa_pool = pool_rows(stage0.filter_a(fit, k_a), pdict)
    out["S4-0i"] = {
        "stream": "Filter A at its frozen constant (reference only)",
        "filter_a_constant": k_a,
        **ceiling(fa_pool, n),
    }

    # S4-0j (4) — κ of F1–F4 on the unfiltered N = 1 stream
    oc = stage0.family_outcomes(raw_taken, pdict)
    base = raw_taken.select("key", "dt", "stop_pct")
    fam: dict[str, Any] = {}
    for f in stage0.FAMILIES:
        o = base.join(oc.filter(pl.col("family") == f), on="key")
        taken = o.filter(pl.col("net_r").is_not_null())
        rep = stage0.session_report(taken, fit_sess)
        rep["unaffordable"] = int(o.filter(pl.col("net_r").is_null()).height)
        rep["kappa_J_over_C_raw"] = round(rep["J_net_r_per_session"] / c_raw, 4) if c_raw else None
        fam[f] = rep
    out["S4-0j"] = {"C_raw_ceiling": c_raw, "families": fam}

    # S4-0k (1) — sd(pool_i) per trade on the N = 1 stream, and the MDE table on C (α = 0.01)
    sd = out["S4-0h"]["pool_per_trade_sd"]
    tp = out["S4-0h"]["trades_per_session"]
    out["S4-0k"] = {
        "sd_pool_per_trade_N1_stream": sd,
        "sd_pool_per_trade_all_fit_rows": out["S4-0g"]["all_rows_pool_per_trade_sd"],
        "alpha_two_sided": ALPHA,
        "power": POWER,
        "MDE_per_trade_on_C": mde_table(sd),
        "MDE_per_session_on_C_at_full_throughput": round(
            mde_table(sd)[f"n={FIT_SESSIONS}"] * tp, 4
        ),
        "C_raw_distance_from_zero": abs(c_raw),
    }

    # Gate G1 and the §5.2 fork — mechanical readings only; interpretation is another session's
    share = out["S4-0g"]["top_decile_rows_share_of_positive_excursion"]
    kappas = {f: fam[f]["kappa_J_over_C_raw"] for f in fam}
    out["gates"] = {
        "G1_fires": bool(c_raw <= 0 and share < 0.25),
        "G1_inputs": {"C_raw": c_raw, "top_decile_share": share, "threshold": 0.25},
        "power_cancellation_fires": bool(
            out["S4-0k"]["MDE_per_session_on_C_at_full_throughput"] > abs(c_raw)
        ),
        "fork_inputs": {"C_raw_sign": "<=0" if c_raw <= 0 else ">0", "kappa": kappas},
    }
    dump("w4-stage0-charged.json", out)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "free"
    {"vix": cmd_vix, "free": cmd_free, "charged": cmd_charged}[cmd]()


if __name__ == "__main__":
    print(f"# w4 {sys.argv[1:]} {datetime.now().isoformat(timespec='seconds')}")
    main()
