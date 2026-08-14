"""Engine lab (#690 follow-on) — the SHARED harness for the rules / risk / exits agents.

Three parallel investigations share this module so their numbers are comparable. **Do not fork it.**
If you need a behaviour it does not have, add it here in a backwards-compatible way and say so in
your write-up; if you change an existing number, every other agent's results silently move.

    from spikes.engine_lab.common import *      # or import common as C

## The population

One row per triggered setup, both stores treated as **one dataset**:

- `recon` — 166 sessions 2025-10-30 -> 2026-06-30, rebuilt from vendor bars
- `live`  — 31 sessions 2026-07-01 -> 2026-08-13, recorded by the tracker

`load_panel()` returns the combined table with two cuts already applied:

1. **Pre-market only** — `trigger_et_min < 570` (09:30 ET). The user is not interested in
   in-market entries, so they are dropped from BOTH halves (not just live) — otherwise the two
   halves would be measuring different populations and no rule could be validated across them.
2. **`cons_has_range`** — rows where entry and stop collapse to the same price have a meaningless
   stop and an undefined R. 25 rows of 3,664.

That leaves **3,639 rows over 197 sessions** (~18.5 per session).

⚠️ `passed` is NOT applied. It is the shape gate, and it is *worse* than the raw pool on this
population (270 rows, -0.278R/trade at a 2R target, vs -0.247R over all 3,639). Treat it as one
candidate filter among many, never as a given.

## The rules every result here must obey

- **Decidable at trigger time.** A rule may read any column measured at or before the trigger bar.
  It may not read `max_r`, `stopped_out`, `mae_r`, `bars_to_max_r`, or anything about the rest of
  the day. `TRIGGER_TIME_SAFE` lists what is allowed; `assert_no_lookahead()` checks a rule's
  column set for you.
- **Order by time, never by rank.** The capacity cap takes the *earliest* N triggers of the day.
  You cannot rank a day's setups against each other at 07:00, so a within-day ranking (by score,
  by anything) is lookahead however innocent it looks. `build_book()` enforces this.
- **No refitting on the holdout.** See the split section.

## The splits

    DEV      2025-10-30 .. 2026-04-30   (~120 sessions, recon)   fit here
    VAL      2026-05-01 .. 2026-06-30   (~40 sessions, recon)    check here, freely
    HOLDOUT  2026-07-01 .. 2026-08-13   (31 sessions, live)      ONE look, at the end

`split_of()` labels a row. `score()` reports all three separately by default so you cannot
accidentally read a combined number as evidence.

Fitting on DEV+VAL is fine once you have stopped iterating; touching HOLDOUT more than once is
what makes the whole exercise worthless, so leave it to the synthesis step unless told otherwise.

## Costs are not optional

The account is **$500**. Commission has a $0.35/side minimum against a mean risk of ~$16.58, so
~7% of every R is gone before slippage and ~10% after. That turned the shipped book's +11.0R gross
into **+1.2R net**. `score()` returns gross and net side by side; **net is the number that counts.**
A rule that improves gross and worsens net is a common and important failure — watch for it.

## What is in here

    load_panel()          the combined pre-market population
    load_paths()          post-trigger bar paths (numpy) for exit replay
    replay_bracket()      one fixed stop + one fixed target, bar by bar
    build_book()          time-ordered capacity cap -> the trades you would actually take
    score()               gross/net R, win rate, frequency, drawdown, per split and per source
    SHIPPED               the shipped rule set, as a selection function
    baseline()            SHIPPED scored on this population — the number to beat
    walk_forward()        expanding-window refit/evaluate, the anti-overfit workhorse
    permutation_pvalue()  is this better than the same trade count picked at random?
    sensitivity()         does the result survive +/-20% on each threshold?
"""

from __future__ import annotations

import pickle
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

REPO = Path(__file__).resolve().parents[2]
PANEL_PATH = REPO / "data/spikes/regime_panel.parquet"
LAB_OUT = REPO / "data/spikes/engine-lab"
PATHS_CACHE = LAB_OUT / "paths.pkl"

#: 09:30 ET in minutes past ET midnight. The pre-market population cut.
PREMARKET_CUT = 570.0

#: Chronological splits, by session date.
DEV_END = date(2026, 4, 30)
VAL_END = date(2026, 6, 30)

TICK = 0.01


