---
title: What the Warrior Trading tapes actually say
published: 2026-08-27
summary: 200 transcripts checked against the locked selection rules — provenance, not new signal.
tags: strategy,data
---

## The short version

We pulled 200 YouTube transcripts from Ross Cameron / Warrior Trading — daily recaps, watchlists,
and a few educational videos — and checked what he actually says he chases and avoids against the
rules already locked into `research/decisions.md`. Nothing here changes a rule. It's a provenance
check: the locked rules (float and news are collected but never gate, selection favours quality
over quantity, sizing is base-hit not home-run) match what he says on tape, not just recollection
of what he says.

## What's in the library

`spikes/warrior_library.py` (#304) collects English auto-captions into a local, gitignored library.
`spikes/warrior_library_synthesis.py` aggregates the 200-video analysis pass that had been sitting
unread — full findings on the issue. The 200 videos split: 149 daily recaps, 28 watchlists, 21
educational, 2 other.

## What he chases

Leading percentage gainers, recent reverse splits (especially paired with fresh news), high-day
breaks, low-float squeezes, micro-pullback entries, VWAP reclaims, breaking-news short squeezes.
The recurring theme across recaps is "quality over quantity" — one obvious stock a day, not a
scattergun.

Of the 200 videos, the catalysts he actually names: breaking news (51), reverse split (34), short
squeeze (24), low float (16), no-news momentum (14), recent IPO (7), circuit-breaker halts (5).

## What he avoids

Penny stocks, easy-to-borrow names, high-float names, low-quality ("C-grade") setups, trading
against a negative MACD, overtrading, swinging for size on a home run instead of taking base hits,
spoofed tape (fake big sellers on the level 2), no-news China names, and IPOs in a cold market.

## Market mood and results

Of the days he narrates: 76 cold, 46 warm, 37 hot, 22 choppy. Results skew green — 119 green days,
17 red, 16 flat, 48 not stated — which fits a strategy built around small, frequent base hits
rather than swinging for home runs.

## Recurring lessons

- "Get in, get green, get out" — take the base hit, don't hold for the moon.
- Start the day with smaller starter positions, build a cushion, then size up.
- Don't disperse attention across many names at once — pick the best, leave the rest.
- On a cold day, live to trade another day rather than forcing a trade.

## Bottom line

This confirms the provenance of already-locked rules — float/news collected-not-gated, selection
favouring quality over quantity, base-hit sizing — it doesn't surface anything that argues for a
rule change. Full frequency tables: the findings comment on #304 (regenerate with
`python spikes/warrior_library_synthesis.py --json ...`).
