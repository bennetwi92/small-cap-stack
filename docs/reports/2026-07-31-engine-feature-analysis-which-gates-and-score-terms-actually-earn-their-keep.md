---
title: Engine feature analysis: which gates and score terms actually earn their keep
published: 2026-07-31
summary: 1050 setups, 21 days. The gate set only separates inside the window the book trades; a third of the score's weight is inert; and the strongest signal in the data — stop distance — isn't a feature at all.
tags: strategy,data
---

## The answer, up front

**The eight gates are not equally load-bearing, and the strongest predictor in the data isn't a
feature at all.** Three things came out of replaying every collected setup through the engine:

1. **The gate set only discriminates inside the population the book actually trades.** Over all 787
   triggered setups, the ones the engine calls takeable are indistinguishable from the ones it
   rejects (+0.04R, p = 0.80). Restrict to the pre-market, $2–20 window the portfolio buys from and
   the same gate set is worth **+0.64R per trade** (day-block 95% CI [+0.14, +1.23]). Over regular
   hours it is worth nothing at all (−0.09R). The gates work; they just work on ~23% of what the
   engine emits.
2. **Stop distance is the largest single effect in the dataset, and the engine never looks at it.**
   Setups whose stop sits ≥ $0.10 below the entry beat tighter ones by **+0.72R** (CI [+0.50,
   +0.97], p < 0.0001 — the only contrast here that survives multiplicity correction). The 17
   setups with a stop under 5¢ went 0-for-17: every one stopped out. Adding a single
   `risk ≥ $0.10` rule to the shipped book takes its 21-day total from **+4.74R to +7.64R** by
   dropping two trades.
3. **The 0–1 quality score does not rank profitability, and about a third of its weight is inert.**
   Spearman ρ(score, Max R) = +0.053, CI [−0.02, +0.12]; realised expectancy is flat across score
   quintiles (−0.40, −0.36, −0.39, −0.34, −0.39). Six of the eleven weighted terms sit at *full
   marks* for more than half the population, so 0.38 of the 1.00 weight budget is a constant offset
   that lifts every score equally and ranks nothing.

And on leaving money on the table: **the filters outside the engine are doing more selection work
than the gates inside it, and they are not too tight.** The 92 takeable setups drop to 16 after the
$2–20 price band and the 09:15 cutoff — but those 16 average Max R 2.98 and reach 2R half the time,
against 1.23 and ~21% for the ones dropped. Widening the band toward the documented $1–50 spec
would import worse trades, not recover good ones.

## What was measured, and how much to trust it

Every opportunity-run published for 2026-07-01 → 2026-07-30 — **1050 runs over 21 trading days,
the whole collected history** — was replayed through the repo's own
`bullflag.detect_day_with_settings`, and the full 21-field `FeatureVector`, the eight `GateResult`s,
the score and its contributions recomputed from the captured bars. The replay is not an
approximation: **all 1050 runs reproduce the published engine output exactly** — identical score,
identical segment, identical trigger bar. Outcomes are the same `rmetrics` Max R the review page
shows, plus realised R from `portfolio.exit.simulate_exit` at fixed 1R–3R targets with the book's
2-tick exit slippage.

Two things bound how far any of this can be pushed:

- **21 days is a small sample, and the unit of independence is the day, not the setup.** Every
  confidence interval here is a day-block bootstrap (resample days with replacement, 3000 draws)
  and every p-value is a within-day permutation of the label, so one explosive morning can't
  masquerade as 40 independent observations. Ten pre-registered contrasts were tested and
  Holm-corrected.
- **The book itself has taken 16 trades.** Anything stated about book-level totals is an
  illustration, not evidence. The per-setup population (787 triggered) is where the statistics live.

The funnel, for orientation:

| stage | count |
| --- | --- |
| runs with a setup detected | 1050 |
| entry actually triggered | 787 |
| all eight gates passed | 108 |
| takeable (passed + triggered + not exhausted) | 92 |
| survives the $2–20 price band | 54 |
| survives the 09:15 pre-market cutoff | 26 |
| book candidates (both) | 16 |

## Finding 1 — the gates only separate where the book trades

The headline test: does `takeable` predict a better trade than `rejected`?

