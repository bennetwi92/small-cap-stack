"""Agent A (rules) — shared helpers for the selection study.

Everything here sits on top of `spikes/engine_lab/common.py`; nothing is forked from it.

The one thing worth knowing: **row-level net R is precomputed once**. Under the fixed exit
(2R target / -1R stop) and non-compounding `Sizing()`, a row's share count and therefore its
cost drag depend only on `entry_fill`, `stop` and the sign of `r` — none of which a selection
rule changes. So `attach_net_r()` can stamp `net_r` on every row of the panel up front, and any
band/decile analysis can then report net R per trade directly without re-running `score()`.
`score()` is still the authority for booked results; this is only for characterisation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from spikes.engine_lab import common as C  # noqa: E402

OUT = REPO / "data/spikes/engine-lab/rules"


def panel(target: float = 2.0) -> pl.DataFrame:
    """Population with `r` (2R bracket) and per-row `net_r` attached, HOLDOUT still present."""
    df = C.load_panel()
    df = C.fixed_target_r(df, target)
    return attach_net_r(df)


#: The fixed sizing and cost model. Module-level singletons so they are not rebuilt per call.
SIZING = C.Sizing()
COSTS = C.Costs()


def attach_net_r(
    df: pl.DataFrame, *, sizing: C.Sizing = SIZING, costs: C.Costs = COSTS
) -> pl.DataFrame:
    """Stamp per-row `qty`, `sized_by`, `cost_r` and `net_r` using the fixed sizing/costs."""
    qty, sized_by, net_r, cost_r = [], [], [], []
    for entry, stop, r in zip(df["entry_fill"], df["stop"], df["r"], strict=False):
        q, how = sizing.qty(float(entry), float(stop))
        if q < 1:
            qty.append(0)
            sized_by.append("unaffordable")
            net_r.append(0.0)
            cost_r.append(0.0)
            continue
        risk_usd = q * (float(entry) - float(stop))
        exit_price = float(entry) + float(r) * (float(entry) - float(stop))
        fees, slip = costs.usd(q, exit_price, won=float(r) > 0)
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


def no_holdout(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(pl.col("split") != "holdout")


def dev(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(pl.col("split") == "dev")


def val(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(pl.col("split") == "val")


# ---------------------------------------------------------------------------------------------
# Characterisation
# ---------------------------------------------------------------------------------------------
def bands(
    df: pl.DataFrame,
    col: str,
    *,
    n: int = 8,
    edges: list[float] | None = None,
) -> pl.DataFrame:
    """Row-level outcome by band of `col`. Quantile bands unless explicit `edges` are given."""
    s = df[col]
    if s.dtype == pl.Boolean:
        g = df.group_by(col, maintain_order=True)
        rows = [_band_row(str(k[0]), grp) for k, grp in g]
        return pl.DataFrame(rows).sort("band")
    v = s.cast(pl.Float64).drop_nulls().to_numpy()
    if edges is None:
        qs = np.unique(np.quantile(v, np.linspace(0, 1, n + 1)))
        edges = [float(x) for x in qs]
    rows = []
    for a, b in zip(edges[:-1], edges[1:], strict=False):
        last = b == edges[-1]
        m = (pl.col(col) >= a) & (pl.col(col) <= b if last else pl.col(col) < b)
        grp = df.filter(m)
        if grp.is_empty():
            continue
        rows.append(_band_row(f"[{a:.4g},{b:.4g}{']' if last else ')'}", grp))
    return pl.DataFrame(rows)


def _band_row(label: str, grp: pl.DataFrame) -> dict[str, Any]:
    return {
        "band": label,
        "n": grp.height,
        "win": round(float((grp["r"] > 0).mean()), 3),
        "gross": round(float(grp["r"].mean()), 3),
        "net": round(float(grp["net_r"].mean()), 3),
        "net_dev": round(float(grp.filter(pl.col("split") == "dev")["net_r"].mean() or 0.0), 3)
        if grp.filter(pl.col("split") == "dev").height
        else None,
        "n_dev": grp.filter(pl.col("split") == "dev").height,
        "net_val": round(float(grp.filter(pl.col("split") == "val")["net_r"].mean() or 0.0), 3)
        if grp.filter(pl.col("split") == "val").height
        else None,
        "n_val": grp.filter(pl.col("split") == "val").height,
    }


def book_score(
    df: pl.DataFrame, sel: Any, *, sessions_df: pl.DataFrame | None = None, max_per_day: int = 2
) -> dict[str, Any]:
    """Apply a selection function, cap at 2/day, score. `df` must already carry `r`."""
    base = df if sessions_df is None else sessions_df
    book = C.build_book(sel(df), max_per_day=max_per_day)
    return C.score(book, sessions=base["dt"].n_unique())


def line(name: str, res: dict[str, Any]) -> str:
    return f"{name:<28} " + C.brief(res)