# ---------------------------------------------------------------------------------------------
# Population
# ---------------------------------------------------------------------------------------------
def load_panel(
    *,
    premarket_cut: float = PREMARKET_CUT,
    require_cons_range: bool = True,
    path: Path = PANEL_PATH,
) -> pl.DataFrame:
    """The combined live+recon pre-market population, one row per triggered setup."""
    df = pl.read_parquet(path)
    df = df.filter(pl.col("trigger_et_min") < premarket_cut)
    if require_cons_range:
        df = df.filter(pl.col("cons_has_range"))
    df = df.with_columns(
        pl.concat_str(
            [pl.col("source"), pl.col("dt").cast(pl.Utf8), pl.col("seg_id")], separator="|"
        ).alias("key"),
        pl.col("dt").map_elements(_split_of, return_dtype=pl.Utf8).alias("split"),
    )
    return df.sort(["dt", "trigger_et_min", "symbol"])


def _split_of(d: date) -> str:
    if d <= DEV_END:
        return "dev"
    if d <= VAL_END:
        return "val"
    return "holdout"


def split_of(d: date) -> str:
    return _split_of(d)


#: Columns a selection rule MAY read — everything measured at or before the trigger bar.
TRIGGER_TIME_SAFE: frozenset[str] = frozenset(
    {
        "dt",
        "source",
        "symbol",
        "seg_id",
        "run",
        "key",
        "split",
        "first_hit_et_min",
        "entry_trigger",
        "entry_fill",
        "breakout_level",
        "stop",
        "planned_risk",
        "stop_pct",
        "pole_len",
        "cons_len",
        "retracement",
        "cons_vol_reducing",
        "pole_has_big_green",
        "passed",
        "failing_gates",
        "cycle_num",
        "cons_has_range",
        "untraded_cons_bars",
        "halted_consolidation",
        "float_shares",
        "short_percent",
        "shares_outstanding",
        "shares_source",
        "trigger_et_min",
        "trigger_idx",
        "staleness_delay_min",
        "pole_pct",
        "pole_volume",
        "day_open",
        "ext_at_peak",
        "ext_at_trigger",
        "bars_before_pole",
        "runup_pre_appearance",
        "rvol_pole",
        "vol_share_pole",
        "range_before_pole_pct",
        "cum_volume_to_trigger",
        "cum_dollar_vol_to_trigger",
        "hits_before_trigger",
    }
)

#: Columns that describe how the day turned out. Reading one in a selection rule is lookahead.
OUTCOME_COLS: frozenset[str] = frozenset(
    {
        "max_r",
        "max_gain_pct",
        "mae_r",
        "stopped_out",
        "stop_index",
        "bars_to_max_r",
        "entry_price",
        "realised_risk",
        "fill_above_entry_bar_high",
        "same_bar_stop",
        "day_volume",
        "day_dollar_volume",
        "day_high",
        "day_low",
        # Day aggregates that read as trigger-time context and are not. `first_rank` is now
        # live-only in the panel (the recon value was derived from the whole day's move) and
        # `n_scanner_hits_day` counts appearances after the break as well as before — use
        # `hits_before_trigger`. Both topped every feature ranking the rules pass ran, which is
        # what a lookahead column always looks like from the inside.
        "n_scanner_hits",
        "n_scanner_hits_day",
        "first_rank",
        "n_day_bars",
        "run_count",
        "total_significant_cycles",
        "triggered",
    }
)


def assert_no_lookahead(columns: Iterable[str]) -> None:
    """Raise if a selection rule reads a column it could not have known at trigger time.

    ⚠️ `day_volume`, `day_dollar_volume`, `day_high`, `day_low` and `run_count` are whole-session
    aggregates and therefore lookahead, even though they feel like context. Use
    `cum_volume_to_trigger` / `cum_dollar_vol_to_trigger` / `ext_at_trigger` instead.
    """
    bad = sorted(set(columns) & OUTCOME_COLS)
    if bad:
        raise ValueError(f"lookahead: selection rule reads outcome columns {bad}")
    unknown = sorted(set(columns) - TRIGGER_TIME_SAFE - OUTCOME_COLS)
    if unknown:
        raise ValueError(
            f"unknown columns (add to TRIGGER_TIME_SAFE if genuinely pre-trigger): {unknown}"
        )


