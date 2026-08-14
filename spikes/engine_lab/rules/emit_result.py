"""Write `data/spikes/engine-lab/rules/result.json` — the machine-readable deliverable.

Emits the proposed rule as thresholds, its DEV and VAL scorecards, the alternative points on the
frequency/quality curve, and the anti-overfit evidence for and against it. Rerun after any change
to the rule; nothing else reads this file.
"""

from __future__ import annotations

import json

import lab
import polars as pl
import search
from lab import C

PROPOSED = {
    "hits_before_trigger_max": 2,
    "planned_risk_min_usd": 0.19,
    "cons_len_max": 3,
}
ALT_TIGHT = {"hits_before_trigger_max": 1, "planned_risk_min_usd": 0.15, "cons_len_max": 3}
ALT_WIDE = {"hits_before_trigger_max": 2, "planned_risk_min_usd": 0.15, "cons_len_max": 3}


def clauses(th: dict) -> list[search.Clause]:
    return [
        search.Clause("hits_before_trigger", "le", float(th["hits_before_trigger_max"])),
        search.Clause("planned_risk", "ge", float(th["planned_risk_min_usd"])),
        search.Clause("cons_len", "le", float(th["cons_len_max"])),
    ]


def card(p: pl.DataFrame, th: dict) -> dict:
    bk = C.build_book(search.selector(clauses(th))(p), max_per_day=2)
    s = C.score(bk, sessions=p["dt"].n_unique())
    keys = (
        "trades",
        "sessions_traded",
        "sessions_available",
        "trades_per_session",
        "gross_r",
        "net_r",
        "r_per_trade",
        "net_r_per_trade",
        "win_rate",
        "max_dd_net_r",
        "net_usd",
        "mean_qty",
        "cap_bound",
        "cost_r_per_trade",
    )
    return {k: s.get(k) for k in keys}


def main() -> None:
    p = lab.no_holdout(lab.panel())
    d, v = lab.dev(p), lab.val(p)

    out = {
        "agent": "A / rules (selection)",
        "question": "which pre-market setups to take, decidable at trigger time",
        "held_fixed": {
            "exit": "2R target / -1R stop",
            "cap": "2 per day, earliest trigger first",
            "sizing": "$500, 5% risk, 50% notional cap",
            "costs": "common.Costs() defaults",
        },
        "verdict": ("NO RULE CLEARS THE BAR. One candidate is worth ONE holdout look, unproven."),
        "confidence": "low",
        "proposed_rule": {
            "thresholds": PROPOSED,
            "expression": "hits_before_trigger <= 2 AND planned_risk >= 0.19 AND cons_len <= 3",
            "n_thresholds": 3,
            "plain_english": (
                "Take the break only if the name has appeared on the scanner at most twice before "
                "it triggers, the stop is at least 19 cents per share away, and the consolidation "
                "is 3 bars or shorter."
            ),
            "dev": card(d, PROPOSED),
            "val": card(v, PROPOSED),
            "dev_plus_val": card(p, PROPOSED),
        },
        "alternatives": {
            "tighter (hits<=1, risk>=0.15)": {
                "thresholds": ALT_TIGHT,
                "dev": card(d, ALT_TIGHT),
                "val": card(v, ALT_TIGHT),
                "dev_plus_val": card(p, ALT_TIGHT),
            },
            "wider (hits<=2, risk>=0.15)": {
                "thresholds": ALT_WIDE,
                "dev": card(d, ALT_WIDE),
                "val": card(v, ALT_WIDE),
                "dev_plus_val": card(p, ALT_WIDE),
            },
        },
        "baseline_shipped": {"dev": None, "val": None},
        "evidence_for": [
            "The hits<=2 / hits>=3 break reproduces independently in DEV (+0.16 vs -0.51 net R per "
            "trade) and in VAL (+0.56 vs -0.15), at the same threshold.",
            "Positive in all six calendar blocks of DEV+VAL and in 7 of 9 calendar months.",
            "Session-level bootstrap 95% CI on DEV+VAL is [+0.013, +0.647] net R per trade.",
            "Insensitive to the capacity cap (net R per trade 0.31-0.33 at caps of 1, 2, 3, 5).",
            "Positive at every fixed target from 1R to 3R, though it peaks at the fitted 2R.",
        ],
        "evidence_against": [
            "No main effect: hits_before_trigger alone is -0.53 net R per trade (indistinguishable "
            "from the pool) and by exact value is non-monotone across 1..8. The edge exists only "
            "as a three-way conjunction over 78 of 2,989 rows.",
            "The honest walk-forward — refit the rule on the past, trade the next block — is "
            "-0.055 net R per trade with 2 of 6 blocks positive. Every clause budget from 1 to 4 "
            "is negative out of sample.",
            "The feature set was chosen by an unrestricted greedy search on DEV. On shuffled "
            "outcomes that same search invents +0.08 net R per trade in sample (90th pct +0.25), "
            "so the observed +0.19 on DEV is inside its own noise band.",
            "Cannot be checked on live data: `source` is perfectly confounded with `split` "
            "(DEV and VAL are 100% recon, HOLDOUT is 100% live), so the mandated per-source check "
            "is impossible without spending the holdout.",
        ],
        "cross_agent_notes": [
            "TWO COLUMNS IN common.TRIGGER_TIME_SAFE ARE LOOKAHEAD HERE: `first_rank` "
            "(recon rank is assigned by whole-day change off the daily bar) and `n_scanner_hits` "
            "(counts hits over the whole session, not just before the trigger). Both look like the "
            "strongest features in the panel. Use `hits_before_trigger` instead of n_scanner_hits; "
            "there is no usable substitute for first_rank.",
            "Selection has very little leverage under a 2-a-day earliest-first cap: a filter that "
            "keeps half the rows still books ~50% of the same trades as no filter at all. Only "
            "filters cutting to <10% of rows change the book materially.",
            "Cost drag is almost entirely a function of stop width: 0.54 R per trade below a 2% "
            "stop, 0.06 R above a 22% stop, crossing over where the 50% notional cap stops binding "
            "at about a 10% stop. Any rule that improves net without improving gross is a sizing "
            "decision wearing a selection costume.",
            "`passed` should not be used. Of its seven component gates only `pole_height` (+0.15 "
            "gross R) and `cons_len` (+0.08) have positive value; `cons_retracement`, which cuts "
            "87% of the pool, is worth -0.03, and `wick_peak` and `cons_holds_base` are negative.",
        ],
        "splits_used": {
            "dev": "2025-10-30..2026-04-30",
            "val": "2026-05-01..2026-06-30",
            "holdout": "NEVER TOUCHED",
        },
        "val_looks": ("roughly 4 (greedy output, fixed-rule ladder, block table, final curve)"),
    }
    out["baseline_shipped"] = {
        "dev": {
            k: C.score(
                C.build_book(C.fixed_target_r(C.SHIPPED(d)), max_per_day=2),
                sessions=d["dt"].n_unique(),
            ).get(k)
            for k in ("trades", "net_r", "net_r_per_trade", "win_rate")
        },
        "val": {
            k: C.score(
                C.build_book(C.fixed_target_r(C.SHIPPED(v)), max_per_day=2),
                sessions=v["dt"].n_unique(),
            ).get(k)
            for k in ("trades", "net_r", "net_r_per_trade", "win_rate")
        },
    }

    lab.OUT.mkdir(parents=True, exist_ok=True)
    (lab.OUT / "result.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out["proposed_rule"], indent=2, default=str))
    print(f"\nwrote {lab.OUT / 'result.json'}")


if __name__ == "__main__":
    main()
