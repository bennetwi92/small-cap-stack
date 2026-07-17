# Bull-flag setup — feature specification

> Working design doc for **redefining how the engine spots a setup** around *pattern
> spotting*. It supersedes the sketch this file started as; the original intent bullets are
> preserved verbatim under each area (marked _intent_).
>
> **Status (2026-07-17): shipped.** This spec is live — the engine is the
> `src/small_cap_stack/bullflag/` package (tokenize → segment → extract → gate+score, driven by
> `day.py::detect_day`). The single anchored detector this document was written against
> (`bullflag.py::detect`) was deleted in #296. Read it as the record of *why* the engine is shaped
> this way, not as a proposal.
>
> Raw input is a list of 5-min `Bar(start, open, high, low, close, volume)`
> (`capture.py`). Core principle (CLAUDE.md): **store raw, compute derived on read** — every
> feature below is a **pure function of the cached bars**, so the definition can change and be
> recomputed retroactively over history.

---

## 1. Detection model (the pipeline)

Detection is reframed from "find a peak, walk back, apply gates" into four stages:

```
raw bars ──▶ [1] tokenize ──▶ [2] segment ──▶ [3] extract features ──▶ [4] gate + score
             H/L string       longest valid     per-area feature        hard rejects, then
                              pole+consolidation      vector             a quality score for ranking
```

Why the split matters:

- **Pattern-first.** The H/L structure (stage 1–2) decides *whether a candidate shape exists at
  all*. It is cheap, explainable, and the thing a human sees on the chart.
- **Features are a vector, not a gate chain.** Stage 3 emits a fixed **feature vector** for the
  segmented shape. Stage 4 then splits features into two roles:
  - **Gate** — a hard constraint; failing it rejects the setup (binary).
  - **Score** — a soft signal contributing to a 0–1 **quality score** used to *rank* setups, not
    reject them. (Today `cons_vol_reducing` / `pole_has_big_green` are recorded-but-ungated; this
    formalises that idea across every area.)
  - **Record** — captured for the review workbench / later analysis; neither gates nor scores yet.
- **Retroactive.** Because stage 3 is pure over raw bars, we can re-tokenize and re-score every
  historical opportunity when a definition changes — no re-capture.

**Open decision — gate vs. score policy.** Which features are hard gates vs. score inputs is the
main tuning surface. Start with the current engine's gates (pattern validity, retracement, peak
volume, peak wick) as gates and make everything else a score input; migrate features between roles
as review data tells us what actually separates good setups from bad.

---

## 2. Pattern grammar (stages 1–2)

### 2.1 Tokens (stage 1)

Walk the bars left→right. The first bar is the **base** (`b0`, no token). Each later bar emits one
token by comparing its high to the previous bar's high, within a flatness tolerance `eps` (a small
fraction of price or a tick):

| Token | Meaning        | Condition                                   |
|-------|----------------|---------------------------------------------|
| `H`   | higher high    | `high[i] > high[i-1] + eps`                 |
| `L`   | lower high     | `high[i] < high[i-1] - eps`                 |
| `E`   | equal high     | `|high[i] - high[i-1]| <= eps`              |

So `n` bars → `n-1` tokens. Example from the original sketch: `["H","H","L","L","L"]` = a 2-bar
pole (two higher highs above the base) then a 3-bar consolidation.

