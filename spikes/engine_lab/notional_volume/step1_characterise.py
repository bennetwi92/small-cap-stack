"""Step 1 — quintile characterisation of the two candidate features.

Row-level (not booked), DEV+VAL only, split by `source` per the README's "must work on both
halves" rule. Reports big-mover rate (max_gain_pct>=50%, and max_r>=2R) and mean net R/trade
per quintile of each candidate.
"""

from __future__ import annotations

import json

import lab
import polars as pl


def main() -> None:
    df = lab.no_holdout(lab.panel())
    print(f"population: {df.height} rows, {df['dt'].n_unique()} sessions (DEV+VAL only)")

    out: dict[str, list[dict]] = {}
    for col in lab.CANDIDATES:
        print(f"\n== {col} — all rows")
        q_all = lab.quintiles(df, col)
        print(q_all)
        print(f"\n== {col} — by source")
        q_src = lab.quintiles(df, col, by="source")
        print(q_src)
        out[col] = {
            "all": q_all.to_dicts(),
            "by_source": q_src.to_dicts(),
        }

    (lab.OUT / "step1_characterise.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {lab.OUT / 'step1_characterise.json'}")

    # correlation between the two candidates and rvol_pole, for context
    corr = df.select(
        pl.corr("pole_dollar_volume", "cum_dollar_vol_to_trigger").alias("pdv_vs_cumdv"),
        pl.corr("pole_dollar_volume", "rvol_pole").alias("pdv_vs_rvol"),
        pl.corr("cum_dollar_vol_to_trigger", "rvol_pole").alias("cumdv_vs_rvol"),
    )
    print("\ncorrelations:")
    print(corr)


if __name__ == "__main__":
    main()
