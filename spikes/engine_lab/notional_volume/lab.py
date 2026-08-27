"""Issue #719 (Refs #690) — does pole-candle dollar volume carry signal `rvol_pole` did not?

Sits on top of `spikes/engine_lab/common.py`; nothing is forked from it. Mirrors the
`rules/lab.py` pattern (precompute `r`/`net_r` once under the fixed 2R/-1R bracket, since neither
candidate feature changes entry/stop/sizing).

Two candidate features, both an *absolute magnitude* rather than a ratio like the retired
`rvol_pole`:

- `pole_dollar_volume = pole_volume * breakout_level` — shares traded on the pole candle, priced
  at the last consolidation candle's high. There is no pole-close price column in the panel, so
  `breakout_level` is used as a proxy: it is trigger-time-safe and temporally adjacent to the pole
  (the structure's own next candle), but it is NOT the pole's own close. Treat this feature as an
  approximation, not a measured dollar volume.
- `cum_dollar_vol_to_trigger` — already a first-class trigger-time-safe column in the panel
  (cumulative pre-market dollar volume run-up to trigger). No proxy needed; used as-is.
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

OUT = REPO / "data/spikes/engine-lab/notional_volume"
OUT.mkdir(parents=True, exist_ok=True)

#: The two candidate features under test in this spike.
CANDIDATES = ["pole_dollar_volume", "cum_dollar_vol_to_trigger"]

SIZING = C.Sizing()
COSTS = C.Costs()


def with_features(df: pl.DataFrame) -> pl.DataFrame:
    """Attach `pole_dollar_volume`. `cum_dollar_vol_to_trigger` is already in the panel."""
    return df.with_columns(
        (pl.col("pole_volume") * pl.col("breakout_level")).alias("pole_dollar_volume")
    )


def check_no_lookahead() -> None:
    """`pole_dollar_volume` is built only from `pole_volume` and `breakout_level`, both
    trigger-time-safe; `cum_dollar_vol_to_trigger` is already in `TRIGGER_TIME_SAFE`. Neither
    candidate is itself in `TRIGGER_TIME_SAFE` (they are new derived columns), so we check their
    *inputs*, which is what a selection rule actually reads once built.
    """
    C.assert_no_lookahead(["pole_volume", "breakout_level", "cum_dollar_vol_to_trigger"])


def panel(target: float = 2.0) -> pl.DataFrame:
    """Population with `r` (2R bracket), per-row `net_r`, and both candidate features attached."""
    check_no_lookahead()
    df = C.load_panel()
    df = with_features(df)
    df = C.fixed_target_r(df, target)
    return attach_net_r(df)


def attach_net_r(
    df: pl.DataFrame, *, sizing: C.Sizing = SIZING, costs: C.Costs = COSTS
) -> pl.DataFrame:
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
    """DEV + VAL only. HOLDOUT (sessions after 2026-07-01) is never touched in this spike."""
    return df.filter(pl.col("split") != "holdout")


def dev(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(pl.col("split") == "dev")


def val(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(pl.col("split") == "val")


def shipped_plus_so(df: pl.DataFrame) -> pl.DataFrame:
    """SHIPPED + the one residue that survived #708/#690 validation (D-45)."""
    return C.SHIPPED(df).filter(pl.col("shares_outstanding") <= 50_000_000.0)


def quintiles(df: pl.DataFrame, col: str, *, by: str | None = None, n: int = 5) -> pl.DataFrame:
    """Row-level big-mover rate, mean net R, by quintile of `col`, optionally split by `by`."""
    groups = [None] if by is None else sorted(df[by].unique().to_list())
    rows: list[dict[str, Any]] = []
    for g in groups:
        d = df if g is None else df.filter(pl.col(by) == g)
        v = d[col].drop_nulls().to_numpy()
        if len(v) < n * 5:
            continue
        qs = np.unique(np.quantile(v, np.linspace(0, 1, n + 1)))
        for i, (a, b) in enumerate(zip(qs[:-1], qs[1:], strict=False)):
            last = i == len(qs) - 2
            mask = (pl.col(col) >= float(a)) & (
                (pl.col(col) <= float(b)) if last else (pl.col(col) < float(b))
            )
            grp = d.filter(mask)
            if grp.is_empty():
                continue
            rows.append(
                {
                    "group": "all" if g is None else str(g),
                    "quintile": i + 1,
                    "range": f"[{a:.3g},{b:.3g}{']' if last else ')'}",
                    "n": grp.height,
                    "big_mover_50pct": round(float((grp["max_gain_pct"] >= 0.50).mean()), 4),
                    "big_mover_2R": round(float((grp["max_r"] >= 2.0).mean()), 4),
                    "net_r_per_trade": round(float(grp["net_r"].mean()), 4),
                }
            )
    return pl.DataFrame(rows)


def book_score(
    df: pl.DataFrame, sel: Any, *, sessions_df: pl.DataFrame | None = None, max_per_day: int = 2
) -> dict[str, Any]:
    base = df if sessions_df is None else sessions_df
    book = C.build_book(sel(df), max_per_day=max_per_day)
    return C.score(book, sessions=base["dt"].n_unique())


def line(name: str, res: dict[str, Any]) -> str:
    return f"{name:<40} " + C.brief(res)