**Decision (locked 2026-07-10, refined) — `E` handling.** An `E` (equal high, within `eps`) is
allowed **only in the consolidation**, never in the pole. The **pole is a run of strict higher
highs (`H`)** — the first non-`H` going back ends it. In the consolidation `E` is permissive (a flat
pullback candle is fine), but a run made *only* of `E` is a flat top, not a genuine pullback. `eps`
is a small flatness tolerance (1 tick / `tick_size`), applied on an FP-rounded delta so an
exactly-1-tick move reads as `E`. Barring `E` from the pole keeps the base strictly below the peak
(so `pole_span > 0`) and stops a long flat run on an illiquid name from drifting the base onto a bar
above the peak (#181: ITRG/IVF).

### 2.2 Segmentation (stage 2)

A candidate shape is `base → POLE → CONSOLIDATION → (trigger)`:

- **Pole** = the maximal leading run of strict `H` (no `E` — equal highs are consolidation-only).
  _Intent: "The higher
  highs are the pole." "A pole cannot have lower highs."_
- **Consolidation** = the run of `L`/`E` immediately after the pole peak. _Intent: "the lower highs
  are the consolidation." "A consolidation cannot have higher highs."_
- **Trigger / entry** = the **first `H` after the consolidation** — the bar whose high breaks back
  above the prior bar's high, ending the pullback. _Intent: "The first higher high when in a
  consolidation is the entry."_

**Length bounds** (segment lengths, from the sketch):

| Bound          | v2 value (locked) | Current engine default        | Note |
|----------------|-------------------|-------------------------------|------|
| max pole `H`   | **4**             | `bull_flag_max_pole = 8`      | reduced |
| max cons `L`   | **4**             | `bull_flag_max_flag = 6`      | reduced |
| min pole `H`   | **1**             | `bull_flag_min_pole = 1`      | single higher-high bar allowed |

**Decision (locked 2026-07-10) — max pole/consolidation length = 4 / 4.** Both segments are hard-
gated at 4 for now. _Intent: "Anecdotally the longer patterns (pole and consolidation) are worse
setups."_ We are **not deleting any data**, so these caps are refinable — the store-raw principle
lets us re-tokenise history with wider bounds later. `SHAPE_pole_len` / `SHAPE_cons_len` still carry
a **score** penalty within the allowed range so a 4-bar shape ranks below a 2-bar one.

**Longest-match rule.** _Intent: "The pattern scanner should look for the longest patterns first
otherwise it could mistake a longer pattern for a shorter pattern."_ When multiple valid
pole/consolidation segmentations exist ending at the same trigger, take the one with the **longest
pole** (earliest valid base). This is the structural analogue of the engine's current
"dominant-high peak" fix (#163), which stops an up-tick *inside* a deeper pullback from being
mistaken for the peak.

---

## 3. Feature areas

Naming: `AREA_feature`. **Type**: `gate` (hard reject) · `score` (soft, feeds quality score) ·
`record` (captured only). Every computation is over the segmented `pole` / `cons` bar lists and the
`base` bar.

### 3.1 `SHAPE` — pattern geometry

The structural features that fall straight out of the token string.

| ID | Feature | Measures | Computation | Type | Default |
|----|---------|----------|-------------|------|---------|
| `SHAPE_valid` | a pole+consolidation exists | segmentation in §2.2 succeeds (≥`min_pole` H, then 1..`max_flag` L, base above nothing yet) | `gate` | — |
| `SHAPE_pole_len` | pole length (higher highs) | count of `H` in pole | `gate`+`score` | gate ≤ 4; score penalises longer |
| `SHAPE_cons_len` | consolidation length | count of `L`/`E` in cons | `gate`+`score` | gate ≤ 4; score penalises longer |
| `SHAPE_cons_strictness` | how clean the pullback is | fraction of cons steps that are strict `L` | `score` | prefer high |
| `SHAPE_token_string` | the raw shape | e.g. `"HHLLL"` | `record` | — |

_Intent: max 4 H in pole; max 6 L in consolidation; pole no lower highs; consolidation no higher
highs; first H in consolidation is entry; longest-first; longer patterns are worse setups._

**Decision (locked 2026-07-10, via per-opportunity visual review, #182/#190) — a bar can only
belong to the pole if it's a genuine green thrust candle.** Validated against 8 real opportunities:
- **No red candle in the pole, including the peak.** A red "peak" (a new high that reverses and
  closes weak within the same bar — a shooting-star top, e.g. IRE) isn't a genuine thrust; that
  candidate peak is disqualified entirely and the search continues for a later green peak.
- **A technically-higher-high bar that's doji-like (small body relative to range) doesn't extend
  the pole**, even though its high still ticks up (e.g. MUZ, CRCG, CONL — a quiet 1–2 bar pause
  sitting between two real thrusts). The walk stops at the first such bar going backward from the
  peak; that bar becomes the base (a height reference only), not an intermediate pole bar.
- Threshold: a thrust candle is green (`close > open`) with body ≥ 50% of its range (reuses the
  existing `_is_big_green`/"big green" concept, #132). The **peak** itself only needs to be green
  (any body size, matching the existing single-bar-pole tolerance) — the body-size threshold only
  gates *extending* the pole past the peak's immediate predecessor.
- Effect: this often SHRINKS the pole to a single bar (the true immediate thrust) versus what a
  pure `H`-token walk would have included, which in turn makes the *retracement* measurement much
  stricter (a shallow-looking pullback against a big multi-bar run can become a rejection-level-deep
  pullback against the true, smaller pole) — seen repeatedly (MUZ, CRCG, CONL) and treated as the
  gates working correctly, not a bug.

### 3.2 `VOL` — volume

| ID | Feature | Measures | Computation | Type | Default |
|----|---------|----------|-------------|------|---------|
| `VOL_peak_gt_cons` | thrust out-traded the pullback | `peak-bar volume > max(cons.volume)` (the locked #127 rule) | `gate` | strict `>` |
| `VOL_cons_reducing` | pullback volume drying up | `cons.volume` non-increasing | `score` | prefer true |
| `VOL_pole_concentration` | thrust volume concentrated on the peak bar | `peak.volume / sum(pole.volume)` | `score` | prefer high |
| `VOL_ratio` | how decisively pole beats cons | `max(pole.volume) / max(cons.volume)` | `score` | ≥ 1 (higher better) |
| `VOL_breakout` | conviction on the trigger bar | trigger-bar volume vs pole peak volume | `record` → maybe `score` | — |
| `VOL_relative` | abnormal vs the name's own baseline | pole peak vs a trailing per-symbol volume baseline | `record` | needs baseline source |

_Intent: "The max bar volume in the pole must be greater than the max bar volume in the
consolidation."_ (The engine's `cons_vol_reducing` / peak-volume gate already implement the first
two rows.)

> **Decision (locked 2026-07-10) — peak-bar, not max-bar-in-pole.** The original sketch said "max
> bar volume in the pole," but the volume gate uses the pole's **peak (thrust) bar** volume >
> consolidation volume — reaffirming the locked #127 rule. They differ only for a multi-bar pole
> where a *non-peak* higher-high bar spikes in volume; the peak-bar rule refuses to let an earlier
> bar's volume rescue a weak breakout bar. Matches the legacy detector exactly (keeps parity).

### 3.3 `WICK` — wickyness

The sketch left this blank; specced here. Wick = the part of a bar's range outside its body.
`upper_wick_frac(bar) = (high - max(open,close)) / (high - low)`; symmetric for lower.

| ID | Feature | Measures | Computation | Type | Default |
|----|---------|----------|-------------|------|---------|
| `WICK_peak_upper` | thrust closed strong (not a rejection) | `upper_wick_frac(peak) ≤ max_peak_wick` | `gate` | ≤ 0.50 |
| `WICK_pole_body` | pole has a strong-bodied candle | any pole bar green with body ≥ ½ range (`pole_has_big_green`) | `score` | prefer true |
| `WICK_pole_avg_body` | overall thrust quality | mean body fraction across pole bars | `score` | prefer high |
| `WICK_cons_indecision` | pullback is orderly, not violent | share of cons bars that are small-bodied / doji | `score` | prefer high |
| `WICK_cons_lower` | pullback rejecting lows (buyers defending) | mean lower-wick fraction across cons bars | `record` | — |

_Intent (new): the thrust should close near its high (a big upper wick = rejection, e.g.
AHMA/VRXA, #132); the pullback should look controlled rather than a violent flush._ (The engine's
`max_peak_wick` gate and `pole_has_big_green` cover the first two rows.)

### 3.4 `POLE` — height / extension

| ID | Feature | Measures | Computation | Type | Default |
|----|---------|----------|-------------|------|---------|
| `POLE_height_pct` | the move is meaningful | `(pole_high - pole_base) / pole_base` | `gate`+`score` | gate ≥ **2%** (`min_pole_pct`); higher scores higher (to a cap) |
| `POLE_height_abs` | absolute thrust size | `pole_high - pole_base` (dollars) | `record` | — |
| `POLE_extension_atr` | abnormal vs the name's normal bar | pole height ÷ **trailing 14-bar true-range ATR** (before the base) | `score` | ≥ 2× ATR = abnormal |
| `POLE_velocity` | how fast the thrust ran | `POLE_height_pct / pole_len` (per-bar) | `score` | steeper (fewer bars) better |
| `POLE_overextended` | too far, too fast (chase risk) | `POLE_height_pct` or `POLE_extension_atr` above an upper band | `score` | penalise extreme |

_Intent: "ensure the Pole represents a meaningful expansion." "It cannot be a weak percentage
change." "It should be an abnormal move."_ → **this area is entirely new to the engine** (today
`detect()` never checks pole magnitude).

**Decision (locked 2026-07-10) — `min_pole_pct` = 2%.** A meaningful-move **gate**: reject a pole
whose total height (`(pole_high - pole_base) / pole_base`) is below 2%. Deliberately loose to start
— it only kills the truly weak; refinable (no data deleted). The *"abnormal"* half of the intent is
carried by the **score** feature `POLE_extension_atr` = pole height ÷ a **trailing 14-bar
true-range ATR** measured on the bars *before* the pole base (≈70 min of 5-min bars); ≥ 2× ATR reads
as abnormal. ATR normalises for each name's own volatility (a fixed % can't), so it ranks rather
than rejects, and returns `None` when there aren't enough trailing bars (then it simply doesn't
contribute to the score).

### 3.5 `CONS` — retracement / depth

| ID | Feature | Measures | Computation | Type | Default |
|----|---------|----------|-------------|------|---------|
| `CONS_retracement` | pullback depth into the pole | `(pole_high - cons_low) / (pole_high - pole_base)` | `gate`+`score` | gate ≤ 0.50; shallower scores higher |
| `CONS_holds_base` | pullback didn't erase the pole | `cons_low > pole_base` | `gate` | strict `>` |
| `CONS_tightness` | how tight the range is | `(max(cons.high) - min(cons.low)) / pole_high` | `score` | tighter better |
| `CONS_drift_slope` | orderly downward drift | slope of cons highs (regression / first-vs-last) | `record` → `score` | gentle negative preferred |

_Intent: "I don't think the consolidation should retrace back beyond halfway down through the
pole."_ (Engine already gates `retracement ≤ 0.50` measured on the flag low vs. pole base — matches
`CONS_retracement`/`CONS_holds_base`.)

