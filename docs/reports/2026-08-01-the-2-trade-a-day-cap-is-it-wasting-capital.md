---
title: The 2-trade-a-day cap: is it wasting capital?
published: 2026-08-01
summary: It has never dropped a setup, and a 75/25 first/second split changes almost nothing — because the notional cap is not what limits position size.
tags: strategy,portfolio
correction: 2026-08-07 — the "adaptive book" here runs the risk-throttle ladder, which shipped OFF on 2026-08-06 (#474) after 500 calendar-preserving shuffles showed it cost a mean $22.35; the adaptive target also now fits over all history behind a paired margin gate (#476). The cap findings and the sizing crossover are unaffected — the throttle never bound in this window.
---

Two questions were put to the virtual book: does `max_trades_per_day = 2` waste capital on days
when only one setup fires, and would giving the day's first trade 75% of the book and its second
25% — instead of the flat 50%/50% — put more capital to work?

**Short answers.** The cap has never cost a trade: across the 25 collected sessions it dropped
**zero** setups. Capital *is* mostly idle, but the cap is not the reason — the 5% risk budget is,
and it binds first on 8 of the 11 trades taken. A 75/25 split therefore moves almost nothing: it
leaves total R **exactly unchanged** and shifts end equity by about ±1.5%, in *opposite directions*
in two different books, which is the signature of noise rather than an edge. On the one day this
month that actually fired two trades, 75/25 would have **reduced** deployment from 85% to 60%.

Window: **2026-07-01 → 2026-07-31**, 25 collected sessions, 11 qualifying setups. Everything below
is a replay of the real simulator (`simulate_portfolio` / `simulate_portfolio_adaptive`) over the
published book; the harness reproduces all eight published books trade-for-trade before any variant
is run.

## What the cap has actually done: nothing

`_take_day` sorts a day's qualifying setups by trigger time, takes the first two, and logs the rest
as `skip_reason="cap"` — the "what did the cap cost me" ledger from #230. That ledger is empty.

| | |
|---|---|
| Collected sessions | 25 |
| Sessions with **zero** qualifying setups | 15 (60%) |
| Sessions with exactly one | 9 |
| Sessions with exactly two | 1 (2026-07-01) |
| Sessions with three or more | **0** |
| Setups dropped by the 2/day cap | **0** |

The cap has never been the binding constraint, in any of the eight published books. Raising it to
3, 5 or 50 would change the record by exactly nothing. The dashboard's portfolio page already says
as much; this confirms it against the full collected record rather than one payload field.

Worth sitting with the first row: **60% of sessions the book is 100% cash all day** because no
setup qualified at all. That dwarfs every question about how to allocate between two trades.

## Where the idle capital actually goes

Capital is genuinely under-used — but not by the trade cap. On a day the book *does* trade it
deploys about 40% of equity; averaged over every collected session, including the empty ones, it
deploys about 16%.

| Book | Avg deployment, trading days | Avg over all 25 sessions | Peak single day |
|---|---|---|---|
| Adaptive (live) | 40.5% | 16.2% | 85.0% (2026-07-01) |
| Fixed 2R | 42.6% | 17.0% | 85.0% (2026-07-01) |

The reason is in `size_position`. Each position is `min(risk_qty, cap_qty)`: the risk budget wants
`equity × 5% / (entry − stop)` shares, the notional cap allows `equity × 50% / entry`. The cap
binds only when the stop is **tighter** than `risk_fraction / position_fraction` = **10% of entry**.
Wider than that, the risk budget sizes smaller and the cap is irrelevant.

Here is every trade the book took, with what the risk budget wanted as a share of opening equity:

| Date | Symbol | Stop distance | Risk budget wants | Cap allows | Got | Bound by |
|---|---|---|---|---|---|---|
| 2026-07-01 | CANF | 13.90% | 36.0% | 50% | 35.1% | risk |
| 2026-07-01 | CORD | 4.97% | 100.6% | 50% | 49.9% | **cap** |
| 2026-07-09 | VRAX | 10.51% | 47.6% | 50% | 47.4% | risk |
| 2026-07-13 | VEEE | 16.74% | 29.9% | 50% | 29.5% | risk |
| 2026-07-14 | CLSK | 2.79% | 179.2% | 50% | 48.1% | **cap** |
| 2026-07-15 | JTAI | 11.58% | 43.2% | 50% | 42.9% | risk |
| 2026-07-22 | LABT | 17.39% | 28.7% | 50% | 28.5% | risk |
| 2026-07-23 | JEM | 15.34% | 32.6% | 50% | 32.3% | risk |
| 2026-07-27 | ENTX | 14.06% | 17.8% | 50% | 17.4% | risk |
| 2026-07-30 | XRX | 5.30% | 47.2% | 50% | 46.8% | risk |
| 2026-07-31 | FCUV | 18.17% | 27.5% | 50% | 27.2% | risk |

Eight of eleven are risk-bound. Median stop distance is **13.9%** — comfortably wider than the 10%
crossover. Those eight trades would size identically if the notional cap were 50%, 75% or 100%.

> **A doc correction this turns up.** `size_position`'s docstring asserts that "bull-flag stops sit
> a few percent below entry, so the cap is the *usual* constraint, not the edge case". Over the
> book's realised candidates the opposite holds: stops run 2.8%–18.2% with a 13.9% median, and the
> risk target binds on 8 of 11. The mechanism the docstring describes is right; the empirical claim
> attached to it is not. Corrected in the same change as this report.

## The 75/25 split, simulated

Each variant gives slot 1 of the day one notional cap and slot 2 another. All of them sum to 100%,
so the settled-cash invariant (#232 §6) holds — a UK cash account can't buy with unsettled
proceeds, and that is what bounds the total, not the trade count.

**Adaptive book** (the live one, with the kill-switch ladder):

| Split | Trades | Total R | End equity | Return | Max DD | Avg deployment |
|---|---|---|---|---|---|---|
| **50/50 (live)** | 11 | 12.94 | **$713.83** | 42.8% | 10.1% | 40.5% |
| 60/40 | 11 | 12.94 | $706.76 | 41.4% | 10.1% | 40.3% |
| 75/25 | 11 | 12.94 | $702.32 | 40.5% | 10.0% | 40.3% |
| 90/10 | 11 | 12.94 | $696.12 | 39.2% | 10.0% | 40.2% |
| 100/0 | 10 | 10.94 | $695.10 | 39.0% | 10.0% | 40.4% |

**Fixed 2R book** (no risk throttle — the same question without the ladder in the way):

| Split | Trades | Total R | End equity | Return | Max DD | Avg deployment |
|---|---|---|---|---|---|---|
| **50/50 (live)** | 11 | 12.94 | **$750.49** | 50.1% | 10.1% | 42.6% |
| 60/40 | 11 | 12.94 | $749.27 | 49.9% | 10.1% | 43.3% |
| 75/25 | 11 | 12.94 | $757.78 | 51.6% | 10.0% | 44.9% |
| 90/10 | 11 | 12.94 | $761.36 | 52.3% | 10.0% | 46.3% |
| 100/0 | 10 | 10.94 | $763.03 | 52.6% | 10.0% | 46.9% |

Three things to read off these.

**Total R is identical at 12.94 across every split.** It has to be: R is size-independent, and no
split drops a setup (except 100/0, which zeroes slot 2 and loses CORD's +2R). A slot split cannot
change the strategy's edge. It can only re-weight dollars across trades that were going to happen
anyway — which means the only thing it can change is compounding order.

**The two books disagree on the sign.** 75/25 costs the adaptive book $11.51 and earns the fixed-2R
book $7.29. Same setups, same split, opposite conclusion. That is one trade's worth of difference,
not a finding.

**Average deployment barely moves** — 40.5% → 40.3% adaptive, 42.6% → 44.9% fixed. The split does
not solve the idle-capital problem because the idle capital is not the cap's doing.

## Why it barely moves

Per-trade, 50/50 → 75/25 changes only three positions by more than a single share:

| Date | Symbol | Slot | Qty 50/50 → 75/25 | Δ net P&L |
|---|---|---|---|---|
| 2026-07-01 | CORD | **2** | 40 → 20 | **−$12.26** |
| 2026-07-14 | CLSK | 1 | 20 → 30 | +$7.98 |
| 2026-07-30 | XRX | 1 | 100 → 149 | +$16.50 *(fixed-2R book only)* |

Everything else shifts by ±1 share — the whole-share floor reacting to a marginally different
equity path. Noise.

The mechanism is now plain. Raising slot 1's cap to 75% moves the crossover from a 10% stop to a
**6.67%** stop, so it only reaches trades with stops tighter than that — three of eleven this month.
Meanwhile, cutting slot 2 to 25% *shrinks* every second trade of the day. This month the only
second trade was CORD, a +2R winner on the book's first day, and halving it compounds through the
entire rest of the curve. That single trade is why the adaptive book comes out worse.

And it produces the opposite of the intended effect on the one day it matters most:

| Day | Setups | Deployment 50/50 | Deployment 75/25 |
|---|---|---|---|
| 2026-07-01 | 2 | **85.0%** | 60.0% |
| 2026-07-14 | 1 | 48.1% | 74.2% |

The split raises deployment on single-setup days (48% → 74%, and only because CLSK's 2.79% stop
made it cap-bound) at the cost of lowering it on the two-setup day (85% → 60%). Over 25 sessions
those roughly cancel.

## The lever that does move deployment

If the goal is to put more capital to work, the knob is `risk_fraction`, not the notional split.
Sweeping it at the live 50/50 cap, fixed 2R:

| Risk / trade | End equity | Return | Max drawdown | Avg position | Cap-bound trades |
|---|---|---|---|---|---|
| **5.0% (live)** | $750.49 | 50.1% | **10.1%** | 38.7% | 3 / 11 |
| 7.5% | $804.97 | 61.0% | 14.8% | 47.4% | 7 / 11 |
| 10.0% | $826.73 | 65.3% | 16.2% | 49.5% | 11 / 11 |
| 15.0% | $826.73 | 65.3% | 16.2% | 49.5% | 11 / 11 |
| 20.0% | $826.73 | 65.3% | 16.2% | 49.5% | 11 / 11 |

Deployment climbs from 38.7% to 49.5% and stops dead — at 10% risk *every* trade becomes cap-bound
and the 50% notional cap takes over. So the two knobs are a ladder, not alternatives: the notional
cap only starts to matter once the risk budget is raised past 10%.

But note what comes with it. Going 5% → 10% adds 15 points of return and **6 points of maximum
drawdown**, on eleven trades of which three were losses. That is not found money; it is the same
edge levered up, on a sample far too small to justify it. This table is here to identify the lever,
not to recommend pulling it.

## Is the cap safe to leave alone?

Dormant is not the same as harmless. The cap is quiet because the book's *qualification* filters
are tight — engine-v2 takeable, $2–20 fill, a 05:30–09:15 ET trigger window. Counting every
takeable setup in the published charts under each gate configuration the book has actually run:

| Gate configuration | Setups | Per session | Days over cap | Setups the cap would drop |
|---|---|---|---|---|
| **LIVE** — $2–20, 05:30–09:15 | 11 | 0.44 | 0 | **0** |
| pre-#405 — $2–20, 04:00–09:15 | 14 | 0.56 | 0 | 0 |
| pre-#386 — $1–20, 05:30–09:15 | 14 | 0.56 | 0 | 0 |
| pre-2026-07-21 — $1–20, 04:00–09:30 | 18 | 0.72 | 0 | 0 |
| scan band — $1–50, 04:00–09:30 | 24 | 0.96 | 3 | 4 |
| scan band — $1–50, whole 04:00–11:59 window | 91 | 3.64 | 15 | **48** |

The cap has been inert under every configuration the book has ever run, including the looser
pre-#386/#405 ones. It only wakes up if the price band widens to the full scan range, and it
becomes the dominant constraint — dropping 48 setups over 25 sessions — if the pre-market
restriction goes. So the right characterisation is: **the 2/day cap is a live constraint that the
pre-market window currently makes redundant.** Relax the window (spike #379 already argued against
that on separate grounds) and this report needs redoing.

## Recommendation

Leave `portfolio_max_trades_per_day = 2` and the flat 50/50 notional cap alone.

- The cap costs nothing today and is a genuine settled-cash guardrail if the entry window is ever
  widened. There is no case for changing a constraint that has never bound.
- A 75/25 split is not a capital-efficiency improvement. It cannot change total R, it reaches only
  the minority of trades with sub-6.67% stops, it penalises the second trade of a day, and this
  month it would have made the live book slightly worse.
- If under-deployment is the real concern, the honest conversation is about `risk_fraction` and the
  kill-switch ladder — and about the 15 of 25 sessions with no setup at all, which no allocation
  rule can help.

## Method, and what this can't tell you

The harness (`spikes/portfolio_slot_split.py`) replays the **published payload** rather than the
Parquet store, because this was run from a cloud session with no box access. That is sound for this
question: a day's candidate set and every exit outcome are size-independent, so the payload fully
describes what the book saw at every target on the published grid. Before any variant runs, the
harness reproduces all eight published books — end equity to the cent and share counts trade-for-
trade — and the chart-derived setup reconstruction is asserted against the published book (11 = 11)
before the gate-variant table is trusted. Both checks pass.

Per-slot caps are applied by wrapping `size_position` with a day-local slot counter, so the real
`_take_day`, cost model and ledgers do all the work; nothing is re-implemented. The variants tested
are all decidable at trigger time — "the first setup of the day" is known when it fires — which is
the standing rule from #379; ranking a day's setups against each other would be look-ahead.

**The limits are severe and worth stating plainly.** Eleven trades. One day with two setups. The
entire sign of the 75/25 result turns on two positions, CORD and XRX. Nothing here is statistically
significant, and none of it should be read as evidence that 50/50 is *optimal* — only that 75/25 is
not demonstrably better, and that the trade cap is not the thing standing between this book and its
capital. The structural findings (the cap has dropped nothing; the risk budget binds before the
notional cap on wide stops; a slot split cannot change total R) are arithmetic and do not depend on
sample size. The dollar deltas entirely do.

What this replay **cannot** do is test a rule that changes which candidates qualify — a wider price
band, a different entry window, or a cap above 2 with the store's full candidate set behind it.
Those need the Parquet store on the box and a re-run of `extract_day_trades`.

Refs #230, #232, #237, #379, #386, #405. Issue #416.
