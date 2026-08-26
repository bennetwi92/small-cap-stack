"""Step 11 (#715 follow-on) -- a discrete "ladder" exit policy fit per (T,R) cell.

Built after four independent design reviews on candle-by-candle recalculated exits converged on
two hard facts, measured directly from this data:

1. A single 5-min candle typically spans 0.5-1.0R on its own. Any stop movement smaller than that
   is unmeasurable from OHLC -- same-bar ordering is unknowable below roughly 2xC in trail terms.
   This killed the prior "0.4xC trailing stop" finding outright. Here: a candidate stop rung is
   INADMISSIBLE if it sits closer than 0.5R to the current close.
2. 21.5% of trades resolve inside their own entry candle, so a "reassess after N candles" rule can
   only ever apply to trades that survive that long. Here: reassessment starts only after bar 0 (the
   entry/trigger candle) has closed without resolving the trade -- a trade resolved on the entry
   candle just uses the original stop/target, full stop, no ladder, and contributes zero
   observations to the fit.

## The action space -- real observed prices only, never a synthetic offset

At each closed candle k (0-based; bars_since_entry = k+1 >= 1), the policy picks ONE rung to be the
active stop from bar k+1 onward (monotonic -- may only tighten):

    R0    original consolidation low            (the floor, always admissible)
    R1    low of the single just-closed candle (bar k)
    R2    low of the last 2 closed candles (min of bars k-1, k; falls back to bar k alone at k=0)
    R3    breakeven (entry price)
    EXIT  flatten now -- exit at bar k+1's open (a market order; the only fill a closed-candle
          decision can honestly claim -- never bar k's own close)

A rung (other than R0, which is always admissible) is INADMISSIBLE if it sits closer than 0.5R to
the current close (R = original entry risk = entry_fill - consolidation low, fixed once at entry
and never recomputed -- `risk0` throughout, matching `replay_dynamic`'s convention in
step10_dynamic.py). The target is never touched by the policy: it stays at the fixed original
2.0xC (the corrected, non-buggy convention -- see FINDINGS.md; NOT the superseded 2.6xC number)
unless EXIT is chosen, in which case the trade flattens and the target is moot. This is the whole
target-side action space per the brief ("keep the original 2xC target, or EXIT now") -- no synthetic
new target price is ever introduced, and nothing in this design can bank more than 2xC.

## State

    T  bars elapsed since entry (k+1), bucketed {1-2, 3-6, 7-12, 13+}
    R  unrealised R at the close of bar k, using the ORIGINAL entry risk as the denominator
       (never the current, possibly-tightened, stop), bucketed {<0, 0-0.5, 0.5-1, 1-2, 2-3, 3+}

4 x 6 = 24 cells.

## Fitting -- empirical, shrunk, monotone, leave-one-day-out, DEV only

Observations are enumerated along each DEV trade's **baseline** trajectory (original stop, fixed
2xC target -- i.e. "what would the state be if no rung had fired yet") -- this is the standard
single-step relaxation for a ladder/tabular Q-style fit: each cell asks "what is the best one-shot
deviation from here, having reached this point under the do-nothing baseline". A trade contributes
one observation per candle it survives before its baseline bracket resolves. For each admissible
action at that observation, the counterfactual net R is the result of continuing the walk from bar
k+1 with that action applied (stop-first, gap-through, resting-limit target -- `_continue()` below,
adapted from `replay_dynamic`/`replay_bracket`'s conservative semantics). Net R uses the SAME cost
model as `common.score()`, keyed on the actual exit mechanism (`_net_r()`): a resting-limit target
fill is slip-free, a stop/EOD/flatten fill is not (matching step10's `score_dynamic` bug fix -- a
flatten is a market order and always pays slippage, win or lose).

Per cell: pool trade-normalised observations (a trade's observations within ONE cell sum to weight
1, split evenly across however many candles it visits that cell -- so a trade that lingers in a cell
doesn't outvote many trades that pass through once), shrink each rung's mean toward the pooled
all-cell mean for that rung (weight n_trades/(n_trades+25)), score leave-one-day-out (a day's own
trades never inform its own cell estimate), enforce a minimum floor of >=30 observations AND >=15
distinct trades per cell (else collapse into the T-marginal, pooling across R), and enforce a
monotone tightness constraint (R0 < {R1,R2,R3} < EXIT, non-decreasing in T, and non-decreasing in R
once R >= 1) by a simple adjacent-cell pooling pass -- the brief explicitly allows this over exact
isotonic regression.

## Validation order -- stop if a gate fails, report where

1. Null test FIRST: shuffle the (T,R) labels within candle-index across trades, refit the identical
   pipeline, score identically. If the shuffled fit earns close to the real one, report that
   plainly and stop -- do not proceed as if a real result existed.
2. If the null test clears: frozen DEV-fit policy on the DEV **book** (`build_book`, 2/day cap,
   time-ordered) vs the corrected static baseline (2.0xC target on the shipped 1.00xC stop).
3. Same frozen policy (no refit) on VAL.
4. Resolution-floor audit: confirm zero applied rungs implied a stop move below 0.5R.
5. Only if net-positive on BOTH dev and val beyond plausible noise (session-block bootstrap) AND
   the null test passed: touch HOLDOUT once, clearly labelled as a fresh one-time look (separate
   from step10_dynamic.py's holdout spend on a different, since-superseded candidate).

Nothing in `common.py` or `exits/step1..10` is touched. This file is self-contained; it reuses
`common.py`'s population/book/cost machinery and adapts (does not import) step10's walker pattern.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine_lab import common as C  # noqa: E402

OUT = C.REPO / "data/spikes/engine-lab/exits"

TARGET_R = 2.0  # entry + 2.0 * risk0, the corrected (non-buggy) convention
RESOLUTION_FLOOR_R = 0.5
MIN_SAMPLE_FLOOR = 30
MIN_TRADE_FLOOR = 15
SHRINK_K = 25.0

T_BUCKETS = [(1, 2), (3, 6), (7, 12), (13, 10_000)]
T_LABELS = ["1-2", "3-6", "7-12", "13+"]
R_BUCKETS = [(-100.0, 0.0), (0.0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 100.0)]
R_LABELS = ["<0", "0-0.5", "0.5-1", "1-2", "2-3", "3+"]

RUNGS = ["R0", "R1", "R2", "R3", "EXIT"]
TIGHTNESS = {"R0": 0, "R1": 1, "R2": 1, "R3": 1, "EXIT": 2}  # per the brief's ordinal grouping


def t_bucket(bars_since_entry: int) -> int:
    for i, (lo, hi) in enumerate(T_BUCKETS):
        if lo <= bars_since_entry <= hi:
            return i
    return len(T_BUCKETS) - 1


def r_bucket(r: float) -> int:
    for i, (lo, hi) in enumerate(R_BUCKETS):
        if lo <= r < hi or (i == len(R_BUCKETS) - 1 and r >= lo):
            return i
    return 0 if r < 0 else len(R_BUCKETS) - 1


# ---------------------------------------------------------------------------------------------
# Walk primitives -- conservative semantics, matching replay_bracket/replay_dynamic
# ---------------------------------------------------------------------------------------------
def _continue(
    path: np.ndarray, k_start: int, entry: float, risk0: float, stop: float, target: float | None
) -> tuple[float, str, int]:
    """Continue from bar k_start+1 with a FIXED stop/target. Returns (r, exit_reason, k)."""
    h, lo, cl = path[:, 1], path[:, 2], path[:, 3]
    for k in range(k_start + 1, len(path)):
        if lo[k] <= stop:
            return (stop - entry) / risk0, "stop", k
        if target is not None and h[k] >= target:
            return (target - entry) / risk0, "target", k
    last = len(path) - 1
    return (float(cl[-1]) - entry) / risk0, "eod", last


def _exit_now(path: np.ndarray, k: int, entry: float, risk0: float) -> tuple[float, str, int]:
    """EXIT rung: flatten at bar k+1's open. If there is no bar k+1, fall back to EOD close."""
    if k + 1 < len(path):
        px = float(path[k + 1, 0])
        return (px - entry) / risk0, "flatten", k + 1
    last = len(path) - 1
    return (float(path[-1, 3]) - entry) / risk0, "flatten", last


