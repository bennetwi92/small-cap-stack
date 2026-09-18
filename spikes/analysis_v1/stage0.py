"""Analysis v1 — Stage 0 (#736): the frozen panel, its redaction, and the shared reconnaissance.

Spec: `research/analysis-protocol.md` §4. This module is the **one** implementation of the
protocol's shared machinery — the v1 splits, Filter A, the four exit families (§10), the
leg-level cost model (§11) and the §6.2 report — so that W1, W2 and W3 measure with one ruler.
Import it; do not fork it.

    .venv/bin/python spikes/analysis_v1/stage0.py paths      # build paths for every panel row
    .venv/bin/python spikes/analysis_v1/stage0.py free       # S0-A/C/D/E + trigger-bar audit (free)
    .venv/bin/python spikes/analysis_v1/stage0.py costs      # S0-F  (2 trials, ledgered on #735)
    .venv/bin/python spikes/analysis_v1/stage0.py baseline   # S0-H  (4 trials, ledgered on #735)
    .venv/bin/python spikes/analysis_v1/stage0.py publish    # S0-B  redaction + artefacts + sha256

The unredacted panel and paths stay in `data/spikes/panel-v1/custodian/` and never leave the Mac.

It reuses the engine-lab primitives (`replay_bracket`, `Costs`, `Sizing`, `TRIGGER_TIME_SAFE`,
`OUTCOME_COLS`) as machinery only (protocol §1.1). It never calls `SHIPPED` or `baseline()`.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from spikes.engine_lab.common import (  # noqa: E402
    OUTCOME_COLS as HARNESS_OUTCOME_COLS,
)
from spikes.engine_lab.common import (  # noqa: E402
    TICK,
    TRIGGER_TIME_SAFE,
    Costs,
    Sizing,
    replay_bracket,
)

OUT = REPO / "data/spikes/panel-v1"
CUSTODIAN = OUT / "custodian"
RAW_PANEL = OUT / "panel-v1-full.parquet"
PATHS_FULL = CUSTODIAN / "paths-v1-full.parquet"
PUBLISH = OUT / "publish"

# ---------------------------------------------------------------------------------------------
# §7.1 — the splits. One set, all three workstreams.
# ---------------------------------------------------------------------------------------------
FIT_START = date(2024, 9, 9)
FIT_END = date(2025, 9, 30)
CHECK_END = date(2026, 3, 31)
HOLDOUT_END = date(2026, 9, 17)
PREMARKET_CUT = 570.0


def split_v1(d: date) -> str:
    if d <= FIT_END:
        return "FIT"
    if d <= CHECK_END:
        return "CHECK"
    if d <= HOLDOUT_END:
        return "HOLDOUT"
    return "OUT"


def sessions_v1() -> pl.DataFrame:
    """The session universe: every date with a `bars` partition, per store, to HOLDOUT_END.

    J's denominator. A session with no trade contributes 0 (protocol §6), so this list — not the
    dates that happen to have panel rows — is what a per-session mean divides by.
    """
    rows = []
    for source in ("recon", "live"):
        for p in sorted((REPO / f"data/{source}/bars").glob("dt=*")):
            d = date.fromisoformat(p.name[3:])
            if d <= HOLDOUT_END and any(p.glob("*.parquet")):
                rows.append((source, d, split_v1(d)))
    return pl.DataFrame(rows, schema=["source", "dt", "split"], orient="row")


# ---------------------------------------------------------------------------------------------
# Columns — S0-C / S0-D
# ---------------------------------------------------------------------------------------------
#: Protocol §4.3: nulled on HOLDOUT rows in the agents' copy.
PROTOCOL_OUTCOME_COLS = frozenset(
    {
        "max_r",
        "mae_r",
        "stopped_out",
        "stop_index",
        "bars_to_max_r",
        "max_gain_pct",
        "entry_price",
        "same_bar_stop",
        "fill_above_entry_bar_high",
    }
)
#: Constraint 1 / inventory §4: the seven day aggregates that read as context and are lookahead.
SEVEN_LOOKAHEAD = frozenset(
    {
        "day_volume",
        "day_dollar_volume",
        "day_high",
        "day_low",
        "n_scanner_hits_day",
        "first_rank",
        "run_count",
    }
)
#: Redacted on HOLDOUT: the protocol's nine, the harness's wider outcome set, and the seven. A
#: holdout `day_high` is the outcome at lower resolution; the inventory's list wins (S0-C rule).
REDACT_COLS = PROTOCOL_OUTCOME_COLS | HARNESS_OUTCOME_COLS | SEVEN_LOOKAHEAD
#: Identity and bookkeeping — populated everywhere, but a predicate on them is not a rule.
IDENTITY_COLS = frozenset({"dt", "source", "symbol", "seg_id", "key", "split", "opportunity_id"})
#: Trigger-bar-inclusive columns found by S0-C: they sum the trigger bar's FULL volume and close,
#: which the order does not have when it fires mid-bar. Strict variants are added alongside.
TRIGGER_BAR_INCLUSIVE = frozenset(
    {"cum_volume_to_trigger", "cum_dollar_vol_to_trigger", "vol_share_pole"}
)


STRICT_VARIANTS = frozenset(
    {"cum_volume_pre_trigger", "cum_dollar_vol_pre_trigger", "vol_share_pole_pre_trigger"}
)


def load_raw_panel() -> pl.DataFrame:
    """The full, unredacted v1 panel (custodian copy) with the two §4.1 cuts applied."""
    df = pl.read_parquet(RAW_PANEL)
    df = df.filter((pl.col("trigger_et_min") < PREMARKET_CUT) & pl.col("cons_has_range"))
    df = df.filter(pl.col("dt") <= HOLDOUT_END)
    df = df.with_columns(
        pl.concat_str(
            [pl.col("source"), pl.col("dt").cast(pl.Utf8), pl.col("seg_id")], separator="|"
        ).alias("key"),
        pl.col("dt").map_elements(split_v1, return_dtype=pl.Utf8).alias("split"),
    )
    if PATHS_FULL.exists():
        tb = pl.read_parquet(PATHS_FULL, columns=["key", "bar", "volume", "close"]).filter(
            pl.col("bar") == 0
        )
        tb = tb.select(
            "key",
            pl.col("volume").alias("_tb_vol"),
            (pl.col("volume") * pl.col("close")).alias("_tb_dvol"),
        )
        df = df.join(tb, on="key", how="left").with_columns(
            (pl.col("cum_volume_to_trigger") - pl.col("_tb_vol")).alias("cum_volume_pre_trigger"),
            (pl.col("cum_dollar_vol_to_trigger") - pl.col("_tb_dvol")).alias(
                "cum_dollar_vol_pre_trigger"
            ),
        )
        df = df.with_columns(
            pl.when(pl.col("cum_volume_pre_trigger") > 0)
            .then(pl.col("pole_volume") / pl.col("cum_volume_pre_trigger"))
            .alias("vol_share_pole_pre_trigger")
        )
        df = df.drop("_tb_vol", "_tb_dvol")
    return df.sort(["dt", "trigger_et_min", "symbol", "run"])


# ---------------------------------------------------------------------------------------------
# Paths — the post-trigger tape, one row per bar, `bar == 0` is the trigger bar
# ---------------------------------------------------------------------------------------------
def build_paths_full(df: pl.DataFrame) -> pl.DataFrame:
    """Same bar list the detector saw (dedupe, sort, clip 04:00–16:00 ET) — `trigger_idx` indexes
    into it — sliced from the trigger bar to the last bar of the day. Volume is kept so the
    trigger-bar audit can run; the published paths drop it to the `load_paths` OHLC shape."""
    parts: list[pl.DataFrame] = []
    for (source, d), grp in df.group_by(["source", "dt"], maintain_order=True):
        files = sorted((REPO / f"data/{source}/bars/dt={d.isoformat()}").glob("*.parquet"))
        if not files:
            continue
        bars = pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")
        bars = bars.unique(subset=["opportunity_id", "bar_start_utc"], keep="first")
        et = bars["bar_start_utc"].dt.convert_time_zone("America/New_York")
        mins = (et.dt.hour().cast(pl.Int32) * 60 + et.dt.minute().cast(pl.Int32)).alias("etmin")
        bars = bars.with_columns(mins).filter((pl.col("etmin") >= 240) & (pl.col("etmin") < 960))
        bars = bars.sort(["opportunity_id", "bar_start_utc"]).with_columns(
            pl.int_range(pl.len()).over("opportunity_id").alias("idx")
        )
        keys = grp.select("key", "opportunity_id", "trigger_idx")
        j = bars.join(keys, on="opportunity_id").filter(pl.col("idx") >= pl.col("trigger_idx"))
        parts.append(
            j.select(
                "key",
                (pl.col("idx") - pl.col("trigger_idx")).cast(pl.Int32).alias("bar"),
                "etmin",
                "open",
                "high",
                "low",
                "close",
                "volume",
            )
        )
    return pl.concat(parts).sort(["key", "bar"])


def paths_dict(paths: pl.DataFrame) -> dict[str, np.ndarray]:
    """`{key: (n_bars, 4) OHLC}` — exactly the `engine_lab.load_paths` format."""
    out: dict[str, np.ndarray] = {}
    for (k,), g in paths.group_by(["key"]):
        out[str(k)] = g.sort("bar").select(["open", "high", "low", "close"]).to_numpy()
    return out


# ---------------------------------------------------------------------------------------------
# Agents' loaders — the published, redacted artefacts (verify the sha256 against the spec first)
# ---------------------------------------------------------------------------------------------
def load_panel_v1(path: Path = PUBLISH / "panel-v1.parquet") -> pl.DataFrame:
    """The frozen panel as the workstreams hold it: HOLDOUT outcomes null, features intact."""
    return pl.read_parquet(path).sort(["dt", "trigger_et_min", "symbol", "run"])


def load_paths_v1(path: Path = PUBLISH / "paths-v1" / "paths-v1.parquet") -> dict[str, np.ndarray]:
    """FIT + CHECK post-trigger paths in the `engine_lab.load_paths` format. No HOLDOUT key."""
    return paths_dict(pl.read_parquet(path))


def load_sessions_v1(path: Path = PUBLISH / "sessions-v1.parquet") -> pl.DataFrame:
    """The session universe (J's denominator), with its split."""
    return pl.read_parquet(path)


# ---------------------------------------------------------------------------------------------
# §10 — the four exit families. Each returns the exit legs so the cost model can price them.
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Leg:
    frac: float  # fraction of the position closed
    price: float
    limit: bool  # a resting limit fills at its price; anything else slips
    r: float


def _entry(path: np.ndarray, entry_fill: float) -> float:
    return max(entry_fill, float(path[0, 0]))


def exit_fixed(path: np.ndarray, entry_fill: float, stop: float, target_r: float) -> list[Leg]:
    """F1 (0.5 R) / F2 (2 R): the harness bracket, stop first on every bar, marked to 16:00."""
    res = replay_bracket(path, entry_fill, stop, target_r=target_r)
    if not res["valid"]:
        return []
    hit_target = not res["stopped"] and abs(res["r"] - target_r) < 1e-9
    return [Leg(1.0, float(res["exit_price"]), hit_target, float(res["r"]))]


def exit_runner(path: np.ndarray, entry_fill: float, stop: float, arm_r: float = 1.0) -> list[Leg]:
    """F3: no target. Once the high has been ≥ `arm_r` in favour, exit at the close of the first
    bar that closes below the prior bar's low. Stop at the consolidation low, checked first on
    every bar. Unresolved → marked to the last visible bar's close."""
    h, lo, cl = path[:, 1], path[:, 2], path[:, 3]
    entry = _entry(path, entry_fill)
    risk = entry - stop
    if risk <= 0:
        return []
    max_high = -math.inf
    for k in range(len(path)):
        if lo[k] <= stop:
            return [Leg(1.0, stop, False, -1.0)]
        max_high = max(max_high, float(h[k]))
        armed = (max_high - entry) / risk >= arm_r
        if armed and k >= 1 and cl[k] < lo[k - 1]:
            return [Leg(1.0, float(cl[k]), False, (float(cl[k]) - entry) / risk)]
    return [Leg(1.0, float(cl[-1]), False, (float(cl[-1]) - entry) / risk)]


def exit_hybrid(path: np.ndarray, entry_fill: float, stop: float) -> list[Leg]:
    """F4: half off at 1 R (limit), stop to break-even on the remainder, remainder on F3's trail.

    Same-bar conventions, both conservative: the original stop is checked before the 1 R target;
    on the bar the target fills, a low at or below entry stops the remainder at break-even."""
    h, lo, cl = path[:, 1], path[:, 2], path[:, 3]
    entry = _entry(path, entry_fill)
    risk = entry - stop
    if risk <= 0:
        return []
    tgt = entry + risk
    for k in range(len(path)):
        if lo[k] <= stop:
            return [Leg(1.0, stop, False, -1.0)]
        if h[k] >= tgt:
            half = Leg(0.5, tgt, True, 1.0)
            for m in range(k, len(path)):
                if lo[m] <= entry:
                    return [half, Leg(0.5, entry, False, 0.0)]
                if m >= 1 and cl[m] < lo[m - 1]:
                    return [half, Leg(0.5, float(cl[m]), False, (float(cl[m]) - entry) / risk)]
            return [half, Leg(0.5, float(cl[-1]), False, (float(cl[-1]) - entry) / risk)]
    return [Leg(1.0, float(cl[-1]), False, (float(cl[-1]) - entry) / risk)]


FAMILIES = {
    "F1": lambda p, e, s: exit_fixed(p, e, s, 0.5),
    "F2": lambda p, e, s: exit_fixed(p, e, s, 2.0),
    "F3": lambda p, e, s: exit_runner(p, e, s, 1.0),
    "F4": exit_hybrid,
}

# ---------------------------------------------------------------------------------------------
# §11 — the cost model, per leg. Matches `Costs.usd` exactly for a one-leg limit/stop exit.
# ---------------------------------------------------------------------------------------------
#: Primary sizing (S0-F/S0-H, operator decision 2026-09-18): full buying power of a $500 cash
#: account — qty = floor(500 / entry). Expressed through the harness class, uncapped by risk.
FULL_BP = Sizing(equity=500.0, risk_fraction=1.0e9, position_fraction=1.0)
HARNESS_SIZING = Sizing()


def leg_costs(
    qty: int, legs: list[Leg], costs: Costs, slip_ticks: float | None = None
) -> tuple[float, float]:
    """(fees, slippage) in dollars for one entry order plus one order per exit leg.

    Slippage is charged on every exit that is not a resting limit — a stop-out, a trailing exit or
    the 16:00 mark — at `slip_ticks` per share. ⚠️ That is an assumption, not a measurement: no
    real fill exists yet (Phase 1 places no orders). S0-F reports 0 / 2 / 4 ticks for this reason.
    """
    ticks = costs.stop_slip_ticks if slip_ticks is None else slip_ticks
    fees = max(costs.comm_min, qty * costs.comm_per_share) + qty * (costs.exchange + costs.clearing)
    slip = 0.0
    remaining = qty
    for i, leg in enumerate(legs):
        n = remaining if i == len(legs) - 1 else int(math.floor(qty * leg.frac))
        remaining -= n
        if n <= 0:
            continue
        fees += max(costs.comm_min, n * costs.comm_per_share) + n * (
            costs.exchange + costs.clearing
        )
        fees += min(n * costs.taf_per_share, costs.taf_max) + n * leg.price * costs.sec_rate
        if not leg.limit:
            slip += n * ticks * TICK
    return fees, slip


def price_trade(
    entry_fill: float,
    stop: float,
    open0: float,
    legs: list[Leg],
    *,
    sizing: Sizing = FULL_BP,
    costs: Costs | None = None,
    slip_ticks: float | None = None,
) -> dict[str, Any]:
    costs = Costs() if costs is None else costs
    entry = max(entry_fill, open0)
    qty, sized_by = sizing.qty(entry, stop)
    gross_r = sum(leg.frac * leg.r for leg in legs)
    if qty < 1 or not legs:
        return {"qty": 0, "sized_by": "unaffordable", "gross_r": gross_r, "net_r": None}
    risk_usd = qty * (entry - stop)
    fees, slip = leg_costs(qty, legs, costs, slip_ticks)
    return {
        "qty": qty,
        "sized_by": sized_by,
        "risk_usd": risk_usd,
        "gross_r": gross_r,
        "cost_r": (fees + slip) / risk_usd,
        "net_r": gross_r - (fees + slip) / risk_usd,
        "net_usd": gross_r * risk_usd - fees - slip,
    }


# ---------------------------------------------------------------------------------------------
# §9 — Filter A. Condition 3's constant is frozen on FIT and published in the spec.
# ---------------------------------------------------------------------------------------------
#: §9 condition 3's column. ⚠️ Amended at S0-C (#735, operator decision 2026-09-18): the protocol
#: names `cum_dollar_vol_to_trigger`, which includes the trigger bar's full volume and close — not
#: known when the order fires mid-bar. The strict variant sums bars strictly before the trigger.
FILTER_A_VOLUME_COL = "cum_dollar_vol_pre_trigger"


def filter_a_constant(df: pl.DataFrame, col: str = FILTER_A_VOLUME_COL, q: float = 0.5) -> float:
    """The q-quantile of `col` over FIT panel rows. Stage 0 computes it once and freezes it."""
    fit = df.filter(pl.col("split") == "FIT")
    return float(fit[col].quantile(q, interpolation="linear"))


def filter_a(df: pl.DataFrame, constant: float, col: str = FILTER_A_VOLUME_COL) -> pl.DataFrame:
    """Conditions 1–4: pre-market (already cut), ≥1 hit before trigger, dollar volume ≥ the frozen
    constant, then the earliest trigger per session — by time, never by rank."""
    d = df.filter((pl.col("hits_before_trigger") >= 1) & (pl.col(col) >= constant))
    d = d.sort(["dt", "trigger_et_min", "symbol", "run"])
    return d.with_columns(pl.int_range(pl.len()).over("dt").alias("_seq")).filter(
        pl.col("_seq") == 0
    )


# ---------------------------------------------------------------------------------------------
# §6.2 — the report every scored block carries
# ---------------------------------------------------------------------------------------------
def session_report(
    trades: pl.DataFrame, sessions: list[date], *, block_len: int = 20
) -> dict[str, Any]:
    """`trades` needs `dt, net_r, gross_r, net_usd`. Sessions with no trade contribute 0."""
    per = dict.fromkeys(sessions, 0.0)
    per_g = dict.fromkeys(sessions, 0.0)
    for r in trades.iter_rows(named=True):
        per[r["dt"]] += r["net_r"]
        per_g[r["dt"]] += r["gross_r"]
    order = sorted(per)
    v = np.array([per[d] for d in order])
    g = np.array([per_g[d] for d in order])
    cum = np.cumsum(v)
    dd = float((cum - np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]).min())
    blocks = [v[i : i + block_len].sum() for i in range(0, len(v) - block_len + 1, block_len)]
    run = best = 0
    for b in blocks:
        run = run + 1 if b < 0 else 0
        best = max(best, run)
    total = float(v.sum())
    top = np.sort(v)[::-1][: max(1, len(v) // 10)]
    nr = trades["net_r"].to_numpy() if trades.height else np.array([])
    eq = 500.0 + np.cumsum(trades.sort("dt")["net_usd"].to_numpy()) if trades.height else [500.0]
    peak = np.maximum.accumulate(np.concatenate([[500.0], eq]))
    return {
        "sessions": len(v),
        "trades": trades.height,
        "trades_per_session": round(trades.height / len(v), 3) if len(v) else 0.0,
        "J_net_r_per_session": round(total / len(v), 4) if len(v) else 0.0,
        "gross_r_per_session": round(float(g.sum()) / len(v), 4) if len(v) else 0.0,
        "session_net_r_deciles": [
            round(float(x), 3) for x in np.quantile(v, np.linspace(0, 1, 11))
        ],
        "best_decile_share_of_total": round(float(top.sum()) / total, 3) if total else None,
        "longest_losing_20_session_run": best,
        "blocks_of_20": len(blocks),
        "deepest_drawdown_r": round(dd, 3),
        "net_r_per_trade_mean": round(float(nr.mean()), 4) if len(nr) else None,
        "net_r_per_trade_sd": round(float(nr.std(ddof=1)), 4) if len(nr) > 1 else None,
        "end_equity_usd": round(float(eq[-1]), 2),
        "max_drawdown_pct": round(
            float(((np.concatenate([[500.0], eq]) - peak) / peak).min() * 100), 2
        ),
    }


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dump(name: str, obj: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=2, default=str))
    print(json.dumps(obj, indent=2, default=str))


# ---------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------
def cmd_paths() -> None:
    df = load_raw_panel()
    paths = build_paths_full(df)
    CUSTODIAN.mkdir(parents=True, exist_ok=True)
    paths.write_parquet(PATHS_FULL)
    have = set(paths["key"].unique())
    # Integrity: the paths must be the detector's tape. `replay_bracket(target=None)` reproduces
    # the row's own max_r iff the bar list lines up with `trigger_idx`. A data check, not a trial:
    # nothing is scored and nothing is selected.
    pdict = paths_dict(paths)
    bad = sum(
        1
        for r in df.iter_rows(named=True)
        if r["key"] in pdict
        and abs(
            replay_bracket(pdict[r["key"]], r["entry_fill"], r["stop"], target_r=None)["max_r"]
            - r["max_r"]
        )
        > 0.02
    )
    dump(
        "paths-check.json",
        {
            "panel_rows": df.height,
            "rows_with_path": len(have),
            "rows_without_path": df.height - len(have),
            "path_bars": paths.height,
            "max_r_reproduction_mismatches": bad,
        },
    )


def _num_summary(s: pl.Series) -> dict[str, Any]:
    s = s.drop_nulls()
    if s.is_empty():
        return {"n": 0}
    if s.dtype == pl.Boolean:
        return {"n": s.len(), "mean": round(float(s.cast(pl.Float64).mean()), 4)}
    if s.dtype == pl.Utf8:
        return {"n": s.len(), "distinct": s.n_unique()}
    q = [float(s.quantile(x, interpolation="linear")) for x in (0.1, 0.5, 0.9)]
    return {"n": s.len(), "q10": round(q[0], 4), "q50": round(q[1], 4), "q90": round(q[2], 4)}


def cmd_free() -> None:
    df = load_raw_panel()
    sess = sessions_v1()
    out: dict[str, Any] = {}

    # S0-A — realised counts.
    out["S0-A"] = {
        "panel_rows": df.height,
        "rows": df.group_by("split", "source")
        .agg(pl.len().alias("rows"), pl.col("dt").n_unique().alias("sessions_with_rows"))
        .sort("split", "source")
        .to_dicts(),
        "sessions": sess.group_by("split", "source")
        .agg(pl.len().alias("sessions"))
        .sort("split", "source")
        .to_dicts(),
        "dropped_premarket_but_no_cons_range": pl.read_parquet(RAW_PANEL)
        .filter((pl.col("trigger_et_min") < PREMARKET_CUT) & ~pl.col("cons_has_range"))
        .height,
    }
    fit_dates = sorted(sess.filter(pl.col("split") == "FIT")["dt"].to_list())
    out["S0-A"]["walk_forward_folds"] = [
        {
            "train": f"{fit_dates[0]}..{fit_dates[k - 1]}",
            "test": f"{fit_dates[k]}..{fit_dates[k + 49]}",
        }
        for k in (95, 145, 195)
    ]

    # S0-C — the whitelist against the seven, the redaction set, and the panel.
    safe_in_panel = sorted(TRIGGER_TIME_SAFE & set(df.columns))
    out["S0-C"] = {
        "whitelist_size": len(TRIGGER_TIME_SAFE),
        "seven_inside_whitelist": sorted(TRIGGER_TIME_SAFE & SEVEN_LOOKAHEAD),
        "redact_cols_inside_whitelist": sorted(TRIGGER_TIME_SAFE & REDACT_COLS),
        "whitelist_cols_missing_from_panel": sorted(TRIGGER_TIME_SAFE - set(df.columns)),
        "trigger_bar_inclusive": sorted(TRIGGER_BAR_INCLUSIVE),
    }
    # How much of the protocol's volume column is the trigger bar itself?
    share = df.filter(pl.col("cum_dollar_vol_to_trigger") > 0).select(
        (1 - pl.col("cum_dollar_vol_pre_trigger") / pl.col("cum_dollar_vol_to_trigger")).alias("s"),
        "split",
    )
    out["S0-C"]["trigger_bar_share_of_cum_dollar_vol"] = {
        sp: _num_summary(share.filter(pl.col("split") == sp)["s"])
        for sp in ("FIT", "CHECK", "HOLDOUT")
    }
    k_inc = filter_a_constant(df, "cum_dollar_vol_to_trigger")
    k_pre = filter_a_constant(df, "cum_dollar_vol_pre_trigger")
    fit = df.filter(pl.col("split") == "FIT")
    a = fit["cum_dollar_vol_to_trigger"] >= k_inc
    b = fit["cum_dollar_vol_pre_trigger"] >= k_pre
    out["S0-C"]["filter_a_cond3_agreement_FIT"] = {
        "inclusive_q50": k_inc,
        "pre_trigger_q50": k_pre,
        "agree": round(float((a == b).mean()), 4),
    }
    fa_inc = filter_a(df, k_inc, "cum_dollar_vol_to_trigger")
    fa_pre = filter_a(df, k_pre, "cum_dollar_vol_pre_trigger")
    out["S0-C"]["filter_a_stream_same_trade_share"] = round(
        len(set(fa_inc["key"]) & set(fa_pre["key"])) / max(1, fa_inc.height), 4
    )

    # S0-D — coverage, nullity, stationarity per split and per holdout half.
    candidates = sorted((set(safe_in_panel) | STRICT_VARIANTS) - IDENTITY_COLS - REDACT_COLS)
    groups = {
        "FIT": df.filter(pl.col("split") == "FIT"),
        "CHECK": df.filter(pl.col("split") == "CHECK"),
        "HOLDOUT_recon": df.filter((pl.col("split") == "HOLDOUT") & (pl.col("source") == "recon")),
        "HOLDOUT_live": df.filter((pl.col("split") == "HOLDOUT") & (pl.col("source") == "live")),
    }
    cols: dict[str, Any] = {}
    dropped = []
    for c in candidates:
        nulls = {g: round(float(d[c].null_count() / d.height), 4) for g, d in groups.items()}
        if any(nulls[g] > 0.20 for g in ("FIT", "HOLDOUT_recon", "HOLDOUT_live")):
            dropped.append(c)
        cols[c] = {"null_frac": nulls, **{g: _num_summary(d[c]) for g, d in groups.items()}}
    out["S0-D"] = {"columns": cols, "dropped_over_20pct_null": dropped, "candidates": candidates}

    # Filter A throughput, at the frozen q50 and the q40/q60 sensitivity band.
    thr = {}
    for q in (0.4, 0.5, 0.6):
        k = filter_a_constant(df, q=q)
        fa = filter_a(df, k)
        thr[f"q{int(q * 100)}"] = {
            "constant": k,
            **{
                sp: round(
                    fa.filter(pl.col("split") == sp).height
                    / sess.filter(pl.col("split") == sp).height,
                    3,
                )
                for sp in ("FIT", "CHECK", "HOLDOUT")
            },
        }
    out["S0-D"]["filter_a_throughput"] = thr

    # S0-E — recon session window and bars_1m coverage.
    paths = pl.read_parquet(PATHS_FULL, columns=["key", "bar", "etmin"])
    last = paths.group_by("key").agg(pl.col("etmin").max().alias("last_etmin"))
    rows = df.select("key", "split", "source").join(last, on="key", how="left")
    recon_sessions = sess.filter(pl.col("source") == "recon")["dt"].to_list()
    sess_max = []
    for d in recon_sessions:
        files = sorted((REPO / f"data/recon/bars/dt={d.isoformat()}").glob("*.parquet"))
        t = pl.concat([pl.read_parquet(f, columns=["bar_start_utc"]) for f in files])
        et = t["bar_start_utc"].dt.convert_time_zone("America/New_York")
        sess_max.append(int((et.dt.hour().cast(pl.Int32) * 60 + et.dt.minute()).max()))
    m1 = []
    for d in recon_sessions:
        files = sorted((REPO / f"data/recon/bars_1m/dt={d.isoformat()}").glob("*.parquet"))
        n = sum(pl.scan_parquet(f).select(pl.len()).collect().item() for f in files)
        m1.append(n)
    covered = [n > 0 for n in m1]
    out["S0-E"] = {
        "recon_sessions": len(recon_sessions),
        "recon_sessions_reaching_1555": int(sum(x >= 955 for x in sess_max)),
        "share_reaching_1555": round(sum(x >= 955 for x in sess_max) / len(sess_max), 4),
        "panel_rows_path_reaching_1555": rows.group_by("split", "source")
        .agg((pl.col("last_etmin") >= 955).mean().round(4).alias("share"), pl.len().alias("rows"))
        .sort("split", "source")
        .to_dicts(),
        "bars_1m_sessions_covered": int(sum(covered)),
        "bars_1m_share": round(sum(covered) / len(covered), 4),
        "bars_1m_rows_total": int(sum(m1)),
    }
    dump("stage0-free.json", out)


def rule_columns() -> list[str]:
    """The frozen RULE_COLUMNS: the S0-D candidates less the >20 %-null drops and less the
    trigger-bar-inclusive columns (S0-C). A candidate predicate sees only these (§5.1)."""
    free = json.loads((OUT / "stage0-free.json").read_text())
    drop = set(free["S0-D"]["dropped_over_20pct_null"]) | TRIGGER_BAR_INCLUSIVE
    return sorted(set(free["S0-D"]["candidates"]) - drop)


def run_rule(df: pl.DataFrame, predicate: Any) -> pl.DataFrame:
    """§5.1 layer 1: the predicate is handed a frame that physically lacks every other column.
    It must return a boolean Series; the rows it keeps come back with their full columns."""
    cols = rule_columns()
    mask = predicate(df.select(cols))
    return df.filter(mask)


def folds_v1() -> list[dict[str, Any]]:
    """§7.2: expanding train on FIT sessions 1..k, test k+1..k+50, k = 95 / 145 / 195."""
    fit = sorted(sessions_v1().filter(pl.col("split") == "FIT")["dt"].to_list())
    return [{"train": fit[:k], "test": fit[k : k + 50]} for k in (95, 145, 195)]


# ---------------------------------------------------------------------------------------------
# Outcomes per row, per family (custodian: every split; agents: FIT + CHECK only)
# ---------------------------------------------------------------------------------------------
def family_outcomes(
    df: pl.DataFrame,
    pdict: dict[str, np.ndarray],
    *,
    sizing: Sizing = FULL_BP,
    slip_ticks: float | None = None,
) -> pl.DataFrame:
    rows = []
    for r in df.iter_rows(named=True):
        path = pdict.get(r["key"])
        if path is None:
            continue
        for fam, fn in FAMILIES.items():
            legs = fn(path, r["entry_fill"], r["stop"])
            pr = price_trade(
                r["entry_fill"],
                r["stop"],
                float(path[0, 0]),
                legs,
                sizing=sizing,
                slip_ticks=slip_ticks,
            )
            rows.append(
                {
                    "key": r["key"],
                    "family": fam,
                    "gross_r": pr["gross_r"],
                    "net_r": pr["net_r"],
                    "net_usd": pr.get("net_usd"),
                    "cost_r": pr.get("cost_r"),
                    "sized_by": pr["sized_by"],
                    "n_legs": len(legs),
                }
            )
    return pl.DataFrame(rows)


# ---------------------------------------------------------------------------------------------
# S0-F — the cost audit. Reads entry_fill and stop only: no outcome column, no post-trigger price.
# ---------------------------------------------------------------------------------------------
def _cost_rows(df: pl.DataFrame, sizing: Sizing, ticks: float) -> pl.DataFrame:
    costs = Costs()
    rows = []
    for r in df.iter_rows(named=True):
        e, s = float(r["entry_fill"]), float(r["stop"])
        qty, _ = sizing.qty(e, s)
        if qty < 1:
            rows.append({"key": r["key"], "stop_pct": r["stop_pct"], "affordable": False})
            continue
        risk = qty * (e - s)

        def c(legs: list[Leg], qty: int = qty, risk: float = risk) -> float:
            f, sl = leg_costs(qty, legs, costs, ticks)
            return (f + sl) / risk

        rows.append(
            {
                "key": r["key"],
                "stop_pct": r["stop_pct"],
                "price": e,
                "affordable": True,
                "risk_usd": risk,
                "c_loss": c([Leg(1.0, s, False, -1.0)]),
                "c_win_F1": c([Leg(1.0, e + 0.5 * (e - s), True, 0.5)]),
                "c_win_F2": c([Leg(1.0, e + 2.0 * (e - s), True, 2.0)]),
                "c_trail_at_entry": c([Leg(1.0, e, False, 0.0)]),
                "c_F4_half_1R_half_BE": c(
                    [Leg(0.5, e + (e - s), True, 1.0), Leg(0.5, e, False, 0.0)]
                ),
            }
        )
    return pl.DataFrame(rows)


def _breakeven(cl: float, cw: float, t: float) -> float:
    """Net break-even hit rate for a fixed target T with per-trade costs c_l (loser), c_w (winner):
    p(T - c_w) = (1 - p)(1 + c_l)."""
    return (1 + cl) / (1 + t + cl - cw)


def _cost_table(c: pl.DataFrame, edges: list[float]) -> dict[str, Any]:
    ok = c.filter(pl.col("affordable"))
    ok = ok.with_columns(
        pl.col("stop_pct").cut(edges[1:-1], labels=[f"D{i}" for i in range(1, 11)]).alias("decile")
    )

    def summ(d: pl.DataFrame) -> dict[str, Any]:
        m = {
            k: float(d[k].mean())
            for k in ("c_loss", "c_win_F1", "c_win_F2", "c_trail_at_entry", "c_F4_half_1R_half_BE")
        }
        return {
            "rows": d.height,
            "stop_pct_median": round(float(d["stop_pct"].median()), 4),
            "c_loss_mean": round(m["c_loss"], 4),
            "c_loss_q10_q50_q90": [
                round(float(d["c_loss"].quantile(q)), 4) for q in (0.1, 0.5, 0.9)
            ],
            "c_win_F1_mean": round(m["c_win_F1"], 4),
            "c_win_F2_mean": round(m["c_win_F2"], 4),
            "c_trail_mean": round(m["c_trail_at_entry"], 4),
            "c_F4_mean": round(m["c_F4_half_1R_half_BE"], 4),
            "breakeven_F1": round(_breakeven(m["c_loss"], m["c_win_F1"], 0.5), 4),
            "breakeven_F2": round(_breakeven(m["c_loss"], m["c_win_F2"], 2.0), 4),
        }

    return {
        "all": summ(ok),
        "unaffordable": c.height - ok.height,
        "by_stop_pct_decile": {
            str(k[0]): summ(g)
            for k, g in ok.sort("stop_pct").group_by(["decile"], maintain_order=True)
        },
    }


def cmd_costs() -> None:
    df = load_raw_panel()
    fit = df.filter(pl.col("split") == "FIT")
    edges = [float(x) for x in np.quantile(fit["stop_pct"].to_numpy(), np.linspace(0, 1, 11))]
    edges[0], edges[-1] = -math.inf, math.inf
    fa = filter_a(fit, filter_a_constant(df))
    out: dict[str, Any] = {"stop_pct_decile_edges_FIT": edges[1:-1]}
    for sz_name, sz in (("full_bp", FULL_BP), ("harness_5pct_50pct", HARNESS_SIZING)):
        for ticks in (0.0, 2.0, 4.0):
            tag = f"{sz_name}__slip{int(ticks)}"
            out[f"FIT_panel__{tag}"] = _cost_table(_cost_rows(fit, sz, ticks), edges)
            out[f"FIT_filterA__{tag}"] = _cost_table(_cost_rows(fa, sz, ticks), edges)["all"]
    gate = out["FIT_panel__full_bp__slip2"]["all"]["breakeven_F1"]
    out["GATE"] = {
        "rule": "F1 net break-even, FIT panel, full-BP sizing, 2-tick slip > 0.80 -> cancel F1",
        "F1_breakeven": gate,
        "F1_cancelled": gate > 0.80,
    }
    dump("S0-F.json", out)


# ---------------------------------------------------------------------------------------------
# S0-H — outcome dispersion and the Filter-A baseline, FIT only, per family
# ---------------------------------------------------------------------------------------------
B_NULL = 200


def cmd_baseline() -> None:
    df = load_raw_panel()
    fit = df.filter(pl.col("split") == "FIT")
    sess = sorted(sessions_v1().filter(pl.col("split") == "FIT")["dt"].to_list())
    paths = pl.read_parquet(PATHS_FULL).filter(pl.col("key").is_in(fit["key"].implode()))
    pdict = paths_dict(paths)
    k = filter_a_constant(df)
    fa_keys = set(filter_a(fit, k)["key"])
    oc = family_outcomes(fit, pdict)
    base = fit.select("key", "dt", "source", "stop_pct")
    rng = np.random.default_rng(20260918)
    out: dict[str, Any] = {"filter_a_constant": k, "fit_sessions": len(sess)}
    for fam in FAMILIES:
        o = base.join(oc.filter(pl.col("family") == fam), on="key")
        taken = o.filter(pl.col("key").is_in(list(fa_keys)) & pl.col("net_r").is_not_null())
        rep = session_report(taken, sess)
        rep["unaffordable"] = int(
            o.filter(pl.col("key").is_in(list(fa_keys)) & pl.col("net_r").is_null()).height
        )
        rep["turned_away_by_capacity"] = int(
            fit.filter(
                (pl.col("hits_before_trigger") >= 1) & (pl.col(FILTER_A_VOLUME_COL) >= k)
            ).height
            - len(fa_keys)
        )
        rep["recon_only"] = "FIT is recon-only; the live half is entirely in HOLDOUT"
        rep["stop_pct_of_taken"] = _num_summary(taken["stop_pct"])
        # Block-by-session permutation null: permute which of a session's setups got which outcome,
        # keeping every session's trigger times and trade count. Filter A's picks are fixed; their
        # outcomes are drawn from their own session's pool. p = P(null J >= real J).
        ok = o.filter(pl.col("net_r").is_not_null())
        by_s = {d[0]: g["net_r"].to_numpy() for d, g in ok.group_by(["dt"])}
        fa_dates = taken["dt"].to_list()
        real_j = rep["J_net_r_per_session"]
        null_j = np.array(
            [sum(float(rng.choice(by_s[d])) for d in fa_dates) / len(sess) for _ in range(B_NULL)]
        )
        rep["perm_null"] = {
            "B": B_NULL,
            "null_J_q50_q95": [round(float(np.quantile(null_j, q)), 4) for q in (0.5, 0.95)],
            "p": round(float((1 + (null_j >= real_j).sum()) / (1 + B_NULL)), 4),
        }
        out[fam] = rep
    dump("S0-H.json", out)


# ---------------------------------------------------------------------------------------------
# S0-G — detector-geometry agreement on the 167 reviews, through the redacting loader
# ---------------------------------------------------------------------------------------------
REVIEW_DROP = frozenset({"note", "max_r"})


def load_reviews_redacted(ref: str = "origin/review-data") -> list[dict[str, Any]]:
    """`note` and `annotations.max_r` are dropped by the JSON object hook as each object is parsed
    — they never exist in a Python object this module holds."""
    import subprocess

    names = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref, "reviews"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    ).stdout.split()
    out = []
    for n in names:
        raw = subprocess.run(
            ["git", "show", f"{ref}:{n}"], capture_output=True, text=True, check=True, cwd=REPO
        ).stdout
        rec = json.loads(
            raw, object_hook=lambda o: {k: v for k, v in o.items() if k not in REVIEW_DROP}
        )
        del raw
        tail = Path(n).stem.rsplit("_", 1)[1]
        # Legacy reviews (no run suffix) were written per opportunity; they are matched to run 1.
        rec["_run"] = int(tail) if tail.isdigit() else 1
        rec["_legacy"] = not tail.isdigit()
        out.append(rec)
    return out