# ---------------------------------------------------------------------------------------------
# Bar paths, for exit replay
# ---------------------------------------------------------------------------------------------
def _day_bars(source: str, d: date) -> pl.DataFrame:
    p = REPO / f"data/{source}/bars/dt={d.isoformat()}"
    files = sorted(p.glob("*.parquet"))
    if not files:
        return pl.DataFrame()
    return pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")


def build_paths(df: pl.DataFrame, *, cache: Path = PATHS_CACHE) -> dict[str, np.ndarray]:
    """For each panel row, the OHLC path from its trigger bar to 16:00 ET.

    Returns ``{key: array of shape (n_bars, 4) = open, high, low, close}``, row 0 being the
    **trigger bar itself**. That is the array `replay_bracket()` consumes.

    The bar list is rebuilt exactly as the detector saw it (dedupe on bar_start_utc, sort, clip to
    04:00-16:00 ET) so that `trigger_idx` from the panel indexes into it. `verify_paths()` proves
    this by reproducing every row's published `max_r`; run it if you touch this function.
    """
    if cache.exists():
        with cache.open("rb") as fh:
            cached: dict[str, np.ndarray] = pickle.load(fh)
        if set(df["key"]) <= set(cached):
            return cached
    out: dict[str, np.ndarray] = {}
    for (source, d), grp in df.group_by(["source", "dt"], maintain_order=True):
        bars = _day_bars(str(source), d)  # type: ignore[arg-type]
        if bars.is_empty():
            continue
        bars = bars.unique(subset=["opportunity_id", "bar_start_utc"], keep="first")
        et = bars["bar_start_utc"].dt.convert_time_zone("America/New_York")
        mins = (et.dt.hour().cast(pl.Int32) * 60 + et.dt.minute().cast(pl.Int32)).alias("etmin")
        bars = bars.with_columns(mins).filter((pl.col("etmin") >= 240) & (pl.col("etmin") < 960))
        by_oid = {
            str(k[0]): g.sort("bar_start_utc").select(["open", "high", "low", "close"]).to_numpy()
            for k, g in bars.group_by(["opportunity_id"])
        }
        for row in grp.iter_rows(named=True):
            arr = by_oid.get(row["opportunity_id"])
            if arr is None:
                continue
            j = int(row["trigger_idx"])
            if j >= len(arr):
                continue
            out[row["key"]] = arr[j:].astype(np.float64)
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("wb") as fh:
        pickle.dump(out, fh)
    return out


def load_paths(df: pl.DataFrame | None = None) -> dict[str, np.ndarray]:
    if df is None:
        df = load_panel()
    return build_paths(df)


def verify_paths(df: pl.DataFrame, paths: dict[str, np.ndarray], *, tol: float = 0.02) -> dict:
    """Reproduce the panel's published `max_r` from the paths. Guards the whole exit study."""
    bad, missing, n = [], 0, 0
    for row in df.iter_rows(named=True):
        arr = paths.get(row["key"])
        if arr is None:
            missing += 1
            continue
        got = replay_bracket(arr, row["entry_fill"], row["stop"], target_r=None)
        n += 1
        if abs(got["max_r"] - row["max_r"]) > tol:
            bad.append((row["key"], row["max_r"], got["max_r"]))
    return {"checked": n, "missing": missing, "mismatched": len(bad), "examples": bad[:10]}


