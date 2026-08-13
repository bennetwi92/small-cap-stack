---
title: The adaptive model: what it should be, and what the data will support
published: 2026-08-13
summary: Day-level regime has no measurable signal; the intelligence belongs in a per-opportunity probability model. What that model should be, and the 20-point hit-rate gap it has to close.
tags: strategy,data
---

## The answer, up front

**Build one model, not three layers.** A single per-opportunity estimate of
`P(max R ≥ T before stop | features)` across a grid of targets `T`, from which selection, target
and risk all fall out as arithmetic. Not a filter, plus a day-level target fit, plus a risk
throttle — one conditional distribution, and three deterministic readings of it.

Three measurements from the collected record force that shape:

1. **The day-level regime the adaptive layer was built to detect does not exist in this data.**
   The share of setup-outcome variance attributable to the day is **0.007** for max R and **0.000**
   for whether a setup reaches 2R. Lag-1 autocorrelation of daily quality is **−0.03** (p = 0.83),
   of daily book R **−0.04** (p = 0.72). Days are not good or bad; setups are.
2. **One day-level quantity is strongly persistent, and it is not quality — it is activity.**
   Trailing-5-day opportunity count predicts today's count at **ρ = +0.81** (p < 0.001). It
   predicts today's *quality* at **ρ = −0.04** (p = 0.79). The market tells you how many shots you
   will get, and nothing about whether they will work.
3. **Because day effects are ~zero, opportunity-level observations are near-independent** — so the
   record holds ~1,676 effective observations for a per-opportunity model against ~60 for a
   per-day one. Same data, **28× the statistical power**, purely from where the model is pointed.

The premise behind the adaptive layer — *recent sessions inform today's* — is testable, was
tested three ways here, and does not survive any of them. But the intuition underneath it is
sound and worth keeping: **the strategy should trade differently depending on what it is looking
at.** The data says the thing it should condition on is the opportunity, not the calendar.

And the gap is larger than it looks. Cost-inclusive, the book needs a **42.9%** hit rate at a 2R
target to break even. It currently gets **22.8%** across all triggered setups and **33.3%** after
the full shipped rule stack. Every feature on the list is worth 2–4 points. **The model has to
find about 20 points, and no single feature in the set contains them.**

## What broke, measured

The rules in `research/strategy.md` — the price floor (§D-39) and the minimum stop distance
(§D-40) — were fitted on 61 sessions and were, on that evidence, strongly positive. The record now
runs to **180 sessions** (147 reconstructed, 33 live). Splitting the shipped book at the edge of
its own fit window:

| | trades | win rate | total R | R per trade |
|---|---|---|---|---|
| Inside the 61-session fit window | 44 | 47.7% | **+18.20** | **+0.414** |
| Everything added since | 48 | 27.1% | **−12.59** | **−0.262** |
| All 180 sessions | 92 | 36.96% | +5.61 | +0.061 |

A swing of **0.68R per trade**. The book that showed $908.97 on the fit window ends at **$267.71**
on the full record, from $500, with a 35.2% maximum drawdown.

Two things this is *not*. It is not a reconstruction artefact: inside the fit window the two
stores agree closely (recon +0.424R, live +0.400R per trade), so the degradation tracks the
*period*, not the data source. And it is not one unlucky stretch — the out-of-sample half is 48
trades over 41 active days.

What it is: the signature of fitting a small number of thresholds on a small sample with a large
number of attempts. §D-38 records >150 variants swept over n=79. Under that much searching, a
threshold that clears a bootstrap CI is not evidence of an effect; it is evidence that the grid
was wide. **This is the failure the next design has to be built against**, and it is not fixed by
having more data — it is fixed by spending less of the data's evidence per decision.

## The premise, tested three ways

*"Recent potential opportunities should help to inform what today's opportunities will look
like."* Three tests, all on the published record.

**Is there a day effect at all?** Decompose the variance of setup outcomes into between-day and
within-day, over 1,676 triggered setups on 60 published chart days:

| outcome | between-day F | ICC (share of variance that is a day effect) |
|---|---|---|
| max R | 1.20 | **0.007** |
| reaches 2R | 0.92 | **0.000** |

