---
title: "Open Drive: picking the day's stock"
published: 2026-08-02
summary: The 09:30 strategy lost money by picking the wrong stock each day — its "first to trigger" rule is an alphabetical lottery landing on cap-crushed 1%-risk trades. A sizing-aware pick turns the same month positive at half the drawdown.
tags: strategy,portfolio,research
---

This morning's report established that Open Drive — the 10-minute opening-range breakout with a
consolidation requirement — made **+5.67R over 13 trades in 2026-07 and still ended the month at
$497.67 on its own $500**, because tight stops leave the 50% notional cap sizing most trades at a
fraction of the configured 5% risk. It called that a capital constraint. This report shows it is
equally a **selection** problem: on most days the strategy had several candidates to choose from,
and the rule that chose between them was doing so by accident. Choosing deliberately — by how much
risk a candidate lets the book deploy — turns the same month into **$529.80 (+6.0%) at less than
half the drawdown**, with no change to the setup, the exits, or the sizing rules.

Same window as the morning report: 2026-07-01 → 2026-07-31, 22 sessions with bars, replayed
through the production `simulate_portfolio` / `simulate_exit` and cost model. The replay reproduces
the published 13-trade book symbol-for-symbol and the $497.67 end equity before any variant is run.

## "First to trigger" is an alphabetical lottery

The one-trade-a-day book takes the day's *first candidate to trigger*, a rule chosen because it is
decidable the moment it fires. But the entry triggers one tick above the 09:35–09:40 consolidation
high, and on this population **nearly every candidate that fires does so on the very first bar
after 09:40**. On 2026-07-30 fifteen candidates triggered on the same bar. Five-minute bars cannot
say which was first, so the replay breaks the tie alphabetically — and so, in effect, does the
strategy. The book bought APLD on 07-30 because APLD sorts before AXTI, BHC and twelve others.

Replaying the identical rule with the same-bar tie-break reversed (last symbol instead of first)
swings the month from $497.67 to $443 — **a spread wider than the strategy's entire measured
edge**. The baseline book is one draw from a lottery, and its published result is partly luck.

## Why the losing picks lose: the ~1% risk trades

`size_position` caps a position at 50% of equity, which binds whenever the stop is tighter than
`risk_fraction / position_fraction` = 10% of entry. A cap-bound trade risks `0.5 × stop%` of
equity. The alphabetical picks landed on stops of 0.8% (BABX), 1.9% (IREN), 2.3% (EFOR), 2.7%
(APLD) — trades risking **0.4–1.4% of equity against the configured 5%**. A +2R win on BABX moved
the book +0.8% before costs; the losers with wide stops (FXHO at 11%, risk-bound at the full 5%)
took their losses at full size. Winning small and losing big is how +5.67R became −0.5%.

Stop width against realised R over all 46 candidates makes the target band obvious:

| planned stop (% of entry) | n | R per trade |
|---|---|---|
| under 2% | 5 | +0.15 |
| 2–3% | 16 | +0.03 |
| **3–5%** | **13** | **+0.95** |
| 5–10% | 8 | +0.25 |
| over 10% | 4 | **−0.35** |

Tight stops can't be sized; stops past 10% buy no extra size (the cap crossover) and measured
negative on top. The tradable middle is wide enough to matter.

## The refined rule: banded sequential commit

Ranking the day's candidates is exactly what the standing look-ahead rule (#379) forbids — for the
bull-flag, whose setups arrive all day. **Open Drive is the exception that makes ranking legal:
every 5/5 setup is clock-fixed and final at 09:40**, the same instant as the universe cutoff, so
the full ranking set exists before any entry can fire. (This is the same "no growing prefix"
property the Phase-2 roadmap already records for this strategy.)

The rule, all of it decidable in real time:

1. At 09:40, rank the day's gate-passing setups by **planned stop width** — `(fill − stop) /
   fill` — keeping only the band **[3%, 10%)**. The ceiling is the sizing crossover
   `risk_fraction / position_fraction`, derived from configuration, not fitted; the floor
   guarantees a cap-bound fill still deploys at least `0.5 × 3% = 1.5%` of equity.
2. Commit to the widest. **One working order at a time** — never a basket, because with several
   orders working, which same-bar fill you get is unknowable from 5-minute bars (the lottery
   again, measured: a top-2 basket swings $443–$557 across tie-break assumptions).
3. If the working setup's stop is breached before it fills, the setup is dead — cancel and commit
   to the next-ranked setup from the next bar. Skip any setup whose stop or entry was already
   crossed while it wasn't active: don't chase.
4. First fill is the day's trade. One trade a day, unchanged.

## What it does to the month

| book | trades | total R | end equity | return | max drawdown |
|---|---|---|---|---|---|
| first to trigger (published) | 13 | +5.67 | $497.67 | −0.5% | 10.4% |
| same, tie-break reversed | 13 | −0.15 | $443.06 | −11.4% | 14.4% |
| **banded sequential commit** | **7** | **+3.49** | **$529.80** | **+6.0%** | **4.2%** |

Every trade in the refined book deploys between 1.4% and 3.8% of equity (average 2.7%) — the
0.4–1% trades are structurally excluded rather than filtered after the fact. The floor sits on a
plateau (0.025–0.035 all land $523–$538); removing the ceiling drops the book to $508 and takes
the drawdown back to 11.6%, because the widest-first ranking then walks into the >10% names (VTAK,
FXHO, AMIX) that lose at full size — the ceiling is load-bearing.

What the band pays for it: JZXN (+2R on a 16% stop, risk-bound, +10% of equity that day) is
outside the ceiling and skipped. Over this month the four >10% setups were one JZXN and three
full-size losers; the band's refusal is a trade-off the bucket table above supports, not a free
lunch.

## What this is worth, honestly

Seven trades. The expectancy interval still covers zero, as it did for the original thirteen, and
nothing here rescues the morning report's other verdicts: no individual gate is separable from
noise at this sample, and the strategy still needs its own book if it is ever traded. Two things
*are* established, because they are arithmetic rather than statistics: the baseline's pick among
simultaneous triggers is undefined at this bar granularity (its published result is partly
tie-break luck), and a cap-bound trade below a 3% stop cannot risk more than ~1.5% of equity
whatever the market does. The refinement fixes both by construction; whether the resulting book
has positive expectancy remains a 60-day question.

The selection spec in `research/open-drive.md` (#423, in flight) still reads "first to trigger,
ranking is look-ahead" — this report is the correction: for this strategy ranking at 09:40 is
legal, and the spec should adopt the banded sequential commit when it lands.

Re-run at 60+ days, alongside the strategy's own re-test, when Phase-1 collection completes around
2026-10-01. If 1-minute capture ever lands, both the lottery measurement and the band edges need
re-deriving — finer bars shrink the same-bar tie problem and tighten every stop in the population.
