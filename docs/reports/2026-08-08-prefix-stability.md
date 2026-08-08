---
title: "The sleeper that wasn't: the detector is causal"
published: 2026-08-08
summary: Gate 5 is log-only because the detector might re-segment as a day's bars accumulate, silently invalidating the paper book as a predictor of the live one. Measured over 81 sessions: 2,018 of 2,018 fired runs match the full-day answer exactly, with zero churn. The concern was correct to hold and is now retired — but it clears the algorithm, not the inputs.
tags: [phase-2, engine, validation]
author: Claude
---

## The claim under test

`research/phase-2-roadmap.md` lists three things that "will actually bite" in Phase 2. The second is
prefix stability, and it is the reason Gate 5 (#312) detects **log-only** and comes before any order
code:

> The v2 detector segments the **longest valid** pole+consolidation over a day's bars. Run live
> against a *growing prefix*, the segmentation it picks at 08:35 may differ from the one it picks at
> 16:00. Every R-metric ever recorded, and the entire portfolio sim, is built on the full-day answer
> — so live and replay disagreeing would **silently invalidate the sim as a predictor of the live
> book**.

That is a serious claim. If true, every number the project has published about expectancy is a
statement about a detector that would behave differently the moment it ran forward.

It is also **falsifiable offline**, with no market-data subscription, no funded account and no live
session. Nobody had run it.

## Method

For every run in both stores, replay `detect_day_with_settings` over growing prefixes of the day's
5-minute bars. Compare the answer at **first fire** — the moment a live system would have acted —
against the **full-day** answer, on every field that decides a trade: `entry_trigger`, `entry_fill`,
`stop`, the trigger bar's timestamp, the three segment indices, `passed`, `takeable`, `exhausted`
and `score`.

Also count **churn**: any run whose emitted answer changed at *any* intermediate prefix, even if it
returned to the full-day answer by the close. A detector that wobbles and settles is still a
detector that would have acted on the wobble.

Tolerance is **exact**. Prices are `round(x, 4)` taken off bar highs and lows, so there is no float
drift to absorb, and a tolerance band would hide the only failure mode worth catching.

The harness is `spikes/prefix_stability.py`, kept live rather than retired so a future detector
change has to re-prove this rather than inherit it.

## Result

81 sessions — 51 reconstructed (2026-04-17 → 06-30) and 30 live (2026-07-01 → 08-07) — under the
settings shipped on 2026-08-08 (#643's $3 floor, #584's 2.5% minimum stop, #644's retired optimiser).

| store | runs | fired at some prefix | first fire == full day | churned |
|---|---|---|---|---|
| recon | 1,220 | 909 | **909 (100%)** | **0** |
| live | 1,454 | 1,109 | **1,109 (100%)** | **0** |
| **total** | **2,674** | **2,018** | **2,018 (100%)** | **0** |

Not one run emitted an answer that later changed.

### The harder variant

Five-minute prefixes assume the detector only ever sees *completed* bars. A live `keepUpToDate`
stream does not offer that — it hands you a bar that is still forming. Using the recon store's
`bars_1m`, each 5-minute bar was synthesised in its in-progress forms (1, 2, 3 and 4 minutes
elapsed, then complete) and the detector fired on the earliest form that triggered:

| | runs | fired | **fired on a partial bar** | match |
|---|---|---|---|---|
| recon, minute resolution | 1,220 | 909 | **762 (84%)** | **909 (100%)** |

**84% of fires happen on a bar that has not finished forming, and every one of them still matches
the full-day answer exactly.**

## Why it holds

This is structural, not luck, and the code says so plainly. In `bullflag/day.py` the candidate loop
takes the **earliest** cycle with a valid trigger and breaks — it does not search for the longest or
best. `entry_trigger` and `entry_fill` derive from `bars[cons_end].high`; `stop` from the
consolidation lows. Those are closed bars strictly *before* the trigger. The gates, the score,
exhaustion, and both selection rules read only bars at or before the trigger.

**The chosen setup is causal.** The only full-day-dependent outputs are `total_significant_cycles`,
which is context and gates nothing, and `bar_interval`'s modal spacing.

The prose describing the detector as picking the "longest valid" segmentation was describing
`segment_at_end`, the end-anchored segmenter — not `detect_day`, the greedy cycle walk that
actually runs live. The concern was a reasonable reading of the documentation; it was not a reading
of the code.

## ⚠️ What this does not clear

Both arms of the experiment use the **same bars, truncated**. That isolates the algorithm and clears
it. It says nothing about the **inputs**:

- live bar formation and revision
- missing or late bars
- feed restarts and reconnect gaps
- run and `first_hit` segmentation derived from live scanner hits rather than stored ones

**So Gate 5's question changes rather than disappears.** It should stop asking *"is the detector
prefix-stable"* — answered — and start asking *"are the live bars the same bars"*. That is a
narrower question and a far more tractable one: it wants a hash of the bar series carried alongside
each live detection, so a future disagreement can be attributed to the data rather than the logic.

Gate 5 staying log-only and preceding order code is **unchanged**. The reason for it moved.

## What this changes

- The roadmap's "sleeper" section stays, with the measurement recorded under it. The concern was
  correct to hold before anyone had measured it, and deleting the history would make a good
  instinct look like an error.
- Gate 5's live-vs-replay diff should be specified against the **fired** population (~37 per
  session) rather than the takeable one (~0.63 per session) — a 59× larger sample for the same
  calendar cost.
- The diff must key on `(trading_date, opportunity_id, run_index)`. `seg_id` is `oid` for a
  single-run opportunity and `oid#1` once a second run appears, and **31.6% of runs sit in
  multi-run opportunities** — so a `seg_id`-keyed diff would report roughly 30% spurious mismatches
  and make a healthy system look broken.

## Reproducing

```bash
python spikes/prefix_stability.py --store data/recon
python spikes/prefix_stability.py --store data/live
python spikes/prefix_stability.py --store data/recon --minute
```

About 35 seconds per store, read-only. Run it after any change to `bullflag/day.py`.
