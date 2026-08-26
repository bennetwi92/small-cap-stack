"""Step 10 (#715) — a stop AND target that recompute after every closed 5-min candle.

Follow-up to #713 (a target fixed once at entry, rejected). This is the real ask: a policy that
looks at `path[:k+1]` (bar k CLOSED) and decides the stop/target that apply from bar k+1 onward.
Built on `spikes/engine_lab/common.py` and, for the C-unit convention, on Agent C's
`spikes/engine_lab/exits/FINDINGS.md` (do not re-litigate: C = entry - shipped stop is the right
unit for both legs; a wider static stop at m=1.30xC is a real, already-measured, unshipped finding
used here as one of the two base cases).

Nothing in `common.py` or `exits/step1..9` is touched. This file is self-contained.

## The no-lookahead discipline

`replay_dynamic()` generalizes `common.replay_bracket()`. `risk0 = entry - initial_stop` is fixed
ONCE at entry -- R is always measured against the original risk, exactly like
`portfolio/exit.py::simulate_exit`. On each bar the stop is checked first (as always), then the
target; only if neither resolves the trade does the policy get a look, and it only ever sees
`path[:k+1]` -- bar k, now closed. A policy may tighten a stop, never loosen it (asserted inside
`replay_dynamic`, since a stop that can loosen is a different, riskier product).

`check_equivalence()` proves `replay_dynamic(..., policy_static)` reproduces `replay_bracket()`
bit-for-bit. `check_no_lookahead()` proves mutating every bar strictly after the bar a trade
resolved on changes nothing about the outcome. Both must pass before anything below is trusted --
`main()` runs them first and aborts the sweep if either fails.

## Naming: `k` collision

The issue brief names the chandelier/trail multiple `k`, but `replay_dynamic` also passes the bar
index as `k` to every policy call. Those can't both be `k` in one Python call. The trail multiple is
named `trail_k` everywhere below; the consolidation-range unit (issue's `C`) is `c_unit` (`C` is
already the module alias for `engine_lab.common` in this package's convention, see `lab.py`).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine_lab import common as C  # noqa: E402

OUT = C.REPO / "data/spikes/engine-lab/exits"

# ---------------------------------------------------------------------------------------------
# The dynamic bracket walker
# ---------------------------------------------------------------------------------------------

PolicyFn = Callable[..., tuple[float, float | None]]


def replay_dynamic(
    path: np.ndarray,
    entry_fill: float,
    initial_stop: float,
    initial_target_r: float | None,
    policy: PolicyFn,
    **policy_kwargs: Any,
) -> dict[str, Any]:
    """Walk a trade with a stop/target that the policy may revise after every closed bar.

    Semantics deliberately mirror `common.replay_bracket`: `path[0]` is the trigger bar, fill is
    `max(entry_fill, open[0])`, the stop is checked before the target on every bar including the
    entry bar. When `policy` is the identity (`policy_static`) this returns byte-for-byte the same
    numbers as `replay_bracket` -- see `check_equivalence()`.

    `risk0` (the R denominator) is fixed at entry and never recomputed. A policy may only tighten
    the stop; loosening raises `ValueError`.
    """
    o, h, lo, cl = path[:, 0], path[:, 1], path[:, 2], path[:, 3]
    entry = max(entry_fill, float(o[0]))
    risk0 = entry - initial_stop
    if risk0 <= 0:
        return {
            "exit_price": entry,
            "r": 0.0,
            "max_r": 0.0,
            "stopped": True,
            "bars_held": 0,
            "valid": False,
            "exit_reason": "invalid",
        }
    stop = initial_stop
    target = entry + initial_target_r * risk0 if initial_target_r is not None else None
    max_high = entry if lo[0] <= stop else float(h[0])

    for k in range(len(path)):
        if lo[k] <= stop:  # stop first, always -- including the entry bar
            mfe = (max_high - entry) / risk0
            return {
                "exit_price": stop,
                "r": (stop - entry) / risk0,
                "max_r": round(mfe, 3),
                "stopped": True,
                "bars_held": k,
                "valid": True,
                "exit_reason": "stop",
            }
        if h[k] > max_high:
            max_high = float(h[k])
        if target is not None and h[k] >= target:
            return {
                "exit_price": target,
                "r": (target - entry) / risk0,
                "max_r": round((max_high - entry) / risk0, 3),
                "stopped": False,
                "bars_held": k,
                "valid": True,
                "exit_reason": "target",
            }
        # bar k closed without resolving the trade -> the policy may revise stop/target for k+1 on.
        # It sees path[:k+1] (bar k INCLUSIVE) and nothing beyond -- this is the no-lookahead line.
        new_stop, new_target = policy(
            closed_bars=path[: k + 1],
            entry=entry,
            risk0=risk0,
            stop=stop,
            target=target,
            max_high=max_high,
            k=k,
            **policy_kwargs,
        )
        if new_stop < stop - 1e-6:
            raise ValueError(f"policy loosened the stop: {stop!r} -> {new_stop!r} at bar {k}")
        stop, target = new_stop, new_target

    # never stopped, never hit target -> out at the last close (16:00). No overnight holds.
    return {
        "exit_price": float(cl[-1]),
        "r": (float(cl[-1]) - entry) / risk0,
        "max_r": round((max_high - entry) / risk0, 3),
        "stopped": False,
        "bars_held": len(path) - 1,
        "valid": True,
        "exit_reason": "eod",
    }


# ---------------------------------------------------------------------------------------------
# Policy families -- every one accepts (and ignores) kwargs it does not need, so the driver can
# pass a uniform kwarg set (including the per-trade c_unit) to any of them.
# ---------------------------------------------------------------------------------------------
def policy_static(
    *,
    closed_bars: np.ndarray,
    entry: float,
    risk0: float,
    stop: float,
    target: float | None,
    max_high: float,
    k: int,
    **_ignore: Any,
) -> tuple[float, float | None]:
    """No-op. Used only to prove `replay_dynamic` == `replay_bracket`."""
    return stop, target


def policy_breakeven(
    *,
    closed_bars: np.ndarray,
    entry: float,
    risk0: float,
    stop: float,
    target: float | None,
    max_high: float,
    k: int,
    be_r: float,
    **_ignore: Any,
) -> tuple[float, float | None]:
    """Continuous version of `portfolio_breakeven_r`: once MFE >= be_r, stop -> max(stop, entry)."""
    if (max_high - entry) / risk0 >= be_r:
        return max(stop, entry), target
    return stop, target


def policy_chandelier(
    *,
    closed_bars: np.ndarray,
    entry: float,
    risk0: float,
    stop: float,
    target: float | None,
    max_high: float,
    k: int,
    arm_r: float,
    trail_k: float,
    c_unit: float,
    **_ignore: Any,
) -> tuple[float, float | None]:
    """Once armed at arm_r R, trail stop = max(stop, running high - trail_k * C) every bar."""
    if (max_high - entry) / risk0 >= arm_r:
        return max(stop, max_high - trail_k * c_unit), target
    return stop, target


def policy_swing_low(
    *,
    closed_bars: np.ndarray,
    entry: float,
    risk0: float,
    stop: float,
    target: float | None,
    max_high: float,
    k: int,
    arm_r: float,
    lookback_n: int,
    **_ignore: Any,
) -> tuple[float, float | None]:
    """Once armed, stop -> max(stop, min low over the last min(lookback_n, bars since entry))."""
    if (max_high - entry) / risk0 >= arm_r:
        n = min(lookback_n, len(closed_bars))
        return max(stop, float(closed_bars[-n:, 2].min())), target
    return stop, target


def policy_breakeven_then_trail(
    *,
    closed_bars: np.ndarray,
    entry: float,
    risk0: float,
    stop: float,
    target: float | None,
    max_high: float,
    k: int,
    be_r: float,
    trail_r: float,
    trail_k: float,
    c_unit: float,
    **_ignore: Any,
) -> tuple[float, float | None]:
    """Breakeven at be_r, then a chandelier trail (k*C) once profit exceeds trail_r (>= be_r)."""
    profit_r = (max_high - entry) / risk0
    new_stop = stop
    if profit_r >= be_r:
        new_stop = max(new_stop, entry)
    if profit_r >= trail_r:
        new_stop = max(new_stop, max_high - trail_k * c_unit)
    return new_stop, target


def policy_target_ratchet(
    *,
    closed_bars: np.ndarray,
    entry: float,
    risk0: float,
    stop: float,
    target: float | None,
    max_high: float,
    k: int,
    arm_r: float,
    trail_k: float,
    c_unit: float,
    **_ignore: Any,
) -> tuple[float, float | None]:
    """Once armed at arm_r R, DROP the fixed target and trail the stop at running high - trail_k*C.

    Starts with NO target at all (see `run_policy`'s `target_r_base=None` for this family) --
    "let it run, protect with a trail" from bar zero, trailing engaged once armed.
    """
    profit_r = (max_high - entry) / risk0
    if profit_r >= arm_r:
        return max(stop, max_high - trail_k * c_unit), None
    return stop, target


def policy_breakeven_then_ratchet(
    *,
    closed_bars: np.ndarray,
    entry: float,
    risk0: float,
    stop: float,
    target: float | None,
    max_high: float,
    k: int,
    be_r: float,
    arm_r: float,
    trail_k: float,
    c_unit: float,
    **_ignore: Any,
) -> tuple[float, float | None]:
    """Widen-round combo: breakeven at be_r, THEN drop the target and trail once armed at arm_r."""
    profit_r = (max_high - entry) / risk0
    new_stop, new_target = stop, target
    if profit_r >= be_r:
        new_stop = max(new_stop, entry)
    if profit_r >= arm_r:
        new_stop = max(new_stop, max_high - trail_k * c_unit)
        new_target = None
    return new_stop, new_target


FAMILIES: dict[str, PolicyFn] = {
    "static": policy_static,
    "breakeven": policy_breakeven,
    "chandelier": policy_chandelier,
    "swing_low": policy_swing_low,
    "breakeven_then_trail": policy_breakeven_then_trail,
    "target_ratchet": policy_target_ratchet,
    "breakeven_then_ratchet": policy_breakeven_then_ratchet,
}


# ---------------------------------------------------------------------------------------------
# Driver: per-row base stop/target -> replay_dynamic -> a scoreable trade frame
# ---------------------------------------------------------------------------------------------
def run_policy(
    df: pl.DataFrame,
    paths: dict[str, np.ndarray],
    *,
    base_m: float,
    target_r_base: float | None,
    policy: PolicyFn,
    policy_kwargs: dict[str, Any],
    max_per_day: int = 2,
) -> pl.DataFrame:
    """SHIPPED (or whatever selection is already applied to `df`) -> 2/day book -> replay -> frame.

    `base_m` is the base stop as a multiple of C = entry - shipped_stop (1.00 = shipped, 1.30 =
    Agent C's proposed, unshipped, wider stop). `target_r_base` is in units of the resulting risk0
    (None for `policy_target_ratchet`, which starts with no resting target at all).

    The `stop` column is overwritten with the INITIAL stop (risk0's denominator) -- that is what
    `score()` sizes and prices costs off, matching `portfolio_/exit.py`'s fixed-risk convention;
    the trailed stop only ever tightens, it does not change what was risked at entry.
    """
    book = C.build_book(df, max_per_day=max_per_day)
    rows: list[dict[str, Any]] = []
    for row in book.iter_rows(named=True):
        arr = paths.get(row["key"])
        if arr is None:
            continue
        entry_fill = float(row["entry_fill"])
        shipped_stop = float(row["stop"])
        entry = max(entry_fill, float(arr[0, 0]))
        c_unit = entry - shipped_stop
        if c_unit <= 0:
            continue
        initial_stop = entry - base_m * c_unit
        res = replay_dynamic(
            arr, entry_fill, initial_stop, target_r_base, policy, c_unit=c_unit, **policy_kwargs
        )
        if not res["valid"]:
            continue
        rows.append(
            {
                **row,
                "stop": initial_stop,
                "r": res["r"],
                "bars_held": res["bars_held"],
                "exit_reason": res["exit_reason"],
            }
        )
    return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()


def _strip(d: dict[str, Any]) -> dict[str, Any]:
    """`score()` embeds a `_trades` polars DataFrame for internal use -- drop it before JSON."""
    return {k: v for k, v in d.items() if k != "_trades"}


# ---------------------------------------------------------------------------------------------
# Bug 2 fix -- `Costs.usd` charges slippage keyed on `won`, which is correct for a static bracket
# (every winner fills on a resting limit) but wrong here: a dynamic policy can win via the trailing
# STOP (a market order), which must pay slippage regardless of sign. `common.py` is not touched --
# this reimplements just enough of `score()` to key the slip leg on `exit_reason` instead of `won`.
# Only an exit_reason of "target" (a resting limit fill) is slip-free; "stop" and "eod" both pay it.
# ---------------------------------------------------------------------------------------------
def score_dynamic(
    trades: pl.DataFrame,
    *,
    sizing: C.Sizing | None = None,
    costs: C.Costs | None = None,
    sessions: int | None = None,
) -> dict[str, Any]:
    sizing = C.DEFAULT_SIZING if sizing is None else sizing
    costs = C.DEFAULT_COSTS if costs is None else costs
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
    out: list[dict[str, Any]] = []
    for t in rows:
        entry, stop, r = float(t["entry_fill"]), float(t["stop"]), float(t["r"])
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
        commission = 2 * max(costs.comm_min, qty * costs.comm_per_share)
        per_share = 2 * qty * (costs.exchange + costs.clearing)
        taf = min(qty * costs.taf_per_share, costs.taf_max)
        sec = qty * exit_price * costs.sec_rate
        # the fix: slip is keyed on HOW the trade exited, not on whether it won.
        paid_via_limit = t["exit_reason"] == "target"
        slip = 0.0 if paid_via_limit else qty * costs.stop_slip_ticks * C.TICK
        fees = commission + per_share + taf + sec
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
    if tf.is_empty():
        return {
            "trades": 0,
            "gross_r": 0.0,
            "net_r": 0.0,
            "r_per_trade": 0.0,
            "net_r_per_trade": 0.0,
            "sessions_available": n_sessions,
        }
    cum = np.cumsum(tf["net_r"].to_numpy())
    dd = float((cum - np.maximum.accumulate(cum)).min()) if len(cum) else 0.0
    return {
        "trades": tf.height,
        "sessions_available": n_sessions,
        "sessions_traded": tf["dt"].n_unique(),
        "trades_per_session": round(tf.height / n_sessions, 3) if n_sessions else 0.0,
        "gross_r": round(float(tf["r"].sum()), 2),
        "net_r": round(float(tf["net_r"].sum()), 2),
        "r_per_trade": round(float(tf["r"].mean()), 4),
        "net_r_per_trade": round(float(tf["net_r"].mean()), 4),
        "win_rate": round(float((tf["r"] > 0).mean()), 4),
        "net_usd": round(float(tf["net_usd"].sum()), 2),
        "max_dd_net_r": round(dd, 2),
        "unaffordable": int(len(out) - tf.height),
        "_trades": tf,
    }


def net_per_trade(df: pl.DataFrame, paths: dict, **kw: Any) -> dict[str, Any]:
    trades = run_policy(df, paths, **kw)
    if trades.is_empty():
        return {
            "trades": 0,
            "net_r_per_trade": 0.0,
            "r_per_trade": 0.0,
            "net_r": 0.0,
            "win_rate": 0.0,
        }
    return score_dynamic(trades, sessions=trades["dt"].n_unique())


# ---------------------------------------------------------------------------------------------
# Equivalence check -- replay_dynamic(policy_static) must equal replay_bracket exactly
# ---------------------------------------------------------------------------------------------
def check_equivalence(
    df: pl.DataFrame, paths: dict[str, np.ndarray], *, n: int = 300
) -> dict[str, Any]:
    rng = np.random.default_rng(11)
    keys = [k for k in df["key"].to_list() if k in paths]
    pick = rng.choice(len(keys), size=min(n, len(keys)), replace=False)
    rows = df.filter(pl.col("key").is_in([keys[i] for i in pick])).iter_rows(named=True)
    shapes = [
        {"m": 1.0, "target_r": 2.0},
        {"m": 1.3, "target_r": 2.0},
        {"m": 0.8, "target_r": 1.0},
        {"m": 2.0, "target_r": None},
        {"m": 1.15, "target_r": 3.0},
    ]
    worst = 0.0
    checks = 0
    for row in rows:
        arr = paths[row["key"]]
        entry_fill = float(row["entry_fill"])
        shipped_stop = float(row["stop"])
        entry = max(entry_fill, float(arr[0, 0]))
        c_unit = entry - shipped_stop
        if c_unit <= 0:
            continue
        for shp in shapes:
            initial_stop = entry - shp["m"] * c_unit
            slow = C.replay_bracket(arr, entry_fill, initial_stop, target_r=shp["target_r"])
            fast = replay_dynamic(arr, entry_fill, initial_stop, shp["target_r"], policy_static)
            worst = max(worst, abs(slow["r"] - fast["r"]), abs(slow["max_r"] - fast["max_r"]))
            checks += 1
    return {
        "rows": checks // len(shapes),
        "shapes": len(shapes),
        "checks": checks,
        "max_abs_diff": worst,
    }


# ---------------------------------------------------------------------------------------------
# No-lookahead property test -- mutate everything after the resolving bar, outcome must not move
# ---------------------------------------------------------------------------------------------
def check_no_lookahead(
    df: pl.DataFrame, paths: dict[str, np.ndarray], *, n: int = 60
) -> dict[str, Any]:
    rng = np.random.default_rng(23)
    policy_kwargs = {"be_r": 0.5, "trail_r": 0.75, "trail_k": 0.8}
    keys = [k for k in df["key"].to_list() if k in paths]
    rng.shuffle(keys)
    checked = 0
    mismatches: list[Any] = []
    for key in keys:
        if checked >= n:
            break
        arr = paths[key]
        row = df.filter(pl.col("key") == key).row(0, named=True)
        entry_fill = float(row["entry_fill"])
        shipped_stop = float(row["stop"])
        entry = max(entry_fill, float(arr[0, 0]))
        c_unit = entry - shipped_stop
        if c_unit <= 0 or len(arr) < 4:
            continue
        initial_stop = entry - 1.3 * c_unit
        base = replay_dynamic(
            arr,
            entry_fill,
            initial_stop,
            2.0,
            policy_breakeven_then_trail,
            c_unit=c_unit,
            **policy_kwargs,
        )
        if not base["valid"] or base["bars_held"] >= len(arr) - 1:
            continue  # nothing after the resolving bar to mutate (EOD exit)
        checked += 1
        mutated = arr.copy()
        m = base["bars_held"]
        tail = len(arr) - (m + 1)
        noise = rng.uniform(0.01, 500.0, size=(tail, 2))
        lo_rand = np.minimum(noise[:, 0], noise[:, 1])
        hi_rand = np.maximum(noise[:, 0], noise[:, 1])
        mutated[m + 1 :, 0] = rng.uniform(0.01, 500.0, size=tail)  # open
        mutated[m + 1 :, 1] = hi_rand  # high
        mutated[m + 1 :, 2] = lo_rand  # low
        mutated[m + 1 :, 3] = rng.uniform(0.01, 500.0, size=tail)  # close
        again = replay_dynamic(
            mutated,
            entry_fill,
            initial_stop,
            2.0,
            policy_breakeven_then_trail,
            c_unit=c_unit,
            **policy_kwargs,
        )
        if (
            again["r"] != base["r"]
            or again["exit_price"] != base["exit_price"]
            or again["bars_held"] != base["bars_held"]
        ):
            mismatches.append((key, base, again))
    return {"checked": checked, "mismatches": len(mismatches), "examples": mismatches[:5]}


# ---------------------------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------------------------
BE_R_GRID = [0.3, 0.5, 0.75, 1.0, 1.5]
TRAIL_K_GRID = [0.4, 0.6, 0.8, 1.0, 1.25, 1.5]
ARM_R_GRID = [0.5, 0.75, 1.0, 1.25, 1.5]
LOOKBACK_N_GRID = [1, 2, 3, 5]
BASE_M_GRID = [1.0, 1.3]

# widened grids, used only if round 1 comes back flat -- see main()
TRAIL_K_WIDE = [0.2, 0.3, *TRAIL_K_GRID, 2.0, 2.5, 3.0]
ARM_R_WIDE = [0.25, *ARM_R_GRID, 1.75, 2.0]
LOOKBACK_N_WIDE = [1, 2, 3, 5, 8]


def sweep_family(
    name: str,
    df_dev: pl.DataFrame,
    paths: dict,
    *,
    base_m_grid: list[float] = BASE_M_GRID,
    be_r_grid: list[float] = BE_R_GRID,
    trail_k_grid: list[float] = TRAIL_K_GRID,
    arm_r_grid: list[float] = ARM_R_GRID,
    lookback_n_grid: list[int] = LOOKBACK_N_GRID,
) -> list[dict[str, Any]]:
    """Every combo for one family, on DEV. No pruning -- every row tried is a row returned."""
    rows: list[dict[str, Any]] = []
    policy = FAMILIES[name]

    def add(base_m: float, target_r_base: float | None, kwargs: dict[str, Any]) -> None:
        r = net_per_trade(
            df_dev,
            paths,
            base_m=base_m,
            target_r_base=target_r_base,
            policy=policy,
            policy_kwargs=kwargs,
        )
        rows.append(
            {
                "family": name,
                "base_m": base_m,
                "target_r_base": target_r_base,
                **kwargs,
                "trades": r["trades"],
                "net_r_per_trade": r.get("net_r_per_trade", 0.0),
                "r_per_trade": r.get("r_per_trade", 0.0),
                "win_rate": r.get("win_rate", 0.0),
                "net_r": r.get("net_r", 0.0),
                "max_dd_net_r": r.get("max_dd_net_r", 0.0),
            }
        )

    if name == "breakeven":
        for base_m in base_m_grid:
            for be_r in be_r_grid:
                add(base_m, 2.0 / base_m, {"be_r": be_r})
    elif name == "chandelier":
        for base_m in base_m_grid:
            for arm_r in arm_r_grid:
                for trail_k in trail_k_grid:
                    add(base_m, 2.0 / base_m, {"arm_r": arm_r, "trail_k": trail_k})
    elif name == "swing_low":
        for base_m in base_m_grid:
            for arm_r in arm_r_grid:
                for lookback_n in lookback_n_grid:
                    add(base_m, 2.0 / base_m, {"arm_r": arm_r, "lookback_n": lookback_n})
    elif name == "breakeven_then_trail":
        for base_m in base_m_grid:
            for be_r in be_r_grid:
                for trail_r in [t for t in be_r_grid if t >= be_r]:
                    for trail_k in trail_k_grid:
                        add(
                            base_m,
                            2.0 / base_m,
                            {"be_r": be_r, "trail_r": trail_r, "trail_k": trail_k},
                        )
    elif name == "target_ratchet":
        for base_m in base_m_grid:
            for arm_r in arm_r_grid:
                for trail_k in trail_k_grid:
                    add(base_m, None, {"arm_r": arm_r, "trail_k": trail_k})
    elif name == "breakeven_then_ratchet":
        for base_m in base_m_grid:
            for be_r in be_r_grid:
                for arm_r in [a for a in arm_r_grid if a >= be_r]:
                    for trail_k in trail_k_grid:
                        add(base_m, None, {"be_r": be_r, "arm_r": arm_r, "trail_k": trail_k})
    else:
        raise ValueError(name)
    return rows


def baseline_static(df: pl.DataFrame, paths: dict, *, base_m: float) -> dict[str, Any]:
    """Target fixed at entry + 2.0*C, regardless of base_m (Bug 1 fix -- see module docstring note
    below `run_policy` and the caller's brief). `target_r_base` is in units of risk0 = base_m*C, so
    the R multiple that lands the target at 2.0*C is `2.0 / base_m`.
    """
    return net_per_trade(
        df,
        paths,
        base_m=base_m,
        target_r_base=2.0 / base_m,
        policy=policy_static,
        policy_kwargs={},
    )


def main() -> None:
    print("=" * 90)
    print("loading panel + paths")
    df = C.load_panel()
    paths = C.load_paths(df)
    vp = C.verify_paths(df, paths)
    print(f"verify_paths: {vp}")
    assert vp["mismatched"] == 0, "verify_paths failed -- do not trust anything downstream"

    print("\nequivalence check (replay_dynamic[policy_static] vs replay_bracket)")
    eq = check_equivalence(df, paths)
    print(f"  {eq}")
    assert eq["max_abs_diff"] == 0.0, "replay_dynamic is not equivalent to replay_bracket -- STOP"

    print("\nno-lookahead property test (mutate every bar after resolution)")
    nl = check_no_lookahead(df, paths)
    print(f"  {nl}")
    assert nl["mismatches"] == 0, "lookahead detected -- STOP, do not trust the sweep"

    shipped = C.SHIPPED(df)
    dev = shipped.filter(pl.col("split") == "dev")
    devval = shipped.filter(pl.col("split") != "holdout")
    raw = df  # no SHIPPED filter -- generality check, used later for the best candidate only

    print(f"\nSHIPPED dev n={dev.height}  SHIPPED dev+val n={devval.height}")

    print("\n" + "=" * 90)
    print("baselines: static bracket, no policy, both base stops, DEV")
    base_results: dict[float, dict[str, Any]] = {}
    for m in BASE_M_GRID:
        b = baseline_static(dev, paths, base_m=m)
        base_results[m] = b
        print(f"  m={m:.2f}  target=2.0R  " + C.brief(b))

    print("\n" + "=" * 90)
    print("ROUND 1 sweep -- all families, DEV, standard grids")
    all_rows: list[dict[str, Any]] = []
    for name in FAMILIES:
        if name == "static":
            continue
        rows = sweep_family(name, dev, paths)
        all_rows.extend(rows)
        best = max(rows, key=lambda r: r["net_r_per_trade"])
        skip = (
            "family",
            "trades",
            "net_r_per_trade",
            "r_per_trade",
            "win_rate",
            "net_r",
            "max_dd_net_r",
        )
        best_params = {k: v for k, v in best.items() if k not in skip}
        print(
            f"  {name:<24} {len(rows):>4} combos tried, best "
            f"net/trade={best['net_r_per_trade']:+.4f} n={best['trades']} @ {best_params}"
        )

    beat_baseline = [
        r for r in all_rows if r["net_r_per_trade"] > base_results[r["base_m"]]["net_r_per_trade"]
    ]
    print(
        f"\n{len(beat_baseline)} / {len(all_rows)} round-1 combos beat their own base_m's "
        "static baseline"
    )

    round_used = 1
    if not beat_baseline:
        print("\n" + "=" * 90)
        print("ROUND 1 found nothing above baseline -- widening grids (ROUND 2)")
        round_used = 2
        all_rows = []
        for name in FAMILIES:
            if name == "static":
                continue
            rows = sweep_family(
                name,
                dev,
                paths,
                trail_k_grid=TRAIL_K_WIDE,
                arm_r_grid=ARM_R_WIDE,
                lookback_n_grid=LOOKBACK_N_WIDE,
            )
            all_rows.extend(rows)
            best = max(rows, key=lambda r: r["net_r_per_trade"])
            print(
                f"  {name:<24} {len(rows):>4} combos tried, best "
                f"net/trade={best['net_r_per_trade']:+.4f} n={best['trades']}"
            )
        beat_baseline = [
            r
            for r in all_rows
            if r["net_r_per_trade"] > base_results[r["base_m"]]["net_r_per_trade"]
        ]
        print(
            f"\n{len(beat_baseline)} / {len(all_rows)} round-2 combos beat "
            "their own base_m's baseline"
        )

    # -----------------------------------------------------------------------------------------
    # Full DEV table -- every combo, no pruning
    # -----------------------------------------------------------------------------------------
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "dynamic_dev_sweep.json").open("w") as fh:
        json.dump({"round": round_used, "rows": all_rows}, fh, indent=1, default=float)
    print(f"\nwrote {OUT / 'dynamic_dev_sweep.json'} ({len(all_rows)} rows)")

    # -----------------------------------------------------------------------------------------
    # DEV-good candidates -> check freely on VAL (same params, no refit)
    # -----------------------------------------------------------------------------------------
    print("\n" + "=" * 90)
    print(f"DEV candidates that beat baseline (n={len(beat_baseline)}) -- checked on VAL, no refit")
    val_only = shipped.filter(pl.col("split") == "val")
    candidates: list[dict[str, Any]] = []
    beat_baseline_sorted = sorted(beat_baseline, key=lambda r: -r["net_r_per_trade"])
    for r in beat_baseline_sorted[:30]:  # cap the printout; all are in the JSON dump above
        kwargs = {
            k: v
            for k, v in r.items()
            if k
            not in (
                "family",
                "base_m",
                "target_r_base",
                "trades",
                "net_r_per_trade",
                "r_per_trade",
                "win_rate",
                "net_r",
                "max_dd_net_r",
            )
        }
        policy = FAMILIES[r["family"]]
        val_res = net_per_trade(
            val_only,
            paths,
            base_m=r["base_m"],
            target_r_base=r["target_r_base"],
            policy=policy,
            policy_kwargs=kwargs,
        )
        devval_res = net_per_trade(
            devval,
            paths,
            base_m=r["base_m"],
            target_r_base=r["target_r_base"],
            policy=policy,
            policy_kwargs=kwargs,
        )
        rec = {
            **r,
            "val_trades": val_res["trades"],
            "val_net_r_per_trade": val_res.get("net_r_per_trade", 0.0),
            "devval_trades": devval_res["trades"],
            "devval_net_r_per_trade": devval_res.get("net_r_per_trade", 0.0),
            "overfit_flag": val_res.get("net_r_per_trade", 0.0) <= 0,
        }
        candidates.append(rec)
        flag = "OVERFIT (DEV-good/VAL-bad)" if rec["overfit_flag"] else "holds on VAL"
        print(
            f"  {r['family']:<24} m={r['base_m']:.2f} "
            f"dev={r['net_r_per_trade']:+.4f}(n={r['trades']}) "
            f"val={rec['val_net_r_per_trade']:+.4f}(n={rec['val_trades']}) "
            f"devval={rec['devval_net_r_per_trade']:+.4f}(n={rec['devval_trades']})  "
            f"{flag}  {kwargs}"
        )

    with (OUT / "dynamic_candidates.json").open("w") as fh:
        json.dump(candidates, fh, indent=1, default=float)

    survivors = [c for c in candidates if not c["overfit_flag"]]
    print(f"\n{len(survivors)} / {len(candidates)} candidates positive on BOTH DEV and VAL")

    if not survivors:
        print("\nNo candidate survives DEV+VAL cleanly -- HOLDOUT is NOT touched, per the brief.")
        return

    # -----------------------------------------------------------------------------------------
    # Pick the single most robust survivor: highest devval net/trade among those positive on
    # both dev and val, with a light sensitivity check (+/-20% on its own params).
    # -----------------------------------------------------------------------------------------
    best = max(survivors, key=lambda c: c["devval_net_r_per_trade"])
    print("\n" + "=" * 90)
    print(
        f"Most robust survivor: {best['family']} m={best['base_m']} "
        f"target_r_base={best['target_r_base']}"
    )
    print(f"  {best}")

    param_keys = [
        k
        for k in best
        if k
        not in (
            "family",
            "base_m",
            "target_r_base",
            "trades",
            "net_r_per_trade",
            "r_per_trade",
            "win_rate",
            "net_r",
            "max_dd_net_r",
            "val_trades",
            "val_net_r_per_trade",
            "devval_trades",
            "devval_net_r_per_trade",
            "overfit_flag",
        )
    ]
    print("\nsensitivity, +/-20% on each own parameter, DEV+VAL:")
    sens_rows = []
    for pk in param_keys:
        base_val = best[pk]
        for mult in (0.8, 1.2):
            kw = {k: best[k] for k in param_keys}
            kw[pk] = (
                base_val * mult if isinstance(base_val, float) else max(1, round(base_val * mult))
            )
            policy = FAMILIES[best["family"]]
            r = net_per_trade(
                devval,
                paths,
                base_m=best["base_m"],
                target_r_base=best["target_r_base"],
                policy=policy,
                policy_kwargs=kw,
            )
            sens_rows.append({"param": pk, "mult": mult, "value": kw[pk], **_strip(r)})
            print(
                f"  {pk} x{mult}={kw[pk]}: net/trade={r.get('net_r_per_trade', 0.0):+.4f} "
                f"n={r['trades']}"
            )

    # -----------------------------------------------------------------------------------------
    # Generality: same candidate against the raw pool, no SHIPPED filter
    # -----------------------------------------------------------------------------------------
    print("\nraw pool (no SHIPPED filter), DEV+VAL, same params:")
    raw_devval = raw.filter(pl.col("split") != "holdout")
    kw_best = {k: best[k] for k in param_keys}
    raw_res = net_per_trade(
        raw_devval,
        paths,
        base_m=best["base_m"],
        target_r_base=best["target_r_base"],
        policy=FAMILIES[best["family"]],
        policy_kwargs=kw_best,
    )
    raw_base = baseline_static(raw_devval, paths, base_m=best["base_m"])
    print(f"  candidate: {C.brief(raw_res)}")
    print(f"  static baseline (same base_m): {C.brief(raw_base)}")

    # -----------------------------------------------------------------------------------------
    # HOLDOUT -- exactly once, only because a survivor exists
    # -----------------------------------------------------------------------------------------
    print("\n" + "=" * 90)
    print("HOLDOUT -- the one look, for the single most robust candidate only")
    holdout = shipped.filter(pl.col("split") == "holdout")
    holdout_res = net_per_trade(
        holdout,
        paths,
        base_m=best["base_m"],
        target_r_base=best["target_r_base"],
        policy=FAMILIES[best["family"]],
        policy_kwargs=kw_best,
    )
    holdout_base = baseline_static(holdout, paths, base_m=best["base_m"])
    print(f"  candidate: {C.brief(holdout_res)}")
    print(f"  static baseline (same base_m): {C.brief(holdout_base)}")

    with (OUT / "dynamic_result.json").open("w") as fh:
        json.dump(
            {
                "round": round_used,
                "best_candidate": best,
                "sensitivity": sens_rows,
                "raw_pool_devval": {"candidate": _strip(raw_res), "baseline": _strip(raw_base)},
                "holdout": {"candidate": _strip(holdout_res), "baseline": _strip(holdout_base)},
            },
            fh,
            indent=1,
            default=float,
        )
    print(f"\nwrote {OUT / 'dynamic_result.json'}")


if __name__ == "__main__":
    main()
