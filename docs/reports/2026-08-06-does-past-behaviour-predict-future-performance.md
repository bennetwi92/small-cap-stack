---
title: Does past behaviour predict future performance?
published: 2026-08-06
summary: The adaptive target and the risk throttle rest on different assumptions. One is sound and undersampled; the other costs money under the null and has no evidence behind it.
tags: strategy,portfolio
---

The adaptive layer has two knobs, and they are usually spoken about as one idea: *recent results
tell you something about the next session*. They don't rest on the same assumption, and the
assumptions are not equally defensible.

**The adaptive target is estimating a distribution.** "What fraction of setups reach +2R before
their stop?" is a property of the trade population. Answering it from past trades needs the
population to be reasonably **stationary** and needs **enough samples**. It does not need momentum.
The premise is sound; the sample is roughly 30× too small.

**The risk throttle is betting on momentum.** Stepping the risk rung after a run of same-direction
days is a claim that a good day makes the next day more likely to be good. That requires **serial
correlation** in daily results. There is no evidence of any in this data — and under the null of
no correlation the throttle is not neutral, it is a **structural drag**. Measured below: it costs
about **$22 per 29 trading days** on a $500 book, roughly 3.5% a month, in exchange for about
0.8 percentage points of drawdown.

Everything here rests on **13 paper trades across 12 active days** out of 29 collected sessions
(2026-07-01 → 2026-08-05), the entire takeable population — the ≤2/day cap never bound, so 13 is
not a sample of the trades, it *is* the trades. That is a severe limit and it is the report's main
finding, not a caveat to it.

## The daily record

Aggregate realised R per active day, at the 2R target, size-independent:

```
07-01 +0.98   07-15 +2.00   07-30 +2.00
07-09 +2.00   07-22 -1.02   07-31 +2.00
07-13 +2.00   07-23 -1.02   08-03 -1.10
07-14 +2.00   07-27 +2.00   08-04 -1.02
```

Mean +0.90R/day, SD 1.40. Eight up days, four down.

## Is there any momentum to bet on?

| Series | n | lag-1 autocorrelation | permutation p | 95% CI |
|---|---|---|---|---|
| Daily R | 12 | +0.307 | 0.27 | −0.32 … +0.75 |
| Per-trade R | 13 | +0.227 | 0.43 | −0.37 … +0.69 |

Both point estimates are positive, which is the direction the throttle assumes. Neither is
distinguishable from zero: reshuffling the series — destroying any memory by construction — produces
an autocorrelation this large or larger **27% of the time**. The confidence intervals span from
meaningful negative correlation to strong positive correlation. This data cannot tell the
difference between a momentum regime and a coin.

Testing the throttle's *actual* rule rather than the general statistic:

| Condition | days | mean next-day R |
|---|---|---|
| Unconditional | 12 | +0.90 |
| After 1 up day | 8 | +1.23 |
| After 2 up days *(the rule's up-trigger)* | 6 | +0.98 |
| After 1 down day | 3 | −0.01 |
| After 2 down days *(the rule's down-trigger)* | 1 | **+2.00** |

Note the shape. Conditioning on *two* up days is **worse** than conditioning on one (+0.98 vs
+1.23) — the opposite of what a momentum story predicts. And the single time the down-trigger
actually fired, the next day was the best outcome available. One instance is an anecdote, not
evidence. But the rule that governs real risk has, so far, been right zero times out of one, and
the broader statistic gives no reason to expect better.

## What the throttle did to the book

Same trades, same days, same target — the ladder on versus off:

| | end equity | trades | total R | max drawdown |
|---|---|---|---|---|
| Throttle on (3 rungs) | $621.84 | 13 | +10.82R | 10.22% |
| Throttle off (1 rung) | **$654.68** | 13 | +10.82R | 10.23% |

The throttle cost **$32.84** — about 5.3% of the book — and bought **0.01 percentage points** of
drawdown protection. Effectively nothing, in exchange for something.

Why: partition the days by the rung the throttle had in force, then score that day's setups.

| Risk in force | days | mean R |
|---|---|---|
| 2.5% (throttled) | 2 | **+2.00** |
| 5.0% (full) | 10 | +0.68 |

It de-risked into the two best days in the sample. With n=2 that is luck, not a mechanism — but it
is the direct measurement of what the rule accomplished, and the sign is wrong.

## What the throttle costs when there is no momentum at all

The A/B above is one path. A single unlucky path proves nothing about a rule. So: hold the calendar
fixed (identical VPS, data-fee and tax ledgers) and **permute which day's setups land on which
date**. That destroys serial correlation entirely while preserving the trade population exactly.
On a memoryless sequence a momentum bet should break even. 500 shuffles:

| | throttle on | throttle off |
|---|---|---|
| Mean end equity | $613.52 | **$635.88** |
| Mean max drawdown | 10.81% | 11.64% |

Mean cost **−$22.35** (median −$14.56). The throttle **lost on 291 shuffles, won on 72**, and made
no difference on 137 (the ladder never left the top rung). Of the shuffles where it did anything at
all, it lost **80%** of the time.

This is the important result, because it is not about luck. The throttle is a **structural drag**
under the null, and the reason is mechanical: fixed-fractional sizing already de-risks after a
loss — risk 5% of a smaller balance and the dollars at risk fall automatically. The ladder cuts a
*second* time on the same information. Kelly sizes on current equity, not on recent streaks; this
double-counts the streak and then compounds the shortfall through the days that follow.

It is not worthless. Under the null it does buy **0.83 percentage points** of drawdown reduction —
real insurance, honestly measured. The question is the premium: **~3.5% of the book per month** to
shave 0.8pp off a drawdown. On a small account trying to compound, that is a bad price.

## The target fit: right idea, wrong sample size

Expectancy per target over all 13 trades, size-independent:

| Target | mean R | SD | hit rate | total R |
|---|---|---|---|---|
| 1.0R | +0.37 | 0.94 | 69% | +4.84 |
| 1.5R | +0.52 | 1.23 | 62% | +6.82 |
| **2.0R** | **+0.83** | 1.48 | 62% | +10.82 |
| 2.5R | +0.52 | 1.70 | 38% | +6.82 |
| 3.0R | +0.52 | 1.77 | 23% | +6.74 |
| 5.0R | +0.98 | 2.47 | 23% | +12.74 |

The grid's argmax is 2.0R, which is reassuringly also the configured fallback. But look at 5.0R,
outside the fit grid: it "wins" on mean R. Its entire result comes from **three trades** that ran
far enough to fill (VEEE peaked at 16.7R, VRAX at 7.0R, FCUV at 6.8R). Three observations, an SD of
2.47R, and a conclusion that would restructure the strategy. That is precisely the trap a 13-trade
sample sets.

Walk-forward is the direct test of whether a trailing fit transfers. Split the trade sequence at
every interior point, fit the target on the past, then check which target actually won on the
future:

- The past's pick matched the future's best on **1 of 6 splits — 17%**, against a 25% chance rate
  for a four-value grid.
- Mean regret (R left on the table by following the past's pick): **0.21R per trade**.

Six splits is far too few to call this a failure. It is entirely sufficient to say there is **no
evidence of skill** — the fit is not currently beating a coin, and the honest reading is that it is
fitting noise.

## How much data would settle any of this?

From the observed per-trade SD of **1.48R**:

| Question | trades / days needed | we have |
|---|---|---|
| Detect daily autocorrelation of 0.3 | 85 active days | 12 |
| Detect daily autocorrelation of 0.2 | 194 active days | 12 |
| Separate two targets 0.5R apart | 138 trades | 13 |
| Separate two targets 0.3R apart | 381 trades | 13 |
| Separate the observed top two (2.0R vs 1.5R, gap 0.31R) | **362 trades** | 13 |

At the current arrival rate — 13 trades and 12 active days per 29 collected sessions — 362 trades is
roughly **800 trading sessions, or about 3.2 years** of live collection. 85 active days is about
**10 months**.

That is the real constraint on the whole adaptive layer, and no amount of parameter tuning touches
it. The nightly harvest (#431), which rebuilds pre-market sessions from purchased vendor bars, is
the only path to that sample inside a useful timeframe.

## What I would change

**Turn the throttle off, or re-specify it.** `portfolio_risk_rungs=1` disables it. It is a bet on
an effect this data cannot detect, it costs ~3.5%/month under the null, and the one time its
down-trigger fired it was wrong. If the intent is capital preservation rather than prediction — a
guard against a regime break or an undiagnosed detector bug dumping garbage into the book — then
say that, and build it as a **drawdown circuit-breaker** (cut risk when equity falls X% from its
high-water mark). A drawdown breaker needs no autocorrelation premise to justify itself. The
current streak trigger fires on noise and charges for the privilege.

**Stop discarding history in the target fit.** A trailing window is itself a regime bet: if the
distribution were stationary you would use *all* history. Shortening it trades estimation error
(longer is better) against regime staleness (shorter is better), and at n=13 we are overwhelmingly
in the estimation-error-dominated half. The window was widened 20 → 40 calendar days on 2026-08-06
(#463) because at 20 it never fired at all; that was a fix for a starved optimiser, not a
considered choice of horizon. The right setting for now is **effectively unbounded** — use every
trade — and only shorten it once N is large enough that regime drift is something we can measure
rather than assume.

**Require a margin before switching targets.** The fit currently takes the argmax of four noisy
means. It should have to *beat* the incumbent by more than the sampling error before moving; below
that, stay at the fallback. As the table above shows, three of the four grid values sit within
0.01R of each other — the argmax among them is a coin flip dressed as a decision.

**Treat the harvest as the critical path.** Every question in this report is answered by sample
size and by nothing else.

---

Method: all figures computed from the live tracker's Parquet store on the box over 2026-07-01 →
2026-08-05 (29 collected sessions, 13 takeable candidates), replayed through the production
simulator (`portfolio/sim.py`) rather than a separate model, so the counterfactual books use the
same exit, cost and ledger code as the published book. Permutation tests use 20,000 resamples; the
null test uses 500 calendar-preserving shuffles. Related: #239 (the throttle), #463 (the starved
target window), `research/decisions.md`.