def cmd_geometry() -> None:
    df = load_raw_panel().filter(pl.col("source") == "live")
    revs = load_reviews_redacted()
    idx = {(str(r["dt"]), r["symbol"], int(r["run"])): r for r in df.iter_rows(named=True)}
    n = matched = no_trig = low_ok = time_ok = both = 0
    unit = None
    for rv in revs:
        n += 1
        if rv.get("no_trigger"):
            no_trig += 1
            continue
        row = idx.get((rv["trading_date"], rv["symbol"], rv["_run"]))
        a = rv.get("annotations") or {}
        cons = a.get("consolidation")
        if row is None or not cons:
            continue
        matched += 1
        t1 = cons["t1"]
        unit = unit or (1000 if t1 > 1e11 else 1)
        cons_end = t1 / unit
        trig = row["trigger_utc"].timestamp()
        lo_agree = abs(row["stop"] - cons["low"]) / cons["low"] <= 0.02
        t_agree = cons["t0"] / unit <= trig <= cons_end + 30 * 60
        low_ok += lo_agree
        time_ok += t_agree
        both += lo_agree and t_agree
    dump(
        "S0-G.json",
        {
            "reviews": n,
            "no_trigger_reviews": no_trig,
            "matched_to_premarket_panel_row": matched,
            "cons_low_within_2pct": low_ok,
            "trigger_within_cons_window_plus_30min": time_ok,
            "agreement_both": both,
            "agreement_rate": round(both / matched, 4) if matched else None,
            "legacy_reviews_matched_as_run_1": sum(1 for r in revs if r["_legacy"]),
            "timestamp_unit_divisor": unit,
            "caveats": [
                "the trader chose which days to review, and that choice is outcome-informed: this "
                "is conditional on being reviewed and is not a population statistic",
                "the residual leak is not zero - human geometry taste correlates with what "
                "happened next. No workstream may condition a rule on a review.",
            ],
        },
    )