An F below 1 means the days differ *less* than random assignment would produce. Whether a setup
reaches its target is, to measurement precision, **not a property of the day it happens on**. This
is the root finding: an adaptive layer that forecasts day quality is estimating a quantity whose
true variance is indistinguishable from zero.

**Does yesterday predict today?**

| series | n | lag-1 | perm p |
|---|---|---|---|
| Daily mean max R | 58 pairs | −0.030 | 0.83 |
| Daily P(reaches 2R) | 58 pairs | −0.094 | 0.53 |
| Daily book R (active days) | 75 | −0.042 | 0.72 |
| Trade sequence R | 92 | +0.026 | — |
| **Daily opportunity count** | 58 pairs | **+0.357** | **0.032** |

Trailing windows do no better. Trailing-3 mean max R against today's is **−0.272** (p = 0.047) —
the *wrong sign* for a momentum story, and one of 36 correlations tested, so best read as noise
rather than as evidence of mean reversion. After a winning trade the next averages +0.109R; after
a loss, +0.051R. The difference is nothing.

**Does the specific proposed rule fire?** *"If the last week has seen no opportunities above max R
of 2, scale back risk."* At the opportunity level it **never fires** — in 60 published days there
was no 3-, 5- or 10-day window without a setup reaching 2R. Restricted to the candidates the book
actually selects, drought windows do occur, and they carry no information:

| trailing window | after a dry window | after a normal window |
|---|---|---|
| 5 days | 19 windows, 9 setups, P(2R) = 0.222 | 31 windows, 104 setups, P(2R) = 0.231 |
| 10 days | 9 windows, 6 setups, P(2R) = 0.333 | 31 windows, 76 setups, P(2R) = 0.184 |

At k=10 the dry windows are *better*, on six setups. That is not a finding either way; it is what
no-signal looks like at this sample size.

**Would a shorter horizon help?** No. Splitting each day's setups by trigger time and asking
whether the morning's outcomes predict the rest of the day: ρ = −0.188 (p = 0.15) over all
setups, and **ρ = −0.007 (p = 0.96)** inside the pre-market window the book trades. Intraday
adaptation has 60× the samples of daily adaptation and finds exactly as much.

This is consistent with the 2026-08-06 report, which reached the same conclusion on 12 active days
and called for more data. There is now 6× more, and it says the same thing more precisely.

## The one thing that does persist

Opportunity **count** is the most predictable quantity in the record — trailing-5 to today,
ρ = **+0.811**; at k=10, +0.732. The tape's activity level is a real, strongly autocorrelated
regime variable.

It is also **uninformative about quality**. Same-day correlation between a day's opportunity count
and its P(2R) is −0.035 (p = 0.79); quiet days average 0.231, busy days 0.228.

So activity belongs in the model as **capacity**, not as a risk multiplier: it forecasts how many
shots arrive, which is what should set a *selectivity threshold* — how picky you can afford to be
before you run out of candidates. Today that constraint is slack in an important way:

| stage | per session | P(reaches 2R) |
|---|---|---|
| opportunities captured | 36.2 | 0.228 |
| triggered | 27.9 | 0.228 |
| in the pre-market window | 12.7 | 0.248 |
| + inside the price band | 6.8 | 0.289 |
| + past the minimum stop rule | 4.8 | 0.277 |
| + shape gates pass | **0.55** | **0.333** |

The book has **2 slots a day and fills 0.55 of one**. Selectivity currently costs nothing in
foregone capacity — which means the correct response to a weak signal is to *not trade*, and the
model should be free to say so on most days.

## What the listed features are actually worth

Base rate over 1,676 triggered setups: **P(reaches 2R) = 0.228**. At a 2R target against a 1R
stop, expectancy is `3p − 1`, so the pre-cost breakeven is **p = 0.333**.

Every feature, bucketed, with day-block bootstrap CIs — the best bucket in each:

| feature | best bucket | P(2R) | worst bucket | P(2R) |
|---|---|---|---|---|
| Entry price | $5–10 | 0.275 | < $2 | 0.172 |
| Time of day | 08:00–09:15 ET | 0.276 | 11:00+ | 0.183 |
| Retracement | > 100% of pole | 0.255 | < 25% | 0.094 |
| Consolidation bars | 2 | 0.260 | 4 | 0.189 |
| Pole bars | 2 | 0.254 | 1 | 0.216 |
| Float | < 5M | 0.261 | > 200M | 0.158 |
| Stop % | 2–2.5% | 0.264 | > 20% | 0.178 |