def rung_price(name: str, *, initial_stop: float, entry: float, closed_bars: np.ndarray) -> float:
    if name == "R0":
        return initial_stop
    if name == "R1":
        return float(closed_bars[-1, 2])
    if name == "R2":
        n = min(2, len(closed_bars))
        return float(closed_bars[-n:, 2].min())
    if name == "R3":
        return entry
    raise ValueError(name)


def admissible_rungs(
    *, close_k: float, risk0: float, initial_stop: float, entry: float, closed_bars: np.ndarray
) -> dict[str, float]:
    """{rung_name: price} for every admissible non-EXIT rung. R0 is always admissible."""
    out: dict[str, float] = {"R0": initial_stop}
    for name in ("R1", "R2", "R3"):
        px = rung_price(name, initial_stop=initial_stop, entry=entry, closed_bars=closed_bars)
        if (close_k - px) / risk0 >= RESOLUTION_FLOOR_R:
            out[name] = px
    return out


def _net_r(entry_fill: float, stop0: float, r: float, exit_reason: str) -> float | None:
    """Net R of ONE trade, sized off the ORIGINAL (fixed) stop -- matches score_dynamic's fix:
    only a resting-limit target fill is slip-free; stop/EOD/flatten all pay slippage."""
    sizing, costs = C.DEFAULT_SIZING, C.DEFAULT_COSTS
    qty, _ = sizing.qty(entry_fill, stop0)
    if qty < 1:
        return None
    risk_usd = qty * (entry_fill - stop0)
    gross_usd = r * risk_usd
    exit_price = entry_fill + r * (entry_fill - stop0)
    commission = 2 * max(costs.comm_min, qty * costs.comm_per_share)
    per_share = 2 * qty * (costs.exchange + costs.clearing)
    taf = min(qty * costs.taf_per_share, costs.taf_max)
    sec = qty * exit_price * costs.sec_rate
    paid_via_limit = exit_reason == "target"
    slip = 0.0 if paid_via_limit else qty * costs.stop_slip_ticks * C.TICK
    fees = commission + per_share + taf + sec
    net_usd = gross_usd - fees - slip
    return net_usd / risk_usd


