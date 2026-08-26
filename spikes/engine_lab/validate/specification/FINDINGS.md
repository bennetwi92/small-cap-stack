# Validator B — the specification surface

**Verdict: `ARTEFACT`** for the claim as specified. Confidence **high** that the three-clause rule
is not what produces the number; **moderate** that the one clause left standing is also noise.

Question asked here: *if this effect is real, many reasonable ways of saying "the stock is in play"
should find it.* The answer turned out to be more specific than either yes or no — **many
reasonable ways do find something, but none of them needs the "in play" idea at all.** Two of the
three clauses are inert. One clause carries everything, and it is not a "running" clause; it is
`shares_outstanding <= 50e6`.

Every number below was computed in this folder from `data/spikes/regime_panel.parquet` and the raw
bars. The holdout (2026-07-01..08-13) is contaminated per `CLAIM.md`; it is reported for
completeness and nothing here rests on it.

---

## Step 0 — re-deriving CLAIM.md

Reproduced exactly, independently: shipped-only **122 trades / +7.1R / +0.058 per trade**; shipped +
in-play **35 / +16.7R / +0.478**; per period **dev +4.8 / val +10.6 / holdout +1.3**; the 50%-move
rates in all three periods (dev 4.5→8.1 on 433 rows, val 9.4→14.1 on 263, holdout 5.2→7.8 on 230);
and the runup quintile ladder 1.8% → 13.6%.

Three disagreements, all of which matter:

1. **The "in play only, no shape gates" row is mislabelled.** 242 trades / −20.3R / −0.084 is
   in-play *plus every SHIPPED rule except `passed`*. In-play with no shipped rules at all books
   **366 trades for −157.3R (−0.430 per trade)** — nearly twice as bad as the −0.25 pool base rate.
   The filter on its own is not mildly unhelpful, it is destructive.
2. **The error bar.** CLAIM.md says +0.50 ± 0.43. Re-derived, the mean is +0.478 and one standard
   error is **±0.260** on n=35.
3. **`shares_outstanding` is not monotone at the small end.** Quintile 1 (smallest, 8.2% 50%-move
   rate) is *below* quintile 2 (9.3%). The inverse relationship is real only at the large end.

## The `rvol_pole` contradiction, settled

CLAIM.md records that varying `rvol_pole` changes almost nothing but removing it flips the holdout.
Both measurements are right; they are measuring different things.

`rvol_pole >= x` does **two** jobs: it drops rows below `x`, and it drops rows where `rvol_pole` is
null (12 of the 50 rows that survive the other two clauses have no pre-pole bars to build a baseline
from). Varying `x` moves only the first job — three trades between 0.0 and 2.0. Removing the clause
moves both — fifteen trades.

| rvol clause | trades | net R | net R/trade |
|---|---|---|---|
| ≥ 0.0 | 38 | +19.3 | +0.507 |
| ≥ 1.0 | 36 | +18.6 | +0.518 |
| **≥ 2.0 (claimed)** | **35** | **+16.7** | **+0.478** |
| ≥ 5.0 | 31 | +14.9 | +0.482 |
| ≥ 25.0 | 26 | +14.3 | +0.552 |
| **removed entirely** | **50** | **+21.5** | **+0.429** |
| removed, non-null rows only | 38 | +19.3 | +0.507 |

The last row reproduces `≥ 0.0` exactly, which is the proof: **the whole apparent effect of
"removing rvol" is the null-drop, not the threshold.** And removing the clause *raises* total net R
by +4.8R. `rvol_pole` is decorative. Independently, it lifts the SHIPPED 2R-hit rate by **+0.5pp**
(38.4% → 38.9%).

---

## 1. Other ways to say "already running"

