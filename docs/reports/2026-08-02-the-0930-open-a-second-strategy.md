---
title: The 09:30 open as a second strategy
published: 2026-08-02
summary: A 10-minute opening-range breakout makes +5.67R over 13 trades and still loses money — because at $500 the notional cap sizes 10 of them.
tags: strategy,portfolio,research
correction: 2026-08-07 — two premises have moved. There is no live `float_max_shares < 20M` gate (#551); that threshold feeds a report count and filters nothing. And the risk throttle described as live shipped OFF on 2026-08-06 (#474), with the adaptive target now fit over all history behind a paired margin gate (#476). The Open Drive measurements are unaffected; the selection rule was superseded the same day by "Open Drive: picking the day's stock".
---

The book trades one strategy: the pre-market bull-flag, entries gated to `[05:30, 09:15)` ET. The
time-of-day report found the tape's forward excursion peaking in the hour the engine ignores, and
recommended looking harder at it. This is that look — a purpose-built second strategy for the open,
specified by the trader and quantified against every day of collected bars.

**Short answer: the setup is real, the money is not.** Over 22 trading days a one-trade-a-day book
returns **+5.67R across 13 trades at 53.8% wins — +0.436R per trade**. Stood up on its own $500 that
same book ends at **$497.67**. It generates five and a half R and gives back a half percent, because
its stops are tight enough that the 50% notional cap sizes **10 of the 13 trades** and each ends up
risking **2.46%** of equity against a configured 5%.

That is a capital constraint, not a verdict on the strategy. But two other findings are less kind:
**not one of ten pre-registered contrasts survives multiple-comparison correction**, and **merging
it into the adaptive book destroys $218 of equity**.

Window: **2026-07-01 → 2026-07-31**, 25 collected sessions, 22 with bars, 876 symbol-days.
Everything below replays the real simulator (`simulate_portfolio` / `simulate_portfolio_adaptive`,
the production `simulate_exit` and cost model) through the same read seams the results page uses.
The harness reproduces the published book trade-for-trade — 11 trades, 12.938R, $713.83 — before any
variant is run.

## What the strategy is

An **opening-range breakout over the first 10 minutes, with a consolidation requirement**:

- the **09:30–09:35** bar is the opening range: green, and its body larger than both wicks combined;
- the **09:35–09:40** bar is a consolidation of it: less volume than the range bar, and shorter;
- **entry** triggers one tick above the consolidation high from 09:40, measured against a
  deliberately worse three-tick fill; **stop** is the consolidation low;
- **one trade a day**, the first to trigger.

### The universe is what could have been traded

A symbol only counts if the scanner had already surfaced it **before the trigger fires**. This is
not a filter applied to the results, it is the definition of the population: a name that appeared at
10:15 was never available at 09:40, so it does not exist for this strategy. It is applied before
anything is counted, and no variant in this report relaxes it. Where a longer opening range pushes
the trigger to 09:50, the cutoff moves with it.

The universe is also conditional on the scanner's own filters — `TOP_PERC_GAIN`, $1–50, change
>10%, trailing 5-min volume >100k. **This is not a backtest over all US small caps.** It is a
backtest over what the live system actually sees.

## What it returns

46 candidates fired on 13 of the 22 days.

| | |
|---|---|
| Trades | **13** |
| Wins / losses | 7 / 6 (**53.8%**) |
| Total R at a 2R target | **+5.67R** |
| Expectancy | **+0.436R per trade** |

ARCT −1.06 · FXHO −1.29 · IRE −1.04 · JZXN +2.00 · BRAI +2.00 · OTLK −1.20 · BABX +2.00 ·
IREN +2.00 · HOVR +2.00 · EFOR −1.04 · GENI +0.33 · AMIX −1.03 · APLD +2.00

Six clean 2R targets, six stops, one close-out. That is what a 2R-target book is supposed to look
like.

## Why the R doesn't become money

Give the strategy its own $500 and run it at a fixed 2R: **$497.67 at the end of the month.**

The reason is sizing. `size_position` takes the smaller of a risk-derived quantity (5% of equity
divided by the stop distance) and a notional cap (50% of equity divided by the entry price). The cap
binds whenever the stop is tighter than `risk_fraction / position_fraction` = **10% of entry**. The
bull-flag's stops run a median 13.9% wide, so it is usually risk-bound. Open Drive's stops are
**1–7%**, so it is almost always cap-bound:

| risk / cap | cap-bound | avg risk taken | end equity | return | max drawdown |
|---|---|---|---|---|---|
| **0.05 / 0.50** (live) | **10 of 13** | 2.46% | $497.67 | −0.5% | 10.4% |
| 0.05 / 1.00 | 8 of 13 | 3.62% | $513.58 | +2.7% | 14.4% |
| 0.10 / 1.00 | 10 of 13 | 4.97% | $506.87 | +1.4% | 20.3% |
| 0.15 / 1.00 | 12 of 13 | 5.59% | $527.13 | +5.4% | 21.5% |
| 0.20 / 1.00 | **13 of 13** | 5.68% | $535.36 | +7.1% | 21.5% |

Even handing it the entire book at four times the risk budget, every trade is cap-bound and the
return is +7.1% for a 21.5% drawdown. **You cannot buy enough shares of a $20 stock with $250 for a
2% stop to be worth anything.** A tight-stop strategy needs capital the account does not have.

This is the same mechanism the 2-trade-cap report found from the other side: there, the risk budget
bound first on 8 of 11 trades and the notional cap was dormant. It is dormant for a strategy with
14% stops and dominant for one with 3% stops.

## Nothing in it is individually defensible

Ten contrasts were fixed before the run and measured on the **215-setup ungated population** — every
symbol whose geometry was well-formed and that triggered, with the gate outcomes carried as labels
rather than applied. A gate cannot be shown to earn its keep on a population it has already
filtered. Day-block bootstrap for intervals, within-day permutation for p-values, Holm across all
ten.

**Not one survives.**

| Contrast | n (pass / fail) | effect | raw p | Holm |
|---|---|---|---|---|
| price ≥ $5 | 97 / 118 | +0.310R | 0.080 | 0.796 |
| range body > wicks | 73 / 142 | +0.296R | 0.555 | 1.000 |
| float ≥ 20M | 66 / 125 | +0.226R | 0.594 | 1.000 |
| cons volume < range volume | 143 / 72 | +0.224R | 0.216 | 1.000 |
| stop ≥ $0.10 | 183 / 32 | +0.135R | 0.592 | 1.000 |
| cons range < range | 152 / 63 | +0.091R | 0.631 | 1.000 |
| cons holds under range high | 93 / 122 | **−0.200R** | 0.499 | 1.000 |

All four gates point the right way and none is distinguishable from noise at this sample. The
combination selects a good slice — +0.436R out of a population averaging −0.03R — but no single
rule in it can be defended on its own evidence.

Two of these are worth flagging beyond the arithmetic. **A consolidation that pokes above the range
high does better, not worse** (−0.200R for the ones that stay under), which is what you would expect
of momentum and is worth watching as the sample grows. And **larger float looks better here**
(+0.226R for ≥20M), against the direction of the live `float_max_shares < 20M` gate. The float-vs-Max-R
report found small float better *for the tail* — P(≥3R) — which is a different question from
expectancy at a fixed 2R target. These may not actually conflict; on 22 days there is no way to tell.

## Two of the trader's rules were dropped

The original statement included two more requirements. Both were measured and neither separated
anything:

- **"the consolidation should be more wicky than the opening candle"** — P(≥2R) is 19% whether it
  holds or not.
- **"the bar at the open should be very high relative volume"** — measured against a pre-market
  volume baseline, tightening from RVOL>1 to RVOL>10 moved the population 137 → 119 with flat
  statistics throughout.

They were removed from the spec rather than kept as ranking terms. The RVOL result deserves a
caveat: the store holds no average daily volume, so the only available baseline was the same
morning's pre-market — and for a stock gapping on news the opening bar is almost always the biggest
bar of the session anyway, which may be why the measure has no range to work with. A real RVOL, the
opening bar against a 20-day average, is untestable until that gets captured.

## No fitted parameter earned its place

Four thresholds were allowed to move, each preferring a plateau over the best cell, and each
required to clear the permissive default's point estimate on a bootstrap interval before being
adopted. **All four stayed at their defaults.**

Staleness is entirely inert — 10, 20 and 30 minutes give identical books. That is structural rather
than surprising: unlike the bull-flag, which can trigger hours after a scanner appearance, this
setup triggers exactly five minutes after its consolidation closes.

A consolidation-volume ratio of ≤0.5 looks attractive at +0.895R, but it is **7 trades** with an
interval running from −0.17 to +1.76. That is precisely the result the plateau rule exists to
refuse.

## Ten minutes is the right amount of time

Four range/consolidation splits, each on its own tradable population:

| Variant | Range / cons | Trigger | Candidates | Trades | Expectancy |
|---|---|---|---|---|---|
| **5 / 5** | 09:30–09:35 / 09:35–09:40 | 09:40 | 46 | 13 | **+0.436R** |
| 10 / 5 | 09:30–09:40 / 09:40–09:45 | 09:45 | 71 | 20 | +0.227R |
| 5 / 10 | 09:30–09:35 / 09:35–09:45 | 09:45 | 16 | 13 | −0.034R |
| 15 / 5 | 09:30–09:45 / 09:45–09:50 | 09:50 | 90 | 21 | **−0.794R** |

The longer variants show more candidates because their later trigger admits symbols the scanner
surfaced later — that is a property of the cutoff, not evidence they are better. What matters is
that expectancy falls monotonically as the trigger moves later, and at 15 minutes it collapses. This
tracks the time-of-day report's monotonic session decline (ρ = −0.166 on realised R). **Waiting for
a longer range costs more than the extra information is worth.**

The trader's original 5/5 choice is the best of the four.

## Do not merge it into the adaptive book

| Book | Total R | End equity | Max drawdown |
|---|---|---|---|
| Bull-flag alone, fixed 2R | +12.94R | $750.49 | 10.1% |
| **+ Open Drive in slot 2**, fixed 2R | **+16.61R** | **$730.48** | 14.9% |
| Bull-flag alone, adaptive | +12.94R | $713.83 | 10.1% |
| **+ Open Drive in slot 2**, adaptive | **+9.56R** | **$496.27** | 12.0% |

At a fixed target Open Drive adds **+3.67R** and *costs* $20 — more R, less money, more drawdown,
for the sizing reason above.

Under the adaptive book it is far worse: total R **falls** by 3.4R and equity by **$218**. The
adaptive machinery is shared — the target is re-fit each day over a trailing window of all
candidates, and the risk ladder steps on the day's aggregate R — so adding a second strategy changes
the target and the risk rung that the **bull-flag** trades get. The merged book's bull-flag leg is
no longer the baseline's. That is not Open Drive losing money; it is Open Drive degrading the
strategy it was supposed to sit alongside.

Letting an unused slot 1 size Open Drive up to the full book recovers about $8 and adds 3.8 points
of drawdown. Not worth it.

**If this is ever traded, it needs its own book and its own target fitting.**

## What this is worth, honestly

Thirteen trades over 22 days. The interval on expectancy runs from −0.40R to +1.26R — it covers
zero, and it covers the point estimate of every variant tested. Nothing here is established.

What can be said is narrower and still useful: the setup the trader described occurs about twice a
week, its geometry survives contact with the data, its four rules all point the right way, the 5/5
split is better than the alternatives, and its failure to make money is fully explained by a sizing
rule rather than by the trades going the wrong way. That is a reasonable basis for keeping the spec
and re-testing it, and not a basis for trading it.

**The largest caveat is bar granularity.** The store holds 5-minute bars only, so the shortest
expressible opening range is five minutes and the 09:30–09:35 bar is atomic. On 1-minute bars this
is a different setup — a one- or three-minute range with a finer consolidation and materially
tighter stops. Tighter stops are exactly what the notional cap is already binding on, so 1-minute
capture would sharpen the entries and worsen the sizing problem at the same time. Both effects need
measuring before this can be judged properly.

Re-run at 60+ days, when Phase-1 collection completes around 2026-10-01.