# ---------------------------------------------------------------------------------------------
# The bracket — one fixed stop, one fixed target
# ---------------------------------------------------------------------------------------------
def replay_bracket(
    path: np.ndarray,
    entry_fill: float,
    stop: float,
    *,
    target_r: float | None = 2.0,
    target_price: float | None = None,
) -> dict[str, Any]:
    """Walk a trade's bars with a stop and (optionally) a fixed target.

    `path[0]` is the trigger bar. Semantics deliberately mirror `rmetrics._measure`:

    - fill = `max(entry_fill, bar_open)` — a gap-through fills no better than the open
    - **the stop is checked before the target on every bar**, including the entry bar. A 5-min bar
      that contains both is booked as a loss. That is conservative and it is not a measurement;
      ~2% of rows are same-bar and re-resolving them at 1-min granularity found the conservative
      reading wrong 38% of the time (#583). Do not "fix" it — but do report how many of your
      trades depend on it.
    - no target (`target_r=None`) measures the maximum favourable excursion, i.e. `max_r`.

    Returns exit_price, r (realised R at the bracket), max_r (MFE), stopped, bars_held.
    """
    o, h, lo, cl = path[:, 0], path[:, 1], path[:, 2], path[:, 3]
    entry = max(entry_fill, float(o[0]))
    risk = entry - stop
    if risk <= 0:
        return {
            "exit_price": entry,
            "r": 0.0,
            "max_r": 0.0,
            "stopped": True,
            "bars_held": 0,
            "valid": False,
        }
    tgt = (
        target_price
        if target_price is not None
        else (entry + target_r * risk if target_r else None)
    )
    # Mirrors `_measure`: a same-bar stop credits NO favourable excursion (max_high := entry),
    # while a surviving entry bar starts the excursion at its own high — which can sit *below* the
    # fill when the conservative +3-tick fill lands above the bar's range (#555), giving a negative
    # max_r. Both readings matter: 325 of 3,639 rows differ between them.
    max_high = entry if lo[0] <= stop else float(h[0])
    for k in range(len(path)):
        if lo[k] <= stop:  # stop first, always
            mfe = (max_high - entry) / risk
            return {
                "exit_price": stop,
                "r": (stop - entry) / risk,
                "max_r": round(mfe, 3),
                "stopped": True,
                "bars_held": k,
                "valid": True,
            }
        if h[k] > max_high:
            max_high = float(h[k])
        if tgt is not None and h[k] >= tgt:
            return {
                "exit_price": tgt,
                "r": (tgt - entry) / risk,
                "max_r": round((max_high - entry) / risk, 3),
                "stopped": False,
                "bars_held": k,
                "valid": True,
            }
    # never stopped, never hit target -> out at the last close (16:00). No overnight holds.
    return {
        "exit_price": float(cl[-1]),
        "r": (float(cl[-1]) - entry) / risk,
        "max_r": round((max_high - entry) / risk, 3),
        "stopped": False,
        "bars_held": len(path) - 1,
        "valid": True,
    }


# ---------------------------------------------------------------------------------------------
# The book — capacity, in time order
# ---------------------------------------------------------------------------------------------
def build_book(
    df: pl.DataFrame,
    *,
    max_per_day: int = 2,
    one_per_symbol: bool = False,
) -> pl.DataFrame:
    """Of the rows that passed selection, the ones a 2-a-day book would actually have taken.

    ⚠️ **Earliest trigger first, always.** Never sort by score, max_r or any ranking — see the
    module docstring. If you want a "best of the day" comparison, build it as a clearly-labelled
    lookahead ceiling and never report it as achievable.
    """
    d = df.sort(["dt", "trigger_et_min", "symbol"])
    if one_per_symbol:
        d = d.with_columns(pl.int_range(pl.len()).over(["dt", "symbol"]).alias("_sym_seq")).filter(
            pl.col("_sym_seq") == 0
        )
    d = d.with_columns(pl.int_range(pl.len()).over("dt").alias("seq_day"))
    return d.filter(pl.col("seq_day") < max_per_day)


# ---------------------------------------------------------------------------------------------
# Money: sizing, commission, slippage
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Costs:
    """IBKR tiered, always liquidity-removing. Reproduces `spikes/excel-fees-brief.md` §6."""

    comm_min: float = 0.35
    comm_per_share: float = 0.0035
    exchange: float = 0.003
    clearing: float = 0.0002
    taf_per_share: float = 0.000166
    taf_max: float = 8.30
    sec_rate: float = 0.0000278
    stop_slip_ticks: float = 2.0  # losers slip through the stop; winners fill at the limit

    def usd(self, qty: int, exit_price: float, *, won: bool) -> tuple[float, float]:
        """(fees, slippage) in dollars for a round trip of `qty` shares."""
        if qty <= 0:
            return 0.0, 0.0
        commission = 2 * max(self.comm_min, qty * self.comm_per_share)
        per_share = 2 * qty * (self.exchange + self.clearing)
        taf = min(qty * self.taf_per_share, self.taf_max)
        sec = qty * exit_price * self.sec_rate
        slip = 0.0 if won else qty * self.stop_slip_ticks * TICK
        return commission + per_share + taf + sec, slip