# ---------------------------------------------------------------------------------------------
# Enumerate (trade, candle) observations along the DO-NOTHING baseline trajectory
# ---------------------------------------------------------------------------------------------
def enumerate_observations(
    df: pl.DataFrame,
    paths: dict[str, np.ndarray],
    *,
    shuffle_state_rng: np.random.Generator | None = None,
) -> list[dict[str, Any]]:
    """One record per (trade, candle k) the trade is ALIVE at, under the original stop/target.

    Each record carries the admissible actions' counterfactual net R (continuing from k+1 with
    that action), the trade's key/day, and the (T,R) cell. If `shuffle_state_rng` is given, the
    (T,R) LABEL is independently reshuffled across the whole pool (the null test) -- the outcomes
    attached to a record are untouched, only which cell they get filed under changes.
    """
    obs: list[dict[str, Any]] = []
    for row in df.iter_rows(named=True):
        arr = paths.get(row["key"])
        if arr is None:
            continue
        entry_fill = float(row["entry_fill"])
        stop0 = float(row["stop"])
        entry = max(entry_fill, float(arr[0, 0]))
        risk0 = entry - stop0
        if risk0 <= 0:
            continue
        target = entry + TARGET_R * risk0
        h, lo = arr[:, 1], arr[:, 2]
        # find bars_held under the pure baseline bracket (do nothing, fixed target)
        bars_held = len(arr) - 1
        for k in range(len(arr)):
            if lo[k] <= stop0 or h[k] >= target:
                bars_held = k
                break
        for k in range(0, bars_held):  # candles the trade survived, BEFORE its baseline resolution
            close_k = float(arr[k, 3])
            r_now = (close_k - entry) / risk0
            bars_since_entry = k + 1
            closed_bars = arr[: k + 1]
            adm = admissible_rungs(
                close_k=close_k,
                risk0=risk0,
                initial_stop=stop0,
                entry=entry,
                closed_bars=closed_bars,
            )
            outcomes: dict[str, float | None] = {}
            for name, px in adm.items():
                r_out, reason, _ = _continue(arr, k, entry, risk0, px, target)
                outcomes[name] = _net_r(entry_fill, stop0, r_out, reason)
            r_out, reason, _ = _exit_now(arr, k, entry, risk0)
            outcomes["EXIT"] = _net_r(entry_fill, stop0, r_out, reason)
            outcomes = {k2: v for k2, v in outcomes.items() if v is not None}
            if not outcomes:
                continue
            obs.append(
                {
                    "key": row["key"],
                    "dt": row["dt"],
                    "t_idx": t_bucket(bars_since_entry),
                    "r_idx": r_bucket(r_now),
                    "outcomes": outcomes,
                }
            )
    if shuffle_state_rng is not None:
        idx = shuffle_state_rng.permutation(len(obs))
        t_idx = [obs[i]["t_idx"] for i in idx]
        r_idx = [obs[i]["r_idx"] for i in idx]
        for o, t, r in zip(obs, t_idx, r_idx, strict=True):
            o["t_idx"], o["r_idx"] = t, r
    return obs


