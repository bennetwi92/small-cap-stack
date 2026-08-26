"""Assemble `data/spikes/engine-lab/validate/specification/result.json` — the deliverable.

Reads the JSON each step wrote and folds it into one machine-readable document: the verdict, the
re-derivation of CLAIM.md, every specification tested with its numbers, and the evidence for and
against. Run the steps first (they are independent), then this.

    for s in step0_rederive sweeps step2_simplest step3_placebo step4_rowlevel step5_interaction; do
        .venv/bin/python spikes/engine_lab/validate/specification/$s.py; done
    .venv/bin/python spikes/engine_lab/validate/specification/emit_result.py
"""

from __future__ import annotations

import json
from typing import Any

import speclab as S

VERDICT = {
    "verdict": "ARTEFACT",
    "verdict_scope": (
        "The claim AS SPECIFIED — IN PLAY = runup_pre_appearance>=0.15 AND rvol_pole>=2.0 AND "
        "shares_outstanding<=50e6 — is an artefact of its specification. Two of the three clauses "
        "are measurably inert. What remains is a single clause, shares_outstanding<=50e6, whose "
        "own standing is PROMISING-but-unproven: it is invisible in every population except the "
        "125 rows it was found in."
    ),
    "confidence": {
        "the_3_clause_rule_is_not_what_works": "high",
        "the_1_clause_residual_is_also_noise": "moderate",
    },
    "strongest_evidence_for": (
        "Across 288 reasonable specifications (18 'already running' measures x 8 'small' measures "
        "x rvol on/off, every threshold set at matched selectivity), 73% are net-positive with a "
        "median of +0.150 net R/trade against a pool base rate of -0.25 and a shipped baseline of "
        "+0.058. A broad positive tilt from 'small' is present under many definitions, not one."
    ),
    "strongest_evidence_against": (
        "The rate at which a setup reaches its 2R target — the only thing a 2R bracket monetises — "
        "is lifted by shares_outstanding<=50e6 by +0.6pp over all 3,639 rows, and by between "
        "+0.0pp and +3.6pp in each of eight sub-populations, every one of those bootstrap "
        "intervals straddling zero. It is +11.6pp only inside the 125-row SHIPPED population where "
        "the rule was found. The effect does not exist outside its own sample."
    ),
    "what_would_change_my_mind": [
        "A +8pp-or-better lift in the 2R-hit rate from shares<=50e6 measured on pre-market rows "
        "outside the 125 SHIPPED rows — a new period, or a widened shipped-like population.",
        "A single point-in-time share-count source across both halves (recon uses EDGAR, live uses "
        "FMP/yfinance), with the recon-only null-drop removed, still producing the effect.",
        "The wide-pool gradient surviving a bigger account: today ~2/3 of the net-R gradient in "
        "runup_pre_appearance is falling commission drag, not a better outcome.",
    ],
    "simplest_specification_that_captures_the_effect": {
        "rule": "SHIPPED AND shares_outstanding <= 50e6",
        "thresholds": 1,
        "trades": 60,
        "trades_per_session": 0.305,
        "net_r": 25.17,
        "net_r_per_trade": 0.4195,
        "net_r_per_trade_se": 0.198,
        "win_rate": 0.5,
        "max_dd_net_r": -6.64,
        "walk_forward_blocks_positive": "5/6",
        "permutation_p_vs_random_shipped_rows": 0.0379,
        "note": (
            "Strictly better than the three-clause original on every axis: 71% more trades, "
            "+8.4R more, a tighter error bar and a permutation p of 0.038 against the original's "
            "0.19. If anything were to ship, this is it — but the population ladder says do not."
        ),
    },
}


def _load(name: str) -> Any:
    p = S.OUT / name
    return json.loads(p.read_text()) if p.exists() else None


def main() -> None:
    steps = {
        "step0_rederive": _load("step0_rederive.json"),
        "sweeps": _load("sweeps.json"),
        "step2_simplest": _load("step2_simplest.json"),
        "step3_placebo": _load("step3_placebo.json"),
        "step4_rowlevel": _load("step4_rowlevel.json"),
        "step5_interaction": _load("step5_interaction.json"),
        "deciles_outcome": _load("deciles_outcome.json"),
        "subsample_nulls": _load("subsample_nulls.json"),
    }
    missing = [k for k, v in steps.items() if v is None]
    if missing:
        print(f"WARNING: missing step outputs {missing} — run those scripts first")

    doc: dict[str, Any] = {
        "agent": "Validator B (specification)",
        "claim_under_test": (
            "SHIPPED + (runup_pre_appearance>=0.15 AND rvol_pole>=2.0 AND "
            "shares_outstanding<=50e6), 2/day, 2R target"
        ),
        **VERDICT,
        "holdout_caveat": (
            "The 2026-07-01..08-13 period is contaminated (queried repeatedly in the exploratory "
            "session that produced the claim). Holdout figures appear in the detail sections for "
            "completeness only; no conclusion here rests on one."
        ),
        "claim_rederivation": _rederivation(steps),
        "specifications_tested": _specs(steps),
        "detail": steps,
    }
    (S.OUT / "result.json").write_text(json.dumps(doc, indent=2, default=str))
    print(f"wrote {S.OUT / 'result.json'}")
    print(json.dumps({k: v for k, v in doc.items() if k != "detail"}, indent=2)[:3000])


