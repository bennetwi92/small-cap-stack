"""Step 9 — emit the proposal as machine-readable parameters plus its DEV and VAL scorecards.

Writes `data/spikes/engine-lab/exits/result.json`. Nothing here re-fits anything; it collects the
numbers the earlier steps produced so the synthesis step can pick the bracket up without re-running
the search. HOLDOUT is never evaluated.
"""

from __future__ import annotations

import json
from typing import Any

import lab as X
import numpy as np
import polars as pl
from lab import C
from step5_robustness import (
    M_STAR,
    T_STAR,
    block_bootstrap,
    bracket,
    exposures,
    sensitivity,
    walk_forward,
)


def card(r: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "trades",
        "sessions_traded",
        "gross_r",
        "net_r",
        "r_per_trade",
        "net_r_per_trade",
        "win_rate",
        "net_usd",
        "mean_qty",
        "cap_bound",
        "max_dd_net_r",
        "max_dd_gross_r",
        "cost_r_per_trade",
        "trades_per_session",
        "pct_stopped",
        "pct_same_bar",
        "pct_open_930",
        "pct_fill_above_high",
        "mean_stop_pct",
        "unaffordable",
    )
    return {k: r[k] for k in keep if k in r}


def main() -> None:
    df, p, g, paths, pre = X.load_all()
    dv = df.filter(pl.col("split") != "holdout")
    b = bracket()

    prop_all = X.evaluate(dv, b, g, p, selector=C.SHIPPED)
    ship_all = X.evaluate(dv, X.Bracket(target_r=2.0), g, p, selector=C.SHIPPED)

    tr = C.score(X.book_with_bracket(dv, b, g, p, selector=C.SHIPPED))["_trades"]
    monthly = (
        tr.with_columns(pl.col("dt").dt.strftime("%Y-%m").alias("mo"))
        .group_by("mo")
        .agg(pl.len().alias("trades"), pl.col("net_r").sum().round(2).alias("net_r"))
        .sort("mo")
        .to_dicts()
    )
    nr = np.sort(tr["net_r"].to_numpy())

    result = {
        "agent": "C (exits)",
        "question": "where does the stop go, and where does the target go?",
        "proposal": {
            "name": "wide-cons-stop / unchanged-target bracket",
            "kind": "simple OCA bracket, both legs fixed at entry, no trailing/BE/scale/time stop",
            "unit": "C = entry_fill - consolidation_low  (the shipped stop distance)",
            "stop_price": "entry - 1.30 * C",
            "target_price": "entry + 2.00 * C",
            "stop_multiple_m": M_STAR,
            "target_multiple_t": T_STAR,
            "target_in_R_of_the_new_stop": round(T_STAR / M_STAR, 3),
            "plain_english": (
                "Put the stop 30% of the consolidation range BELOW the consolidation low instead "
                "of on it. Leave the profit target exactly where it is in price -- the level that "
                "is 2x the consolidation range above the entry. In the new risk that target is "
                "1.54R, not 2R."
            ),
            "free_parameters": 2,
            "held_fixed": {
                "selection": "common.SHIPPED",
                "capacity": "2 per day, earliest trigger first (common.build_book)",
                "sizing": "common.Sizing() defaults",
                "costs": "common.Costs() defaults",
            },
        },
        "scorecards": {
            "DEV": card(prop_all["split"]["dev"]),
            "VAL": card(prop_all["split"]["val"]),
            "DEV+VAL": card(prop_all),
            "HOLDOUT": "NOT EVALUATED — reserved for the synthesis step",
        },
        "baseline_same_book_shipped_bracket": {
            "DEV": card(ship_all["split"]["dev"]),
            "VAL": card(ship_all["split"]["val"]),
            "DEV+VAL": card(ship_all),
        },
        "anti_overfit": {
            "walk_forward_fixed": walk_forward(dv, g, p, C.SHIPPED, refit=False),
            "walk_forward_refit": walk_forward(dv, g, p, C.SHIPPED, refit=True),
            "sensitivity_pm20pct": sensitivity(dv, g, p, C.SHIPPED),
            "session_bootstrap": block_bootstrap(dv, g, p, C.SHIPPED),
            "per_source": {
                "note": (
                    "NOT TESTABLE outside the holdout. `source` and `split` are collinear by "
                    "construction: recon covers 2025-10-30..2026-06-30 (= DEV+VAL) and live covers "
                    "2026-07-01..2026-08-13 (= HOLDOUT). Every DEV+VAL trade is recon."
                ),
                "dev_val_sources": sorted(dv["source"].unique().to_list()),
            },
            "monthly_net_r": monthly,
            "concentration": {
                "total_net_r": round(float(nr.sum()), 2),
                "ex_top_3_trades": round(float(nr[:-3].sum()), 2),
                "ex_top_5_trades": round(float(nr[:-5].sum()), 2),
                "best_trade_net_r": round(float(nr[-1]), 2),
                "worst_trade_net_r": round(float(nr[0]), 2),
            },
        },
        "exposures": exposures(dv, g, p, C.SHIPPED),
        "caveats": [
            "80 trades on DEV+VAL. Two free parameters against 80 observations is thin.",
            "May 2026 contributes +10.4R of the +13.4R total. Ex-May the proposal is +3.0R over "
            "66 trades (+0.045/trade) -- but the SHIPPED bracket over the same ex-May trades is "
            "-6.6R, so the improvement is broad even though the level is not.",
            "The DIRECTION (wider stop) holds on every selection tested; the MAGNITUDE does not. "
            "Looser selections want m ~ 2-3, not 1.3. If Agent A replaces selection, m must be "
            "re-fitted against the new one.",
            "Only the shipped selection is net-positive at any bracket. The raw pre-market pool's "
            "best bracket is about break-even (+0.004 net R/trade at m=3.0, t=1.0).",
            "Selection entanglement: SHIPPED's `stop_pct >= 0.025` floor is measured against the "
            "SHIPPED stop, so widening it raises the effective floor to 3.25% of entry. Lowering "
            "the floor to 2.0% so the bracket stop still clears 2.5% adds 8 trades and takes the "
            "result from +0.168 to -0.002 net R/trade. The floor is load-bearing.",
            "Sensitivity: the only +/-20% sign flip is the target's high side (t=2.4 -> -0.082). "
            "The stop is positive across m = 1.04 .. 3.0; the cliff is on the TIGHT side.",
        ],
        "upper_bounds_not_proposable": {
            "note": "measured for context only -- the user has ruled these out",
            "breakeven_stop_once_+0.5R_seen_gross_r_per_trade": 0.563,
            "breakeven_stop_once_+1.0R_seen_gross_r_per_trade": 0.376,
            "scale_half_out_at_+0.75R_gross_r_per_trade": 0.399,
            "perfect_foresight_exit_at_MFE_gross_r_per_trade": 1.971,
            "best_time_stop_60min_gross_r_per_trade": 0.261,
            "proposed_simple_bracket_gross_r_per_trade": prop_all["r_per_trade"],
        },
        "verification": {
            "paths_reproduce_published_max_r": "3639/3639 exact (common.verify_paths)",
            "fast_replay_vs_common.replay_bracket": "10 bracket shapes x 400 rows, max |dR| = 0.0",
        },
    }
    X.OUT.mkdir(parents=True, exist_ok=True)
    with (X.OUT / "result.json").open("w") as fh:
        json.dump(result, fh, indent=2, default=str)
    print(json.dumps(result["scorecards"], indent=2, default=str))
    print(f"\nwrote {X.OUT / 'result.json'}")


if __name__ == "__main__":
    main()