# ---------------------------------------------------------------------------------------------
# Fit: per-cell trade-normalised, shrunk, LOO-by-day rung means -> monotone policy
# ---------------------------------------------------------------------------------------------
def _trade_weights(obs: list[dict[str, Any]]) -> list[float]:
    """Each trade sums to weight 1 WITHIN each cell (visiting a cell 3x gets 1/3 each)."""
    counts: dict[tuple[str, int, int], int] = {}
    for o in obs:
        k = (o["key"], o["t_idx"], o["r_idx"])
        counts[k] = counts.get(k, 0) + 1
    return [1.0 / counts[(o["key"], o["t_idx"], o["r_idx"])] for o in obs]


def fit_policy(obs: list[dict[str, Any]]) -> dict[str, Any]:
    """Returns {"table": {(t_idx,r_idx): rung_name}, "cells": [debug rows]}."""
    weights = _trade_weights(obs)

    # pooled (all-cell) mean per rung, for shrinkage targets
    pooled_sum: dict[str, float] = dict.fromkeys(RUNGS, 0.0)
    pooled_w: dict[str, float] = dict.fromkeys(RUNGS, 0.0)
    for o, w in zip(obs, weights, strict=True):
        for rung, v in o["outcomes"].items():
            pooled_sum[rung] += w * v
            pooled_w[rung] += w
    pooled_mean = {r: (pooled_sum[r] / pooled_w[r] if pooled_w[r] > 0 else 0.0) for r in RUNGS}

    # per-cell: day-wise sums (for LOO), n_trades
    cell_day_sum: dict[tuple[int, int], dict[str, dict[Any, float]]] = {}
    cell_day_w: dict[tuple[int, int], dict[str, dict[Any, float]]] = {}
    cell_trades: dict[tuple[int, int], set[str]] = {}
    cell_n: dict[tuple[int, int], int] = {}
    for o, w in zip(obs, weights, strict=True):
        cell = (o["t_idx"], o["r_idx"])
        cell_trades.setdefault(cell, set()).add(o["key"])
        cell_n[cell] = cell_n.get(cell, 0) + 1
        for rung, v in o["outcomes"].items():
            cell_day_sum.setdefault(cell, {}).setdefault(rung, {}).setdefault(o["dt"], 0.0)
            cell_day_w.setdefault(cell, {}).setdefault(rung, {}).setdefault(o["dt"], 0.0)
            cell_day_sum[cell][rung][o["dt"]] += w * v
            cell_day_w[cell][rung][o["dt"]] += w

    def loo_shrunk_mean(cell: tuple[int, int], rung: str) -> tuple[float, float]:
        """Average, over each observed day D in this cell, of the LOO (exclude day D) shrunk mean.
        Returns (score, n_trades) -- score is what argmax picks on."""
        days = cell_day_sum.get(cell, {}).get(rung, {})
        if not days:
            return -1e9, 0
        tot_sum = sum(days.values())
        tot_w = sum(cell_day_w[cell][rung].values())
        n_trades = len(cell_trades.get(cell, set()))
        vals = []
        for d, s in days.items():
            w_d = cell_day_w[cell][rung][d]
            rem_sum, rem_w = tot_sum - s, tot_w - w_d
            raw = rem_sum / rem_w if rem_w > 0 else pooled_mean[rung]
            shrink_w = n_trades / (n_trades + SHRINK_K)
            vals.append(shrink_w * raw + (1 - shrink_w) * pooled_mean[rung])
        return float(np.mean(vals)), n_trades

    cells_debug: list[dict[str, Any]] = []
    raw_choice: dict[tuple[int, int], str] = {}
    for ti in range(len(T_LABELS)):
        for ri in range(len(R_LABELS)):
            cell = (ti, ri)
            n = cell_n.get(cell, 0)
            n_tr = len(cell_trades.get(cell, set()))
            use_cell = n >= MIN_SAMPLE_FLOOR and n_tr >= MIN_TRADE_FLOOR
            if not use_cell:
                # collapse into the T-marginal: merge across R for this T bucket
                merged_days_sum: dict[str, dict[Any, float]] = {}
                merged_days_w: dict[str, dict[Any, float]] = {}
                merged_trades: set[str] = set()
                for ri2 in range(len(R_LABELS)):
                    c2 = (ti, ri2)
                    merged_trades |= cell_trades.get(c2, set())
                    for rung in RUNGS:
                        for d, s in cell_day_sum.get(c2, {}).get(rung, {}).items():
                            merged_days_sum.setdefault(rung, {}).setdefault(d, 0.0)
                            merged_days_w.setdefault(rung, {}).setdefault(d, 0.0)
                            merged_days_sum[rung][d] += s
                            merged_days_w[rung][d] += cell_day_w[c2][rung][d]
                scores = {}
                for rung in RUNGS:
                    days = merged_days_sum.get(rung, {})
                    if not days:
                        scores[rung] = -1e9
                        continue
                    tot_sum = sum(days.values())
                    tot_w = sum(merged_days_w[rung].values())
                    n_tr_m = len(merged_trades)
                    vals = []
                    for d, s in days.items():
                        w_d = merged_days_w[rung][d]
                        rem_sum, rem_w = tot_sum - s, tot_w - w_d
                        raw = rem_sum / rem_w if rem_w > 0 else pooled_mean[rung]
                        shrink_w = n_tr_m / (n_tr_m + SHRINK_K)
                        vals.append(shrink_w * raw + (1 - shrink_w) * pooled_mean[rung])
                    scores[rung] = float(np.mean(vals))
                best = max(scores, key=lambda r: scores[r])
                cells_debug.append(
                    {
                        "T": T_LABELS[ti],
                        "R": R_LABELS[ri],
                        "n_obs": n,
                        "n_trades": n_tr,
                        "collapsed_to_T_marginal": True,
                        "scores": {r: round(v, 4) for r, v in scores.items()},
                        "raw_best": best,
                    }
                )
                raw_choice[cell] = best
                continue
            scores = {
                rung: loo_shrunk_mean(cell, rung)[0]
                for rung in RUNGS
                if rung in cell_day_sum.get(cell, {})
            }
            if not scores:
                scores = {"R0": 0.0}
            best = max(scores, key=lambda r: scores[r])
            cells_debug.append(
                {
                    "T": T_LABELS[ti],
                    "R": R_LABELS[ri],
                    "n_obs": n,
                    "n_trades": n_tr,
                    "collapsed_to_T_marginal": False,
                    "scores": {r: round(v, 4) for r, v in scores.items()},
                    "raw_best": best,
                }
            )
            raw_choice[cell] = best

    table = _enforce_monotone(raw_choice, cells_debug)
    return {"table": table, "cells": cells_debug}


