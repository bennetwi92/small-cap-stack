"""Step 2 — additive test: does a dollar-volume threshold improve net R/trade on top of
(a) SHIPPED alone and (b) SHIPPED + shares_outstanding<=50e6?

DEV+VAL only. `build_book` / `score` from `common.py`, unmodified.
"""

from __future__ import annotations

import json

import lab
import polars as pl
from spikes.engine_lab import common as C

#: threshold grid, one per candidate — spans below/above the median of each on the SHIPPED pool
GRID = {
    "pole_dollar_volume": [0.0, 5e5, 1e6, 2e6, 3e6, 5e6, 8e6],
    "cum_dollar_vol_to_trigger": [0.0, 2e6, 5e6, 8e6, 1.2e7, 2e7, 3e7],
}


def sweep(df: pl.DataFrame, base_sel, base_name: str) -> list[dict]:
    base = base_sel(df)
    base_book = C.build_book(base, max_per_day=2)
    base_score = C.score(base_book, sessions=df["dt"].n_unique())
    rows = [
        {
            "base": base_name,
            "feature": "-",
            "threshold": None,
            "trades": base_score["trades"],
            "trades_per_session": base_score["trades_per_session"],
            "net_r": base_score["net_r"],
            "net_r_per_trade": base_score["net_r_per_trade"],
        }
    ]
    for feature, grid in GRID.items():
        for th in grid:
            sel = base.filter(pl.col(feature) >= th)
            book = C.build_book(sel, max_per_day=2)
            s = C.score(book, sessions=df["dt"].n_unique())
            rows.append(
                {
                    "base": base_name,
                    "feature": feature,
                    "threshold": th,
                    "trades": s["trades"],
                    "trades_per_session": s["trades_per_session"],
                    "net_r": s["net_r"],
                    "net_r_per_trade": s["net_r_per_trade"],
                }
            )
    return rows


def main() -> None:
    df = lab.no_holdout(lab.panel())
    out = []
    out += sweep(df, C.SHIPPED, "SHIPPED")
    out += sweep(df, lab.shipped_plus_so, "SHIPPED+shares_out<=50e6")

    tbl = pl.DataFrame(out)
    with pl.Config(tbl_rows=-1, tbl_cols=-1):
        print(tbl)

    (lab.OUT / "step2_additive.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {lab.OUT / 'step2_additive.json'}")


if __name__ == "__main__":
    main()