@dataclass(frozen=True)
class Sizing:
    """$500 account, 5% risk, 50% notional cap. Whole shares; qty<1 means the trade is skipped.

    ⚠️ The **notional cap binds on tight stops**, not wide ones — at 5%/50% the crossover is a stop
    10% from entry, and 64 of the shipped book's 100 trades are cap-bound. A cap-bound trade risks
    less than 5%, so it earns less in dollars than its R implies. This is why R and dollars
    disagree on this account, and it is a first-class part of the risk problem, not a detail.
    """

    equity: float = 500.0
    risk_fraction: float = 0.05
    position_fraction: float = 0.50
    compound: bool = False

    def qty(self, entry: float, stop: float, equity: float | None = None) -> tuple[int, str]:
        eq = self.equity if equity is None else equity
        rps = entry - stop
        if rps <= 0 or entry <= 0:
            return 0, "invalid"
        risk_qty = int(eq * self.risk_fraction // rps)
        cap_qty = int(eq * self.position_fraction // entry)
        return (risk_qty, "risk") if risk_qty <= cap_qty else (cap_qty, "cap")


# ---------------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------------
def score(
    trades: pl.DataFrame,
    *,
    r_col: str = "r",
    sizing: Sizing | None = None,
    costs: Costs | None = None,
    sessions: int | None = None,
    by: Sequence[str] = ("split",),
) -> dict[str, Any]:
    """Gross and net performance of a book. `trades` needs `dt, entry_fill, stop, <r_col>`.

    Net R is computed **per trade from its own share count**, never as a flat haircut off gross —
    the drag runs from 3% to 19% of R depending on stop width and price, so a flat subtraction
    flatters tight stops and punishes wide ones, which is backwards.
    """
    sizing = DEFAULT_SIZING if sizing is None else sizing
    costs = DEFAULT_COSTS if costs is None else costs
    if trades.is_empty():
        return {
            "trades": 0,
            "gross_r": 0.0,
            "net_r": 0.0,
            "r_per_trade": 0.0,
            "net_r_per_trade": 0.0,
        }
    rows = trades.sort(["dt", "trigger_et_min"]).to_dicts()
    equity = sizing.equity
    out = []
    for t in rows:
        entry, stop, r = float(t["entry_fill"]), float(t["stop"]), float(t[r_col])
        qty, sized_by = sizing.qty(entry, stop, equity if sizing.compound else None)
        if qty < 1:
            out.append(
                {
                    **t,
                    "qty": 0,
                    "sized_by": "unaffordable",
                    "net_r": 0.0,
                    "net_usd": 0.0,
                    "gross_usd": 0.0,
                    "cost_usd": 0.0,
                    "taken": False,
                }
            )
            continue
        risk_usd = qty * (entry - stop)
        gross_usd = r * risk_usd
        exit_price = entry + r * (entry - stop)
        fees, slip = costs.usd(qty, exit_price, won=r > 0)
        net_usd = gross_usd - fees - slip
        if sizing.compound:
            equity += net_usd
        out.append(
            {
                **t,
                "qty": qty,
                "sized_by": sized_by,
                "gross_usd": gross_usd,
                "cost_usd": fees + slip,
                "net_usd": net_usd,
                "net_r": net_usd / risk_usd,
                "taken": True,
            }
        )
    tf = pl.DataFrame(out, infer_schema_length=None).filter(pl.col("taken"))
    n_sessions = sessions if sessions is not None else trades["dt"].n_unique()

    def block(d: pl.DataFrame, label: str) -> dict[str, Any]:
        if d.is_empty():
            return {"label": label, "trades": 0}
        cum = np.cumsum(d["net_r"].to_numpy())
        dd = float((cum - np.maximum.accumulate(cum)).min()) if len(cum) else 0.0
        cum_g = np.cumsum(d[r_col].to_numpy())
        dd_g = float((cum_g - np.maximum.accumulate(cum_g)).min()) if len(cum_g) else 0.0
        return {
            "label": label,
            "trades": d.height,
            "sessions_traded": d["dt"].n_unique(),
            "gross_r": round(float(d[r_col].sum()), 2),
            "net_r": round(float(d["net_r"].sum()), 2),
            "r_per_trade": round(float(d[r_col].mean()), 4),
            "net_r_per_trade": round(float(d["net_r"].mean()), 4),
            "win_rate": round(float((d[r_col] > 0).mean()), 4),
            "net_usd": round(float(d["net_usd"].sum()), 2),
            "mean_qty": round(float(d["qty"].mean()), 1),
            "cap_bound": int((d["sized_by"] == "cap").sum()),
            "max_dd_net_r": round(dd, 2),
            "max_dd_gross_r": round(dd_g, 2),
            "cost_r_per_trade": round(
                float((d["cost_usd"] / (d["qty"] * (d["entry_fill"] - d["stop"]))).mean()), 4
            ),
        }

    res: dict[str, Any] = block(tf, "all")
    res["sessions_available"] = n_sessions
    res["trades_per_session"] = round(tf.height / n_sessions, 3) if n_sessions else 0.0
    res["unaffordable"] = int(len(out) - tf.height)
    for col in by:
        if col in tf.columns:
            res[col] = {
                str(k[0]): block(g, str(k[0])) for k, g in tf.group_by([col], maintain_order=True)
            }
    res["_trades"] = tf
    return res


def brief(res: dict[str, Any]) -> str:
    """One-line summary. Net first, because net is the number that counts."""
    return (
        f"{res['trades']:>4} trades ({res.get('trades_per_session', 0):.2f}/session)  "
        f"net {res['net_r']:+7.1f}R ({res['net_r_per_trade']:+.3f}/trade)  "
        f"gross {res['gross_r']:+7.1f}R ({res['r_per_trade']:+.3f})  "
        f"win {res['win_rate'] * 100:4.1f}%  ddNet {res['max_dd_net_r']:.1f}R"
    )


# ---------------------------------------------------------------------------------------------
# The baseline every proposal is scored against
# ---------------------------------------------------------------------------------------------
def SHIPPED(df: pl.DataFrame) -> pl.DataFrame:
    """The rules the system ships today, as a filter over this population (config.py values)."""
    return df.filter(
        pl.col("passed")
        & (pl.col("cycle_num") <= 2)
        & (pl.col("staleness_delay_min") <= 30)
        & pl.col("entry_fill").is_between(3.0, 50.0)
        & (pl.col("stop_pct") >= 0.025)
        & pl.col("trigger_et_min").is_between(240.0, 555.0)
    )


def fixed_target_r(df: pl.DataFrame, target: float = 2.0) -> pl.DataFrame:
    """Book each row at a fixed target / -1R stop, straight off the panel's `max_r`.

    Cheap and exact for the *shipped* stop. If you move the stop you must re-derive R from the
    bars with `replay_bracket()` — `max_r` is denominated in the shipped stop's risk and means
    nothing against a different one.
    """
    return df.with_columns(
        pl.when(pl.col("max_r") >= target).then(target).otherwise(-1.0).alias("r")
    )


def baseline(df: pl.DataFrame | None = None, **kw: Any) -> dict[str, Any]:
    """SHIPPED rules, 2/day, 2R target, on this population. The number to beat."""
    if df is None:
        df = load_panel()
    book = build_book(fixed_target_r(SHIPPED(df)), max_per_day=2)
    return score(book, sessions=df["dt"].n_unique(), **kw)


# ---------------------------------------------------------------------------------------------
# Anti-overfitting
# ---------------------------------------------------------------------------------------------
def walk_forward(
    df: pl.DataFrame,
    fit: Callable[[pl.DataFrame], Callable[[pl.DataFrame], pl.DataFrame]],
    *,
    n_blocks: int = 6,
    min_train_sessions: int = 60,
    max_per_day: int = 2,
    r_builder: Callable[[pl.DataFrame], pl.DataFrame] = fixed_target_r,
) -> dict[str, Any]:
    """Expanding-window walk-forward: fit on everything before a block, trade the block.

    `fit(train_df)` returns a selection function. This is the honest test of a rule-finding
    *procedure* — it answers "would this method have made money as the record accumulated?",
    which a single in-sample fit cannot. A rule that only works when fitted on all 197 sessions
    is a description of the past, not a rule.
    """
    dates = sorted(df["dt"].unique().to_list())
    start = min_train_sessions
    if start >= len(dates):
        raise ValueError("not enough sessions")
    edges = np.linspace(start, len(dates), n_blocks + 1).astype(int)
    blocks = []
    for a, b in zip(edges[:-1], edges[1:], strict=True):
        if b <= a:
            continue
        train = df.filter(pl.col("dt") < dates[a])
        test = df.filter(pl.col("dt").is_in(dates[a:b]))
        sel = fit(train)
        book = build_book(r_builder(sel(test)), max_per_day=max_per_day)
        s = score(book, sessions=test["dt"].n_unique())
        blocks.append(
            {
                "from": dates[a],
                "to": dates[b - 1],
                "train_sessions": train["dt"].n_unique(),
                **{
                    k: s[k]
                    for k in (
                        "trades",
                        "net_r",
                        "net_r_per_trade",
                        "gross_r",
                        "win_rate",
                        "trades_per_session",
                    )
                },
            }
        )
    tot_net = sum(b["net_r"] for b in blocks)
    tot_tr = sum(b["trades"] for b in blocks)
    return {
        "blocks": blocks,
        "total_trades": tot_tr,
        "total_net_r": round(tot_net, 2),
        "net_r_per_trade": round(tot_net / tot_tr, 4) if tot_tr else 0.0,
        "blocks_positive": sum(1 for b in blocks if b["net_r"] > 0),
        "n_blocks": len(blocks),
    }


def permutation_pvalue(
    df: pl.DataFrame,
    selected: pl.DataFrame,
    *,
    n: int = 500,
    seed: int = 7,
    target: float = 2.0,
) -> float:
    """How often does a random rule taking the same number of trades per day do this well?

    Preserves the calendar and the per-day trade count, and resamples *which* rows are picked from
    the same day's pool. A rule that clears the baseline but not this is picking a trade count, not
    a population.
    """
    rng = np.random.default_rng(seed)
    per_day = selected.group_by("dt").agg(pl.len().alias("k")).to_dicts()
    pool = {k[0]: g for k, g in df.group_by(["dt"])}
    obs = float(fixed_target_r(selected, target)["r"].mean()) if selected.height else 0.0
    hits = 0
    for _ in range(n):
        rs = []
        for row in per_day:
            g = pool.get(row["dt"])
            if g is None or g.is_empty():
                continue
            idx = rng.choice(g.height, size=min(row["k"], g.height), replace=False)
            rs.append(g[idx.tolist()])
        if not rs:
            continue
        samp = fixed_target_r(pl.concat(rs), target)
        if float(samp["r"].mean()) >= obs:
            hits += 1
    return (hits + 1) / (n + 1)


def sensitivity(
    df: pl.DataFrame,
    thresholds: dict[str, float],
    make_selector: Callable[[dict[str, float]], Callable[[pl.DataFrame], pl.DataFrame]],
    *,
    pct: float = 0.2,
    max_per_day: int = 2,
) -> list[dict[str, Any]]:
    """Move each threshold +/-pct on its own and re-score. A cliff is an overfit.

    A rule you can trust sits on a plateau: every neighbour is also positive. If nudging one
    number 20% flips the sign, you found a hole in the data, not an edge.
    """
    rows = []
    for name, base in thresholds.items():
        for mult in (1 - pct, 1 + pct):
            th = dict(thresholds)
            th[name] = base * mult
            book = build_book(fixed_target_r(make_selector(th)(df)), max_per_day=max_per_day)
            s = score(book, sessions=df["dt"].n_unique())
            rows.append(
                {
                    "threshold": name,
                    "value": round(th[name], 4),
                    "mult": round(mult, 2),
                    "trades": s["trades"],
                    "net_r": s["net_r"],
                    "net_r_per_trade": s["net_r_per_trade"],
                }
            )
    return rows


def summarise(res: dict[str, Any], name: str = "") -> str:
    """Multi-line report: overall, then per split, then per source."""
    lines = [f"== {name}", "  ALL      " + brief(res)]
    for key in ("split", "source"):
        blk = res.get(key)
        if isinstance(blk, dict):
            for label, b in blk.items():
                if b.get("trades"):
                    lines.append(f"  {label:<8} " + brief({**b, "trades_per_session": 0}))
    return "\n".join(lines)


DEFAULT_SIZING = Sizing()
DEFAULT_COSTS = Costs()


if __name__ == "__main__":
    df = load_panel()
    print(f"population: {df.height} rows, {df['dt'].n_unique()} sessions")
    print(
        df.group_by("split").agg(pl.len(), pl.col("dt").n_unique().alias("sessions")).sort("split")
    )
    print(summarise(baseline(df), "SHIPPED rules, 2/day, 2R target"))
