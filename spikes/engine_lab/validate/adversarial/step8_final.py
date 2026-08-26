"""Step 8 — the fairest single test I can build, then the verdict payload.

The honest question is not "is `shares <= 50M` significant?" (it was chosen after looking) but
"would an analyst who searched this feature space on DEV+VAL have come away with a real rule?"

So: on DEV+VAL only, search every candidate feature for the best single clause on top of SHIPPED,
then run the SAME search on permuted outcomes. Everything the analyst could have picked is in the
null. If the observed best still stands out, there is something here. If not, there is not.

Also emits `result.json`.
"""

from __future__ import annotations

import json

import lab as L
import numpy as np
import polars as pl
import search as S


def main() -> None:
    df = L.load_panel_checked()
    nohold = df.filter(pl.col("split") != "holdout")
    pop = S.Pop(nohold)
    base = S.shipped_mask(nohold)
    out: dict = {}

    L.hr("8a. DEV+VAL: best SINGLE clause over the whole feature menu, vs its own null")
    menu = pop.menu(base)
    print(
        f"  menu: {len(menu)} clauses over {len(pop.feat)} features, {int(base.sum())} shipped rows"
    )
    for min_trades in (20, 30, 40):
        cls, obs, n = S.greedy(pop, base, menu=menu, max_clauses=1, min_trades=min_trades)
        rng = np.random.default_rng(1234 + min_trades)
        idx = np.flatnonzero(base)
        vals = []
        for _ in range(1000):
            mr = pop.max_r.copy()
            mr[idx] = pop.max_r[rng.permutation(idx)]
            _c, bv, _bn = S.greedy(
                pop, base, menu=menu, max_clauses=1, min_trades=min_trades, max_r=mr
            )
            vals.append(bv)
        va = np.array([v for v in vals if np.isfinite(v)])
        p = (int((va >= obs).sum()) + 1) / (len(va) + 1)
        print(
            f"  min_trades={min_trades:>2}: picked {[str(c) for c in cls]} -> {n} trades "
            f"{obs:+.4f}/trade | null median {np.median(va):+.4f} p90 "
            f"{np.quantile(va, 0.9):+.4f} -> p={p:.3f}"
        )
        out.setdefault("devval_best_single_clause", {})[str(min_trades)] = {
            "picked": [str(c) for c in cls],
            "trades": n,
            "observed": round(obs, 4),
            "null_median": round(float(np.median(va)), 4),
            "null_p90": round(float(np.quantile(va, 0.9)), 4),
            "p_value": round(p, 4),
        }

    L.hr("8b. How often does the search pick shares_outstanding at all, on real vs noise?")
    rng = np.random.default_rng(4321)
    idx = np.flatnonzero(base)
    picks = []
    for _ in range(500):
        mr = pop.max_r.copy()
        mr[idx] = pop.max_r[rng.permutation(idx)]
        c, _v, _n = S.greedy(pop, base, menu=menu, max_clauses=1, min_trades=30, max_r=mr)
        picks.append(c[0].col if c else None)
    from collections import Counter

    cnt = Counter(picks)
    print(
        f"  under noise the top pick is spread over {len(cnt)} different features; "
        f"most common: {cnt.most_common(5)}"
    )
    print(f"  shares_outstanding chosen under noise {cnt.get('shares_outstanding', 0)}/500 times")
    out["noise_pick_spread"] = {
        "distinct_features": len(cnt),
        "top5": cnt.most_common(5),
        "shares_outstanding_rate": cnt.get("shares_outstanding", 0) / 500,
    }

    # ------------------------------------------------------------------------------ result.json
    L.hr("8c. Verdict payload")
    prev = {}
    for f in (
        "step0_rederive.json",
        "step0b_discrepancies.json",
        "step1_anatomy.json",
        "step2_null.json",
        "step2b_null_calibration.json",
        "step3_walkforward.json",
        "step3b_wf_null.json",
        "step3c_wf_decompose.json",
        "step4_robustness.json",
        "step5_plateau_and_rivals.json",
        "step6_shares_provenance.json",
        "step7_residue.json",
    ):
        p = L.OUT / f
        if p.exists():
            prev[f.replace(".json", "")] = json.loads(p.read_text())
    L.write("step8_final.json", out)

    result = {
        "validator": "A (adversarial)",
        "claim": "SHIPPED + (runup>=0.15 & rvol>=2.0 & shares<=50e6), 2/day, 2R/-1R "
        "= +0.478 net R/trade over 35 trades",
        "verdict": "ARTEFACT",
        "confidence": "high on the three-clause rule as stated; moderate that a much weaker "
        "single-threshold size effect is real",
        "headline": {
            "claim_reproduces_exactly": True,
            "claim_net_r_per_trade": 0.4784,
            "claim_trades": 35,
            "search_null_median_net_r_per_trade_at_this_trade_count": 0.6472,
            "p_vs_search_null_wide": 0.9202,
            "p_vs_search_null_narrow_3_features": 0.3784,
            "p_vs_search_null_roundnumber_grid": 0.2579,
            "p_vs_fixed_rule_null_if_never_searched": 0.0402,
            "matched_trade_count_permutation_p_shipped_pool": 0.1955,
            "fairest_single_test_devval_best_single_clause": {
                "picked": "shares_outstanding <= 33M (min 30 trades) / <= 63M (min 40)",
                "p_vs_full_menu_search_null": [0.092, 0.068],
                "note": "runup and rvol are never picked; the size feature is picked 3.2% of the "
                "time under noise, so the feature choice itself is weak evidence FOR",
            },
        },
        "what_it_was_really_measuring": {
            "summary": "Two of the three clauses do nothing. `runup_pre_appearance >= 0.15` "
            "changes net R/trade by +0.01 and rvol_pole SUBTRACTS. The load-bearing "
            "part is `shares_outstanding` — and 60% of even that is the field being "
            "POPULATED rather than being small.",
            "decomposition_net_r_per_trade": {
                "SHIPPED": 0.0583,
                "+ all three fields non-null (no thresholds)": 0.1962,
                "+ runup >= 0.15": 0.2088,
                "+ rvol >= 2.0": 0.1485,
                "+ shares <= 50M (the claim)": 0.4784,
            },
            "shares_present_alone_is_better_value": {
                "trades": 96,
                "net_r": 18.40,
                "net_r_per_trade": 0.1917,
                "note": "more total R than the claim's rule, at 2.7x the trade count",
            },
            "one_clause_residue": {
                "rule": "SHIPPED + shares_outstanding <= 50e6",
                "trades": 60,
                "net_r": 25.17,
                "net_r_per_trade": 0.4195,
                "per_session": 0.30,
                "note": "beats the 3-clause claim on total R, trade count and every null",
            },
        },
        "tests": {
            "step0_rederivation": prev.get("step0_rederive", {}).get("headline_table"),
            "claim_md_errors": {
                "in_play_only_row": {
                    "claimed": {"trades": 242, "net_r": -20.3, "net_r_per_trade": -0.084},
                    "actual_in_play_only": {
                        "trades": 366,
                        "net_r": -157.3,
                        "net_r_per_trade": -0.430,
                    },
                    "what_242_actually_is": "SHIPPED minus `passed` PLUS in play — i.e. the row "
                    "labelled 'no shape gates' still carries the price "
                    "band, trigger window, cycle, staleness and stop_pct "
                    "selection rules.",
                },
                "intermediate_signal_denominators": {
                    "claimed_n": [433, 263, 230],
                    "population_whose_RATES_match": "the raw 3,639-row panel (n = 2012/977/650)",
                    "note": "the rates 4.5->8.1 / 9.4->14.1 / 5.2->7.8 reproduce exactly on the "
                    "raw panel, so the quoted denominators are wrong; on the SHIPPED pool "
                    "the rule actually operates on, the samples are 63/37/25.",
                },
                "error_bar": {
                    "claimed": "+0.50 +/- 0.43",
                    "recomputed_naive_se": 0.2596,
                    "session_block_bootstrap_ci95": [-0.0392, 0.9964],
                },
            },
            "walk_forward": prev.get("step3_walkforward", {}),
            "walk_forward_vs_null": prev.get("step3b_wf_null", {}),
            "walk_forward_decomposed": prev.get("step3c_wf_decompose", {}),
            "nulls": prev.get("step2_null", {}),
            "null_calibration": prev.get("step2b_null_calibration", {}),
            "robustness": prev.get("step4_robustness", {}),
            "plateau_and_rivals": prev.get("step5_plateau_and_rivals", {}),
            "shares_provenance": prev.get("step6_shares_provenance", {}),
            "residue": prev.get("step7_residue", {}),
            "devval_final": out,
        },
        "attacks_that_landed": [
            "Search null: a 3-clause greedy search over the same feature menu, on SCRAMBLED "
            "outcomes, produces a median best rule of +0.65R/trade at a 35-trade floor. The "
            "claim's +0.478 is beaten by noise 94% of the time.",
            "Even the narrowest possible null — the claim's own 3 features on a 150-point "
            "round-number grid — beats +0.478 on scrambled outcomes 26% of the time.",
            "The lab's own mandatory permutation test (README anti-overfit #4) FAILS: random rows "
            "from the SHIPPED pool taking the same trades on the same days do as well p=0.196.",
            "Two of three clauses are inert or harmful: runup >= 0.15 moves net R/trade by +0.013; "
            "rvol >= 2.0 moves it by -0.060. CLAIM.md suspected rvol; it is worse than suspected.",
            "The largest single jump in the chain is nullity, not a threshold: requiring "
            "shares_outstanding to merely EXIST takes +0.058 to +0.192 (125 -> 96 rows). The "
            "null rate is 24.7% in dev and 4.8% in the live period.",
            "0 of 23 sensitivity cells beat the search null's median at their own trade count — "
            "the claimed 'plateau' is the small-sample selection effect, flat in TOTAL R "
            "(+12 to +19R everywhere) with only the denominator moving.",
            "The '2 per day' cap is inert: 35 trades over 35 distinct sessions.",
            "CLAIM.md's 'in play only, no shape gates' row is mislabelled by 7.7x per trade.",
            "Frequency objective missed by 3x: 0.18 trades/session against the lab's stated 0.5.",
        ],
        "attacks_that_failed": [
            "Outlier concentration: dropping the top 8 of 35 trades still leaves +1.04R. The "
            "winners are all ~+1.96R (the 2R target), so this is a win-rate story, not one trade.",
            "Leave-one-out: no month, no symbol and no period turns it negative.",
            "Sub-sample stability: 15 of 15 sub-samples (odd/even sessions, halves, price band, "
            "stop band, source, split, sizing mode) are positive.",
            "Lookahead in the as-of date: `shares_as_of` never post-dates the session (max lag 0 "
            "days, median -89). The dev+val half is entirely EDGAR point-in-time data.",
            "Session block bootstrap: 95% CI on net R/trade is [-0.04, +1.00], P(<=0) = 3.3%.",
            "Exit target: the rule's edge over SHIPPED is positive at every target 1R-4R.",
            "The one-clause residue survived everything I threw at it: dev+val only, "
            "shares<=50M gives 42 trades, +26.6R, +0.634/trade, p=0.0012 against a fixed-rule "
            "permutation null, and 5/6 walk-forward blocks positive at p=0.009.",
        ],
        "what_would_change_my_mind": [
            "A pre-registered `shares_outstanding <= 50e6` on top of the shipped rules, traded "
            "forward for ~60 fresh sessions with no other change. At 0.3 trades/session that is "
            "~18 trades — still not enough to settle it, but it is the only clean evidence "
            "available and it costs nothing to collect while Phase 1 runs.",
            "A mechanism: if small share counts win because thin supply makes the 2R target "
            "reachable, that should show as a higher P(max_r >= 2) at matched stop width and "
            "matched dollar volume. Measured, not argued.",
            "Anything that shows the size effect at the same magnitude OUTSIDE the 125-row "
            "shipped pool. Today it is +0.58R/row inside and +0.02R/row outside — a 24x gap on "
            "96 rows, which is what an interaction looks like when it is really small-sample "
            "noise.",
        ],
        "hard_rules_observed": {
            "holdout_used_as_evidence": False,
            "holdout_note": "Holdout figures were computed and are in the JSON, but no verdict "
            "claim rests on them. Note a structural confound worth flagging to "
            "the synthesis: source, split and shares provenance are perfectly "
            "collinear (recon = dev+val = EDGAR dated; live = holdout = "
            "fmp/yfinance undated), so the README's 'must work on both halves' "
            "test CANNOT be run independently of the spent holdout.",
            "assert_no_lookahead": "passed on the claim's column set",
            "book_ordering": "earliest-trigger-first throughout (common.build_book and a numpy "
            "reimplementation verified equal to it)",
        },
    }
    L.write("result.json", result)
    print(json.dumps({k: result[k] for k in ("verdict", "confidence", "headline")}, indent=2))
    print("\nwrote result.json")


if __name__ == "__main__":
    main()
