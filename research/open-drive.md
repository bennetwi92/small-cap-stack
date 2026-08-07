# Open Drive — feature specification

> The **second strategy**: a 10-minute opening-range breakout with a consolidation requirement,
> trading the 09:30 bell rather than the pre-market. Companion to `bull-flag.md`, which specifies
> the strategy the book trades today. Same document shape: this is the *what*.
>
> **Status (2026-08-02): specified, measured, NOT trading.** The rules below are locked; no
> `opendrive/` package exists and none should be built yet. The measurement is
> `docs/reports/2026-08-02-the-0930-open-a-second-strategy.md`, from the harness
> `spikes/open_drive_sweep.py` (#418). It found a positive-expectancy setup that **cannot be
> monetised at $500** and **degrades the existing book if merged into it**. Read this as the record
> of a strategy held ready, not one queued for build.
>
> Raw input is a list of 5-min `Bar(start, open, high, low, close, volume)` (`capture.py`). Core
> principle (CLAUDE.md): **store raw, compute derived on read** — every feature below is a pure
> function of the cached bars, so the definition can change and be recomputed retroactively.

---

## 1. Detection model

There is no pipeline. Unlike the bull-flag — which tokenises a whole day and searches for the
longest valid pole+consolidation over a growing window — Open Drive's two candles are **fixed by
the clock**. There is nothing to segment and nothing to search.

```
09:30 ─────── 09:35 ─────── 09:40 ──────────────────────────────►
│  OPENING    │  CONSOL-    │  trigger window
│  RANGE      │  IDATION    │  entry = cons.high + 1 tick
└─ green,     └─ less vol,  └─ stop  = cons.low
   body>wicks    shorter
```

**This has one consequence worth stating loudly.** `research/phase-2-roadmap.md` names
**prefix stability** as "the sleeper": the bull-flag detector's answer at 08:35 may differ from its
answer at 16:00, because the segmentation is over a growing prefix — which would silently invalidate
the simulation as a predictor of live behaviour. Open Drive has no such exposure. Both candles are
final at 09:40 and the levels never move. Live and replay cannot diverge.

It is also the first strategy that could use **broker-native brackets** — the pre-market is
limit-only (#37), and this trades after the bell.

## 2. The universe (not a gate)

A symbol qualifies only if the scanner surfaced it **strictly before the trigger time**
(`first_seen_utc < 09:40` at the locked 5/5 split).

This is not a filter over results, it is the definition of the population: a name the scanner
surfaced at 10:15 was never available at 09:40. It is applied at extraction before anything is
counted, and **no variant, sensitivity or baseline relaxes it.** Where a proposed range length moves
the trigger later, the cutoff moves with it — variants then sit on different populations, each
legitimately tradable, and any comparison between them must say so.

The universe inherits the scanner's own filters (`TOP_PERC_GAIN`, $1–50, change >10%, trailing
5-min volume >100k). Nothing measured against it is a claim about US small caps generally.

## 3. Feature areas

Naming follows `bull-flag.md`: `AREA_feature`, typed `gate` (rejects) / `score` (ranks) /
`record` (logged only).

### 3.1 `RANGE` — the opening candle

| ID | Feature | Measures | Computation | Type | Default |
|----|---------|----------|-------------|------|---------|
| `RANGE_green` | the open drove up | `close > open` on 09:30–09:35 | `gate` | required |
| `RANGE_body_dominant` | conviction, not chop | `body > upper_wick + lower_wick` | `gate` | required |
| `RANGE_high` / `RANGE_low` | the range itself | max/min over the range window | `record` | — |
| `RANGE_move_pct` | size of the drive | `body / open` | `record` | — |
| `RANGE_volume` | the denominator for `CONS_vol_ratio` | sum over the range window | `record` | — |

_Intent: "The bar at the open should be very high relative volume. The candle should be green. And
the close-open must be larger than the sum of the wicks."_

**Decision (locked 2026-08-02, #418) — the relative-volume requirement is dropped.** Measured
against a pre-market volume baseline it separated nothing: tightening from RVOL>1 to RVOL>10 moved
the population 137 → 119 with flat statistics throughout. It is removed from the spec rather than
demoted to a score term. ⚠️ **The caveat is real:** the store holds no average daily volume, so the
only available baseline was the same morning's pre-market — and for a stock gapping on news the
opening bar is nearly always the largest bar of the session, leaving the measure no range to work
with. A genuine RVOL (opening bar vs 20-day ADV) is untestable until ADV is captured, and this
decision should be revisited when it is.

`RANGE_body_dominant` measured **+0.296R** on the ungated population — the largest of the four gate
effects, and not separable from noise (raw p = 0.555).

### 3.2 `CONS` — the consolidation candle

| ID | Feature | Measures | Computation | Type | Default |
|----|---------|----------|-------------|------|---------|
| `CONS_lower_vol` | supply drying up | `cons.volume < range.volume` | `gate` | required |
| `CONS_shorter` | the move pausing | `cons.range < range.range` | `gate` | required |
| `CONS_vol_ratio` | how much it dried up | `cons.volume / range.volume` | `record` | — |
| `CONS_range_ratio` | how tight the pause is | `cons.range / range.range` | `record` | — |
| `CONS_green` | direction of the pause | `close > open` | `record` | — |
| `CONS_holds_under_range_high` | whether it stayed inside | `cons.high <= range.high` | `record` | — |

_Intent: "The second candle should represent some sort of consolidation. This could be less volume
than the opening candle, it should be more wicky than the opening candle and it should probably be
shorter too."_

**Decision (locked 2026-08-02, #418) — the "more wicky" requirement is dropped.** P(≥2R) is 19%
whether it holds or not; it separated nothing and cost ~35% of the population. Removed, not demoted.

**Recorded, not gated: `CONS_holds_under_range_high` measures −0.200R** — a consolidation that pokes
*above* the opening range's high does **better**, not worse. That is what one would expect of
momentum, and it is the one feature here whose sign contradicts an intuitive reading of the setup.
It is logged so the direction can be confirmed as the sample grows; it is not gated in either
direction on 22 days of evidence.

### 3.3 `RISK` — stop geometry

| ID | Feature | Measures | Computation | Type | Default |
|----|---------|----------|-------------|------|---------|
| `RISK_abs` | stop distance in dollars | `entry_fill − stop` | `record` | — |
| `RISK_pct` | stop distance as a fraction of entry | `(entry_fill − stop) / entry_fill` | `record` | — |

No gate. Both a floor (0/5/10/15¢) and a ceiling (∞/10/7/5% of entry) were swept and **neither
cleared the permissive default** on a bootstrap interval, so neither was adopted — despite stop
distance being the single strongest effect the engine-feature report found for the bull-flag
(+0.72R at `risk ≥ $0.10`, the only contrast to survive Holm there).

⚠️ **`RISK_pct` is nonetheless the most important number in this strategy**, because of §5: stops
of 1–7% put it permanently against the notional cap. It is a `record` because no *threshold* on it
is defensible, not because it doesn't matter.

### 3.4 `LOC` — location

| ID | Feature | Measures | Computation | Type | Default |
|----|---------|----------|-------------|------|---------|
| `LOC_seen_before_trigger` | the universe, §2 | `first_seen_utc < trigger_from` | `gate` | required |
| `LOC_staleness` | delay from trigger window open | bars between `trigger_from` and the fill | `record` | — |

`LOC_staleness` is **structurally inert here** — 10, 20 and 30-minute bounds give identical books,
because the setup triggers exactly five minutes after its consolidation closes. Contrast the
bull-flag, where `entry_staleness_min = 30` is load-bearing because a break can come hours after the
scanner appearance.

### 3.5 Deliberately absent

| Not in the spec | Why |
|---|---|
| Float gate | `float ≥ 20M` measured **+0.226R** — the *opposite* direction to what `float_max_shares < 20M` implies. ⚠️ **There is no live float gate** (#551): that threshold feeds an EOD report count and filters nothing, so this row contrasts against an unapplied rule, not against current behaviour. `docs/reports/2026-07-31-float-vs-max-r.md` found small float better *for the tail* (P(≥3R)), which is a different question from expectancy at a fixed 2R. Possibly not a real conflict; 22 days cannot tell. **Open.** |
| Price band | `price ≥ $5` measured +0.310R with the lowest raw p of any contrast (0.080 → 0.796 after Holm). The strongest candidate for a future gate, and still not adoptable. |
| Quality score | The bull-flag's 0–1 score was measured to have **no rank power** (ρ = +0.053). Do not build a second one before the first is fixed. |
| News gate | Not tested. The scanner's change% and volume filters already select for it upstream. |

## 4. Entry & stop

Unchanged from the house convention (`decisions.md` #182/#190) — the split exists so the trigger
decides *when* while R is measured against something deliberately worse:

| | |
|---|---|
| **Trigger** | last consolidation candle's high **+ 1 tick** — decides when the setup fires |
| **Fill (for R)** | that same high **+ 3 ticks** — conservative, so the edge isn't overstated |
| **Stop** | the consolidation candle's **low** |
| **Gap-through** | fill no better than the trigger bar's open, mirroring `rmetrics._measure` |
| **Stop-first** | a bar breaching the stop *before* the trigger kills the setup; a bar doing both in the same 5 minutes counts as the break |

**Selection: banded sequential commit.** At 09:40, rank the day's gate-passing setups by **planned
stop width** — `(fill − stop) / fill` — keep only the band **[3%, 10%)**, and commit to the widest.
**One working order at a time**, never a basket. The one-a-day cap is the trader's own
quality-over-quantity constraint, and #418 measured it as non-binding anyway: 46 candidates over 13
days.

> ⚠️ **Corrected 2026-08-07 (#535).** This section locked "**one trade per day, first to trigger**"
> on the grounds that ranking a day's candidates is look-ahead bias (#379). That caution is right
> for the bull-flag and **over-general here.**
>
> Two things went wrong with it. First, *first to trigger is not a rule* on 5-minute bars: on
> 2026-07-30 fifteen candidates triggered on the same bar, and the replay broke the tie
> **alphabetically**. Reversing that arbitrary tie-break moves the published month from +5.67R /
> −0.5% to −0.15R / −11.4% — the rule was a lottery, and the spec locked the winning ticket.
> Second, the look-ahead objection doesn't bind: **every OD-5/5 setup is clock-fixed and final at
> 09:40**, the same instant as the universe cutoff, so the whole ranking set exists before any
> entry can fire. Ranking a set that is complete is not the same as ranking a stream that is
> still arriving — which is what makes this legal here and illegal for the bull-flag.
>
> Measured over the same month: 7 trades, **+3.49R, +6.0%** at **4.2%** drawdown, against −0.5% at
> 10.4% for the locked rule. `docs/reports/2026-08-02-open-drive-picking-the-days-stock.md` has the
> derivation. #423 locked this spec knowing the correction was outstanding
> (`findings-index.md` flagged it); this applies it.
>
> **The residual objection is in-sample fitting, not look-ahead.** The 10% ceiling is structural —
> it is the sizing crossover `risk_fraction / position_fraction`, read off configuration and not
> fitted — and it is load-bearing: removing it lets widest-first walk into the >10% names that
> lose at full size. The **3% floor was read off the same 46-candidate month**. It sits on a
> plateau (0.025–0.035 all land within $15), which is reassuring rather than conclusive. Treat the
> band edge as provisional until the out-of-sample re-run at 60+ days.

## 5. Capital — the finding that matters most

`size_position` takes `min(risk_qty, cap_qty)`; the notional cap binds whenever the stop is tighter
than `risk_fraction / position_fraction` = **10% of entry**. The bull-flag's stops run a median
13.9% wide and are usually risk-bound. **Open Drive's stops are 1–7%, so it is almost always
cap-bound** — 10 of 13 trades at the live configuration, each risking **2.46%** of equity against a
configured 5%.

The consequence: **+5.67R over 13 trades ends the month at $497.67 on its own $500.** Even at 20%
risk and a 100% notional cap every trade is cap-bound, returning +7.1% for a 21.5% drawdown.

**If it were ever traded**, the shape is: `portfolio_max_trades_per_day` stays **2** — slot 1
pre-market bull-flag, slot 2 Open Drive, fractions 0.50/0.50 — which keeps the settled-cash
invariant (`0.50 × 2 = 1.0`) and satisfies `broker-costs.md`'s good-faith rule ($250 + $250 ≤ $500)
by construction. It would also need **its own target fitting**: merging it into the shared adaptive
book costs **$218**, because the target re-fit and risk ladder see the merged candidate stream, so
the bull-flag leg is no longer the one that was measured.

## 6. Locked decisions & open items

**Locked 2026-08-02 (#418):**

1. **Open Drive is its own strategy**, not a bull-flag variant — fixed-clock, no segmentation. If
   built, it is a sibling package, not a mode of `bullflag/`.
2. **The range/consolidation split is 5/5.** Measured against 10/5 (+0.227R), 5/10 (−0.034R) and
   15/5 (−0.794R); expectancy falls monotonically as the trigger moves later, tracking the
   time-of-day report's session decline (ρ = −0.166).
3. **`CONS_wickier` and pre-market-relative RVOL are dropped** — measured inert, removed rather than
   demoted, with the measurement retained as the record (§3.1, §3.2).
4. **No fitted threshold is adopted.** All four swept knobs stayed at their permissive defaults;
   nothing cleared its baseline on a bootstrap interval.
5. **Do not trade it, and do not merge it into the adaptive book.**
6. **Fitting is pre-registered and thresholds-only** for any future pass — day-block bootstrap,
   within-day permutation, Holm across pre-registered contrasts, matching the method of the existing
   reports.

**Locked 2026-08-07 (#535), correcting the above:**

7. **Selection is banded sequential commit, not first-to-trigger** (§4). Rank at 09:40 by planned
   stop width, keep `[3%, 10%)`, commit to the widest, one working order at a time. First-to-trigger
   was not a rule at all on 5-min bars — fifteen candidates shared a trigger bar on 2026-07-30 and
   the tie broke alphabetically, worth ±6R depending on which way. Ranking is legal *here*, and only
   here, because every OD-5/5 setup is final at 09:40 before any entry can fire; the bull-flag's
   triggers arrive over hours and the same rule would be look-ahead. ⚠️ The **3% floor is
   in-sample** (one month, 46 candidates, on a plateau); the 10% ceiling is structural. Re-derive
   the floor out of sample, and re-derive both if 1-minute capture ever lands.

**Open:**

- **1-minute bars.** The store is 5-min only, so the shortest expressible range is 5 minutes. On
  1-min bars this is a different setup with materially tighter stops — which would sharpen entries
  and *worsen* the sizing problem simultaneously. The largest single caveat.
- **A real RVOL baseline** (opening bar vs 20-day ADV). Needs ADV capture; may reverse §3.1.
- **The float direction.** Positive here, negative for the tail elsewhere, gated the other way live.
- **Re-run at 60+ days** — Phase-1 collection completes ~2026-10-01. Nothing above is established:
  13 trades, expectancy interval −0.40R to +1.26R, covering zero and covering every variant tested.
