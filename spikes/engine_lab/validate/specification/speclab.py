"""Validator B (specification) — local helpers. Sits on top of `engine_lab/common.py`.

Nothing here forks `common.py`; it is imported as `C` and remains the authority for the
population, the book, the costs and `score()`. What this module adds is only:

- `panel()` — the population with a fixed-target `r` and a per-row `net_r` attached, so that
  band/gradient analysis can quote net R per row without re-running `score()` for every cut.
  Legitimate because sizing is non-compounding: a row's share count and cost drag depend only on
  `entry_fill`, `stop` and `sign(r)`, none of which a *selection* rule changes.
  `check_net_r_matches_score()` proves the equivalence against `C.score()`.
- `book()` — build_book + score in one call, always with the full session count as denominator.

⚠️ HOLDOUT is contaminated (see CLAIM.md). It is computed here for completeness and is always
labelled; no verdict in this folder rests on it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spikes.engine_lab import common as C  # noqa: E402

OUT = REPO / "data/spikes/engine-lab/validate/specification"
OUT.mkdir(parents=True, exist_ok=True)

SIZING = C.Sizing()
COSTS = C.Costs()


def attach_net_r(df: pl.DataFrame, r_col: str = "r") -> pl.DataFrame:
    qty, sized_by, net_r, cost_r = [], [], [], []
    for entry, stop, r in zip(df["entry_fill"], df["stop"], df[r_col], strict=False):
        q, how = SIZING.qty(float(entry), float(stop))
        if q < 1:
            qty.append(0)
            sized_by.append("unaffordable")
            net_r.append(0.0)
            cost_r.append(0.0)
            continue
        risk_usd = q * (float(entry) - float(stop))
        exit_price = float(entry) + float(r) * (float(entry) - float(stop))
        fees, slip = COSTS.usd(q, exit_price, won=float(r) > 0)
        qty.append(q)
        sized_by.append(how)
        net_r.append((float(r) * risk_usd - fees - slip) / risk_usd)
        cost_r.append((fees + slip) / risk_usd)
    return df.with_columns(
        pl.Series("qty", qty),
        pl.Series("sized_by", sized_by),
        pl.Series("net_r", net_r, dtype=pl.Float64),
        pl.Series("cost_r", cost_r, dtype=pl.Float64),
    )


def panel(
    target: float = 2.0,
    *,
    premarket_cut: float = C.PREMARKET_CUT,
    require_cons_range: bool = True,
) -> pl.DataFrame:
    """The engine-lab population with `r` (fixed target / -1R) and per-row `net_r`."""
    df = C.load_panel(premarket_cut=premarket_cut, require_cons_range=require_cons_range)
    return attach_net_r(C.fixed_target_r(df, target))


def book(
    sel: pl.DataFrame,
    *,
    sessions: int,
    max_per_day: int = 2,
) -> dict[str, Any]:
    """Score a selection as a book. `sel` must already carry `r`."""
    return C.score(C.build_book(sel, max_per_day=max_per_day), sessions=sessions)


def split_block(res: dict[str, Any], name: str) -> dict[str, Any]:
    blk = res.get("split", {})
    b = blk.get(name) if isinstance(blk, dict) else None
    if not b or not b.get("trades"):
        return {"trades": 0, "net_r": 0.0, "net_r_per_trade": 0.0, "gross_r": 0.0}
    return b


def flat(res: dict[str, Any]) -> dict[str, Any]:
    """A compact, JSON-safe view of a `score()` result, with per-split detail."""
    out = {
        "trades": res.get("trades", 0),
        "trades_per_session": res.get("trades_per_session", 0.0),
        "gross_r": res.get("gross_r", 0.0),
        "net_r": res.get("net_r", 0.0),
        "net_r_per_trade": res.get("net_r_per_trade", 0.0),
        "win_rate": res.get("win_rate", 0.0),
        "max_dd_net_r": res.get("max_dd_net_r", 0.0),
    }
    for s in ("dev", "val", "holdout"):
        b = split_block(res, s)
        out[f"{s}_trades"] = b.get("trades", 0)
        out[f"{s}_net_r"] = b.get("net_r", 0.0)
        out[f"{s}_net_rpt"] = b.get("net_r_per_trade", 0.0)
    for s in ("recon", "live"):
        blk = res.get("source", {})
        b = blk.get(s, {}) if isinstance(blk, dict) else {}
        out[f"{s}_trades"] = b.get("trades", 0)
        out[f"{s}_net_r"] = b.get("net_r", 0.0)
    return out


def mean_ci(x: np.ndarray) -> tuple[float, float]:
    """Mean and its 1-sigma standard error. Reported as +/- 1 s.e., not as a test."""
    if len(x) == 0:
        return 0.0, 0.0
    return float(np.mean(x)), float(np.std(x, ddof=1) / np.sqrt(len(x))) if len(x) > 1 else 0.0


def check_net_r_matches_score(df: pl.DataFrame) -> dict[str, Any]:
    """Prove `attach_net_r` reproduces `C.score()`'s per-trade net R on a real book."""
    bk = C.build_book(df, max_per_day=2)
    res = C.score(bk, sessions=df["dt"].n_unique(), by=("split", "source"))
    got = res["_trades"]
    joined = got.select(["key", "net_r"]).join(
        bk.select(["key", pl.col("net_r").alias("mine")]), on="key"
    )
    d = (joined["net_r"] - joined["mine"]).abs().max()
    return {"rows": joined.height, "max_abs_diff": float(d or 0.0)}