Seventeen alternatives, each swapped in for `runup_pre_appearance` at **matched selectivity** (the
threshold is set so it admits exactly as many SHIPPED rows as the original clause, removing "how
much did you cut" as an explanation). Ten are built from raw bars; how each is bounded is documented
in `features.py` and validated by reproducing the panel's own `ext_at_trigger` and
`range_before_pole_pct` to five decimal places.

The original ranks **11th of 18** by net R. Seventeen of eighteen are net-positive. But look at what
sits above it:

| feature | threshold | trades | net R | note |
|---|---|---|---|---|
| ret over 12 bars before the pole | −0.0035 | 29 | +19.9 | |
| extension at the scanner appearance | 0.142 | 38 | +19.4 | |
| pole base low → running high | 0.194 | 36 | +18.6 | |
| **running high before the pole ≥ 0** | **0** | **39** | **+18.2** | **admits every row** |
| **pre-pole range ≥ 0** | **0** | **39** | **+18.2** | **admits every row** |
| extension when the pole started | −0.0089 | 31 | +17.8 | |
| *runup_pre_appearance ≥ 0.15* | *0.152* | *35* | *+16.7* | *the claim* |
| extension at the trigger | 0.151 | 35 | +13.7 | |
| move since the scanner saw it | −0.059 | 29 | +1.8 | |

Two of the alternatives that beat the original have thresholds of **zero** — they admit every row.
In other words, **deleting the running clause outright scores better than the claimed one**
(+18.2R on 39 trades vs +16.7R on 35). Two more of the top six have *negative* thresholds, i.e. they
also admit nearly everything.

The direct measurement confirms it: `runup_pre_appearance >= 0.15` lifts the SHIPPED 2R-hit rate by
**+0.2pp** (38.4% → 38.6%).

## 2. Other ways to say "small"

| feature | trades | net R | net R/trade |
|---|---|---|---|
| **shares_outstanding (original)** | 35 | **+16.7** | +0.478 |
| dollar volume to the trigger | 26 | +13.7 | +0.527 |
| market cap (shares × entry fill) | 33 | +12.7 | +0.384 |
| market cap (shares × 04:00 open) | 34 | +11.6 | +0.342 |
| float_shares (live rows only) | 18 | +1.7 | +0.094 |
| float × price (live rows only) | 18 | +1.7 | +0.094 |
| **price alone** | 33 | **+0.4** | +0.011 |
| dollars of risk per share | 27 | +0.2 | +0.008 |

Across the 288-spec family the picture is the same (median net R by size measure):
shares_outstanding +18.6R, mktcap +15.3, mktcap at the open +14.2, dollar volume +12.5, then a cliff
to float +0.4, **price −0.6**, planned risk −1.8.

So: **it is size, not price.** Market capitalisation — arguably the more meaningful quantity —
delivers about three quarters of it. Float delivers none of it, on the live rows where float exists.

## 3. Continuous vs threshold — the finding that breaks the claim

Deciles over all 3,639 rows. Two outcome columns side by side: `rate50` = the setup made a 50%+
move; `rate2R` = the setup reached its 2R target, which is what the money is actually made of.

`runup_pre_appearance`, low decile → high decile:

| | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 | D9 | D10 |
|---|---|---|---|---|---|---|---|---|---|---|
| rate50 | 1.4% | 2.2% | 0.5% | 2.2% | 2.2% | 7.1% | 8.2% | 8.5% | 10.4% | **16.8%** |
| **rate2R** | 20.9% | 23.4% | 20.3% | 24.9% | 19.8% | 28.0% | 26.4% | 31.9% | 28.3% | **26.9%** |
| gross R | −0.374 | −0.297 | −0.390 | −0.252 | −0.405 | −0.159 | −0.209 | −0.044 | −0.151 | −0.192 |
| **cost R** | 0.468 | 0.408 | 0.376 | 0.320 | 0.291 | 0.244 | 0.221 | 0.189 | 0.160 | **0.111** |
| net R | −0.842 | −0.706 | −0.766 | −0.572 | −0.696 | −0.404 | −0.429 | −0.233 | −0.311 | −0.304 |

Two things fall out of that table.

**(a) The 50%-move evidence does not transfer to R.** `rate50` rises 12× across the range; `rate2R`
does not rise at all in any stable way. The reason is mechanical: names that have already run have
wider consolidation ranges, so a 50% move is fewer R for them. The size of the move and the size of
the stop go up together and cancel.

**(b) Two thirds of the net-R gradient is commission, not outcome.** Net improves by +0.538 across
the range. Gross improves by +0.182. **Cost falls by 0.357.** On a $500 account the $0.35/side
commission minimum is a bigger share of R when the dollar stop is narrow, and extended names have
wider dollar stops (mean planned risk 0.35 → 0.43). The feature is a proxy for *cheapness to trade*,
not for *quality of setup*.

`shares_outstanding` is worse: `rate2R` by decile runs 22.9, 28.3, 27.4, 26.9, 28.6, 21.8, 24.3,
27.4, 25.3, 19.9. **There is no gradient.** Only the largest decile is below the pack.

## 4. Combination logic — one feature does all the work

Every combination on the SHIPPED population:

| rule | trades | net R | net R/trade |
|---|---|---|---|
| shipped only | 122 | +7.1 | +0.058 |
| runup alone | 98 | +8.1 | +0.082 |
| rvol alone | 89 | +8.0 | +0.090 |
| runup + rvol | 73 | +8.7 | +0.119 |
| **shares alone** | **60** | **+25.2** | **+0.419** |
| runup + shares | 50 | +21.5 | +0.429 |
| rvol + shares | 39 | +18.2 | +0.467 |
| **AND-of-3 (the claim)** | **35** | **+16.7** | **+0.478** |
| at least 2 of 3 | 92 | +14.9 | +0.162 |
| additive rank score, matched | 35 | +1.9 | +0.054 |

Read it as two families. **Every combination that contains `shares` scores +0.42 to +0.48 per
trade. Every combination that does not is +0.08 to +0.12 — indistinguishable from the shipped
baseline.** Adding runup and rvol to the size clause costs 25 trades and 8.5R of total return in
exchange for +0.06 R/trade, which is a quarter of one standard error.

The additive rank score at matched selectivity collapses to +1.9R, because averaging one useful
feature with two useless ones dilutes it.

## 5. Outcome definitions

| target | shipped only | AND-of-3 | shares alone |
|---|---|---|---|
| 1.0R | −15.3 | +2.8 | +5.4 |
| 1.5R | −3.6 | +7.8 | +12.7 |
| **2.0R** | +7.1 | **+16.7** | **+25.2** |
| 2.5R | −19.1 | +4.5 | +15.4 |
| 3.0R | −27.1 | +10.6 | +10.6 |
| 4.0R | −60.5 | −2.6 | −10.6 |

Good news: the in-play book beats the shipped book at **all six** targets. Bad news: its own return
is non-monotone and peaks **exactly at the claimed 2R** — 2.5R yields +4.5R where 2R yields +16.7R,
a 73% drop from a 25% change in the target. On 35 trades that is a coin-flip artefact, but it means
the claim's headline is quoted at its best point.

Row statistics on the selected rows (a bigger sample than the book): shipped 38.4% reach 2R, mean
max R 2.33; shipped + in-play 51.4%, mean max R 3.04 — but that is 35 rows against 125.

## 6. Population definitions

| population | base | + in play |
|---|---|---|
| SHIPPED (the claim's) | +0.058 | **+0.478** |
| `passed` off, rest of SHIPPED on (955 rows) | −0.259 | **−0.084** |
| `passed` only (317 rows) | −0.496 | **−0.107** |
| no gates at all (3,639 rows) | −0.521 | **−0.430** |

Pre-market cut at 540 / 555 / 570 / 600 and `cons_has_range` on/off change the number by at most
−3.9R and never the sign. The 2-a-day cap is **irrelevant**: the rule books 35 trades at a cap of 1,
2, 3 and 5 alike — no day ever produces two qualifying setups.

The honest reading of the table cuts both ways. **The rule is net-positive in exactly one
population, the one it was fitted on.** But its *increment* over the local baseline is positive in
all four (+0.420, +0.175, +0.390, +0.092), which is what you would expect of a weak real effect
sitting on top of a losing pool.

## 7. How surprising is +16.7R?

- **Random 35-row subsets of SHIPPED** (5,000 draws): mean +1.9R, sd 7.4, p95 +13.3, max +27.8.
  Observed +16.7R → **p = 0.019**.
- **Eighty arbitrary feature cuts** — every trigger-time-safe numeric column, cut in both directions
  at the same selectivity: median +2.1R, p90 +11.2R, and **2 of 80 (2%) beat +16.7R**. That reads
  well until you see the winner: `move_since_appearance <= X` books **+40.6R (+1.16 per trade)** —
  2.4× the claim, from a feature invented for this study an hour ago, with no story behind it and
  the "already running" logic pointing the *wrong way*. When an arbitrary new feature can beat the
  claim by that much, "97.5th percentile of 80" is not a small number.
- **Permutation** (same trade count, same days, random rows drawn from the SHIPPED pool):
  **AND-of-3 p = 0.19** — it is *not* distinguishable from picking the same number of shipped rows
  at random. The single shares clause gets **p = 0.038**.

## 8. The specification family — the direct answer to my question

288 specifications: 18 running measures × 8 size measures × rvol on/off, every threshold at matched
selectivity.

- **73% are net-positive.** Median **+4.2R (+0.150 per trade)** against a pool base of −0.25 and a
  shipped baseline of +0.058.
- The original ranks **57th of 288**.
- The spread is explained almost entirely by the **size** axis (median +18.6R down to −1.8R), barely
  at all by the **running** axis (+11.0R down to −1.8R, the original mid-pack at +6.7R), and not at
  all by **rvol** (+5.0R on vs +3.1R off).

So: many reasonable specifications do find *something*. But what they find is one third of the
claimed magnitude, and it is entirely attributable to the size clause.

## 9. The population ladder — the decisive test

If `shares_outstanding <= 50e6` is a real property of small-cap momentum setups, it should lift the
2R-hit rate everywhere, not only where it was found. Lift in percentage points, 90% bootstrap
interval in brackets:

| population | n | base 2R% | small 2R% | lift |
|---|---|---|---|---|
| all rows | 3,639 | 25.1% | 25.7% | **+0.6** [−0.5, +1.6] |
| `passed` only | 317 | 25.2% | 28.9% | **+3.6** [−0.7, +7.8] |
| price band 3–50 only | 1,809 | 27.8% | 28.4% | **+0.6** [−1.2, +2.4] |
| stop_pct ≥ 0.025 only | 2,981 | 25.3% | 25.8% | **+0.5** [−0.5, +1.5] |
| cycle ≤ 2 only | 3,580 | 25.2% | 25.8% | **+0.6** [−0.4, +1.6] |
| staleness ≤ 30 only | 2,761 | 25.4% | 25.5% | **+0.0** [−1.2, +1.3] |
| trigger 240–555 only | 3,522 | 25.0% | 25.7% | **+0.7** [−0.3, +1.7] |
| SHIPPED minus `passed` | 955 | 29.1% | 29.7% | **+0.6** [−1.4, +2.6] |
| **SHIPPED** | **125** | **38.4%** | **50.0%** | **+11.6** [+4.2, +19.1] |

Eight populations, every interval straddling zero. One population — the 125 rows the rule was found
in — at +11.6pp. The same ladder for `runup_pre_appearance` ends at **+0.2pp** inside SHIPPED.

Drawing 125 random rows repeatedly and applying the size clause gives a mean lift of +0.6pp (sd 3.2)
from the wide pool and +3.5pp (sd 3.3) from the `passed`-only pool, so +11.6pp is not *merely*
small-sample noise of that size (p = 0.0003 and 0.0075 respectively). But those p-values are for a
hypothesis discovered in the same 125 rows, so they cannot be read as evidence.

## 10. Two data problems inside the size clause

**(a) Half of it is a null-drop, and the null-drop is recon-only.** Within SHIPPED:

| group | rows | net R/row | rate50 |
|---|---|---|---|
| shares ≤ 50e6 | 60 | **+0.420** | 16.7% |
| shares > 50e6 | 36 | −0.188 | 5.6% |
| shares **null** | 29 | −0.396 | 3.4% |

Dropping only the big companies (keeping nulls) books 89 trades for +13.7R (+0.154/trade). Dropping
big companies *and* nulls books 60 for +25.2R (+0.419). Having a share count at all is worth roughly
as much as being small. **28 of those 29 null rows are recon; live has 1 null in 25.** So that half
of the edge is a property of the recon backfill's EDGAR coverage and would deliver nothing live.

**(b) The two halves use different vendors.** Recon share counts come from EDGAR (median 88 days
before the session; verified — zero rows have an as-of date after the session, so no lookahead).
Live counts come from FMP (20 rows) and yfinance (4). Net R per row for the 60 selected:
recon/EDGAR **+0.634**, live/FMP **+0.129**, live/yfinance **−1.132**. And **live net R is negative
at every shares threshold tested** (2e6 through 1e12, −0.1R to −3.8R). The engine-lab protocol says
a rule must work on both halves; this one works on one.

## 11. The simplest specification that would actually ship

`SHIPPED AND shares_outstanding <= 50e6` — one threshold.

- 60 trades over 197 sessions (0.30/session), **+25.2R net, +0.419 ± 0.198 per trade**, 50% win
  rate, max drawdown −6.6R.
- Threshold plateau: 3e7 → 2e8 all book +18R to +25R; the peak is at 5e7.
- Walk-forward **5/6 blocks positive**, +22.2R over 49 trades; refitting the threshold in every
  block gives 4/6 and +19.2R.
- Permutation **p = 0.038**; the AND-of-3's is 0.19.
- Not concentrated: 53 distinct symbols in 60 trades, top 5 trades = 39% of the total.
- Monthly: eight of nine months positive; 2026-08 is −6.6R on 6 trades.

It is strictly better than the three-clause rule on every axis — 71% more trades, +8.4R more return,
a tighter error bar, a better permutation p — and it uses one threshold instead of three. **If
anything from this claim were to ship, it should be this.** Section 9 is why I would not ship it.

## What would change my mind

1. A **+8pp or better** lift in the 2R-hit rate from `shares <= 50e6` measured on pre-market rows
   *outside* the 125 SHIPPED rows — a fresh period, or a widened shipped-like population. That is
   the one measurement that is currently flat everywhere except where the rule was found.
2. A **single point-in-time share-count source across both halves**, with the recon-only null-drop
   removed, still producing the effect.
3. The wide-pool gradient **surviving a larger account**. Today about two thirds of the net-R
   gradient in `runup_pre_appearance` is falling commission drag, not a better outcome; on an
   account where the $0.35 minimum did not bind, that gradient should mostly disappear.

## Files

    speclab.py            local helpers on top of common.py (never forks it)
    features.py           bar-derived alternative features + how each is bounded
    step0_rederive.py     re-derivation of CLAIM.md
    sweeps.py             sections 1-7 of the brief
    step2_simplest.py     the one-clause rule, walk-forward, permutation, calendar
    step3_placebo.py      random-subset null, arbitrary-feature null, the 288-spec family
    step4_rowlevel.py     large-sample row-level decomposition by split and source
    step5_interaction.py  the population ladder
    emit_result.py        assembles result.json

Outputs: `data/spikes/engine-lab/validate/specification/`.
