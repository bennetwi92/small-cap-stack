---
title: Time of day: when the entry fires, and what it's worth
published: 2026-07-31
summary: 21 days, 787 triggered setups. Within the pre-market no window is statistically distinguishable from another — but entries decay monotonically through the session, after 11:00 significantly so, and R-normalisation hides a large percentage-move effect.
tags: strategy,data
---

## The answer, up front

**Within the pre-market, no window is better than another — not at this sample size.** The
04:00–09:30 block splits into six sub-windows whose realised expectancy ranges from −0.54R to
−0.00R, which looks like a strong pattern until you test it: permuting entry-time labels *within
each trading day* reproduces a spread that large 68% of the time (p = 0.68 on realised R, 0.52 on
Max R, 0.53 on percentage gain). No individual pre-market window separates from the rest either —
the best pairwise contrast, 08:30–09:00, comes in at p = 0.17. The suspicion is reasonable and the
data does not confirm it.

**Three things about time of day are real, and none of them is a pre-market sweet spot:**

1. **The session decays monotonically.** Later entries are worse on every outcome measured
   (Spearman ρ = −0.17 on realised R, p = 0.0001). Entries after 11:00 ET are −0.37R worse than
   everything else (p = 0.006, and p = 0.037 after Holm correction across six pre-registered
   contrasts) and the effect survives controls for price, stop width, shape score, cycle number and
   staleness.
2. **R-normalisation is hiding the biggest time effect there is.** Measured as a *move*, the
   pre-market is far richer than the regular session: median peak gain 2.9% before 06:00 and 6.3%
   between 06:00–07:00, against 1.05% at 10:00–11:00 and 0.46% after 11:00. Pre-market versus RTH
   is +6.0 percentage points (p = 0.0002). It doesn't show up in R because pre-market stops are
   three times wider in percentage terms — the same 1R is simply a much bigger trade.
3. **Friction is not time-invariant, and it points the same way.** Because R is measured against
   stop distance, the tight late-day stops make fixed per-share costs and the 2-tick exit slip cost
   **0.31R** on an 11:00 entry against **0.15–0.20R** on a pre-market one. Late entries are the
   worst place to be twice over.

The one lever that beats the clock outright is **staleness**: entering within 15 minutes of the
scanner appearance is worth +0.27R against entering later (p = 0.009), and the controlled estimate
is −0.016R for every minute of delay. That is a bigger, better-evidenced edge than any hour of the
day in this record.

## What this is measured on

Every **triggered** engine-v2 setup in the collected record — 787 triggers across 431 distinct
symbols and 677 symbol-days, over the 21 trading days from 2026-07-01 to 2026-07-30. Each is keyed
on the ET time of its **trigger bar**, not on the scanner appearance: the question is when the
entry fires, not when the name shows up.

Three deliberate choices about the population:

- **All triggers, not just takeable ones.** Only 92 of the 787 triggers passed every gate and were
  un-exhausted. Cut 92 trades nine ways and the hourly cells hold two to thirty observations; a
  leave-one-day-out check on that subset flips its "best window" between 09:30–10:00 and
  10:00–11:00 depending on which day you drop. The full triggered population measures what the
  tape offered a bull-flag breakout at each hour, which is the question that has enough data to
  answer. Where the takeable subset says something different, it is noted.