**Not one bucket of one feature clears the 0.333 breakeven.** The spread from best to worst bucket
is 6–16 points, and each *filter* — the whole population above a threshold, which is what a rule
actually buys — is worth 2 to 4 points:

| filter | n | P(2R) |
|---|---|---|
| price ≥ $3 | 1038 | 0.256 |
| stop ≥ 2.5% | 1148 | 0.226 |
| pre-market only | 759 | 0.248 |
| retracement ≥ 50% | 769 | 0.248 |
| consolidation 2–3 bars | 743 | 0.250 |
| float < 50M | 758 | 0.251 |

Stacked, they compound to the shipped stack's 0.333 — exactly breakeven, on 33 candidates. That
number is the honest summary of eight months of rule-fitting: **the entire selection stack buys
10.5 points of hit rate and lands on the breakeven line.**

One note on **stop %**, because it is the feature with the most history here. As a *filter* it is
worth nothing on this population (0.226 vs a 0.228 base) — but that is measured after §D-39's
price floor already removed the cheap names where a fixed 3-tick fill offset is a large share of
price. §D-40's evidence stands; the rule is doing measurement hygiene, and should not be expected
to show up again as an independent edge.

And **float**, the feature the spec deliberately collects but never gates on. It is the strongest
*unused* signal in the set and still not strong enough: ≤50M vs >50M is **+0.059** paired
within-day, 95% CI **[−0.007, +0.125]**. The direction is consistent inside every price band, so
it is not a price proxy. But the fine buckets are not monotone (2–5M sits at 0.147, below >300M),
it is **live-only** (the recon store carries EDGAR share counts, not float — §D-41), and inside
the population the book already selects from it vanishes: 0.301 vs 0.286, on 14 high-float
candidates. **Keep collecting, keep not gating.** The current spec is right, and now for a
measured reason rather than an unexamined one.

## The gap the model has to close

The pre-cost breakeven is 33.3%. The book's real one is higher, because a $500 account pays fixed
costs that do not scale:

| component | per trade | as R |
|---|---|---|
| Average risk | $10.35 | 1.00 |
| Commission + fees | $0.83 | 0.106 |
| Market-data + VPS, spread over 92 trades | $1.87 | 0.181 |
| **Total drag** | | **0.287** |

Required expectancy is therefore **+0.287R**, and required hit rate **42.9%** — against a base
rate of 22.8%. **The model needs about 20 points of hit rate.**

Two-thirds of that drag is an account-size artefact, not a strategy problem. The $0.35 commission
minimum binds on **100%** of orders (median position: 15 shares), and the monthly fees are fixed:

| account size | drag per trade | required hit rate |
|---|---|---|
| $500 (today) | 0.287R | **42.9%** |
| $1,000 | 0.151R | 38.4% |
| $2,000 | 0.086R | 36.2% |
| $5,000 | 0.050R | 35.0% |

**Capitalisation is the single largest lever on the required edge, and it is not a modelling
problem.** Worth stating plainly before any amount of feature engineering: going from $500 to
$2,000 closes a third of the gap that the model would otherwise have to find.

## Can a model of this shape be fitted today? Not yet

Rather than assert it, I fitted it. L2-regularised logistic regression on the full engine feature
vector — the seven listed features plus the shape features already computed, plus the trailing
activity term — predicting whether a setup reaches 2R, validated with 5-fold cross-validation
**blocked by day** (whole days assigned to folds), penalty chosen on the same out-of-fold loss:

| population | outcome | n | events | blocked-CV AUC | top decile P(2R) |
|---|---|---|---|---|---|
| All triggered | reaches 1R | 753 | 284 | 0.565 | 0.427 |
| All triggered | reaches 2R | 753 | 176 | **0.555** | 0.293 |
| All triggered | reaches 3R | 753 | 122 | 0.588 | 0.280 |
| Pre-market only | reaches 2R | 314 | 82 | 0.521 | 0.387 |

And the honest test — train on one store, predict the other across a six-month gap:

| | AUC | top 20% by p̂ | store base |
|---|---|---|---|
| recon → live | 0.551 | 0.266 | 0.222 |
| live → recon | 0.555 | 0.250 | 0.254 |