| population | n (take / reject) | mean Max R | P(Max R ≥ 2) | mean R @ 2R | take − reject |
| --- | --- | --- | --- | --- | --- |
| all triggered | 92 / 695 | 1.51 / 1.45 | 25.0% / 23.3% | −0.34 / −0.38 | **+0.04** CI [−0.27, +0.41] |
| pre-market only (< 09:15) | 26 / 252 | 2.25 / 1.82 | 38.5% / 24.2% | +0.07 / −0.33 | **+0.39** CI [−0.08, +0.98] |
| book window (pre-market **and** $2–20) | 16 / 165 | 2.98 / 2.27 | 50.0% / 27.3% | +0.42 / −0.22 | **+0.64** CI [+0.14, +1.23] |
| regular hours only | 66 / 443 | 1.23 / 1.23 | 19.7% / 22.8% | −0.50 / −0.41 | **−0.09** CI [−0.50, +0.40] |

Read across that table and the shape of the problem is clear. The gate set was validated against
reviewed pre-market bull flags (#194's 25 cases), and that is exactly and only where it works.
Two-thirds of what the detector emits fires after 09:30, where the gates carry no information —
those setups are pure compute, and they are also what makes the gates look inert when you evaluate
them over everything.

Inside the pre-market, leaving each gate out in turn (all others passing) shows which ones do the
rejecting that matters. Baseline for the kept set: +0.07R, 38.5% reach 2R.

| gate | it rejects, n | their mean R @ 2R | their P(≥ 2R) | verdict |
| --- | --- | --- | --- | --- |
| `wick_peak` | 5 | −1.21 | 0 / 5 | earns its keep |
| `vol_peak_gt_cons` | 6 | −0.92 | 0 / 6 | earns its keep |
| `cons_retracement` | 42 | −0.12 | 31.0% | mild, right direction |
| `peak_green` | 3 | −0.06 | 1 / 3 | no evidence either way |
| `pole_height` | 0 | — | — | never binds at the margin |
| `cons_len` | 1 | — | — | never binds at the margin |

Every n there is tiny — five rejections is an anecdote, not a result — but the direction is
consistent, and it is the opposite of what the full-population view suggests. Over all 787 triggered
setups the wick gate's *rejected* side is the better side (the [0.65, 0.80] upper-wick bucket has
the highest mean Max R of six, at 1.99), and the volume gate's rejected side has the best realised
expectancy of any `vol_ratio` bucket (−0.16 for ratio < 0.5). Both reverse inside the pre-market.
That is either a genuine interaction or the small-sample gods having fun, and three weeks of data
can't tell them apart. The safe reading: **don't rip out the wick or volume gates on the strength of
the full-population numbers, and don't trust either gate on a 10:30 setup.**

## Finding 2 — the missing feature: stop distance

Nothing in the engine — no gate, no score term, no config knob — asks how far the stop sits from the
entry. It should. Bucketing all 787 triggered setups by `entry − stop`:

| stop distance | n | 2-tick slip, as R | mean Max R | mean R @ 2R | win rate | median entry |
| --- | --- | --- | --- | --- | --- | --- |
| < $0.05 | 17 | 0.52R | 0.05 | **−2.91** | 0% | $1.82 |
| $0.05 – $0.10 | 177 | 0.29R | 1.13 | −0.73 | 16.9% | $2.10 |
| $0.10 – $0.20 | 261 | 0.15R | 1.45 | −0.26 | 29.5% | $3.57 |
| $0.20 – $0.40 | 163 | 0.08R | 1.73 | **−0.07** | 34.4% | $6.24 |
| ≥ $0.40 | 169 | 0.03R | 1.68 | −0.23 | 27.8% | $13.69 |

The first row is not a rounding artefact: **all 17 of those setups stopped out**, and they lose
nearly 3R apiece because a 2-tick slip on a 4-cent stop is half the risk again. But this is not
merely the exit model being harsh. Recompute the same buckets with slippage switched off entirely
and the tightest bucket still averages −1.00R (every trade stopped) and the $0.05–0.10 bucket
−0.48R, against −0.02R for $0.20–0.40. A stop parked a few ticks under the consolidation low gets
taken out by noise; the slippage model only adds insult.

It isn't a price proxy either. Inside the book's own $2–20 band the gradient survives (mean R@2 of
−1.08 / −0.23 / −0.06 for < $0.10 / $0.10–0.20 / ≥ $0.20), and normalising by price says the same
thing (risk below 2% of entry: −0.66R; 4–8%: −0.22R; ≥ 8%: −0.20R).