- **Outcomes measured four ways** — Max R, P(≥1R), realised R under the book's fixed-target exit
  (2R target, 2-tick exit slippage, stop-first intrabar), and **max gain as a fraction of entry
  price** (#390). The last one matters more than it looks: see below.
- **Every test is day-clustered.** Bootstrap CIs resample whole trading days, and permutation tests
  shuffle time labels *within* a day. Without that, one good session that happened to hold its
  triggers at 08:30 manufactures an 08:30 effect.

Numbers are recomputed from the stored full-day bar series with the live `rmetrics` and
`portfolio.exit` code, not read from the payload's cached fields. The replay reproduces the stored
Max R and stop-out flag on 778 of 787 triggers; the nine that disagree all sit on 2026-07-01 to
07-13 and look like payloads written by an earlier engine build. Dropping them moves nothing (the
11:00+ contrast goes from −0.371R/p=0.006 to −0.374R/p=0.005, ρ from −0.166 to −0.162).

## The hour-by-hour picture

Realised R is under the book's 2R target and already carries the 2-tick exit slip; it excludes
commissions and fees, which are broken out later. CIs are 95%, day-clustered.

| Entry window (ET) | n | days | P(≥1R) | Realised R @2R | 95% CI | Median peak gain | Stop width | Exhausted |
|---|---:|---:|---:|---:|---|---:|---:|---:|
| 04:00–06:00 | 86 | 21 | 39.5% | −0.32 | [−0.61, −0.03] | 2.94% | 6.5% | 0% |
| 06:00–07:00 | 41 | 18 | 46.3% | −0.18 | [−0.73, +0.30] | 6.29% | 6.7% | 2% |
| 07:00–08:00 | 68 | 20 | 32.4% | −0.39 | [−0.65, −0.12] | 2.41% | 5.8% | 0% |
| 08:00–08:30 | 32 | 18 | 21.9% | −0.54 | [−0.86, −0.14] | 1.48% | 9.2% | 6% |
| 08:30–09:00 | 33 | 16 | 42.4% | −0.00 | [−0.43, +0.57] | 2.69% | 5.8% | 3% |
| 09:00–09:30 | 32 | 17 | 37.5% | −0.28 | [−0.84, +0.26] | 1.64% | 3.4% | 6% |
| 09:30–10:00 | 160 | 21 | 40.6% | −0.18 | [−0.40, +0.05] | 2.10% | 3.7% | 1% |
| 10:00–11:00 | 208 | 21 | 36.1% | −0.43 | [−0.66, −0.22] | 1.05% | 2.7% | 8% |
| 11:00–12:30 | 127 | 21 | 27.6% | −0.68 | [−1.01, −0.40] | 0.46% | 2.1% | 15% |

Read the CIs before the point estimates. Every pre-market window except 04:00–06:00, 07:00–08:00
and 08:00–08:30 has a confidence interval that spans zero, and the two that look most interesting
(06:00–07:00 at −0.18 and 08:30–09:00 at −0.00) are the two with the widest intervals — 41 and 33
trades spread over 18 and 16 days. **The apparent pre-market structure is mostly sampling noise.**

The columns that *do* move cleanly with the clock are the last three. Median peak gain falls from
~3–6% pre-08:00 to 0.46% after 11:00. Stop width falls from 6.5% to 2.1%. Exhaustion — the engine's
own "this is the Nth pump of the day" counter (#102) — climbs from 0% to 15%. Those are not
close calls; the stop-width trend alone has ρ = −0.38 at p = 0.0001.

At 30-minute resolution inside the pre-market the picture is noisier still, which is the point:

| Slot | n | days | P(≥1R) | Realised R | Median gain | Stop width | No-trade bars before entry |
|---|---:|---:|---:|---:|---:|---:|---:|
| 04:00 | 20 | 12 | 30.0% | −0.65 | 0.97% | 7.04% | 0% |
| 04:30 | 41 | 17 | 41.5% | −0.31 | 3.70% | 6.10% | 5% |
| 05:00 | 14 | 10 | 50.0% | +0.15 | 4.35% | 6.85% | 0% |
| 05:30 | 11 | 9 | 36.4% | −0.33 | 3.07% | 5.39% | 18% |
| 06:00 | 30 | 14 | 50.0% | −0.07 | 6.52% | 6.63% | 10% |
| 06:30 | 11 | 8 | 36.4% | −0.46 | 4.03% | 7.21% | 18% |
| 07:00 | 44 | 19 | 38.6% | −0.23 | 2.54% | 5.78% | 14% |
| 07:30 | 24 | 14 | 20.8% | −0.69 | 1.60% | 6.59% | 4% |
| 08:00 | 32 | 18 | 21.9% | −0.54 | 1.48% | 9.22% | 31% |
| 08:30 | 33 | 16 | 42.4% | −0.00 | 2.69% | 5.82% | 3% |
| 09:00 | 32 | 17 | 37.5% | −0.28 | 1.64% | 3.42% | 13% |

Slots alternate good/bad on 11–44 observations each. That is what noise looks like.

## What survives testing

Six contrasts were specified before looking at the outcome, then tested by permuting labels within
each trading day. Holm-adjusted p-values are across the six contrasts within each outcome.

| Contrast | Realised R @2R | p | Peak gain % | p |
|---|---:|---:|---:|---:|
| Pre-market (<09:30) vs RTH | +0.116 | 0.269 | **+6.0 pp** | **0.0002** ✓ |
| Prime 06:00–09:30 vs rest | +0.109 | 0.358 | +3.6 pp | 0.049 (Holm 0.15) |
| Deep pre-market <06:00 vs rest | +0.063 | 0.717 | **+7.2 pp** | **0.012** ✓ |
| Late ≥11:00 vs rest | **−0.371** | **0.006** ✓ | **−5.0 pp** | **0.008** ✓ |
| 08:00–08:30 vs rest | −0.176 | 0.511 | +1.5 pp | 0.645 |
| 08:30–09:30 vs rest of pre-market | +0.207 | 0.275 | +0.7 pp | 0.874 |

✓ = survives Holm correction. Two findings clear the bar: **late entries are worse**, and
**pre-market entries are much bigger moves**. Notice that they clear it on *different* outcomes.
The pre-market advantage exists almost entirely in percentage terms and almost vanishes in R.

The monotone trend is the cleanest statement of the whole analysis:

| Outcome | Spearman ρ vs entry minute | p |
|---|---:|---:|
| Realised R @2R | −0.166 | 0.0001 |
| Max R | −0.099 | 0.008 |
| Peak gain % | −0.196 | 0.0001 |
| Stop width % | −0.377 | 0.0001 |

Leave-one-day-out agrees: across all 21 folds, 11:00–12:30 is the worst window **21 times out of
21**. (08:30–09:00 comes out best in all 21 folds too, but that is a weak check — consecutive folds
share 20 of 21 days. The honest cross-check is a half-sample split, and there 08:30–09:00 flips
from −0.25 in the first ten days to +0.57 in the last eleven, on 23 and 10 trades. Treat it as
unproven. The 11:00+ result holds in both halves: −0.74 and −0.59.)

Controlling for everything at once — OLS of realised R on time dummies plus covariates, with
day-clustered bootstrap intervals, baseline = 09:30–11:00 entries:

| Term | Coefficient | 95% CI |
|---|---:|---|
| Deep pre-market (<06:00) | +0.065 | [−0.242, +0.402] |
| Prime (06:00–09:30) | +0.021 | [−0.308, +0.316] |
| **Late (≥11:00)** | **−0.328** | **[−0.699, −0.007]** |
| log(entry price) | **+0.142** | **[+0.031, +0.248]** |
| Stop width | +1.907 | [−0.427, +4.911] |
| Shape score | −0.380 | [−1.956, +1.092] |
| Cycle number | −0.081 | [−0.200, +0.029] |
| **Minutes since scanner hit** | **−0.016** | **[−0.032, −0.001]** |

The late-entry penalty is not a proxy for cheap stocks, tight stops, bad shapes or worn-out cycles —
it survives all of them. Two other coefficients are worth as much as the time effect: higher-priced
names do better (+0.14R per log unit of price, consistent with #126's widening of the band to
$1–50), and **every minute between the scanner appearance and the entry costs 0.016R**.

## The thing R is hiding

R normalises by stop distance. Stop distance is not constant through the session — it collapses
from 6.5% of price in the deep pre-market to 2.1% after 11:00, because the consolidations get
tighter as the day's volatility drains away. So a 1R win at 05:00 and a 1R win at 11:30 are
recorded identically and are not remotely the same trade.

| Entry window | Median stop width | Median peak gain | P(peak ≥ +5%) | P(peak ≥ +10%) |
|---|---:|---:|---:|---:|
| 04:00–06:00 | 6.5% | 2.94% | 44.2% | 32.6% |
| 06:00–07:00 | 6.7% | 6.29% | 51.2% | 29.3% |
| 07:00–08:00 | 5.8% | 2.41% | 35.3% | 26.5% |
| 08:00–08:30 | 9.2% | 1.48% | 40.6% | 31.2% |
| 08:30–09:00 | 5.8% | 2.69% | 33.3% | 27.3% |
| 09:00–09:30 | 3.4% | 1.64% | 21.9% | 21.9% |
| 09:30–10:00 | 3.7% | 2.10% | 30.0% | 17.5% |
| 10:00–11:00 | 2.7% | 1.05% | 24.0% | 11.5% |
| 11:00–12:30 | 2.1% | 0.46% | 18.1% | 10.2% |

A pre-market trigger is roughly **three times** as likely to be sitting on a +10% move as an
11:00 trigger. In R terms those two are nearly indistinguishable. This is the strongest
time-of-day signal in the record and the current R-only view of the results page cannot see it.

## Friction, in R

R also makes friction time-dependent, in the direction that hurts. A round trip costs a roughly
fixed number of cents per share: 2 × $0.0035 commission, 2 × $0.0032 exchange + clearing, TAF and
SEC on the sell, plus the book's 2-tick exit slip. Divide a fixed cent cost by a shrinking stop
distance and the cost in R grows through the day.

| Entry window | Median risk $/share | Fees in R | Exit slip in R | Total friction | All-in expectancy |
|---|---:|---:|---:|---:|---:|
| 04:00–06:00 | $0.180 | 0.076 | 0.111 | 0.187 | −0.393 |
| 06:00–07:00 | $0.170 | 0.080 | 0.118 | 0.198 | −0.255 |
| 07:00–08:00 | $0.184 | 0.074 | 0.109 | 0.183 | −0.467 |
| 08:00–08:30 | $0.213 | 0.066 | 0.094 | 0.160 | −0.608 |
| 08:30–09:00 | $0.220 | 0.063 | 0.091 | 0.154 | −0.064 |
| 09:00–09:30 | $0.175 | 0.079 | 0.114 | 0.193 | −0.359 |
| 09:30–10:00 | $0.230 | 0.059 | 0.087 | 0.146 | −0.244 |
| 10:00–11:00 | $0.140 | 0.099 | 0.143 | 0.241 | −0.529 |
| 11:00–12:30 | $0.110 | 0.127 | 0.182 | 0.310 | −0.811 |

(The exit slip is already inside the realised-R column of the earlier table; fees are not, so the
all-in column subtracts them. Per-share only — the $0.35 commission minimum is excluded, since it
binds only at the book's current toy size and would swamp the comparison.)

Friction on an 11:00 entry is **twice** what it is at 09:30 and it lands on the window that was
already the worst. Any future rule that trades late needs to clear a 0.31R hurdle before it earns
anything.

## Where the moves actually are — an engine-free view

The tables above are all conditioned on the engine firing. To check whether the engine is simply
missing the good hours, here is the tape itself: for every 5-minute bar of every tracked symbol-day
(117k bars over 856 symbol-days), the maximum forward excursion over the following 60 minutes.

| Bar time | Median 60-min upside | P(+5% in 60 min) | Median 60-min downside | Median $/bar | Bars that never trade |
|---|---:|---:|---:|---:|---:|
| 04:00–06:00 | +1.12% | 18.9% | −0.70% | $3,647 | 34.0% |
| 06:00–07:00 | +1.82% | 26.2% | −1.01% | $2,754 | 37.0% |
| 07:00–08:00 | +1.85% | 23.9% | −1.27% | $16,688 | 21.8% |
| 08:00–08:30 | +2.21% | 27.2% | −1.18% | $22,734 | 19.0% |
| 08:30–09:00 | +5.98% | 58.0% | −1.90% | $28,645 | 17.0% |
| **09:00–09:30** | **+8.52%** | **72.5%** | −2.58% | $39,744 | 14.0% |
| 09:30–10:00 | +5.60% | 54.1% | −2.65% | $631,074 | 1.3% |
| 10:00–11:00 | +3.02% | 33.4% | −2.58% | $385,111 | 1.2% |
| 11:00–12:30 | +1.95% | 21.5% | −2.12% | $201,073 | 1.4% |

The tape's momentum is concentrated in **08:30–10:00**, and it peaks in the half hour *before* the
bell: a random bar at 09:00–09:30 has a 72.5% chance of a +5% print within the hour, against 19% at
04:00–06:00. The engine put only 32 of its 787 triggers into that window. That is a genuine gap
between where the movement is and where the strategy is looking — though note the same window is
where stop widths are already collapsing (3.4%), so the engine finding little there is partly the
setup definition and partly the tape.

The deep pre-market is a different story: 14.5% of symbol-days print their eventual high of the day
before 06:00 — level with 09:30–10:00 (14.5%), a little behind 10:00–11:00 (15.8%), and well behind
the 23.1% that print theirs after 12:30 — yet the median 04:00–06:00 bar goes on to gain only 1.1%
over the next hour. Deep pre-market is high-variance, not high-drift: a handful of names make their
whole day there and the rest do nothing.

## Can you actually trade it?

This is the constraint that makes the pre-market advantage partly theoretical.

| Bar time | Bars with zero volume | Bars that print flat (H=L) | Median $/bar | 25th-pct $/bar |
|---|---:|---:|---:|---:|
| 04:00–06:00 | 34.0% | 46.7% | $3,647 | $0 |
| 06:00–07:00 | 37.0% | 49.8% | $2,754 | $0 |
| 07:00–08:00 | 21.8% | 32.3% | $16,688 | $554 |
| 08:00–08:30 | 19.0% | 28.5% | $22,734 | $1,181 |
| 08:30–09:00 | 17.0% | 25.6% | $28,645 | $1,938 |
| 09:00–09:30 | 14.0% | 21.4% | $39,744 | $4,098 |
| 09:30–10:00 | 1.3% | 2.1% | $631,074 | $109,463 |
| 10:00–11:00 | 1.2% | 2.1% | $385,111 | $71,983 |
| 11:00–12:30 | 1.4% | 3.1% | $201,073 | $34,253 |

A quarter of pre-06:00 bars in these names trade **nothing at all**, and half print a single price.
The median 05:00 bar turns over $3.6k against $631k in the first half hour of RTH — a factor of
170. At the book's current $500 equity none of this binds; at any size worth having, the
04:00–07:00 window's measured expectancy is not a number you could realise. That also gives the
04:00–06:00 P(≥1R) of 39.5% an asterisk: some of those breakouts are one print through a stale
level, not a fill you would have got.

The 08:00–08:30 window is the standout offender among the entries themselves: **31% of its triggers
had a bar that never traded in the five bars before entry**, three to ten times any other window.
It also has the widest stops (9.2%) and the worst expectancy. A plausible reading is the lull
before the 08:30 macro-data release — activity drains out, quotes go stale, and a breakout through
a level nobody is defending is a breakout into nothing. That is a hypothesis this data can suggest
but not confirm.

## Two secondary findings worth acting on before the clock

**Staleness beats the hour.** The gap between the scanner appearance and the trigger is a stronger,
better-evidenced signal than time of day:

| Minutes appearance → entry | n | P(≥1R) | Realised R @2R |
|---|---:|---:|---:|
| ≤5 | 36 | 44.4% | −0.20 |
| 6–10 | 185 | 35.7% | −0.32 |
| 11–15 | 194 | 43.3% | −0.18 |
| 16–20 | 148 | 34.5% | −0.47 |
| 21–30 | 224 | 29.5% | −0.55 |

Pooled, ≤15 minutes beats >15 minutes by **+0.27R** (p = 0.009), and it holds separately in RTH
(+0.28R, p = 0.035) and directionally in the pre-market (+0.28R, p = 0.096). The current staleness
cap is 30 minutes (#130). On this evidence, 15–20 minutes is the defensible number, and it is worth
more than any hour-of-day filter.

**Holding a pre-market trade through the bell is a coin flip weighted against you.** Of the 292
pre-09:30 triggers, 80% resolved to their stop or their 2R target before the open. The 57 that were
still live went into the bell at a median of exactly 0.00R unrealised, and then **53% stopped out
against 25% reaching target**. There is no free option in carrying a flat pre-market position
across 09:30.

## What this does and does not change

Nothing here justifies a pre-market time filter — the evidence for one is not there, and adding a
window rule on a 33-trade cell would be fitting the noise. What it does support:

1. **Stop taking late entries.** ≥11:00 is worse on every outcome, survives every control and every
   robustness check, and pays double the friction. That is one rule with real evidence behind it.
   In the current record it would remove 127 of 787 triggers at an average of −0.68R each.
2. **Tighten the staleness cap** from 30 minutes toward 15–20 (#130). Better-evidenced than any
   time-of-day rule and cheaper to implement.
3. **Put percentage move next to R on the results page.** #390 added `max_gain_pct`; this analysis
   is the argument for surfacing it as a first-class column. R alone reports the pre-market and the
   late morning as near-equivalent, and they are not — one is a 3% move and the other is a 0.5%
   move against the same nominal risk.
4. **Look harder at 08:30–09:30.** It is where the tape's forward excursion peaks (+8.5% median,
   72.5% chance of +5% within the hour) and where the engine currently produces 4% of its triggers.
   Whether that is the pole-height gate, the 2% minimum move or the collapsing consolidation width
   is a separate investigation, and a more promising one than partitioning the pre-market by clock.

The honest caveat on all of it: 21 trading days, one month, one market regime. The late-entry
penalty and the percentage-move gradient are strong enough to survive within-day permutation and
half-sample splits. Nothing at the level of an individual half-hour window is, and won't be until
the Phase-1 collection has considerably more than 21 days behind it.

Refs #400, #1. Data: `dashboard-data` chart payloads, 2026-07-01 → 2026-07-30, replayed through
`rmetrics.compute_r_metrics` and `portfolio.exit.simulate_exit`.