def cmd_power() -> None:
    """S0-J: §7.2's table from S0-H's measured sd. MDE = (z_alpha + z_0.80) * sd / sqrt(n), with
    n = 0.8 trades x realised sessions per block (the throughput target, as §7.2 assumed)."""
    from statistics import NormalDist

    z = NormalDist().inv_cdf
    h = json.loads((OUT / "S0-H.json").read_text())
    sess = sessions_v1().group_by("split").agg(pl.len()).to_dict(as_series=False)
    n = {sp: round(0.8 * c) for sp, c in zip(sess["split"], sess["len"], strict=True)}
    zp = z(0.80)
    cols = {
        "FIT_1": ("FIT", z(1 - 0.05 / 2)),
        "FIT_W3_24": ("FIT", z(1 - 0.05 / 2 / 24)),
        "FIT_W2_36": ("FIT", z(1 - 0.05 / 2 / 36)),
        "FIT_W1_54": ("FIT", z(1 - 0.05 / 2 / 54)),
        "FIT_120": ("FIT", z(1 - 0.05 / 2 / 120)),
        "CHECK_3": ("CHECK", z(1 - 0.05 / 2 / 3)),
        "HOLDOUT_3_one_sided_0.0167": ("HOLDOUT", z(1 - 0.0167)),
    }
    table = {}
    for fam in FAMILIES:
        sd = h[fam]["net_r_per_trade_sd"]
        row = {"sd": sd}
        for name, (sp, za) in cols.items():
            row[name] = round((za + zp) * sd / math.sqrt(n[sp]), 3)
        row["under_powered_at_0.6"] = sorted(k for k, v in row.items() if k != "sd" and v > 0.6)
        table[fam] = row
    dump("S0-J.json", {"trades_assumed": n, "table": table})