`risk ≥ $0.10` versus below it is worth **+0.72R** (CI [+0.50, +0.97]), and it is the one contrast
in this study that survives Holm correction across all ten tests. Applied to the shipped book it
removes exactly two trades — SUNE on 07-09 (4¢ stop, −1.50R) and SNDQ on 07-13 (5¢ stop, −1.40R) —
and lifts the 21-day total from +4.74R to +7.64R.

Two ways to spend that finding, not exclusive: an absolute floor (`risk ≥ 10 ticks`) is simpler and
directly targets fills that can't survive friction; a relative floor (`risk / entry ≥ 2%`) travels
better across the $1–50 price range the strategy is specced for (#126). Either belongs in
`config.py` next to the other caps so `detect_day_with_settings` threads it, per the #302 rule.

## Finding 3 — two of the eight gates can never reject anything

- **`cons_holds_base` is implied by `cons_retracement`.** If the flag retraces at most 50% of the
  pole, its low is by definition above the pole base — formally, `retracement ≤ 1` ⟹
  `cons_low > pole_base`. Empirically: **0 of the 181 setups with retracement ≤ 0.50 fail
  `holds_base`**, and across all 787 triggered setups it never once rejects alone. It can only bind
  if `bull_flag_max_retracement` is ever set above 1.0.
- **`pole_len` never fires.** 0 rejections in 787 — the segmenter already caps the pole at
  `max_pole`, exactly as the gate's own docstring says. It exists for callers gating tighter than
  they segmented (the #181 divergence spike), which is a real use, just not this one.

Neither is a bug and neither costs money. They cost *clarity*: the review page presents an
eight-gate verdict when six gates can ever explain a rejection, which makes the gate set look more
discriminating than it is.

## Finding 4 — the score doesn't rank, and a third of its weight is inert

`score()` is documented as a straw man to be fit later against reviewed outcomes. It has not been
fit, and it shows.

**It has no rank power.** ρ(score, Max R) = +0.053, CI [−0.02, +0.12]; ρ against realised R at a 2R
target = +0.086. By score quintile over the triggered population, realised expectancy is flat:

| score quintile | n | mean Max R | P(≥ 2R) | mean R @ 2R |
| --- | --- | --- | --- | --- |
| Q1 (0.19–0.40) | 157 | 1.28 | 21.0% | −0.40 |
| Q2 (0.40–0.46) | 157 | 1.26 | 22.9% | −0.36 |
| Q3 (0.46–0.51) | 157 | 1.28 | 22.3% | −0.39 |
| Q4 (0.51–0.57) | 157 | 1.78 | 22.3% | −0.34 |
| Q5 (0.57–0.93) | 159 | 1.68 | 28.9% | −0.39 |

Restricted to the pre-market the gradient is slightly better behaved (quartile mean R@2 of −0.29,
−0.47, −0.26, −0.15) but still well inside the noise at n ≈ 69 per quartile.

**Why it doesn't rank** — the per-term audit over the triggered population:

| term | weight | mean contribution | share of setups at full marks |
| --- | --- | --- | --- |
| `retracement_shallow` | 0.24 | 0.018 | 1.5% |
| `pole_height` | 0.16 | 0.090 | 28.1% |
| `vol_ratio` | 0.13 | 0.035 | 7.9% |
| `cons_vol_reducing` | 0.09 | 0.067 | **74.0%** |
| `pole_short` | 0.08 | 0.061 | **54.1%** |
| `cons_strictness` | 0.06 | 0.052 | **76.4%** |
| `pole_big_green` | 0.05 | 0.035 | **69.8%** |
| `pole_vol_conc` | 0.05 | 0.041 | **54.1%** |
| `cons_tightness` | 0.05 | 0.024 | 1.8% |
| `pole_ext_atr` | 0.05 | 0.044 | **68.1%** |
| `pole_avg_body` | 0.04 | 0.019 | 0.0% |

Six terms totalling **0.38 of the weight** are at full marks for most setups. A term that pays
everyone the same pays no information — it inflates the score's level and compresses its spread.
Five specific problems sit behind that table:

- **`retracement_shallow` carries the top weight and delivers 7.5% of it.** Median retracement over
  triggered setups is 0.80, well past the 0.50 cap where the term pins at zero, and inside the
  accepted band it barely varies. The heaviest term in the score is in practice a restatement of
  "did you pass the retracement gate".
- **`pole_vol_conc` and `pole_short` are the same bet made twice.** `pole_vol_concentration` is
  `peak.vol / sum(thrust.vol)`, which is **exactly 1.0 for 100% of single-bar poles** (54% of the
  population) and falls monotonically with pole length (0.67 / 0.55 / 0.38 at pole_len 2 / 3 / 4).
  It is pole length wearing a volume costume, and it collects 0.05 alongside `pole_short`'s 0.08.
- **…and that bet points the wrong way.** Single-bar poles have the *lowest* mean Max R of any
  length (1.26); two-bar poles the highest (1.93). `pole_short` pays maximum marks to the
  worst-performing bucket.
- **`pole_extension_atr` is degenerate at the top end.** 80% of setups measure ≥ 2× trailing ATR —
  the value at which the term saturates — so 68% collect the full 0.05. The maximum observed is
  **6000× ATR**: the 14-bar pre-market baseline is frequently a run of flat, zero-range bars, so the
  denominator collapses and the ratio explodes. The `atr > 0` guard prevents a divide-by-zero, not a
  divide-by-almost-zero.
- **`vol_ratio` carries the third-largest weight on a flat-to-inverted relationship.** ρ = −0.009
  against Max R and −0.108 against realised R at a 3R target, across the whole population. (The
  *gate* form of the same signal does look useful pre-market — Finding 1 — but the graded score term
  does not.)

Across all 21 features, the ones whose rank correlation with Max R has a bootstrap CI clearing zero
are `pole_height_abs` (ρ = +0.18), `cons_strictness` (+0.10), `pole_velocity` (+0.08),
`cons_tightness` (+0.08) and `trigger_in_window` (+0.07). Note the first: `pole_height_abs` is the
pole measured in *dollars*, it is not in the score at all — the score uses the percentage version,
ρ = +0.067 — and much of its apparent power is the stop-distance effect of Finding 2 arriving
through a side door, since a tall dollar pole implies a wide dollar stop.

## Finding 5 — two thresholds the data argues with

**`bull_flag_max_cons = 4` admits its own worst bucket.** By consolidation length:

| cons_len | n | mean Max R | P(≥ 2R) | mean R @ 2R |
| --- | --- | --- | --- | --- |
| 1 | 291 | 1.41 | 23.0% | −0.42 |
| 2 | 208 | 1.65 | 29.8% | −0.20 |
| 3 | 137 | 1.55 | 22.6% | −0.37 |
| **4 (admitted)** | 90 | **1.17** | **15.6%** | **−0.54** |
| 5–6 (rejected) | 55 | 1.28 | 18.2% | −0.54 |

A four-candle flag performs like a rejected five-candle flag, not like the three-candle flags it
sits beside. Tightening the cap to 3 removes the weakest admitted bucket at the cost of ~11% of the
triggered population.

**The exhaustion cap may be one cycle too tight.** By cycle number: cycle 1 (n = 672) 24.1% reach
2R; cycle 2 (n = 73) 19.2%; cycle 3 (n = 29) **27.6%**; cycle 4+ (n = 13) 7.7% and −0.99R. The
cliff is between the third and fourth pump of the day, not the second and third — but n = 29 and
n = 13 are thin enough that this is a flag for the next re-check, not a change to make today.

## Finding 6 — are we leaving money on the table?

Not in the places you'd guess. Two candidate answers, both of which the data declines:

**Widening the price band.** The strategy spec is $1–50 (#126); the book buys $2–20. The 38 takeable
setups the band drops average Max R 1.23 and reach 2R 23.7% of the time, against 2.98 and 50% for
the 16 that survive. Removing the band from the book's selection takes the 21-day total from +4.74R
to **−8.22R** across 35 trades. The band is carrying weight.

**Trading later in the day.** The 66 takeable setups the 09:15 cutoff drops average −0.50R realised.
That agrees with the time-of-day report published today: the session decays monotonically, and after
11:00 significantly so. Dropping the cutoff takes the book from +4.74R to +1.12R.

Where money *is* being left: the two tight-stop trades of Finding 2 (+2.9R over 21 days from one
rule), and — more speculatively — the ~65% of engine compute spent detecting regular-hours setups
the book will never buy and the gates cannot rank.

The honest frame for all of it: **at a fixed target the whole triggered population is
negative-expectancy** — mean R@2 = −0.377, CI [−0.511, −0.250], and negative at every target from 1R
to 3R. The book is positive only because the price band, the pre-market cutoff and the 2-trade cap
select a small, good slice out of it. That slice is 16 trades in three weeks. It is a promising
slice, not a demonstrated edge.

## Significance, honestly

Ten pre-registered contrasts, within-day permutation of the label (20,000 draws), Holm-corrected
across the family:

| contrast | Δ mean R @ 2R | n (yes / no) | p | Holm p |
| --- | --- | --- | --- | --- |
| stop distance ≥ $0.10 | **+0.724** | 593 / 194 | **< 0.0001** | **0.0005** |
| `cycle_num ≤ 3` | +0.623 | 774 / 13 | 0.084 | 0.757 |
| `cons_len ≤ 3` | +0.212 | 636 / 151 | 0.124 | 0.993 |
| gate set separates — pre-market | +0.394 | 26 / 252 | 0.154 | 1.000 |
| `vol peak > cons` | −0.140 | 610 / 177 | 0.246 | 1.000 |
| `wick_peak ≤ 0.50` | −0.093 | 507 / 280 | 0.401 | 1.000 |
| `retracement ≤ 0.50` | +0.104 | 181 / 606 | 0.416 | 1.000 |
| `peak is green` | +0.077 | 529 / 258 | 0.478 | 1.000 |
| score above median | +0.053 | 379 / 408 | 0.610 | 1.000 |
| gate set separates — all triggered | +0.040 | 92 / 695 | 0.805 | 1.000 |

The book-window gate contrast of Finding 1 (+0.638) is a post-hoc subset, not one of the ten; its
own permutation p is 0.068.

**Only the stop-distance contrast survives correction.** Everything else in this report is a
direction, not a result — including the gate-set finding, whose bootstrap CI clears zero but whose
permutation p would not survive being one of ten tests.

## What I'd change, ranked

1. **Add a minimum stop-distance gate** (`risk ≥ 10 ticks`, and/or `risk / entry ≥ 2%`), wired
   through `Settings` → `detect_day_with_settings`. Largest measured effect in the study, the only
   one surviving multiplicity correction, and the engine currently has no opinion on it at all.
2. **Mark `cons_holds_base` and `pole_len` as implied gates** in the spec and on the review page, so
   a "passed 8 of 8" verdict doesn't read as more evidence than it is.
3. **Don't let the score influence anything until it's fit.** It doesn't rank outcomes. Before
   refitting: drop `pole_vol_conc` (collinear with `pole_short`), re-examine or flip `pole_short`
   (single-bar poles are the worst bucket, not the best), fix `pole_extension_atr`'s baseline (floor
   the ATR denominator, or drop the term), and rescale `retracement_shallow` so it varies *inside*
   the accepted band instead of pinning at zero for 98.5% of setups.
4. **Tighten `bull_flag_max_cons` to 3.** It currently admits the worst-performing flag length.
5. **Don't widen the price band or the pre-market cutoff.** Both earn more than the gates do.
6. **Consider not emitting regular-hours setups at all**, or at least flagging them: they are ~65%
   of triggered setups, the gates carry no signal over them, and the book never buys them.
7. **Re-run this at 60+ trading days.** Every finding except the stop-distance one is a direction
   that a month of fresh data could reverse.

## Method

Source data is the `dashboard-data` branch's per-day chart payloads for 2026-07-01 → 2026-07-30
(1050 opportunity-runs; that branch holds the whole collected history to date). Each run's full-day
5-minute bars were replayed through `bullflag.detect_day_with_settings` under `config.Settings`
defaults, with the scanner appearance taken from the chart's `first_hit` marker. The replay
reproduces the published engine block for **1050 / 1050** runs — score, segment and trigger bar all
identical — so the feature vectors analysed here are the engine's own, not a re-derivation.
Outcomes are `rmetrics.compute_r_metrics` Max R (stop-first, gap-through) and
`portfolio.exit.simulate_exit` realised R at fixed targets with `portfolio_exit_slippage_ticks = 2`.
Confidence intervals are day-block bootstraps over the 21 days; p-values are within-day label
permutations; multiplicity is Holm across the ten pre-registered contrasts.

Refs #1, #408. Touches the locked #127 volume rule and the #302 config-is-the-source-of-truth rule.