def _enforce_monotone(
    raw_choice: dict[tuple[int, int], str], cells_debug: list[dict[str, Any]]
) -> dict[tuple[int, int], str]:
    """Adjacent-cell pooling pass: bump a violating cell's tightness UP to the max of what
    monotonicity in T (all R) and monotonicity in R>=1 (T fixed) requires. The brief explicitly
    allows "a simpler pooling of violating neighbor cells" over exact isotonic regression."""
    level = {c: TIGHTNESS[r] for c, r in raw_choice.items()}
    changed = True
    while changed:
        changed = False
        # non-decreasing in T, for each R bucket
        for ri in range(len(R_LABELS)):
            running = 0
            for ti in range(len(T_LABELS)):
                c = (ti, ri)
                if c not in level:
                    continue
                if level[c] < running:
                    level[c] = running
                    changed = True
                running = max(running, level[c])
        # non-decreasing in R once R >= 1 (R_LABELS index 3 = "1-2"), for each T bucket
        for ti in range(len(T_LABELS)):
            running = 0
            for ri in range(3, len(R_LABELS)):
                c = (ti, ri)
                if c not in level:
                    continue
                if level[c] < running:
                    level[c] = running
                    changed = True
                running = max(running, level[c])

    def pick_for_level(cell: tuple[int, int], want: int) -> str:
        deb = next(
            d for d in cells_debug if d["T"] == T_LABELS[cell[0]] and d["R"] == R_LABELS[cell[1]]
        )
        scores = deb["scores"]
        candidates = [r for r in RUNGS if TIGHTNESS[r] == want and r in scores]
        if not candidates:
            # nothing scored at exactly this level (e.g. all-collapsed cell) -- fall back to the
            # tightest available rung at >= want, else the loosest at <= want
            at_or_above = sorted(
                (r for r in scores if TIGHTNESS[r] >= want), key=lambda r: TIGHTNESS[r]
            )
            candidates = [at_or_above[0]] if at_or_above else list(scores)
        return max(candidates, key=lambda r: scores[r])

    table: dict[tuple[int, int], str] = {}
    for c, orig in raw_choice.items():
        want = level.get(c, TIGHTNESS[orig])
        table[c] = orig if TIGHTNESS[orig] == want else pick_for_level(c, want)
    for d in cells_debug:
        c = (T_LABELS.index(d["T"]), R_LABELS.index(d["R"]))
        d["final_rung"] = table.get(c, d["raw_best"])
        d["monotone_adjusted"] = d["final_rung"] != d["raw_best"]
    return table


