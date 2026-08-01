---
title: The plan, and what the projection assumes
published: 2026-08-01
summary: The commentary that used to live on the Plan page and the Projection view: the five standing orders and why each exists, and the assumptions every projected number rests on.
tags: strategy,process
---

The Plan page and the Projection view were rebuilt as status boards (#414): numbers, statuses,
and the graphics, computed from the most recent published data on every render. What they no
longer carry is argument. This report is where that argument now lives, so a page can't quietly
go stale while still reading as though it were true.

Nothing here is a rule change. The rules are in `research/decisions.md`; the gate sequence is in
`research/phase-2-roadmap.md`. This is the reasoning behind them.

Figures below are as of **2026-08-01**: 22 sessions collected inside the 2026-07-01 → 2026-09-30
window, 1,074 flagged opportunities, 806 of them triggered, and 11 paper trades in the adaptive
book.

## The five standing orders

These are the rules of Phase 1. They are not suggestions, and they do not change because a chart
looks good.

**1. Place no orders. Not one.** Phase 1 is a tracker by decision (`decisions.md` §11). There is
no order code in the repo at all — no bracket, no limit, nothing. `grep -r 'placeOrder|bracketOrder|LimitOrder' src/`
returns nothing; `ibkr/` has transport, supervisor, retry and errors, and stops there. There is
deliberately nothing to click.

**2. Never change a rule to catch a trade you're watching.** Store raw, compute on read: a rule
changed later re-scores the entire history on the next publish, so nothing is lost by waiting for
the sample. A rule bent live is the one change that can never be undone — it contaminates every
number the decision to go live will be made on.

**3. Judge the plan on the sample, not on the session.** Roughly two qualifying setups a day reach
the book. The 2026-07-31 time-of-day study found that window spreads which looked real were
reproduced by chance 68% of the time. One green day is not evidence, and neither is one red one.

**4. Let the book take the trades.** The virtual portfolio already takes every trade you would
have taken — selected, sized at 5% risk, costed at full IBKR tiered rates, exited on bars. If you
would have taken it, it is already in the trade log, and the Skipped table shows what the two-a-day
cap turned away.

**5. Advance by gate, not by feeling.** Phase 2 opens when the gates close, not when the waiting
gets boring. Four of the eight gates are workable today and need nothing from the market; when the
itch comes, the productive answer is to work one.

## Why each phase ends where it does

**Phase 1 — Tracker.** Record every flagged opportunity and what it would have paid; place no
orders. Scanner and capture run 04:00–11:59 ET and the day's bars land in one EOD batch. It ends
when three months of sessions are in and the book clears the bar Gate 2 writes.

**Phase 2 — Paper.** Detect live, place paper orders, and prove the live engine agrees with the
replay. Live detection ships log-only first, because prefix stability is the sleeper risk: the v2
detector segments the *longest valid* pole and consolidation over a day's bars, and run against a
growing prefix it may pick a different segmentation at 08:35 than it picks at 16:00. Every R-metric
ever recorded and the whole portfolio sim are built on the full-day answer, so a live-vs-replay
disagreement would silently invalidate the sim as a predictor of the live book. Pre-market is
limit-only (#37), so the app fires every entry and every exit itself. The phase ends when live
fills track the sim and the edge survives real spreads.

**Phase 3 — Live.** $500 of real money, the same rules, the same size, the same two trades a day.
The tradability gate gets re-validated on the live account (PRIIPs blocks some runners), and
withdrawals, the CGT reserve and the box bill stop being a model and become cash. This is the
destination — reached by finishing the phases, not by skipping them.

## What each Phase-2 gate is actually for

The ladder on the Plan page now derives its status from GitHub issue state. What it no longer says
is *why* each gate exists:

| Gate | Issues | What it's for |
|---|---|---|
| 0 · Truth debt | #302 · #297 · #270 | Settings flip and docs are done; the unrunnable spike import is not. |
| 1 · Spread capture | #309 | `BID_ASK` in the EOD batch → a quotes table. Sets the exit-limit policy from evidence instead of guesswork. |
| 2 · Go/no-go criteria | #310 | Write the bar for entering Phase 2 **now**, before the data can argue back. |
| 3 · Validation | #49 | The collection countdown. A calendar wait, nothing else. |
| 4 · Market data | #311 | The $10/mo L1 bundle. Unblocks everything real-time; already in the cost model. |
| 5 · Live detection | #312 | Shadow mode: stream bars, detect, log only. Measures live-vs-replay drift. |
| 6 · Execution | #313 | Limit entries, app-side stops, an OMS. The first code that can lose money. |
| 7 · Paper live | #314 | Reconciliation and the live-vs-sim divergence report. The Phase-2 finish line. |

The exit-limit fill policy (Gate 6) is the one that will bite hardest. Limit-only means the
app-side stop fires a *limit* order, which can simply not fill in a fast drop, leaving the book
holding a loser well through its stop. The mitigation is a marketable limit priced through the
bid — and how far through is a parameter that costs money on every single exit. That, not feed
latency, is where "accuracy at the stop matters more than at the target" actually lands.

## The projection: what every number on it assumes

The Projection view resamples this book's own trading days to build 500 simulated years. Six
assumptions sit under it, in the order they'd bite.

**Returns don't shrink with size.** Every projected day replays a historical day as a *percentage*
of the balance. This book trades a few hundred dollars a clip; the capital column asks whether the
same bull-flag entries fill the same way at ~$1,800 of notional on a sub-20M-float name. On thin
small-cap tape they almost certainly won't — which makes every income figure on that page a
ceiling, not a target.

**The sample is tiny.** 10 trading days and 11 trades behind a 252-session projection. Resampling
cannot manufacture information the sample doesn't contain: if these weeks weren't representative,
neither is the fan. It widens honestly with time, and that is the only cure.

**Blocks, not shuffled days.** Days are drawn in 5-day runs so losing streaks survive as streaks.
This is what makes the drawdown figures believable — shuffling days independently would roughly
halve them. As of today the projection puts the median worst drawdown inside a year at −15%, the
1-in-10 year at −24%, and the worst path drawn at −38%.

**The strategy is frozen.** The kill-switch rung and the daily target re-fit are baked into the
resampled days as they were actually taken. A future change to the rules is not modelled.

**Tax is the simple case.** 24% CGT above the £3,000 allowance, one flat £/$ rate of 1.27, no loss
carry-forward. If HMRC ever treated this as trading income the rate would be closer to 45% and
every year on the income ramp gets longer.

**The day rate is net of a guess.** £800/day × 220 days, times a 52% take-home fraction for an
inside-IR35 umbrella. That fraction is a setting, not a tax calculation — change it and the gold
rule on the ramp chart moves with it.

## Why the day-rate answer currently reads "sample too small"

The projection's median compound growth from this sample annualises to about **41×/yr**. No
account sustains that; it is a short lucky run amplified by compounding a fixed fraction of a
growing balance. Every capital and years figure in the income ladder divides by that rate, so they
collapse toward zero and mean nothing — quoting "£91,520/yr needs $221 of capital" with a straight
face would be a worse failure than admitting the sample can't support the question. That is why
the ladder greys out and the status pill reads *Sample too small* rather than naming a date.

The drawdown and payout figures on the same page still stand: they don't divide by the growth
rate, they read the distribution of paths. The verdict panel will start answering the question it
was built for once there are months of history behind it, not weeks.

Two failure modes the same panel guards against, for the same reason: a book with no positive
growth has no balance to compound to and gets *No growth to compound* rather than a fabricated
date; a positive-but-tiny edge can honestly return 687 years, and printing "687.7 yr of reinvesting
everything" dresses a no up as a plan, so past 50 years it says *Beyond a lifetime* instead.

## Where commentary goes from here

The working agreement is now explicit in `CLAUDE.md`: dashboard pages carry numbers, statuses and
instructional labels — what a control does, what a metric is, what a mark on a chart means.
Anything that argues, justifies or interprets belongs in a report, where it is dated, attributed
and allowed to be superseded. A page that argues has to be re-read and re-approved every time the
data moves underneath it; a report simply gets a successor.
