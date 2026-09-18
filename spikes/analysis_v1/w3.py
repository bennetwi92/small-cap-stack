"""Analysis v1 — W3 (#739): time structure and the re-tuning protocol.

Plan: `research/analysis-plan-3-regime.md`, executed against `research/analysis-protocol.md`.
This module is W3's measurement layer only — it produces numbers and never draws a conclusion
(protocol §13.4). It imports `stage0` for the splits, Filter A, the exit families, the cost model
and the §6.2 report; it forks none of them.

    .venv/bin/python spikes/analysis_v1/w3.py stage0    # W3-0a … W3-0d  (free — reads no outcome)
    .venv/bin/python spikes/analysis_v1/w3.py period    # §6 period-outcome distribution (S0-H)
    .venv/bin/python spikes/analysis_v1/w3.py t1        # T1 marginal gates + the search null
    .venv/bin/python spikes/analysis_v1/w3.py t2        # T2 conjunction
    .venv/bin/python spikes/analysis_v1/w3.py t4        # T4 refit cadence
    .venv/bin/python spikes/analysis_v1/w3.py t3wf5     # T3 + walk-forward + T5, candidate only

Outputs land in `data/spikes/w3/` (gitignored); the tables that matter are copied into the
findings doc and the ledger.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from spikes.analysis_v1 import stage0  # noqa: E402

OUT = REPO / "data/spikes/w3"
VIX_PATH = REPO / "data/spikes/vix_daily_w3.parquet"

#: The panel is verified by bytes before anything else (protocol §4.4).
PANEL_SHA = "ff77c4516e78db9cd05081ce0c9f5bf7db3b6052eaccbbc9c557950c1cde37e7"
SESSIONS_SHA = "d797f739329d0c8dff7b7b4abe6baa14507815a2c7d4e0504989fe3539026b60"
PATHS_SHA = "2b5bd485ca0010f925ec343c4174ab0ef28a78029424489f75e4af79b3f594a0"

#: Filter A condition 3, frozen on FIT by Stage 0 and published in `panel-v1-spec.md` §5.
#: W3 may not tune it. It is read here as a literal, never recomputed for T1–T3 or T5.
FILTER_A_CONSTANT = 4857910.10055

#: The 5-session lookback for the breadth and attention features (plan §5.1).
LOOKBACK = 5
#: The trailing-trade window for R4 (plan §5.1).
R4_TRADES = 20
#: §7.3, as W3 reads it for a session-level hypothesis: a circular block permutation of the
#: per-session outcome series against a fixed session-state series, block length 20 sessions.
NULL_BLOCK = 20
B_NULL = 200
SEED = 20260918


def verify() -> None:
    """Protocol §4.4 — same bytes, or stop. Never a row count, never a rebuild."""
    pairs = [
        (stage0.PUBLISH / "panel-v1.parquet", PANEL_SHA),
        (stage0.PUBLISH / "sessions-v1.parquet", SESSIONS_SHA),
        (stage0.PUBLISH / "paths-v1" / "paths-v1.parquet", PATHS_SHA),
    ]
    for path, want in pairs:
        got = stage0.sha256(path)
        if got != want:
            raise SystemExit(f"STOP-AND-REPORT: {path.name} sha256 {got} != spec {want}")
    print("panel hash verified against panel-v1-spec.md §1 (3 artefacts)")


def dump(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, default=str))
    print(json.dumps(obj, indent=2, default=str))


# ---------------------------------------------------------------------------------------------
# W3-0a — the session-state series
# ---------------------------------------------------------------------------------------------
def _roll_prior_mean(values: list[float | None], n: int) -> list[float | None]:
    """Mean of the `n` sessions strictly before each session. Undefined until `n` are available."""
    out: list[float | None] = []
    for i in range(len(values)):
        if i < n:
            out.append(None)
            continue
        window = [v for v in values[i - n : i] if v is not None]
        out.append(float(np.mean(window)) if len(window) == n else None)
    return out


def vix_features(sessions: list[date]) -> tuple[list[float | None], list[float | None]]:
    """Prior VIX close, and its 5-day change, for each session. Both are complete before the
    session opens, so both are decidable at trigger time."""
    vix = pl.read_parquet(VIX_PATH).sort("d")
    days = vix["d"].to_list()
    closes = vix["vix_close"].to_list()
    level: list[float | None] = []
    chg5: list[float | None] = []
    for s in sessions:
        prior = [i for i, d in enumerate(days) if d < s]
        if not prior:
            level.append(None)
            chg5.append(None)
            continue
        i = prior[-1]
        level.append(float(closes[i]))
        chg5.append(float(closes[i] - closes[i - 5]) if i >= 5 else None)
    return level, chg5


def filter_a_stream(panel: pl.DataFrame) -> pl.DataFrame:
    """Filter A at its frozen constant, over every split. Features only — no outcome is read."""
    return stage0.filter_a(panel, FILTER_A_CONSTANT)


def session_state(panel: pl.DataFrame, sessions: pl.DataFrame) -> pl.DataFrame:
    """W3-0a. One row per session, carrying the four features of plan §5.1 and the raw
    per-session quantities they are built from. Every feature is a function of sessions strictly
    before the one it labels, so every one is decidable before the session opens.

    ⚠️ Amendment W3-A1 (ledger #735): R1 and R2 are built from **panel-v1's own rows**, not from
    the raw `opportunities` / `scanner_hits` datasets. The raw tapes published for the analysis
    are recon-only and FIT+CHECK-bounded, so a feature built on them cannot be shown populated on
    the live half — which constraint 12 requires and which is 47 % of the holdout. The panel ships
    HOLDOUT features intact on both halves, so the panel-derived form is the only one this
    workstream can verify. §3's fallback for W3-0a is "drop the feature"; this keeps the feature
    at the cost of a measured substitution, and the agreement with the raw spine is reported on
    FIT+CHECK where both exist.
    """
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


def add_r4(state: pl.DataFrame, trades: pl.DataFrame, family: str) -> pl.DataFrame:
    """R4 — the trailing 20-trade realised **net** R of the Filter-A stream, over trades closed
    strictly before the session. It reads outcomes, but only of trades already closed at trigger
    time, so it is not lookahead. Undefined until 20 trades have closed."""
    by_dt = {r["dt"]: float(r["net_r"]) for r in trades.iter_rows(named=True)}
    order = state["dt"].to_list()
    closed: list[float] = []
    vals: list[float | None] = []
    for d in order:
        vals.append(float(np.mean(closed[-R4_TRADES:])) if len(closed) >= R4_TRADES else None)
        if d in by_dt:
            closed.append(by_dt[d])
    return state.with_columns(pl.Series(f"R4_stream_{family}", vals, dtype=pl.Float64))


FEATURES = ["R1_breadth", "R2_attention", "R3_vix", "R4_stream_F2"]
#: Pre-declared per feature, before the fit split was opened (plan §5.2 — the direction is part
#: of the hypothesis and is not searched). "low" = stand aside when the feature is in the lower
#: tail; "high" = stand aside when it is in the upper tail.
STAND_ASIDE_SIDE = {
    "R1_breadth": "low",
    "R2_attention": "low",
    "R3_vix": "high",
    "R3b_vix_chg5": "high",
    "R4_stream_F2": "low",
    "R4_stream_F1": "low",
    "R4_stream_F3": "low",
    "R4_stream_F4": "low",
}


def trade_stream(
    panel: pl.DataFrame, split: str, families: tuple[str, ...] = ("F2",)
) -> pl.DataFrame:
    """The Filter-A stream's per-trade net and gross R on `split`, one row per (session, family).

    ⚠️ This reads outcome columns. Every call is charged as the trials the caller declares; the
    FIT call reproduces S0-H, which is already paid for (plan §4).
    """
    part = panel.filter(pl.col("split") == split)
    fa = filter_a_stream(part)
    pdict = stage0.load_paths_v1()
    oc = stage0.family_outcomes(fa, pdict)
    out = (
        fa.select("key", "dt", "source", "stop_pct")
        .join(oc, on="key")
        .filter(pl.col("family").is_in(list(families)) & pl.col("net_r").is_not_null())
    )
    return out.sort("dt")


# ---------------------------------------------------------------------------------------------
# The gate, and how a gated stream is scored
# ---------------------------------------------------------------------------------------------
def gate_mask(state: pl.DataFrame, feature: str, cut: float, side: str) -> dict[date, bool]:
    """True = trade this session. A session whose feature is undefined trades — the ungated
    default. Standing aside on an undefined state would let the warm-up period flatter the gate
    by removing sessions it never evaluated."""
    keep: dict[date, bool] = {}
    for r in state.iter_rows(named=True):
        v = r[feature]
        if v is None:
            keep[r["dt"]] = True
        elif side == "low":
            keep[r["dt"]] = v >= cut
        else:
            keep[r["dt"]] = v <= cut
    return keep


def score_gated(
    trades: pl.DataFrame, sessions: list[date], keep: dict[date, bool] | None
) -> dict[str, Any]:
    """§6 — J over **every** session in the block. A session the gate stands aside on contributes
    0; it is never dropped from the denominator."""
    taken = (
        trades
        if keep is None
        else trades.filter(
            pl.col("dt").map_elements(lambda d: keep.get(d, True), return_dtype=pl.Boolean)
        )
    )
    rep = stage0.session_report(taken, sessions)
    rep["sessions_stood_aside"] = (
        0 if keep is None else sum(1 for d in sessions if not keep.get(d, True))
    )
    return rep


def fit_quantile(state: pl.DataFrame, feature: str, q: float) -> float:
    """§5.3 — every threshold is a quantile of the FIT distribution, never an absolute."""
    fit = state.filter(pl.col("split") == "FIT")[feature].drop_nulls()
    return float(fit.quantile(q, interpolation="linear"))


# ---------------------------------------------------------------------------------------------
# §7.3 — the null, at matched search intensity
# ---------------------------------------------------------------------------------------------
def _session_r(trades: pl.DataFrame, sessions: list[date]) -> np.ndarray:
    per = dict.fromkeys(sessions, 0.0)
    for r in trades.iter_rows(named=True):
        per[r["dt"]] += float(r["net_r"])
    return np.array([per[d] for d in sorted(per)])


def block_permutations(n: int, b: int, block: int, rng: np.random.Generator) -> list[np.ndarray]:
    """Circular block permutation of a session-indexed series: cut the record into contiguous
    blocks of `block` sessions, permute the blocks, and rotate. It preserves the burst structure
    inside a block — which prior (1) says is real and which a global i.i.d. shuffle would destroy
    — while breaking the alignment between the session-state series and the outcome series, which
    is exactly the association every W3 hypothesis claims."""
    idx = np.arange(n)
    out = []
    for _ in range(b):
        shift = int(rng.integers(n))
        rolled = np.roll(idx, shift)
        blocks = [rolled[i : i + block] for i in range(0, n, block)]
        order = rng.permutation(len(blocks))
        out.append(np.concatenate([blocks[i] for i in order]))
    return out


def search_null(
    state: pl.DataFrame,
    trades: pl.DataFrame,
    sessions: list[date],
    grid: list[tuple[str, float, float, str]],
    *,
    b: int = B_NULL,
    block: int = NULL_BLOCK,
) -> dict[str, Any]:
    """Re-run the **entire** search against each permuted record and compare best-of-search to
    best-of-search (§7.3). `grid` is (feature, q, cut, side)."""
    rng = np.random.default_rng(SEED)
    r = _session_r(trades, sessions)
    n = len(sessions)
    masks = []
    for feature, _q, cut, side in grid:
        keep = gate_mask(state, feature, cut, side)
        masks.append(np.array([keep.get(d, True) for d in sessions], dtype=bool))
    real = max(float(r[m].sum()) / n for m in masks)
    nulls = []
    for perm in block_permutations(n, b, block, rng):
        rp = r[perm]
        nulls.append(max(float(rp[m].sum()) / n for m in masks))
    nulls_arr = np.array(nulls)
    return {
        "B": b,
        "block_sessions": block,
        "grid_points": len(grid),
        "real_best_J": round(real, 4),
        "null_best_J_q50": round(float(np.quantile(nulls_arr, 0.5)), 4),
        "null_best_J_q95": round(float(np.quantile(nulls_arr, 0.95)), 4),
        "p": round(float((1 + (nulls_arr >= real).sum()) / (1 + b)), 4),
    }


# ---------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------
def load() -> tuple[pl.DataFrame, pl.DataFrame]:
    return stage0.load_panel_v1(), stage0.load_sessions_v1()


def cmd_stage0() -> None:
    """W3-0a … W3-0d. Free: nothing here reads an outcome column or a post-trigger price."""
    verify()
    panel, sessions = load()
    state = session_state(panel, sessions)
    fa = filter_a_stream(panel)

    # W3-0a — coverage, per split and per half (constraint 12).
    cov: dict[str, Any] = {}
    for feat in ["R1_breadth", "R2_attention", "R3_vix", "R3b_vix_chg5"]:
        cov[feat] = {}
        for split in ["FIT", "CHECK", "HOLDOUT"]:
            for src in ["recon", "live"]:
                part = state.filter((pl.col("split") == split) & (pl.col("source") == src))
                if not part.height:
                    continue
                s = part[feat]
                cov[feat][f"{split}:{src}"] = {
                    "sessions": part.height,
                    "populated": int(s.drop_nulls().len()),
                    "frac": round(s.drop_nulls().len() / part.height, 4),
                }

    # W3-0b — stationarity across splits and halves.
    stat: dict[str, Any] = {}
    for feat in ["R1_breadth", "R2_attention", "R3_vix", "R3b_vix_chg5"]:
        stat[feat] = {}
        for split in ["FIT", "CHECK", "HOLDOUT"]:
            for src in ["recon", "live"]:
                part = state.filter((pl.col("split") == split) & (pl.col("source") == src))
                s = part[feat].drop_nulls()
                if not s.len():
                    continue
                stat[feat][f"{split}:{src}"] = {
                    "n": s.len(),
                    "q10": round(float(s.quantile(0.1)), 4),
                    "q50": round(float(s.quantile(0.5)), 4),
                    "q90": round(float(s.quantile(0.9)), 4),
                    "mean": round(float(s.mean()), 4),
                }

    # autocorrelation of each feature on FIT — how much a session-state gate can even vary
    ac: dict[str, Any] = {}
    for feat in ["R1_breadth", "R2_attention", "R3_vix", "R3b_vix_chg5"]:
        s = state.filter(pl.col("split") == "FIT")[feat].drop_nulls().to_numpy()
        ac[feat] = [round(float(np.corrcoef(s[:-k], s[k:])[0, 1]), 3) for k in (1, 5, 20)]

    # W3-0c — feature-feature correlation on FIT.
    fit = state.filter(pl.col("split") == "FIT")
    feats = ["R1_breadth", "R2_attention", "R3_vix", "R3b_vix_chg5"]
    corr: dict[str, float] = {}
    for i, a in enumerate(feats):
        for b in feats[i + 1 :]:
            sub = fit.select(a, b).drop_nulls()
            corr[f"{a}|{b}"] = round(float(np.corrcoef(sub[a], sub[b])[0, 1]), 3)

    # W3-0d — the sessions-with-a-trade census on the Filter-A stream (reads trigger times only).
    census: dict[str, Any] = {}
    for split in ["FIT", "CHECK", "HOLDOUT"]:
        sess = sessions.filter(pl.col("split") == split)
        qualifying = panel.filter(
            (pl.col("split") == split)
            & (pl.col("hits_before_trigger") >= 1)
            & (pl.col(stage0.FILTER_A_VOLUME_COL) >= FILTER_A_CONSTANT)
        )
        traded = fa.filter(pl.col("split") == split)
        census[split] = {
            "sessions": sess.height,
            "sessions_with_a_qualifying_setup": int(qualifying["dt"].n_unique()),
            "sessions_with_a_trade": int(traded["dt"].n_unique()),
            "frac_sessions_with_a_trade": round(traded["dt"].n_unique() / sess.height, 4),
            "trades_per_session": round(traded.height / sess.height, 4),
            "qualifying_setups_turned_away": int(qualifying.height - traded.height),
        }

    # The throughput arithmetic that decides which quantiles are even admissible (plan §5.2).
    thr = {
        f"stand_aside_q{int(q * 100):02d}": round(1.0 * (1 - q), 3)
        for q in (0.10, 0.20, 0.30, 0.50, 0.70)
    }

    OUT.mkdir(parents=True, exist_ok=True)
    state.write_parquet(OUT / "session-state.parquet")
    dump(
        "W3-stage0.json",
        {
            "panel_sha_verified": True,
            "sessions": sessions.height,
            "W3_0a_coverage": cov,
            "W3_0b_stationarity": stat,
            "W3_0b_feature_autocorrelation_FIT_lags_1_5_20": ac,
            "W3_0c_feature_correlation_FIT": corr,
            "W3_0d_census": census,
            "throughput_if_gate_stands_aside": thr,
        },
    )


def cmd_spine_check() -> None:
    """Amendment W3-A1's evidence: how closely the panel-derived breadth and attention series
    track the raw `opportunities` / `scanner_hits` spine, on the FIT+CHECK window where the raw
    tapes exist. Free — neither series reads an outcome."""
    panel, sessions = load()
    state = session_state(panel, sessions)
    tapes = REPO / "data/spikes/panel-v1/tapes"
    opps = pl.read_parquet(tapes / "recon_opportunities_fitcheck.parquet")
    hits = pl.read_parquet(tapes / "recon_scanner_hits_fitcheck.parquet")
    odt = "trading_date" if "trading_date" in opps.columns else "dt"
    raw_breadth = opps.group_by(odt).agg(pl.col("symbol").n_unique().alias("spine_breadth"))
    raw_breadth = raw_breadth.rename({odt: "dt"}).with_columns(pl.col("dt").cast(pl.Date))
    hit_counts = hits.group_by("opportunity_id").len().rename({"len": "n_hits"})
    raw_att = (
        opps.select(pl.col(odt).cast(pl.Date).alias("dt"), "opportunity_id")
        .join(hit_counts, on="opportunity_id", how="left")
        .group_by("dt")
        .agg(pl.col("n_hits").fill_null(0).mean().alias("spine_attention"))
    )
    j = (
        state.filter(pl.col("split").is_in(["FIT", "CHECK"]))
        .join(raw_breadth, on="dt", how="inner")
        .join(raw_att, on="dt", how="inner")
    )
    out = {"sessions_matched": j.height}
    for a, b in (("breadth_raw", "spine_breadth"), ("attention_raw", "spine_attention")):
        sub = j.select(a, b).drop_nulls()
        out[f"{a}~{b}"] = {
            "pearson": round(float(np.corrcoef(sub[a], sub[b])[0, 1]), 3),
            "spearman": round(
                float(
                    np.corrcoef(
                        sub[a].rank().to_numpy(),
                        sub[b].rank().to_numpy(),
                    )[0, 1]
                ),
                3,
            ),
            "panel_q50": round(float(sub[a].median()), 3),
            "spine_q50": round(float(sub[b].median()), 3),
        }
    dump("W3-spine-check.json", out)


# ---------------------------------------------------------------------------------------------
# §6 — the period-outcome distribution. Computed from the S0-H baseline, charged as nothing.
# ---------------------------------------------------------------------------------------------
def cold_stretches(r: np.ndarray) -> dict[str, Any]:
    """How often a run of 20 / 40 / 60 net-losing sessions occurs, and how deep the cumulative-R
    drawdown gets. A 'cold stretch of n' is any window of n consecutive sessions summing < 0."""
    out: dict[str, Any] = {}
    cum = np.cumsum(r)
    for n in (20, 40, 60):
        if len(r) < n:
            continue
        wins = np.array([r[i : i + n].sum() for i in range(len(r) - n + 1)])
        out[f"windows_of_{n}"] = {
            "n_windows": int(len(wins)),
            "frac_negative": round(float((wins < 0).mean()), 4),
            "worst_window_r": round(float(wins.min()), 3),
            "median_window_r": round(float(np.median(wins)), 3),
        }
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    dd = cum - peak
    under = dd < 0
    runs, cur = [], 0
    for u in under:
        if u:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    out["drawdown"] = {
        "deepest_r": round(float(dd.min()), 3),
        "longest_underwater_sessions": int(max(runs)) if runs else 0,
        "median_underwater_sessions": round(float(np.median(runs)), 1) if runs else 0.0,
        "frac_sessions_underwater": round(float(under.mean()), 4),
    }
    return out


def autocorr(r: np.ndarray, lags: int = 20) -> dict[str, float]:
    out = {}
    for k in range(1, lags + 1):
        a, b = r[:-k], r[k:]
        out[f"lag_{k}"] = round(float(np.corrcoef(a, b)[0, 1]), 4)
    return out


def cmd_period() -> None:
    """Plan §6 — the period-outcome distribution prior (1) asks for, on the FIT Filter-A stream.
    Reproduces S0-H's F2 row first: the same ruler, or the numbers are not comparable."""
    verify()
    panel, sessions = load()
    fit_sessions = sorted(sessions.filter(pl.col("split") == "FIT")["dt"].to_list())
    trades = trade_stream(panel, "FIT", ("F1", "F2", "F3", "F4"))
    out: dict[str, Any] = {}
    for fam in ("F1", "F2", "F3", "F4"):
        t = trades.filter(pl.col("family") == fam)
        rep = score_gated(t, fit_sessions, None)
        r = _session_r(t, fit_sessions)
        rep["cold_stretches"] = cold_stretches(r)
        if fam == "F2":
            rep["autocorrelation_lags_1_20"] = autocorr(r, 20)
            # Ljung-Box on the first 20 lags: the direct test of "hot and cold" as serial
            # dependence rather than a pattern the eye imposes on independent bursts.
            n = len(r)
            ac = [float(np.corrcoef(r[:-k], r[k:])[0, 1]) for k in range(1, 21)]
            q = n * (n + 2) * sum(a**2 / (n - k) for k, a in enumerate(ac, start=1))
            rep["ljung_box_20"] = {
                "Q": round(q, 3),
                "df": 20,
                "chi2_95": 31.41,
                "reject_independence_at_05": bool(q > 31.41),
            }
            rep["runs_test"] = _runs_test(r)
        out[fam] = rep
    dump("W3-period.json", out)


def _runs_test(r: np.ndarray) -> dict[str, Any]:
    """Wald–Wolfowitz runs test on the sign of per-session net R. Clustering of winning and
    losing sessions shows up as too few runs."""
    sign = r > 0
    n1, n2 = int(sign.sum()), int((~sign).sum())
    runs = 1 + int((sign[1:] != sign[:-1]).sum())
    mu = 2 * n1 * n2 / (n1 + n2) + 1
    var = (mu - 1) * (mu - 2) / (n1 + n2 - 1)
    z = (runs - mu) / np.sqrt(var) if var > 0 else 0.0
    return {
        "winning_sessions": n1,
        "losing_or_flat": n2,
        "runs": runs,
        "expected_runs": round(float(mu), 2),
        "z": round(float(z), 3),
        "clustered_at_05": bool(abs(z) > 1.96),
    }


# ---------------------------------------------------------------------------------------------
# T1 — the twelve marginal gates
# ---------------------------------------------------------------------------------------------
FRACTIONS = (0.10, 0.20, 0.30)


def _state_with_r4(
    panel: pl.DataFrame, sessions: pl.DataFrame, trades: pl.DataFrame
) -> pl.DataFrame:
    state = session_state(panel, sessions)
    return add_r4(state, trades, "F2")


def grid_for(state: pl.DataFrame, features: list[str]) -> list[tuple[str, float, float, str]]:
    """(feature, stand-aside fraction, cut, side). The cut is a FIT quantile from the declared
    tail; `side` is pre-declared in STAND_ASIDE_SIDE and is never searched."""
    grid = []
    for feat in features:
        side = STAND_ASIDE_SIDE[feat]
        for x in FRACTIONS:
            q = x if side == "low" else 1.0 - x
            grid.append((feat, x, fit_quantile(state, feat, q), side))
    return grid


def cmd_t1() -> None:
    """T1 — 12 trials. 4 features × 3 stand-aside fractions, F2, FIT."""
    verify()
    panel, sessions = load()
    fit_sessions = sorted(sessions.filter(pl.col("split") == "FIT")["dt"].to_list())
    trades = trade_stream(panel, "FIT", ("F2",))
    state = _state_with_r4(panel, sessions, trades)
    state.write_parquet(OUT / "session-state-r4.parquet")
    base = score_gated(trades, fit_sessions, None)
    grid = grid_for(state, FEATURES)
    rows = []
    for feat, x, cut, side in grid:
        keep = gate_mask(state.filter(pl.col("split") == "FIT"), feat, cut, side)
        rep = score_gated(trades, fit_sessions, keep)
        rows.append(
            {
                "feature": feat,
                "stand_aside_frac": x,
                "cut": round(cut, 4),
                "side": side,
                "J": rep["J_net_r_per_session"],
                "J_minus_baseline": round(
                    rep["J_net_r_per_session"] - base["J_net_r_per_session"], 4
                ),
                "gross_r_per_session": rep["gross_r_per_session"],
                "trades": rep["trades"],
                "trades_per_session": rep["trades_per_session"],
                "stood_aside": rep["sessions_stood_aside"],
                "throughput_ok": 0.6 <= rep["trades_per_session"] <= 1.0,
                "net_r_per_trade_mean": rep["net_r_per_trade_mean"],
                "report": rep,
            }
        )
    null = search_null(state.filter(pl.col("split") == "FIT"), trades, fit_sessions, grid)
    dump(
        "W3-T1.json",
        {
            "trials_spent": len(grid),
            "baseline": base,
            "r4_cuts": {
                f"q{int(x * 100):02d}": round(fit_quantile(state, "R4_stream_F2", x), 4)
                for x in FRACTIONS
            },
            "gates": rows,
            "search_null_block20": null,
        },
    )


# ---------------------------------------------------------------------------------------------
# T2 — the conjunction of the two best marginal features
# ---------------------------------------------------------------------------------------------
def union_mask(state: pl.DataFrame, parts: list[tuple[str, float, str]]) -> dict[date, bool]:
    """Stand aside if **either** feature fires. An undefined feature never fires."""
    keep: dict[date, bool] = {}
    for r in state.iter_rows(named=True):
        aside = False
        for feat, cut, side in parts:
            v = r[feat]
            if v is None:
                continue
            if (side == "low" and v < cut) or (side == "high" and v > cut):
                aside = True
        keep[r["dt"]] = not aside
    return keep


def cmd_t2() -> None:
    """T2 — 4 trials. The two best T1 features, 2 stand-aside fractions each, union gate, F2."""
    verify()
    panel, sessions = load()
    fit_sessions = sorted(sessions.filter(pl.col("split") == "FIT")["dt"].to_list())
    trades = trade_stream(panel, "FIT", ("F2",))
    state = _state_with_r4(panel, sessions, trades)
    fit_state = state.filter(pl.col("split") == "FIT")
    base = score_gated(trades, fit_sessions, None)
    t1 = json.loads((OUT / "W3-T1.json").read_text())
    best = sorted(
        {g["feature"]: 0.0 for g in t1["gates"]},
        key=lambda f: -max(g["J_minus_baseline"] for g in t1["gates"] if g["feature"] == f),
    )[:2]
    rows = []
    masks = []
    for xa in (0.10, 0.20):
        for xb in (0.10, 0.20):
            parts = []
            for feat, x in ((best[0], xa), (best[1], xb)):
                side = STAND_ASIDE_SIDE[feat]
                parts.append((feat, fit_quantile(state, feat, x if side == "low" else 1 - x), side))
            keep = union_mask(fit_state, parts)
            rep = score_gated(trades, fit_sessions, keep)
            masks.append(np.array([keep.get(d, True) for d in fit_sessions], dtype=bool))
            rows.append(
                {
                    "features": [best[0], best[1]],
                    "stand_aside_fracs": [xa, xb],
                    "cuts": [round(p[1], 4) for p in parts],
                    "J": rep["J_net_r_per_session"],
                    "J_minus_baseline": round(
                        rep["J_net_r_per_session"] - base["J_net_r_per_session"], 4
                    ),
                    "trades_per_session": rep["trades_per_session"],
                    "stood_aside": rep["sessions_stood_aside"],
                    "throughput_ok": 0.6 <= rep["trades_per_session"] <= 1.0,
                    "report": rep,
                }
            )
    # The final null of §4: the whole T1+T2 sequence re-run against each replicate.
    grid = grid_for(state, FEATURES)
    rng = np.random.default_rng(SEED)
    r = _session_r(trades, fit_sessions)
    n = len(fit_sessions)
    all_masks = [
        np.array([gate_mask(fit_state, f, c, s).get(d, True) for d in fit_sessions], dtype=bool)
        for f, _x, c, s in grid
    ] + masks
    real = max(float(r[m].sum()) / n for m in all_masks)
    nulls = np.array(
        [
            max(float(r[perm][m].sum()) / n for m in all_masks)
            for perm in block_permutations(n, B_NULL, NULL_BLOCK, rng)
        ]
    )
    dump(
        "W3-T2.json",
        {
            "trials_spent": 4,
            "two_best_features": best,
            "baseline_J": base["J_net_r_per_session"],
            "gates": rows,
            "sequence_null_T1_plus_T2": {
                "B": B_NULL,
                "block_sessions": NULL_BLOCK,
                "grid_points": len(all_masks),
                "real_best_J": round(real, 4),
                "null_best_J_q50": round(float(np.quantile(nulls, 0.5)), 4),
                "null_best_J_q95": round(float(np.quantile(nulls, 0.95)), 4),
                "p": round(float((1 + (nulls >= real).sum()) / (1 + B_NULL)), 4),
            },
        },
    )


# ---------------------------------------------------------------------------------------------
# T4 — the refit cadence. The measurement that produces the tuning protocol.
# ---------------------------------------------------------------------------------------------
QUARTERLY = 63
SEMIANNUAL = 126


def refit_schedule(
    panel: pl.DataFrame, sessions: list[date], window: int, cadence: int
) -> list[tuple[date, float]]:
    """Filter A's condition-3 constant, refit on the trailing `window` sessions every `cadence`
    sessions. Until `window` sessions have accrued the window is expanding — which is what an
    operator running this forward would actually have. Reads a feature column only: **free**."""
    out = []
    for i in range(0, len(sessions), cadence):
        hist = sessions[max(0, i - window) : i] if i else []
        if not hist:
            out.append((sessions[i], FILTER_A_CONSTANT))
            continue
        rows = panel.filter(pl.col("dt").is_in(hist))
        out.append((sessions[i], float(rows[stage0.FILTER_A_VOLUME_COL].quantile(0.5))))
    return out


def stream_under_schedule(
    panel: pl.DataFrame, sessions: list[date], schedule: list[tuple[date, float]]
) -> pl.DataFrame:
    """Re-apply Filter A session by session under a time-varying constant, then score."""
    keys = []
    for d in sessions:
        k = [c for start, c in schedule if start <= d][-1]
        day = panel.filter(pl.col("dt") == d)
        picked = stage0.filter_a(day, k)
        keys.extend(picked["key"].to_list())
    return panel.filter(pl.col("key").is_in(keys))


def cmd_t4() -> None:
    """T4 — 2 trials (amendment W3-A2). The constant's trajectory is free; the two J scores are
    the trials. Measures whether this record supports re-tuning at a cadence it can see."""
    verify()
    panel, sessions_df = load()
    fit_sessions = sorted(sessions_df.filter(pl.col("split") == "FIT")["dt"].to_list())
    all_sessions = sorted(sessions_df["dt"].to_list())
    fit_panel = panel.filter(pl.col("split") == "FIT")
    pdict = stage0.load_paths_v1()
    base = score_gated(trade_stream(panel, "FIT", ("F2",)), fit_sessions, None)

    band = (FILTER_A_CONSTANT * 0.8, FILTER_A_CONSTANT * 1.2)
    combos = [("w125_quarterly", 125, QUARTERLY), ("w250_semiannual", 250, SEMIANNUAL)]
    out: dict[str, Any] = {
        "trials_spent": 2,
        "frozen_constant": FILTER_A_CONSTANT,
        "sensitivity_band_pm20pct": [round(band[0], 2), round(band[1], 2)],
        "baseline_J": base["J_net_r_per_session"],
    }

    # Free: the trajectory over the whole record. A feature quantile reads no outcome.
    for name, window, cadence in combos:
        sched_all = refit_schedule(panel, all_sessions, window, cadence)
        vals = [c for _d, c in sched_all[1:]]
        out[f"{name}_trajectory_all_splits"] = {
            "refits": [
                {"from": str(d), "constant": round(c, 2), "in_pm20_band": band[0] <= c <= band[1]}
                for d, c in sched_all
            ],
            "min": round(min(vals), 2),
            "max": round(max(vals), 2),
            "spread_vs_frozen": round((max(vals) - min(vals)) / FILTER_A_CONSTANT, 3),
            "frac_refits_inside_pm20_band": round(
                sum(band[0] <= c <= band[1] for c in vals) / len(vals), 3
            ),
        }

    # Charged: J on FIT under each schedule.
    for name, window, cadence in combos:
        sched = refit_schedule(panel, fit_sessions, window, cadence)
        picked = stream_under_schedule(fit_panel, fit_sessions, sched)
        oc = stage0.family_outcomes(picked, pdict)
        t = (
            picked.select("key", "dt", "source")
            .join(oc.filter(pl.col("family") == "F2"), on="key")
            .filter(pl.col("net_r").is_not_null())
        )
        rep = score_gated(t, fit_sessions, None)
        out[name] = {
            "window_sessions": window,
            "cadence_sessions": cadence,
            "J": rep["J_net_r_per_session"],
            "J_minus_frozen_baseline": round(
                rep["J_net_r_per_session"] - base["J_net_r_per_session"], 4
            ),
            "trades_per_session": rep["trades_per_session"],
            "picks_differing_from_frozen": int(
                picked.height - len(set(picked["key"]) & set(filter_a_stream(fit_panel)["key"]))
            ),
            "report": rep,
        }
    dump("W3-T4.json", out)


def cmd_sensitivity() -> None:
    """§7.4 — the ±20 % band on the best T1 gate. Free, and never a selection device."""
    verify()
    panel, sessions = load()
    fit_sessions = sorted(sessions.filter(pl.col("split") == "FIT")["dt"].to_list())
    trades = trade_stream(panel, "FIT", ("F2",))
    state = _state_with_r4(panel, sessions, trades)
    fit_state = state.filter(pl.col("split") == "FIT")
    t1 = json.loads((OUT / "W3-T1.json").read_text())
    best = max(t1["gates"], key=lambda g: g["J_minus_baseline"])
    rows = []
    for mult in (0.8, 0.9, 1.0, 1.1, 1.2):
        cut = best["cut"] * mult
        keep = gate_mask(fit_state, best["feature"], cut, best["side"])
        rep = score_gated(trades, fit_sessions, keep)
        rows.append(
            {
                "mult": mult,
                "cut": round(cut, 4),
                "J": rep["J_net_r_per_session"],
                "trades_per_session": rep["trades_per_session"],
                "stood_aside": rep["sessions_stood_aside"],
            }
        )
    dump(
        "W3-sensitivity.json",
        {"gate": {k: best[k] for k in ("feature", "cut", "side", "J")}, "band": rows},
    )


def cmd_null_r4() -> None:
    """A robustness check on §7.3's null, not a trial.

    Three of W3's four features are functions of the panel alone, so holding their masks fixed
    while the outcome series is permuted is exactly the right construction. **R4 is not** — it is
    the trailing mean of the outcome series itself, so a search run against a permuted record
    would have re-derived R4, and its cuts, from the permuted outcomes. This re-runs the T1 null
    that way: R1/R2/R3's masks fixed, R4 rebuilt inside every replicate. If `p` moves, the
    headline null was measuring the wrong thing.
    """
    verify()
    panel, sessions = load()
    fit_sessions = sorted(sessions.filter(pl.col("split") == "FIT")["dt"].to_list())
    trades = trade_stream(panel, "FIT", ("F2",))
    state = _state_with_r4(panel, sessions, trades)
    fit_state = state.filter(pl.col("split") == "FIT")
    r = _session_r(trades, fit_sessions)
    n = len(fit_sessions)

    fixed = []
    for feat in ("R1_breadth", "R2_attention", "R3_vix"):
        side = STAND_ASIDE_SIDE[feat]
        for x in FRACTIONS:
            cut = fit_quantile(state, feat, x if side == "low" else 1 - x)
            keep = gate_mask(fit_state, feat, cut, side)
            fixed.append(np.array([keep.get(d, True) for d in fit_sessions], dtype=bool))

    def r4_masks(series: np.ndarray) -> list[np.ndarray]:
        """Filter A takes one trade per session, so the trailing 20 trades are the trailing 20
        sessions. Undefined during the warm-up ⇒ trade, as everywhere else."""
        trail = np.full(len(series), np.nan)
        for i in range(R4_TRADES, len(series)):
            trail[i] = series[i - R4_TRADES : i].mean()
        ok = ~np.isnan(trail)
        out = []
        for x in FRACTIONS:
            cut = float(np.quantile(trail[ok], x))
            out.append(~ok | (trail >= cut))
        return out

    real = max(float(r[m].sum()) / n for m in fixed + r4_masks(r))
    rng = np.random.default_rng(SEED)
    nulls = np.array(
        [
            max(float(r[perm][m].sum()) / n for m in fixed + r4_masks(r[perm]))
            for perm in block_permutations(n, B_NULL, NULL_BLOCK, rng)
        ]
    )
    dump(
        "W3-null-r4-refit.json",
        {
            "note": "R4 and its cuts rebuilt inside every replicate; R1/R2/R3 masks fixed",
            "B": B_NULL,
            "block_sessions": NULL_BLOCK,
            "grid_points": 12,
            "real_best_J": round(real, 4),
            "null_best_J_q50": round(float(np.quantile(nulls, 0.5)), 4),
            "null_best_J_q95": round(float(np.quantile(nulls, 0.95)), 4),
            "p": round(float((1 + (nulls >= real).sum()) / (1 + B_NULL)), 4),
        },
    )


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stage0"
    {
        "stage0": cmd_stage0,
        "spine": cmd_spine_check,
        "period": cmd_period,
        "t1": cmd_t1,
        "t2": cmd_t2,
        "t4": cmd_t4,
        "sens": cmd_sensitivity,
        "nullr4": cmd_null_r4,
    }[cmd]()


if __name__ == "__main__":
    main()