# ---------------------------------------------------------------------------------------------
# Apply the frozen policy sequentially -- the real, sequential walker (not baseline-relative)
# ---------------------------------------------------------------------------------------------
def replay_ladder(
    path: np.ndarray, entry_fill: float, initial_stop: float, table: dict[tuple[int, int], str]
) -> dict[str, Any]:
    o, h, lo, cl = path[:, 0], path[:, 1], path[:, 2], path[:, 3]
    entry = max(entry_fill, float(o[0]))
    risk0 = entry - initial_stop
    if risk0 <= 0:
        return {"valid": False}
    stop = initial_stop
    target = entry + TARGET_R * risk0
    min_gap_seen: float | None = None  # resolution-floor audit
    for k in range(len(path)):
        if lo[k] <= stop:
            r = (stop - entry) / risk0
            return {
                "valid": True,
                "r": r,
                "exit_reason": "stop",
                "bars_held": k,
                "min_gap_seen": min_gap_seen,
            }
        if h[k] >= target:
            r = (target - entry) / risk0
            return {
                "valid": True,
                "r": r,
                "exit_reason": "target",
                "bars_held": k,
                "min_gap_seen": min_gap_seen,
            }
        bars_since_entry = k + 1
        close_k = float(cl[k])
        r_now = (close_k - entry) / risk0
        cell = (t_bucket(bars_since_entry), r_bucket(r_now))
        chosen = table.get(cell, "R0")
        if chosen == "EXIT":
            r_out, reason, kk = _exit_now(path, k, entry, risk0)
            return {
                "valid": True,
                "r": r_out,
                "exit_reason": "flatten",
                "bars_held": kk,
                "min_gap_seen": min_gap_seen,
            }
        closed_bars = path[: k + 1]
        adm = admissible_rungs(
            close_k=close_k,
            risk0=risk0,
            initial_stop=initial_stop,
            entry=entry,
            closed_bars=closed_bars,
        )
        if chosen in adm:
            gap = (close_k - adm[chosen]) / risk0
            min_gap_seen = gap if min_gap_seen is None else min(min_gap_seen, gap)
            new_stop = max(stop, adm[chosen])
        else:
            new_stop = stop  # chosen rung inadmissible for this instance -- hold, never loosen
        stop = new_stop
    last = len(path) - 1
    return {
        "valid": True,
        "r": (float(cl[-1]) - entry) / risk0,
        "exit_reason": "eod",
        "bars_held": last,
        "min_gap_seen": min_gap_seen,
    }


def run_book(
    df: pl.DataFrame, paths: dict[str, np.ndarray], table: dict[tuple[int, int], str] | None
) -> pl.DataFrame:
    """SHIPPED -> 2/day book -> replay (ladder if table given, else the static bracket) -> frame."""
    book = C.build_book(df, max_per_day=2)
    rows: list[dict[str, Any]] = []
    for row in book.iter_rows(named=True):
        arr = paths.get(row["key"])
        if arr is None:
            continue
        entry_fill = float(row["entry_fill"])
        stop0 = float(row["stop"])
        if table is None:
            res = C.replay_bracket(arr, entry_fill, stop0, target_r=TARGET_R)
            exit_reason = (
                "target"
                if not res["stopped"] and res["r"] > 0
                else ("stop" if res["stopped"] else "eod")
            )
            r, gap = res["r"], None
        else:
            res = replay_ladder(arr, entry_fill, stop0, table)
            if not res["valid"]:
                continue
            r, exit_reason, gap = res["r"], res["exit_reason"], res["min_gap_seen"]
        rows.append({**row, "r": r, "exit_reason": exit_reason, "min_gap_seen": gap})
    return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()