**AUC ≈ 0.55.** Real, but small — and nowhere near enough. Against a 22.8% base rate, what
different ranker qualities deliver:

| ranker AUC | top 30% | top 20% | top 10% |
|---|---|---|---|
| 0.55 (what we have) | 0.265 | 0.273 | 0.287 |
| 0.60 | 0.305 | 0.325 | 0.351 |
| 0.65 | 0.345 | 0.374 | **0.421** |
| 0.70 | 0.387 | 0.431 | **0.501** |

To reach 42.9% at 10% selectivity the ranker needs **AUC ≈ 0.65–0.70**. The features as they stand
deliver 0.55. That is the size of the problem, stated as one number.

Note also that the fitted model does **not** beat the shipped rule stack — the stack's 33.3% on 33
candidates is above the model's top decile. The rules are not obviously worse than a model; they
are differently wrong, and both are short.

**The binding constraint is not sample size.** Detecting a 10.5-point lift at 80% power needs ~287
setups per group; the pre-market population alone holds 759. There is enough data to settle a
handful of pre-specified questions. There is nowhere near enough to *search* — which is precisely
what has been happening, and precisely what §D-38's 150-variant sweep and the D-39/D-40 collapse
record.

## So what should the model be

**One conditional distribution, three readings.** Estimate

```
F(T | x) = P(max R ≥ T before stop | opportunity features, day features)
```

over a grid of targets `T`, and derive everything else:

- **Target** — `T* = argmax_T [ T·F(T|x) − (1 − F(T|x)) ]`. This is the adaptive target the layer
  always wanted, moved from the day (60 observations, no signal) to the opportunity (1,676
  near-independent observations). §D-38 retired the day-level version after it moved twice in 61
  sessions and was wrong both times; this is the same idea with 28× the evidence behind each fit.
- **Selection** — take the setup iff `max_T [ … ] > drag`, with `drag` the measured cost per trade
  (0.287R today, and falling with account size per the table above). The price band, the minimum
  stop rule and the trigger window stop being hand-set thresholds and become consequences of a
  fitted surface. They should be *retained as-is* until the model demonstrably beats them.
- **Risk** — fractional Kelly on the model's own edge, `f* = (p(T+1) − 1)/T`, taken at a quarter,
  capped by the existing notional cap, floored at zero. This delivers what the adaptive risk
  throttle was reaching for — *"in a weaker period, scale risk back, possibly to zero"* — by a
  route that is measurable per trade rather than a bet on serial correlation that the data has now
  refused three times. On a genuinely weak morning, most candidates fail the drag test, the book
  stands down, and regime behaviour is an **emergent consequence of the per-trade arithmetic**
  rather than a hand-built override.

Five properties that matter, each earned by a measurement above:

1. **Day features are columns of `x`, never an override layer.** Fitted jointly, a day feature
   that does not earn its weight is shrunk to zero by the penalty — automatically, every refit.
   A hand-built throttle cannot be falsified without commissioning its own study, which is why the
   last one survived from #239 to #474 costing ~3.5%/month.
2. **Model the survival curve, not one binary.** Fit `F` for all `T` at once with monotonicity in
   `T` enforced (stacked binaries with `T` as a feature, or proportional odds). Monotonicity is a
   free, strong regulariser, and the whole curve is what prices an exit.
3. **Calibration, not just ranking.** The decision is a *threshold* on expected R, so `p̂` has to
   be right in level, not merely in order. Report reliability curves and recalibrate on held-out
   folds. An AUC-only evaluation would hide the failure mode that matters.
4. **A parameter budget.** 382 events at 10–20 events per parameter supports **~15 free
   parameters**. Not a 20-feature interaction search. Write the budget down before fitting.
5. **One regularised model is one hypothesis; a threshold grid is hundreds.** This is the real
   argument for the model over more rules — not that it is smarter, but that it *spends less of
   the record's finite evidence per decision made*.

**The protocol, which is not optional.** The reason to trust the next version more than the last
one is the validation, not the architecture:

- Pre-register the feature list, the target grid and the penalty path **before** looking at
  outcomes; record the hypothesis count in the PR.
- Purged, embargoed, day-blocked walk-forward — never a random split, since setups within a day
  share a tape.