### 3.6 `LOC` — pattern location (context)

Non-price context: where the pattern sits in time and relative to the scanner.

| ID | Feature | Measures | Computation | Type | Default |
|----|---------|----------|-------------|------|---------|
| `LOC_in_window` | inside the tradeable window | trigger time within 04:00–11:59 ET | `gate` | — |
| `LOC_time_of_day` | session bucket | pre-market / open / late-morning bucket of trigger | `score`/`record` | — |
| `LOC_bars_before_scan` | pattern predates scanner pickup | count of pole/cons bars before the first `scanner_hit` for the symbol/day | `record` | — |
| `LOC_scan_alignment` | scanner catch explains the thrust | is the scanner hit on/near the pole peak vs. mid-consolidation | `record`→`score` | — |
| `LOC_start_after_scan` | pattern started after pickup (harder to explain) | base bar time vs. first scanner hit | `record` | — |

_Intent: "The pattern could have started before the stock scanner picks it up." "The pattern could
start marginally after but this means it is difficult to explain what triggered the high volume that
saw it in the stock scanner."_ → needs `scanner_hits` timestamps joined to the bar series (available
in the store); this is the area most dependent on data plumbing beyond the bar list.

---

## 4. Entry & stop (unchanged intent, restated on the new grammar)

- **Trigger bar** = first `H` after the consolidation (§2.2). **Entry** = its breakout confirmation.
  - **Decision (superseded 2026-07-10, revised same day via per-opportunity visual review, #182/
    #190) — entry price = last consolidation high + 1 tick.** The earlier "+3 ticks" lock (a
    middle-ground guess between the bare-break sketch and the old engine's +5) is **replaced**:
    validated against 8 real opportunities (VRAX/MSTZ/MUZ/TVRD/CRCG/ARCT/IRE/CONL/FCEL/OKLL), the
    trigger the trader actually means is the bare mechanical break — **1 tick** above the last
    consolidation candle's high (which must be a lower high). `entry_trigger = last_cons_high +
    entry_offset` with `Settings.bull_flag_trigger_offset_ticks = 1` (`$0.01` at a penny tick).
  - **Decision (resolved 2026-07-10, same-day follow-up, #182/#190) — the old "+3 ticks" survives
    as a separate, conservative FILL price for R-measurement, not the trigger.** Confirmed by the
    trader: *"the 3 ticks does become a slippage modelled fill price for R. The trigger is always
    the tick above the last high in the consolidation. Often I actually fill at that price anyway.
    3 ticks is being conservative."* So: `entry_trigger` (+1 tick) decides **when** a setup fires;
    `entry_fill = last_cons_high + fill_offset` (+3 ticks, `Settings.bull_flag_fill_offset_ticks =
    3`) is the price R is **measured against** — deliberately worse than the trigger, to avoid
    overstating the edge, even though the real fill is often the trigger price itself. Captured on
    `Setup.entry_fill` (no legacy `BullFlag` slot); #180 must wire `rmetrics` to read it for R
    rather than reusing `entry_trigger`.
- **Stop** = consolidation low (`cons_low`) — the risk the retracement gate is measured against.

---

## 5. Quality score (stage 4, sketch)

A single 0–1 score to **rank** setups that pass the gates. Straw-man: weighted sum of the `score`
features, each normalised to 0–1, weights initialised by intuition and later fit against the review
workbench's corrected-annotation outcomes (Max R). Report the **per-feature contributions**
alongside the score so a low-ranked setup is explainable on the review page, not a black box.

Gates (reject) vs. score (rank), starting point:

- **Gates:** `SHAPE_valid`, `SHAPE_pole_len ≤ cap`, `SHAPE_cons_len ≤ cap`, `VOL_peak_gt_cons`,
  `WICK_peak_upper`, `POLE_height_pct ≥ min`, `CONS_retracement ≤ 0.50`, `CONS_holds_base`,
  `LOC_in_window`.
- **Score:** everything else, plus the graded sides of `SHAPE_pole_len` / `CONS_retracement` /
  `POLE_height_pct`.

---

## 6. Decisions & remaining open items

**Locked 2026-07-10:**

1. **Max pole / consolidation length = 4 / 4** (hard gate; refinable, no data deleted).
2. **`E` (equal-high) token** — allowed only in the consolidation (not the pole); `eps` = 1 tick.
3. **`POLE_height_pct` floor = 2%** (`min_pole_pct`); "abnormal" carried by `POLE_extension_atr`
   (trailing 14-bar true-range ATR, ≥ 2× = abnormal).
4. **Volume gate = peak-bar** (not max-bar-in-pole) — reaffirms #127 (§3.2).

**Locked 2026-07-10 (revised same day via per-opportunity visual review, #182/#190):**

5. **Pole = green thrust candles only** — no red candle in the pole (including the peak); a
   doji-like technically-higher-high bar breaks the pole walk and becomes the base instead (§3.1).
6. **Entry price** = last consolidation high **+ 1 tick** (supersedes the earlier "+3 ticks" lock —
   see §4). `Settings.bull_flag_trigger_offset_ticks = 1`.
7. **Fill price for R = last consolidation high + 3 ticks** (`Settings.bull_flag_fill_offset_ticks
   = 3`), a deliberately conservative slippage estimate applied *downstream* of the 1-tick trigger
   — resolves item 9 below (was open). `Setup.entry_fill` (no legacy `BullFlag` slot yet).

**Still open:**

8. **Gate-vs-score assignment** — the tuning surface; migrate features between roles from review
   data.
9. **`LOC` plumbing** — join `scanner_hits` timestamps to the bar series to compute location
   features.
10. **Appearance-anchoring** — a pole whose peak bar had already fully closed before the symbol's
    first scanner appearance isn't observable and can't be "the" pole for execution (validated on
    MSTZ). Needs `first_hit`, which `detect_setup` doesn't take — deferred to #180's rmetrics
    wiring (an orchestration-layer concern, mirrors the existing #99/#122 appearance gate).