def _rederivation(steps: dict[str, Any]) -> dict[str, Any]:
    s0 = steps.get("step0_rederive") or {}
    h = s0.get("headline", {})
    return {
        "reproduced": {
            "shipped_only": "122 trades / +7.1R / +0.058 per trade — MATCHES CLAIM.md",
            "shipped_plus_in_play": "35 trades / +16.7R / +0.478 per trade — MATCHES CLAIM.md",
            "per_period": "dev +4.8R, val +10.6R, holdout +1.3R — MATCHES CLAIM.md",
            "quintile_monotonicity_runup": "1.8% -> 13.6% on a 50%+ move — MATCHES "
            "(CLAIM.md said 13.7)",
            "quintile_monotonicity_shares": "8.2% -> 1.5% inverse — MATCHES, but not monotone: "
            "quintile 2 (9.3%) is above quintile 1 (8.2%)",
            "intermediate_50pct_rates": "dev 4.5->8.1 (433), val 9.4->14.1 (263), "
            "holdout 5.2->7.8 (230) — MATCHES CLAIM.md exactly",
        },
        "disagreements": {
            "in_play_only_row": (
                "CLAIM.md's 'in play only, no shape gates' row (242 trades, -20.3R, -0.084) is "
                "NOT in-play alone. It is in-play plus every SHIPPED rule except `passed`. "
                "In-play with no shipped rules at all books 366 trades for -157.3R (-0.430 per "
                "trade) — far worse than the -0.25 pool base rate. The label understates how bad "
                "the in-play filter is on its own."
            ),
            "error_bar": (
                "CLAIM.md says +0.50 +/- 0.43 R/trade. Re-derived: +0.478 +/- 0.260 (1 s.e., "
                "n=35). The mean matches; the stated error bar is ~1.65x the 1-s.e. figure, so it "
                "is presumably a wider interval. Stated as +/- 1 s.e. it is +/-0.260."
            ),
            "rvol_contradiction_resolved": (
                "CLAIM.md's 'varying rvol does nothing but removing it flips the holdout' is not a "
                "contradiction: `rvol_pole >= x` also drops the 12 rows where rvol_pole is null. "
                "Varying x moves 3 trades; removing the clause restores 15 (12 null + 3 low). "
                "Removing it entirely RAISES total net R from +16.7 to +21.5 on 50 trades."
            ),
        },
        "headline_numbers": h,
    }