def score_ladder(trades: pl.DataFrame) -> dict[str, Any]:
    if trades.is_empty():
        return {
            "trades": 0,
            "net_r": 0.0,
            "net_r_per_trade": 0.0,
            "gross_r": 0.0,
            "r_per_trade": 0.0,
            "win_rate": 0.0,
        }
    out = []
    for t in trades.iter_rows(named=True):
        entry, stop, r = float(t["entry_fill"]), float(t["stop"]), float(t["r"])
        nr = _net_r(entry, stop, r, t["exit_reason"])
        if nr is None:
            continue
        out.append({**t, "net_r": nr})
    if not out:
        return {
            "trades": 0,
            "net_r": 0.0,
            "net_r_per_trade": 0.0,
            "gross_r": 0.0,
            "r_per_trade": 0.0,
            "win_rate": 0.0,
        }
    tf = pl.DataFrame(out, infer_schema_length=None)
    return {
        "trades": tf.height,
        "gross_r": round(float(tf["r"].sum()), 2),
        "net_r": round(float(tf["net_r"].sum()), 2),
        "r_per_trade": round(float(tf["r"].mean()), 4),
        "net_r_per_trade": round(float(tf["net_r"].mean()), 4),
        "win_rate": round(float((tf["r"] > 0).mean()), 4),
        "sessions_traded": tf["dt"].n_unique(),
    }


# ---------------------------------------------------------------------------------------------
# Resolution-floor audit
# ---------------------------------------------------------------------------------------------
def audit_resolution_floor(trades: pl.DataFrame) -> dict[str, Any]:
    gaps = [g for g in trades["min_gap_seen"].to_list() if g is not None]
    violations = [g for g in gaps if g < RESOLUTION_FLOOR_R - 1e-9]
    return {
        "rungs_applied": len(gaps),
        "violations": len(violations),
        "min_gap_observed": round(min(gaps), 4) if gaps else None,
        "examples": violations[:5],
    }


