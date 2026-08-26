# Validator A (adversarial) — findings on the in-play claim

**Verdict: `ARTEFACT`** for the rule as stated. Confidence **high**.

There is a residue — a *single* threshold on `shares_outstanding` — that I could not break, and it
is materially stronger than the rule I was asked to test. I report it separately at the end; it is
not the claim.

Re-run everything: `.venv/bin/python spikes/engine_lab/validate/adversarial/stepN_*.py` (steps 0,
0b, 1, 2, 2b, 3, 3b, 3c, 4, 5, 6, 7, 8, in that order). Outputs land in
`data/spikes/engine-lab/validate/adversarial/`. The numpy engine in `search.py` is asserted equal
to `common.score` / `common.build_book` at the top of step 2 (`agree: true`, max abs diff 0.0 on
`fast_net_r`); the shared harness is not forked.

---

## Step 0 — re-deriving CLAIM.md. The headline holds; two supporting numbers do not

The main result **reproduces exactly**: SHIPPED + in-play = **35 trades, +16.74R net, +0.4784
net R/trade**, dev +4.83 / val +10.62 / holdout +1.29. So does the quintile monotonicity
(`runup` 1.8%→13.6%, `shares_outstanding` 8.2%→1.4%). `assert_no_lookahead` passes on the rule's
column set.

Three numbers in CLAIM.md are wrong, and the first two matter:

| CLAIM.md says | I get | what it really is |
|---|---|---|
| "in play only, **no shape gates**": 242 trades, −20.3R, **−0.084**/trade | in play only: **366 trades, −157.3R, −0.430**/trade | the 242-trade row is SHIPPED-*minus-`passed`* plus in-play. It still carries the price band, trigger window, cycle, staleness and stop_pct rules. Mislabelled by **7.7x per trade**. |
| intermediate signal "on 433 / 263 / 230 rows" | rates reproduce exactly on the **raw 3,639-row panel** (n = 2012 / 977 / 650) | the quoted denominators match no population I can construct. On the pool the rule actually operates on (SHIPPED) the samples are **63 / 37 / 25**, and the dev-period doubling vanishes (0.063→0.067). |
| error bar "+0.50 ± 0.43" | naive trade-level SE = **0.26**; session-block bootstrap 95% CI **[−0.04, +1.00]** | the ± is not reproducible from the data either way. |

The first row is the important one. "In play on its own loses a bit" and "in play on its own loses
catastrophically" are different claims, and the second is true. The filter only becomes positive
*inside* the shipped pool — it is entirely an interaction, on 125 rows.

`rvol_pole`'s quintiles are also **not** monotone (0.031, 0.064, 0.037, 0.053, 0.102).

---

## Step 1 — where the selectivity actually comes from

Adding the in-play clauses to SHIPPED one at a time, net R/trade of the booked result:

| step | rows | net R/trade |
|---|---|---|
| SHIPPED | 125 | +0.058 |
| + `rvol_pole` **is not null** | 102 | +0.073 |
| + `shares_outstanding` **is not null** | 96 | **+0.192** |
| + all three non-null | 77 | +0.196 |
| + `runup >= 0.15` | 63 | +0.209 |
| + `rvol >= 2.0` | 59 | **+0.148** <- goes backwards |
| + `shares <= 50M` (**= the claim**) | 35 | +0.478 |

**The largest single jump in the whole chain is not a threshold. It is the field being populated
at all.** `x <= 50e6` is False for a null, so the rule silently contains
`shares_outstanding is not null`, and that clause alone is worth +0.058 → +0.192. Shipped rows
where the field is *missing* run at **−0.396**/trade over 29 trades.

That is a data-pipeline property, and it is not stable across the record: the null rate is
**24.7% in dev, 19.3% in val, 4.8% in the live period**.

Of the three stated thresholds: `runup >= 0.15` moves net R/trade by **+0.013**, `rvol >= 2.0`
moves it by **−0.060**, and only `shares <= 50M` does real work.

At row level, in-play never makes money on its own — raw panel −0.164R/row (pool: −0.247),
`passed`-only +0.015R/row.

The 35 trades: 33 symbols, **35 distinct sessions**, 18 winners / 17 losers, 8 same-bar stops,
17 cap-bound.

---

## Step 2 — the null. This is what broke the claim

