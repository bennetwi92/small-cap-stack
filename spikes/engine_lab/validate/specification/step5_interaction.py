"""Step 5 — where does the size effect actually live?

`step4` showed the size clause lifts the 2R-hit rate from 38.4% to 50.0% inside the 125-row SHIPPED
population, but from 25.1% to only 25.7% across all 3,639 rows. Either the size effect is real and
the shipped gates are what make it visible, or it is a coincidence confined to the 125 rows it was
found in. This step measures the size clause's effect in a ladder of populations of increasing
restrictiveness, and bootstraps the SHIPPED figure so its error bar is visible.

Outcome used here is `max_r >= 2` — the rate at which a setup would reach the 2R target. It is a
per-row binary with no capacity effect, no cost model and no book, so it is the cleanest place to
ask whether the rows are actually better.
"""

from __future__ import annotations

import json
from typing import Any

import features
import numpy as np
import polars as pl
import speclab as S
from speclab import C

SHARES = 50e6


def rate(g: pl.DataFrame) -> float:
    return float((g["max_r"] >= 2.0).mean()) if g.height else float("nan")


def main() -> None:
    df = features.attach(S.panel(2.0))
    pops: dict[str, pl.DataFrame] = {
        "all rows": df,
        "passed only": df.filter(pl.col("passed")),
        "price 3-50 only": df.filter(pl.col("entry_fill").is_between(3.0, 50.0)),
        "stop_pct>=0.025 only": df.filter(pl.col("stop_pct") >= 0.025),
        "cycle<=2 only": df.filter(pl.col("cycle_num") <= 2),
        "staleness<=30 only": df.filter(pl.col("staleness_delay_min") <= 30),
        "trigger 240-555 only": df.filter(pl.col("trigger_et_min").is_between(240.0, 555.0)),
        "SHIPPED minus passed": df.filter(
            (pl.col("cycle_num") <= 2)
            & (pl.col("staleness_delay_min") <= 30)
            & pl.col("entry_fill").is_between(3.0, 50.0)
            & (pl.col("stop_pct") >= 0.025)
            & pl.col("trigger_et_min").is_between(240.0, 555.0)
        ),
        "SHIPPED": C.SHIPPED(df),
    }
    rows = []
    print(
        f"{'population':<24}{'n':>7}{'base 2R%':>10}{'small 2R%':>11}{'delta pp':>10}"
        f"{'n small':>9}{'boot 90% CI':>22}"
    )
    for label, pop in pops.items():
        small = pop.filter(pl.col("shares_outstanding") <= SHARES)
        b, s = rate(pop), rate(small)
        ci = _boot_delta(pop, n=4000)
        rows.append(
            {
                "population": label,
                "n": pop.height,
                "base_rate2r": round(b, 4),
                "small_rate2r": round(s, 4),
                "delta_pp": round((s - b) * 100, 2),
                "n_small": small.height,
                "boot_lo_pp": round(ci[0] * 100, 2),
                "boot_hi_pp": round(ci[1] * 100, 2),
            }
        )
        print(
            f"{label:<24}{pop.height:>7}{b * 100:>9.1f}%{s * 100:>10.1f}%"
            f"{(s - b) * 100:>+9.1f}{small.height:>9}"
            f"{f'[{ci[0] * 100:+.1f}, {ci[1] * 100:+.1f}]':>22}"
        )

    # The same ladder for the running clause, for contrast.
    print()
    rows2 = []
    for label, pop in pops.items():
        run = pop.filter(pl.col("runup_pre_appearance") >= 0.15)
        b, s = rate(pop), rate(run)
        rows2.append(
            {
                "population": label,
                "n": pop.height,
                "base_rate2r": round(b, 4),
                "runup_rate2r": round(s, 4),
                "delta_pp": round((s - b) * 100, 2),
                "n_runup": run.height,
            }
        )
        print(
            f"{label:<24}{pop.height:>7}{b * 100:>9.1f}%{s * 100:>10.1f}%"
            f"{(s - b) * 100:>+9.1f}{run.height:>9}   (runup>=0.15)"
        )

    # Would the SHIPPED-sized effect show up in a like-sized draw from the wider pool?
    # Take 125 random rows from the "SHIPPED minus passed" pool, apply the size clause, record the
    # delta. If SHIPPED's +11.6pp is inside that distribution, the shipped gates explain nothing.
    pool = pops["SHIPPED minus passed"]
    rng = np.random.default_rng(5)
    deltas = []
    for _ in range(4000):
        idx = rng.choice(pool.height, size=125, replace=False)
        g = pool[idx.tolist()]
        sm = g.filter(pl.col("shares_outstanding") <= SHARES)
        if sm.height < 10:
            continue
        deltas.append(rate(sm) - rate(g))
    a = np.array(deltas)
    obs = rows[-1]["delta_pp"] / 100
    sub = {
        "n_draws": len(a),
        "mean_pp": round(float(a.mean()) * 100, 2),
        "sd_pp": round(float(a.std(ddof=1)) * 100, 2),
        "p95_pp": round(float(np.quantile(a, 0.95)) * 100, 2),
        "observed_pp": round(obs * 100, 2),
        "p_ge_observed": round(float((a >= obs).mean()), 4),
    }
    print(
        f"\n125-row draws from 'SHIPPED minus passed' ({pool.height} rows): size-clause delta "
        f"mean {sub['mean_pp']:+.1f}pp sd {sub['sd_pp']:.1f}, p95 {sub['p95_pp']:+.1f}pp; "
        f"SHIPPED observed {sub['observed_pp']:+.1f}pp -> p = {sub['p_ge_observed']}"
    )

    out: dict[str, Any] = {
        "size_ladder": rows,
        "runup_ladder": rows2,
        "subsample_null": sub,
    }
    (S.OUT / "step5_interaction.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {S.OUT / 'step5_interaction.json'}")


def _boot_delta(pop: pl.DataFrame, *, n: int = 4000, seed: int = 9) -> tuple[float, float]:
    """Bootstrap 90% CI for (2R rate | small) - (2R rate | all) inside `pop`."""
    if pop.height < 20:
        return (float("nan"), float("nan"))
    y = (pop["max_r"] >= 2.0).cast(pl.Float64).to_numpy()
    small = (pop["shares_outstanding"] <= SHARES).fill_null(False).to_numpy()
    rng = np.random.default_rng(seed)
    ds = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        ys, ss = y[i], small[i]
        if ss.sum() < 5:
            continue
        ds.append(ys[ss].mean() - ys.mean())
    a = np.array(ds)
    return float(np.quantile(a, 0.05)), float(np.quantile(a, 0.95))


if __name__ == "__main__":
    main()