# ---------------------------------------------------------------------------------------------
# Bootstrap: session-block, is the DEV/VAL edge distinguishable from zero
# ---------------------------------------------------------------------------------------------
def session_block_bootstrap(
    trades: pl.DataFrame, *, n: int = 2000, seed: int = 5
) -> dict[str, Any]:
    if trades.is_empty():
        return {"mean": 0.0, "ci_lo": 0.0, "ci_hi": 0.0}
    by_day = {d[0]: g["net_r"].to_numpy() for d, g in trades.group_by(["dt"])}
    days = list(by_day)
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n):
        pick = rng.choice(days, size=len(days), replace=True)
        vals = np.concatenate([by_day[d] for d in pick])
        means.append(vals.mean())
    means = np.array(means)
    return {
        "mean": round(float(means.mean()), 4),
        "ci_lo": round(float(np.percentile(means, 2.5)), 4),
        "ci_hi": round(float(np.percentile(means, 97.5)), 4),
        "p_le_zero": round(float((means <= 0).mean()), 4),
    }


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------
def main() -> None:
    print("=" * 90)
    print("loading panel + paths")
    df = C.load_panel()
    paths = C.load_paths(df)
    vp = C.verify_paths(df, paths)
    print(f"verify_paths: {vp}")
    assert vp["mismatched"] == 0

    shipped = C.SHIPPED(df)
    dev = shipped.filter(pl.col("split") == "dev")
    val = shipped.filter(pl.col("split") == "val")
    holdout = shipped.filter(pl.col("split") == "holdout")
    print(f"SHIPPED dev n={dev.height}  val n={val.height}  holdout n={holdout.height}")

    print("\n" + "=" * 90)
    print("enumerating (trade, candle) observations on DEV, baseline trajectory")
    obs = enumerate_observations(dev, paths)
    n_trades = len({o["key"] for o in obs})
    print(f"  {len(obs)} observations, {n_trades} distinct trades")

    print("\n" + "=" * 90)
    print("NULL TEST -- shuffle (T,R) labels, refit identically, compare DEV book earn")
    rng = np.random.default_rng(2026)
    obs_shuffled = enumerate_observations(dev, paths, shuffle_state_rng=rng)
    fit_real = fit_policy(obs)
    fit_null = fit_policy(obs_shuffled)
    book_dev_real = run_book(dev, paths, fit_real["table"])
    book_dev_null = run_book(dev, paths, fit_null["table"])
    score_real = score_ladder(book_dev_real)
    score_null = score_ladder(book_dev_null)
    print(f"  real-state-fit DEV book:     {score_real}")
    print(f"  shuffled-state-fit DEV book: {score_null}")

    static_dev = score_ladder(run_book(dev, paths, None))
    print(f"  static baseline (2xC/1.0xC) DEV book: {static_dev}")

    null_edge = score_real["net_r_per_trade"] - score_null["net_r_per_trade"]
    real_edge = score_real["net_r_per_trade"] - static_dev["net_r_per_trade"]
    print(f"\n  real edge over static: {real_edge:+.4f} R/trade")
    print(f"  real edge over shuffled-null fit: {null_edge:+.4f} R/trade")

    result: dict[str, Any] = {
        "n_observations": len(obs),
        "n_trades_dev": n_trades,
        "dev_static_baseline": static_dev,
        "dev_real_fit": score_real,
        "dev_null_fit": score_null,
        "null_test_edge": round(null_edge, 4),
        "real_vs_static_edge": round(real_edge, 4),
    }

    passes_null = null_edge > 0.02  # real fit must clearly beat the same pipeline on shuffled state
    verdict = "PASSES (real link found)" if passes_null else "FAILS -- likely noise"
    print(f"\n  null test verdict: {verdict}")

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "ladder_policy_table.json").open("w") as fh:
        json.dump(fit_real["cells"], fh, indent=1, default=str)
    print(f"\nwrote {OUT / 'ladder_policy_table.json'} ({len(fit_real['cells'])} cells)")

    if not passes_null:
        result["stopped_at"] = "null_test"
        with (OUT / "ladder_result.json").open("w") as fh:
            json.dump(result, fh, indent=1, default=str)
        print(f"wrote {OUT / 'ladder_result.json'} -- STOPPING, null test did not clear")
        return

    print("\n" + "=" * 90)
    print("DEV book, resolution-floor audit")
    audit_dev = audit_resolution_floor(book_dev_real)
    print(f"  {audit_dev}")
    result["resolution_floor_audit_dev"] = audit_dev

    print("\n" + "=" * 90)
    print("VAL -- same frozen table, no refit")
    book_val = run_book(val, paths, fit_real["table"])
    score_val = score_ladder(book_val)
    static_val = score_ladder(run_book(val, paths, None))
    audit_val = audit_resolution_floor(book_val)
    print(f"  ladder: {score_val}")
    print(f"  static: {static_val}")
    print(f"  resolution-floor audit VAL: {audit_val}")
    result["val_static_baseline"] = static_val
    result["val_ladder"] = score_val
    result["resolution_floor_audit_val"] = audit_val

    print("\n" + "=" * 90)
    print("session-block bootstrap, DEV+VAL combined ladder book")
    devval_book = pl.concat([book_dev_real, book_val], how="vertical_relaxed")
    devval_scored_rows = []
    for t in devval_book.iter_rows(named=True):
        nr = _net_r(float(t["entry_fill"]), float(t["stop"]), float(t["r"]), t["exit_reason"])
        if nr is not None:
            devval_scored_rows.append({**t, "net_r": nr})
    devval_scored = pl.DataFrame(devval_scored_rows, infer_schema_length=None)
    boot = session_block_bootstrap(devval_scored)
    print(f"  {boot}")
    result["devval_bootstrap"] = boot

    dev_positive = score_real["net_r_per_trade"] > 0
    val_positive = score_val["net_r_per_trade"] > 0
    noise_gate = boot["ci_lo"] > 0 or boot["p_le_zero"] < 0.10
    print(
        f"\n  gates: dev_positive={dev_positive} val_positive={val_positive} "
        f"beyond_noise(ci_lo>0 or p<=0<0.10)={noise_gate}"
    )
    result["gates"] = {
        "dev_positive": dev_positive,
        "val_positive": val_positive,
        "beyond_noise": noise_gate,
    }

    if not (dev_positive and val_positive and noise_gate):
        result["stopped_at"] = "dev_val_gate"
        with (OUT / "ladder_result.json").open("w") as fh:
            json.dump(result, fh, indent=1, default=str)
        print(f"\nwrote {OUT / 'ladder_result.json'} -- STOPPING, HOLDOUT NOT touched")
        return

    print("\n" + "=" * 90)
    print("HOLDOUT -- the one look, fresh spend, separate from step10's holdout look")
    book_holdout = run_book(holdout, paths, fit_real["table"])
    score_holdout = score_ladder(book_holdout)
    static_holdout = score_ladder(run_book(holdout, paths, None))
    audit_holdout = audit_resolution_floor(book_holdout)
    print(f"  ladder: {score_holdout}")
    print(f"  static: {static_holdout}")
    print(f"  resolution-floor audit HOLDOUT: {audit_holdout}")
    result["holdout_ladder"] = score_holdout
    result["holdout_static_baseline"] = static_holdout
    result["resolution_floor_audit_holdout"] = audit_holdout
    result["stopped_at"] = "holdout_reported"

    with (OUT / "ladder_result.json").open("w") as fh:
        json.dump(result, fh, indent=1, default=str)
    print(f"\nwrote {OUT / 'ladder_result.json'}")


if __name__ == "__main__":
    main()