Run the *same kind of search that produced this rule* — greedy forward selection of <=3 clauses over
decile cuts, on top of SHIPPED, floor of 25 trades — against **scrambled outcomes**
(`max_r` permuted among the shipped rows, so every feature and the whole calendar are preserved and
only the feature→outcome link is destroyed).

| null | iterations | null median | null p90 | P(noise >= the claim's +0.478) |
|---|---|---|---|---|
| A: rows permuted, 470-clause menu | 400 | **+0.703** | +1.000 | **0.920** |
| B: day-block permuted, 470-clause menu | 400 | +0.674 | +0.980 | **0.953** |
| narrow: only the claim's own 3 features, decile cuts (27 clauses) | 1500 | +0.388 | +0.643 | **0.378** |
| narrowest: 3 fixed features, 150-point round-number grid | 600 | +0.328 | +0.611 | **0.258** |
| the claim's exact rule, **no search at all** | 5000 | +0.078 | — | **0.040** |

Read the last two rows together and the whole thing is visible. If the rule had been written down
in advance, +0.478 would be a marginal result (p = 0.04). It was not: CLAIM.md says the thresholds
were chosen after looking at a quintile table for these features on this data. **Once you let the
search choose even the three cut values from a round-number grid, noise reaches +0.478 or better a
quarter of the time.** Let it choose the features too and noise beats it 92–95% of the time.

The lab's own mandatory anti-overfit test (README §4, `common.permutation_pvalue`) agrees:
random rows from the SHIPPED pool taking the same trades on the same days do as well **p = 0.196**;
from the shares-present pool, **p = 0.248**. Only the whole-panel pool gives p = 0.001, and that
pool is the wrong comparator — it credits the in-play filter with all of SHIPPED's selectivity.

### Why: the null's mean is essentially a function of trade count

| clause budget | 25 trades | 35 | 50 | 70 | 99 |
|---|---|---|---|---|---|
| 1 clause | +0.592 | +0.480 | +0.391 | +0.287 | +0.174 |
| 2 clauses | +0.697 | +0.628 | +0.446 | +0.338 | +0.188 |
| 3 clauses | +0.707 | +0.647 | +0.466 | +0.355 | +0.191 |

**+0.478R/trade at 35 trades is below what a best-of-search returns on pure noise at 35 trades.**
That is the direct answer to "is the rule doing anything beyond taking fewer trades": no. Small
books have enormous variance, and taking the best of a search over small books buys you ~+0.5R/trade
for nothing. At the lab's own frequency objective (99 trades ~ 0.5/session) the same search only
manages +0.19 on noise — and the claim's rule fires at **0.18 trades/session**, three times below
that objective.

**Multiple-comparisons accounting.** Menu on the shipped pool: 470 clauses (33 features x 9 deciles
x directions, plus booleans). One greedy run evaluates 1,407 of them; the 3-clause space contains
**17.2 million** distinct rules. On top of that the base population, the exit target, the capacity
cap and the trade floor were all chosen by hand.

---

## Step 3 — walk-forward on the procedure

`common.walk_forward`, 6 blocks, 60-session minimum train, with four different `fit` functions:

| fit | trades | net R | /trade | blocks + |
|---|---|---|---|---|
| FIXED (claim rule, never refitted) | 30 | +16.25 | +0.542 | 5/6 |
| GRID (refit the 3 thresholds each window) | 31 | +15.05 | +0.486 | 5/6 |
| NARROW (greedy over the claim's 3 features) | 37 | +23.29 | +0.630 | 5/6 |
| **WIDE (greedy over the full menu)** | 22 | **+2.50** | **+0.114** | **3/6** |
| control: SHIPPED alone, no fitting | 92 | +13.18 | +0.143 | 4/6 |

The refitted thresholds **wander**: `rvol` picks 1.0 (i.e. off) in five of six windows against the
claim's 2.0; `shares` alternates 50M/100M; `runup` picks 0.05 and 0.30. The NARROW search picks
`shares_outstanding <= X` first in every single window with X ranging 34M → 138M, a **4x spread**.
The only thing stable across windows is *which feature*, never *which threshold*.

The unconstrained WIDE search — the version where the analyst is not told in advance which three
features to look at — earns **+0.114/trade over 22 trades and 3/6 blocks**, i.e. worse than the
shipped baseline it started from.

### Step 3b/3c — the walk-forward against its own null

The FIXED and GRID walk-forwards do beat an outcome-permutation null (p = 0.031 and 0.044 on
net R/trade). Three things dismantle that:

1. **"5 of 6 blocks positive" is not evidence.** Blocks hold 2–10 trades. Under the permuted null,
   blocks-positive averaged 3.3/6 and reached 5/6 **12%** of the time. A fair coin gives 11%.
2. **Half the apparent significance is the nullity effect.** Every one of the 150 grid combos
   silently contains `shares_outstanding is not null`. Restrict the base population to rows where
   all three fields are present, and the null median jumps +0.096 → +0.211 while p goes
   0.044 → **0.087** (GRID) and 0.031 → 0.062 (FIXED).
3. The WIDE walk-forward against its own null: **p = 0.214**. No signal.

And the comparison that says it best — **`shares_outstanding is present`, walk-forwarded, with no
thresholds at all: 74 trades, +18.53R.** The claim's three-clause rule over the same blocks: 30
trades, +16.25R. *Less total money, 2.5x fewer trades, three more parameters.*

---

## Step 4 — the attacks that did NOT land

I want these on the record because they are the evidence in the claim's favour.

- **Outliers.** Dropping the top 8 of 35 trades still leaves **+1.04R**. Every winner is ~+1.96R
  (the 2R target minus costs), so this is a win-rate story (51.4%, against ~35% breakeven), not one
  lucky trade. This is the single best thing about the claim.
- **Leave-one-out.** No period, no calendar month and no symbol turns it negative. Worst month to
  lose is 2026-06 (+0.478 → +0.333). 2026-08 is the only negative month (−4.20R over 4 trades).
- **Sub-samples.** 15 of 15 positive — odd/even sessions, first/second half, price band, stop band,
  source, split, sizing mode. Two are lopsided: H1 +0.166 vs H2 +0.642, and recon +0.702 vs live
  +0.100.
- **Session block bootstrap** (20,000 resamples of the 35 traded sessions): net R/trade
  **+0.478, 95% CI [−0.039, +0.996]**, P(<=0) = 3.3%. Total net R CI [−1.37, +34.88]. Positive, but
  it answers the wrong question — it takes the rule as given and ignores that it was searched for.
- **Exit target.** The rule's edge over SHIPPED is positive at every target from 1R to 4R
  (+0.20 to +0.52), so it is not knife-edge on 2R.
- **Same-bar stops.** 8 of 35 (23%) depend on the conservative same-bar convention that #583 found
  wrong 38% of the time — but correcting it would move the result **up** (~+0.74), so this is a
  caveat, not an attack.
- **As-of lookahead.** `shares_as_of` never post-dates the session (max lag 0 days, median −89).
  The dev+val half is 100% EDGAR point-in-time data. Clean.

---

## Step 5 — the "plateau" is the selection effect

CLAIM.md's plateau replicates: across every one-at-a-time threshold move, net R/trade stays
+0.148…+0.627 with no sign flip. But **total** net R is flat too — +12R to +19R in almost every
cell — while the trade count swings 18 → 59. The per-trade number is moving because the
denominator is moving.

Scored against the step-2b null median **at each cell's own trade count**: **0 of 23 cells beat
it.** The plateau is a plateau of the small-sample selection effect.

Two more:

- **The 2-a-day cap is inert.** 35 selected rows over 35 distinct sessions; `max_per_day` of 1, 2,
  3 or 99 all give the identical book. It is a parameter in the spec doing nothing.
- **Simpler rivals beat it in-sample**, on the same pool, chosen with the same freedom:

| rule | trades | /session | net R | /trade |
|---|---|---|---|---|
| SHIPPED | 122 | 0.62 | +7.12 | +0.058 |
| `shares_outstanding` present | 96 | 0.49 | **+18.40** | +0.192 |
| **`shares <= 50M`** (one clause) | 60 | 0.30 | **+25.17** | +0.420 |
| `shares <= 50M` and `runup >= 0.15` | 50 | 0.25 | +21.46 | +0.429 |
| the CLAIM (three clauses) | 35 | 0.18 | +16.74 | +0.478 |

The claim's rule has the best per-trade number and the **worst** total R of the three size rules,
at the lowest frequency. That is the signature of a denominator effect.

---

## What it was really measuring

**`shares_outstanding`, and mostly just whether the field was filled in.** Of the +0.42 gap between
the shipped baseline and the claim, roughly a third is the fundamentals lookup having succeeded,
essentially none is `runup_pre_appearance`, `rvol_pole` is a subtraction, and the rest is a single
size threshold whose value is unstable window to window.

Two structural confounds the synthesis needs to know about, because they mean some checks simply
**cannot be run** on this data:

- **`source`, `split` and shares provenance are perfectly collinear.** recon = dev+val = EDGAR
  (dated, point-in-time, 2,304 rows); live = holdout = fmp/yfinance (**no as-of date at all**, 619
  rows). So the README's "any rule must work on both recon and live halves" test cannot be
  separated from the spent holdout, and the possibility that the live half's share counts are
  as-served-today values is **not measurable with this data**. 13 of the claim's 35 trades carry an
  undated share count; all 13 are holdout.
- The size effect is a **shipped-pool** effect, not a population effect. Small-minus-big gross R
  per row: inside SHIPPED **+0.583 ± 0.305** (n=96); outside SHIPPED **+0.024 ± 0.053** (n=2,827);
  `passed`-only +0.221 ± 0.170; whole panel +0.038 ± 0.053. A 24x gap resting on 96 rows.

---

## The residue I could not break (reported separately — it is NOT the claim)

Everything that survives reduces to **one clause: `SHIPPED + shares_outstanding <= 50e6`.**

- All data: 60 trades, +25.17R, +0.420/trade, 0.30/session.
- **Walk-forward, fixed 50M**: 49 trades, +22.19R, +0.453/trade, 5/6 blocks, **p = 0.009** vs its
  permutation null. Refitting the cut each window: 48 trades, +0.482/trade, p = 0.014, and the
  chosen cut settles at 35M for the last four windows.
- **DEV+VAL only** (owes nothing to the spent holdout): 42 trades, +26.63R, **+0.634/trade**,
  p = 0.0012 as a fixed rule.
- The fairest test I can construct — search *every* feature for the best single clause on DEV+VAL,
  and null it against the same search: the search picks `shares_outstanding <= 33M`
  (36 trades, +0.673/trade) at **p = 0.092**, and `<= 63M` (43 trades, +0.595) at **p = 0.068**.
  Under noise the top pick scatters across 24 different features and lands on
  `shares_outstanding` only **3.2%** of the time.

So the size feature is the one thing here that a blind search keeps rediscovering. **p ~ 0.07–0.09
after honest correction, on 36–43 trades, is `PROMISING`, not `REAL`** — and it is a *different,
simpler rule* than the one under test. It also still fails the frequency objective (0.30 vs 0.50
trades/session).

---

## What would change my mind

1. **Pre-registration.** `shares_outstanding <= 50e6` on top of the shipped rules, nothing else
   changed, traded forward for ~60 fresh sessions. At 0.3/session that is ~18 trades — not enough
   to settle it, but it is the only clean evidence obtainable and it costs nothing to collect while
   Phase 1 runs. Every result in this file is a statement about a record that has already been
   searched.
2. **A mechanism, measured.** If small share counts win because thin supply makes a 2R target
   reachable, that should appear as a higher P(`max_r >= 2`) at *matched* stop width and matched
   `cum_dollar_vol_to_trigger`. Measured, not argued.
3. **The effect at the same magnitude outside the 125-row shipped pool.** Today it is +0.58R/row
   inside and +0.02R/row outside. Close that gap and this stops looking like an interaction found
   in a small sample.
4. Nothing on the holdout would change my mind, in either direction. It is spent.

## Compliance

- No holdout figure is used as out-of-sample evidence; the verdict rests on step 2 (nulls), step 3
  (walk-forward on the procedure) and step 5 (plateau vs null), all of which are dev+val-driven or
  null-referenced. Holdout numbers appear in the JSON for completeness and are labelled.
- `assert_no_lookahead()` run on the claim's column set (passes). All books are earliest-trigger
  first, via `common.build_book` and a numpy reimplementation asserted equal to it.
- Nothing outside `spikes/engine_lab/validate/adversarial/` and
  `data/spikes/engine-lab/validate/adversarial/` was written. `common.py` was not modified.
