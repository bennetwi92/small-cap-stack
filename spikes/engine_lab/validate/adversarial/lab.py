"""Adversarial validator — shared helpers.

Validator A in the three-way triangulation on the in-play claim (see ../CLAIM.md).
Everything here is a thin wrapper over `spikes.engine_lab.common`; the harness is NOT forked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spikes.engine_lab import common as C  # noqa: E402

OUT = REPO / "data/spikes/engine-lab/validate/adversarial"
OUT.mkdir(parents=True, exist_ok=True)

# The claim's thresholds, verbatim from CLAIM.md.
IN_PLAY = {"runup": 0.15, "rvol": 2.0, "shares": 50e6}

# Columns the claim's rule reads. Checked against the harness's lookahead guard.
CLAIM_COLS = [
    "passed",
    "cycle_num",
    "staleness_delay_min",
    "entry_fill",
    "stop_pct",
    "trigger_et_min",
    "runup_pre_appearance",
    "rvol_pole",
    "shares_outstanding",
]


def load_panel_checked() -> pl.DataFrame:
    """The shared population, with the harness's own lookahead guard run over the claim's rule."""
    df = C.load_panel()
    C.assert_no_lookahead(CLAIM_COLS)
    return df


def in_play(
    df: pl.DataFrame,
    *,
    runup: float = IN_PLAY["runup"],
    rvol: float = IN_PLAY["rvol"],
    shares: float = IN_PLAY["shares"],
) -> pl.DataFrame:
    """The IN PLAY filter, exactly as CLAIM.md states it."""
    return df.filter(
        (pl.col("runup_pre_appearance") >= runup)
        & (pl.col("rvol_pole") >= rvol)
        & (pl.col("shares_outstanding") <= shares)
    )


def claim_selector(df: pl.DataFrame, **th: float) -> pl.DataFrame:
    """SHIPPED + IN PLAY."""
    return in_play(C.SHIPPED(df), **th)


def book_of(df: pl.DataFrame, *, max_per_day: int = 2, target: float = 2.0) -> pl.DataFrame:
    return C.build_book(C.fixed_target_r(df, target), max_per_day=max_per_day)


def score_sel(
    df: pl.DataFrame, selected: pl.DataFrame, *, max_per_day: int = 2, target: float = 2.0
) -> dict[str, Any]:
    return C.score(
        book_of(selected, max_per_day=max_per_day, target=target),
        sessions=df["dt"].n_unique(),
    )


def net_per_trade(res: dict[str, Any]) -> float:
    return float(res.get("net_r_per_trade", 0.0)) if res.get("trades") else 0.0


def fast_net_r(entry: np.ndarray, stop: np.ndarray, r: np.ndarray) -> np.ndarray:
    """Vectorised copy of `common.score`'s per-trade net-R maths (non-compounding).

    NOT a fork of the harness — the same formulas, needed because the null tests run ~10^5 books
    and `score()` round-trips through dicts. `verify_fast()` asserts it reproduces `score()`
    trade-for-trade on the claim's own book.
    """
    s, c = C.DEFAULT_SIZING, C.DEFAULT_COSTS
    rps = entry - stop
    ok = (rps > 0) & (entry > 0)
    rps = np.where(ok, rps, np.nan)
    risk_qty = np.floor(s.equity * s.risk_fraction / rps)
    cap_qty = np.floor(s.equity * s.position_fraction / np.where(entry > 0, entry, np.nan))
    qty = np.where(risk_qty <= cap_qty, risk_qty, cap_qty)
    qty = np.where(ok & np.isfinite(qty), qty, 0.0)
    taken = qty >= 1
    risk_usd = qty * rps
    gross_usd = r * risk_usd
    exit_price = entry + r * rps
    commission = 2 * np.maximum(c.comm_min, qty * c.comm_per_share)
    per_share = 2 * qty * (c.exchange + c.clearing)
    taf = np.minimum(qty * c.taf_per_share, c.taf_max)
    sec = qty * exit_price * c.sec_rate
    slip = np.where(r > 0, 0.0, qty * c.stop_slip_ticks * C.TICK)
    net_usd = gross_usd - (commission + per_share + taf + sec) - slip
    out = np.where(taken & (risk_usd > 0), net_usd / np.where(risk_usd > 0, risk_usd, 1.0), np.nan)
    return out


def verify_fast() -> dict[str, float]:
    """Prove `fast_net_r` == `common.score`'s net_r on the claim's book. Run before trusting it."""
    df = C.load_panel()
    book = book_of(claim_selector(df))
    res = C.score(book, sessions=df["dt"].n_unique())
    tr = res["_trades"]
    mine = fast_net_r(tr["entry_fill"].to_numpy(), tr["stop"].to_numpy(), tr["r"].to_numpy())
    theirs = tr["net_r"].to_numpy()
    return {
        "n": len(theirs),
        "max_abs_diff": float(np.nanmax(np.abs(mine - theirs))),
        "mean_mine": float(np.nanmean(mine)),
        "mean_theirs": float(theirs.mean()),
    }


def write(name: str, payload: Any) -> Path:
    p = OUT / name
    p.write_text(json.dumps(payload, indent=2, default=str))
    return p


def hr(title: str) -> None:
    print(f"\n{'=' * 92}\n{title}\n{'=' * 92}")