- Keep a **frozen holdout** the model is never fitted on, and report on it once.
- **Ship in shadow mode first**: publish `p̂`, `T*` and the implied position size beside the live
  book without trading them, and let them accumulate. Go live only when a pre-registered
  out-of-sample window clears the drag with a lower confidence bound above zero.
- Refit on a **schedule**, never in response to a drawdown — refitting after losses is the
  throttle's mistake wearing new clothes.

## What would raise the ceiling

Ranked by expected value per unit of work:

1. **Capitalise the book.** Worth ~7 points of required hit rate on its own. No modelling risk.
2. **Features that describe the tape, not the flag.** Every feature currently measured is
   geometry — pole, consolidation, retracement, wick. Nothing measures the *situation*: gap
   percentage, volume relative to average, how far the name has already travelled before the scan
   hit, days since its first run, spread, prior-day range. The bars to compute these are already
   stored, and this is the most plausible source of the missing 10–15 points, because the flag
   shape is a weak description of why a small-cap runs.
3. **Exit structure.** Everything here prices a fixed 2R-or-stop. The survival curve directly
   prices the alternatives — partial at 1R, trail after 2R, a time stop — and the whole-population
   curve already shows the expectancy ordering changes with target and with population. §D-38
   ruled out a *day-level* target fit; it did not rule out a better exit.
4. **Accept the possibility that Phase 1's job is measurement.** Every honest reading of this
   record says the strategy is presently at breakeven before costs and below it after. That is a
   legitimate finding for a data-collection phase, and a better outcome than a rule set that looks
   profitable on 61 sessions.

## What I would build first

Nothing about the shipped configuration should change on the strength of this report. In order:

1. **The feature store** — persist the full engine feature vector plus the new tape features per
   opportunity, so the modelling set stops being reconstructed from chart payloads.
2. **The survival model in shadow mode** — fitted, calibrated, published beside the book, trading
   nothing.
3. **The pre-registered evaluation** — one document, written before the first fit, naming the
   features, the grid, the holdout and the go-live bar.
4. **Only then**, a decision about replacing the hand-set selection rules.

## Method, and what to distrust

Everything here comes from the published `dashboard-data` payloads as of 2026-08-13: the
portfolio payload (180 sessions — 147 reconstructed 2025-11-26→2026-06-30, 33 live
2026-07-01→08-12 — 92 trades in the combined book) and the 60 published per-day chart payloads
(30 recon 2025-11-26→2026-01-09, 30 live 2026-07-01→08-12; 2,174 opportunities, 1,676 triggered
with outcomes).

Selection was **recomputed** from raw levels and features under the shipped configuration rather
than read from the payloads, because the `takeable` flag stored in older chart payloads predates
the current selection rules. The recomputation reproduces the book's own candidate list on
**60 of 60** days, which is the check that makes the rest of the numbers usable.

Confidence intervals are day-block bootstraps (resample days with replacement, 3,000–4,000 draws);
p-values are permutation tests (5,000–20,000 draws); the model is validated with day-blocked
5-fold CV and a store-to-store transfer test.

Five things to hold against these numbers:

- **The chart population is 60 of 180 sessions**, and the recon half of it is one contiguous
  six-week block (Nov–Jan). Feature-level conclusions rest on that slice, not on the full record.
- **Float is live-only** — 1,139 of 1,676 rows, all from the live store — so the float result
  cannot be cross-validated against recon at all.
- **The out-of-sample half of the collapse test runs backwards in time** (the harvest walks into
  the past), so it demonstrates non-stationarity across periods, not forward-in-time decay.
- **Reaching 2R before the stop is a proxy for realised R.** It is exact for the target-or-stop
  outcomes that dominate, and ignores mark-to-close on the 7% of setups that neither hit nor
  stopped.
- **The AUC figures are the best of several specifications** — populations, targets, penalties.
  Read 0.55 as an optimistic estimate of what these features currently deliver, not a conservative
  one.

Refs #1, #689. Touches `research/decisions.md` §D-23 (the throttle), §D-38 (the retired target
optimiser), §D-39/§D-40 (the selection rules), §D-41 (recon share counts). Supersedes nothing;
extends the 2026-08-06 report *Does past behaviour predict future performance?* to 6× the sample.