def _specs(steps: dict[str, Any]) -> dict[str, Any]:
    sw = steps.get("sweeps") or {}
    s2 = steps.get("step2_simplest") or {}
    s3 = steps.get("step3_placebo") or {}
    return {
        "1_running_alternatives": {
            "method": "replace the runup clause with 17 alternatives at matched selectivity",
            "result": "the original ranks 11th of 18 by net R; 17 of 18 are net-positive; two "
            "alternatives that admit EVERY row (range_before_pole_pct>=0, runup_to_pole>=0) score "
            "+18.2R vs the original's +16.7R — i.e. deleting the running clause beats it",
            "rows": sw.get("running_alternatives"),
        },
        "2_small_alternatives": {
            "method": "replace the shares clause with 7 alternatives at matched selectivity",
            "result": "shares_outstanding 16.7R > cum_dollar_vol 13.7 > mktcap 12.7 > "
            "mktcap_open 11.6 >> float_shares 1.7 = float_cap 1.7 > price 0.4 > planned_risk 0.2. "
            "Size/liquidity carries it; PRICE carries none of it; float carries none of it",
            "rows": sw.get("small_alternatives"),
        },
        "3_rvol": {
            "method": "threshold grid 0..25 vs full removal vs removal-keeping-non-nulls",
            "result": "net R/trade is +0.43..+0.56 across the whole grid and +0.43 with the clause "
            "removed; removal-keeping-non-nulls reproduces the ge_0.0 book exactly. rvol_pole is "
            "DECORATIVE: it lifts the SHIPPED 2R-hit rate by +0.5pp and total net R is higher "
            "without it",
            "detail": sw.get("rvol"),
        },
        "4_gradients": {
            "method": "deciles on 3,639 rows, and a book sweep from keep-10% to keep-90%",
            "result": "rate50 (a 50%+ move) IS monotone in runup (1.4%->16.8%); rate_2r (a 2R "
            "excursion) is NOT (20.9%->26.9%, non-monotone) and is flat in shares_outstanding "
            "(22.9%..19.9%, no gradient). The net-R gradient is mostly a COST gradient: runup "
            "deciles run cost_r 0.468 -> 0.111 while gross runs only -0.374 -> -0.192",
            "detail": sw.get("gradients"),
        },
        "5_combination_logic": {
            "method": "singles, pairs, 2-of-3, AND-of-3, additive rank score, matched singles",
            "result": "shares alone 60tr/+25.2R; runup alone 98tr/+8.1R; rvol alone 89tr/+8.0R; "
            "runup+rvol 73tr/+8.7R (~= the +7.1R shipped baseline). EVERY combination containing "
            "shares scores +0.42..+0.48/trade; every combination without it scores +0.08..+0.12. "
            "One feature does all the work",
            "detail": sw.get("combination"),
        },
        "6_outcome_definitions": {
            "method": "targets 1.0..4.0, gross vs net, P(50%+ move), mean/median max_r",
            "result": "the in-play book beats the shipped book at all six targets, but its own "
            "net R is non-monotone and peaks exactly at the claimed 2R: +2.8 / +7.8 / +16.7 / "
            "+4.5 / +10.6 / -2.6. The supporting 50%-move evidence does not transfer to R",
            "detail": sw.get("outcomes"),
        },
        "7_population_definitions": {
            "method": "passed on/off, pre-market cut 540/555/570/600, cons_range on/off, cap 1-5",
            "result": "the rule is net-positive ONLY on the full SHIPPED population. passed off "
            "(rest of SHIPPED on): -0.084/trade. passed only: -0.107. no gates: -0.430. Its "
            "INCREMENT over the local baseline is positive in all four (+0.175, +0.390, +0.092, "
            "+0.420). The 2-a-day cap is irrelevant: the rule books 35 trades at cap 1, 2, 3 and 5",
            "detail": sw.get("populations"),
        },
        "8_placebo_and_family": {
            "method": "random k-row subsets; 80 arbitrary feature cuts; 288-spec family",
            "result": "random 35-row subsets of SHIPPED average +1.9R (sd 7.4) so +16.7R sits at "
            "p=0.019; of 80 arbitrary feature cuts at the same selectivity only 2 beat it, but the "
            "best (move_since_appearance<=X, invented for this study) books +40.6R — 2.4x the "
            "claim. 288-spec family: 73% net-positive, median +4.2R, the original ranks 57th",
            "random_subset_null": s3.get("random_subset_null"),
            "placebo_summary_n35": s3.get("placebo_summary_n35"),
            "placebo_summary_n60": s3.get("placebo_summary_n60"),
            "spec_family_summary": s3.get("spec_family_summary"),
        },
        "9_simplest_rule": {
            "method": "shares clause alone: decomposition, threshold sweep, walk-forward, "
            "permutation, calendar",
            "result": "60tr/+25.2R/+0.419 per trade; plateau from 3e7 to 2e8; 5/6 walk-forward "
            "blocks positive; permutation p=0.038 vs the AND-of-3's p=0.19. BUT half the book "
            "effect is the NULL-DROP (dropping rows with no share count alone takes shipped from "
            "+0.058 to +0.192/trade) and 28 of those 29 null rows are recon, so that half cannot "
            "exist live. And live net R is negative at EVERY shares threshold tested",
            "detail": {
                "decomposition": s2.get("shares_decomposition"),
                "threshold_sweep": s2.get("shares_threshold_sweep"),
                "walk_forward": s2.get("walk_forward"),
                "permutation": s2.get("permutation"),
            },
        },
        "10_population_ladder": {
            "method": "lift in the 2R-hit rate from shares<=50e6, in nine populations, "
            "bootstrapped; plus 125-row subsample nulls",
            "result": "+0.6pp (all 3,639) / +3.6pp (passed, 317) / +0.6pp (price band, 1809) / "
            "+0.5pp (stop_pct, 2981) / +0.6pp (cycle, 3580) / +0.0pp (staleness, 2761) / +0.7pp "
            "(trigger window, 3522) / +0.6pp (SHIPPED minus passed, 955) / +11.6pp (SHIPPED, 125). "
            "Every wide-population interval straddles zero; only SHIPPED's does not",
            "detail": (steps.get("step5_interaction") or {}).get("size_ladder"),
        },
    }


if __name__ == "__main__":
    main()