HOLDOUT_START = date(2026, 4, 1)


def cmd_publish() -> None:
    """S0-B: redact HOLDOUT outcomes, drop HOLDOUT paths, verify both row for row, hash."""
    df = load_raw_panel()
    redact = sorted(REDACT_COLS & set(df.columns))
    is_h = pl.col("split") == "HOLDOUT"
    agents = df.with_columns(
        [pl.when(is_h).then(None).otherwise(pl.col(c)).alias(c) for c in redact]
    )
    paths = pl.read_parquet(PATHS_FULL)
    keep = agents.filter(~is_h)["key"].implode()
    agent_paths = paths.filter(pl.col("key").is_in(keep)).drop("volume")

    # Verification — nothing ships unless every check holds.
    h = agents.filter(is_h)
    assert h["dt"].min() >= HOLDOUT_START and h["dt"].max() <= HOLDOUT_END
    assert agents.filter(~is_h)["dt"].max() < HOLDOUT_START
    assert all(h[c].null_count() == h.height for c in redact), "holdout outcome survived"
    assert all(
        agents.filter(pl.col("dt") >= HOLDOUT_START)[c].null_count()
        == agents.filter(pl.col("dt") >= HOLDOUT_START).height
        for c in redact
    )
    hkeys = set(h["key"])
    assert not (set(agent_paths["key"].unique()) & hkeys), "holdout path survived"
    assert set(agent_paths["key"].unique()) == set(agents.filter(~is_h)["key"])
    feats = [c for c in df.columns if c not in redact]
    assert agents.select(feats).equals(df.select(feats)), "a feature column changed"

    PUBLISH.mkdir(parents=True, exist_ok=True)
    (PUBLISH / "paths-v1").mkdir(exist_ok=True)
    agents.write_parquet(PUBLISH / "panel-v1.parquet")
    agent_paths.write_parquet(PUBLISH / "paths-v1" / "paths-v1.parquet")
    sessions_v1().write_parquet(PUBLISH / "sessions-v1.parquet")
    df.write_parquet(CUSTODIAN / "panel-v1-custodian.parquet")
    files = {
        "panel-v1.parquet": PUBLISH / "panel-v1.parquet",
        "paths-v1/paths-v1.parquet": PUBLISH / "paths-v1" / "paths-v1.parquet",
        "sessions-v1.parquet": PUBLISH / "sessions-v1.parquet",
        "custodian/panel-v1-custodian.parquet": CUSTODIAN / "panel-v1-custodian.parquet",
        "custodian/paths-v1-full.parquet": PATHS_FULL,
    }
    hashes = {k: sha256(v) for k, v in files.items()}
    (PUBLISH / "panel-v1.sha256").write_text(
        "".join(f"{v}  {k}\n" for k, v in hashes.items() if not k.startswith("custodian"))
    )
    dump(
        "S0-B.json",
        {
            "redacted_columns": redact,
            "holdout_rows_redacted": h.height,
            "holdout_date_range": [str(h["dt"].min()), str(h["dt"].max())],
            "agent_panel_rows": agents.height,
            "agent_path_keys": agent_paths["key"].n_unique(),
            "agent_path_bars": agent_paths.height,
            "sha256": hashes,
            "rule_columns": rule_columns(),
        },
    )


if __name__ == "__main__":
    {
        "publish": cmd_publish,
        "power": cmd_power,
        "paths": cmd_paths,
        "free": cmd_free,
        "costs": cmd_costs,
        "baseline": cmd_baseline,
        "geometry": cmd_geometry,
    }[sys.argv[1]]()
