# Agent C — exits: where the stop goes, and where the target goes

**Answer in one line: the target is already in the right place; the stop is too tight.** Move the
stop 30% of the consolidation range *below* the consolidation low, leave the target at the same
price it sits at today, and the shipped book goes from **-1.1R net to +13.4R net** over DEV+VAL on
exactly the same 80 trades.

    stop   = entry - 1.30 x C          (today: entry - 1.00 x C, i.e. the consolidation low)
    target = entry + 2.00 x C          (today: entry + 2.00 x C -- unchanged)

    where C = entry_fill - consolidation_low, the shipped stop distance.

Because the risk got wider, that target is **1.54R**, not 2R. It is the same *price*.

| | trades | gross R | net R | net/trade | win | stopped | max DD (net R) |
|---|---|---|---|---|---|---|---|
| shipped bracket, DEV+VAL | 80 | +7.0 | **-1.1** | -0.014 | 36.2% | 63.7% | -12.6 |
| **proposal, DEV+VAL** | 80 | +19.3 | **+13.4** | **+0.168** | 48.8% | 50.0% | **-6.8** |
| proposal, DEV | 50 | +8.7 | +4.6 | +0.092 | 46.0% | — | -6.8 |
| proposal, VAL | 30 | +10.6 | +8.8 | +0.293 | 53.3% | — | -4.7 |

+$279 net on a $500 account over 166 sessions, 0.48 trades/session, drawdown roughly halved.
**HOLDOUT was never evaluated.**

---

## 0. The harness, and why you can trust it

`lab.py` packs every trade's post-trigger 5-minute path into padded `(n_rows, max_bars)` matrices so
a joint stop x target sweep resolves in milliseconds instead of minutes. Two checks guard it:

- `common.verify_paths()` — **3,639 / 3,639** published `max_r` values reproduced exactly.
- `lab.check_equivalence()` — 10 bracket shapes x 400 random rows resolved both by the fast path and
  by `common.replay_bracket()`: **max |dR| = 0.0**.

`common.py` was not modified. Everything else lives in `spikes/engine_lab/exits/`, steps 1-9.

WARNING: every R in this document is **re-derived from the bars**. The panel's `max_r` is
denominated in the shipped stop's risk and is meaningless the moment the stop moves.

---

## 1. The stop

### The surface, with stop and target properly decoupled

The first sweep (`step2_surface.py`) is misleading in one specific way, and it matters: `target_r`
is denominated in the *bracket's own* risk, so widening the stop moves the target in price even
though the column heading didn't change. "cons+25%, 1.5R" is a target 1.875 C above entry — almost
exactly where the shipped 2R target already sat. That grid cannot separate the two decisions.

`step3_decoupled.py` puts both axes in the same fixed unit, C, which does not move when the bracket
does. Net R per trade, DEV+VAL, shipped selection, 80 trades in every cell:

```
  m \ t     0.5    0.75     1.0    1.25     1.5    1.75     2.0     2.5     3.0     4.0
  0.6    -0.526  -0.468  -0.508  -0.524  -0.480  -0.446  -0.368  -0.537  -0.588  -1.002
  0.8    -0.346  -0.299  -0.338  -0.294  -0.229  -0.212  -0.212  -0.410  -0.423  -0.690
  1.0    -0.192  -0.132  -0.198  -0.142  -0.068  -0.034  -0.014  -0.271  -0.286  -0.560   <- shipped
  1.15   -0.035  -0.004  -0.053  -0.012   0.040   0.083   0.113  -0.215  -0.261  -0.544
  1.25    0.035   0.070   0.007   0.052   0.080   0.124   0.158  -0.141  -0.171  -0.366
  1.4     0.011   0.034  -0.030   0.005   0.053   0.092   0.122  -0.192  -0.223  -0.400
  1.5     0.029   0.031  -0.033  -0.000   0.045   0.082   0.110  -0.162  -0.189  -0.371
  1.75   -0.002  -0.013  -0.063  -0.038   0.022   0.054   0.078  -0.148  -0.169  -0.356
  2.0    -0.026  -0.029  -0.063  -0.063  -0.011   0.016   0.035  -0.148  -0.197  -0.402
  3.0    -0.007  -0.004  -0.011  -0.015   0.005   0.023   0.002  -0.146  -0.195  -0.289
```

Read it as a picture, not as a maximum. There is a **ridge along t = 2.0** and a **broad positive
region for every m above ~1.15**. The cliff is entirely on the **tight** side of the stop and on the
**far** side of the target. Nothing about the shape is sharp except the two edges you should be
staying away from anyway.

### Why the wider stop pays — and it is not a costs artefact

The stop is the only thing that changes; the target price is fixed. That makes the effect
**monotone**: a wider stop is reached later, never earlier, so no trade that used to win can start
losing. Measured on the 80 trades (`step7_diagnostics.py`):

```
loser -> winner : 10        winner -> loser : 0
stayed winner   : 29        stayed loser    : 41
the 10 flipped trades: -11.5R before, +14.8R after
```

Ten setups were being shaken out on noise inside the consolidation and then going on to reach the
same target anyway. The price-path effect is worth **+0.154 R/trade**; lower cost drag adds
**+0.028** (mean risk rises $16.14 -> $18.34 as fewer trades are notional-cap-bound, so the $0.70
commission floor is a smaller share of each R: 0.102R -> 0.074R). **86% of the gain is the price
path**, not the cost model.

### Everything else about stop placement was worse

| stop family | best DEV+VAL net/trade | verdict |
|---|---|---|
| consolidation low x 1.15-1.5 | **+0.168** | the answer |
| a flat % of entry (2%...15%) | -0.002 (at 10%) | never better; the range is a property of the setup, not of the price |
| a fraction of the pole (0.25x...1x) | +0.007 | no |
| a floor and/or ceiling on stop distance | +0.052 | every clamp costs; a ceiling is actively harmful |
| tighter than the consolidation low | -0.86 | catastrophic — 55% of trades become same-bar stops |

The clamps are worth calling out: capping stop distance at 6-12% of entry (which sounds prudent on a
$500 account) is *strictly worse* at every target. On this population the wide-stop setups are the
ones that pay.

---

## 2. The target

`step4_targets.py`, stop held at m = 1.25, fine scan of t in 0.1 steps:

```
  t (xC)  R mult    net/tr     dev     val
  1.7      1.36     +0.104   +0.001  +0.277
  1.8      1.44     +0.113   +0.038  +0.239
  1.9      1.52     +0.120   +0.023  +0.281
  2.0      1.60     +0.158   +0.058  +0.324
  2.1      1.68     +0.129   +0.040  +0.277
  2.2      1.76     +0.095   +0.018  +0.224
  2.3      1.84     +0.018   -0.128  +0.262   <- cliff
  2.5      2.00     -0.141   -0.258  +0.053
  3.0      2.40     -0.171   -0.284  +0.018
```

The plateau is **t in [1.7, 2.2]**, and 2.0 is inside it. The peak at exactly 2.0 is noise on 80
trades; the plateau is the finding. The cliff at 2.3 is real and is the single most fragile thing
here — see section 4.

**Targets in other units, all decidable at the trigger, all worse:**

| target | DEV+VAL net/trade | dev | val |
|---|---|---|---|
| **2.0 x C** | **+0.158** | +0.058 | +0.324 |
| min(2C, pre-market high x 1.5) | +0.144 | +0.045 | +0.310 |
| pre-market high x 2.0 | +0.091 | +0.049 | +0.162 |
| 5% of entry | +0.080 | +0.022 | +0.178 |
| 0.40 x pole height | +0.038 | -0.022 | +0.138 |
| 8% of entry | -0.062 | -0.250 | +0.252 |
| 1.0 x pole height | -0.302 | -0.532 | +0.080 |

The pole is a bad ruler for the target — measured-move logic ("the flag runs as far as the pole")
does not describe these moves at all; 1x pole wins only 16% of the time. A percentage of entry is
mediocre because it ignores how volatile the individual setup is. The pre-market high does about as
well as 2C but needs an extra parameter and a fallback rule for names already trading above it.
**The consolidation range is the right ruler, and 2x is the right multiple** — which is exactly
where the shipped target already is. The target was never the problem.

---

## 3. The interaction

They are one decision and they are not independent — but the dependence is mild and it has an
interpretable shape. Along the t = 2.0 ridge the whole range m in [1.15, 1.75] is positive on
DEV+VAL and m in [1.15, 1.5] is positive on DEV *and* VAL separately. Along the m = 1.3 ridge,
t in [1.7, 2.2] is positive. The joint plateau is roughly a rectangle:

    m in [1.15, 1.5]   x   t in [1.7, 2.2]

**m = 1.30 was chosen as the middle of that rectangle, not as its maximum.** (m = 1.25 is the
grid-search maximum; 1.30 scores marginally better on both halves and sits further from the tight-
side cliff, which is where all the danger is.)

The economics of the trade-off are simple: raising m raises the win rate but dilutes each winner as
2/m. Win rate goes 36.2% (m=1.0) -> 48.8% (m=1.3) -> 53.7% (m=2.0) while the winner shrinks 2.0R ->
1.54R -> 1.0R. m ~ 1.3 is where the marginal win stops paying for the dilution.

---

## 4. Anti-overfit evidence

**Complexity budget: 2 thresholds** (m and t), both in a unit the setup already provides. No new
data is required, no new column, no new gate. The bracket adds nothing to selection.

### Walk-forward (`step5_robustness.py`) — six expanding-window blocks

| | blocks positive | total net R | net/trade |
|---|---|---|---|
| bracket fixed at (1.30, 2.00) | **4 / 6** | +13.5R | +0.242 |
| bracket **refit** on each training window | **4 / 6** | +10.1R | +0.180 |
| shipped bracket, same blocks | 3 / 6 | -0.5R | -0.009 |

The refit run is the important one: at every block, (m, t) is chosen by grid search on the training
window only and then traded blind. It picked **m in {1.2, 1.3} in all six blocks and t in {1.75,
2.0} in all six**. The search lands in the same place regardless of which slice of history it is
given — that is much stronger evidence than the single in-sample fit.

The one losing block (2026-03-19 .. 2026-04-14, -3.8R over 8 trades) loses under every bracket
tested, including the shipped one.

### Sensitivity, +/-20% on each parameter alone

```
stop m    x0.8 = 1.04   +0.038  (dev -0.051 / val +0.186)
          x0.9 = 1.17   +0.101  (dev +0.046 / val +0.193)
          x1.0 = 1.30   +0.168  (dev +0.092 / val +0.293)
          x1.1 = 1.43   +0.109  (dev +0.041 / val +0.223)
          x1.2 = 1.56   +0.087  (dev -0.005 / val +0.241)
target t  x0.8 = 1.60   +0.103  (dev -0.004 / val +0.280)
          x0.9 = 1.80   +0.123  (dev +0.070 / val +0.211)
          x1.0 = 2.00   +0.168  (dev +0.092 / val +0.293)
          x1.1 = 2.20   +0.073  (dev +0.000 / val +0.195)
          x1.2 = 2.40   -0.082  (dev -0.233 / val +0.171)
```

**One sign flip, on the target's high side (t = 2.4).** Everything else stays positive on DEV+VAL.
That flip is the 2.3-C cliff from section 2 and it is the honest weak point of the proposal: nine or
so trades in the book have their maximum excursion between 2.0 C and 2.4 C, and pushing the target
past them converts each from a +1.54R winner into a -1R loser. On 80 trades that is enough to flip
the sign. **Set the target at or below 2.0 C, never above.**

### Session bootstrap instead of a permutation test

The README's `permutation_pvalue()` resamples *which rows get selected*. This proposal does not
touch selection — it books the identical 80 trades — so a selection permutation tests nothing about
it. The equivalent test is resampling **whole sessions** (not trades: a day's two trades are not
independent):

    2,000 resamples of 66 sessions: observed +0.168, 90% band [-0.071, +0.410],
    positive in 87% of resamples.

Positive but not overwhelming. On 80 trades it cannot be.

### Per-source: **not testable, and nobody should pretend otherwise**

`source` and `split` are perfectly collinear by construction. Recon covers 2025-10-30 .. 2026-06-30
(= DEV + VAL) and live covers 2026-07-01 .. 2026-08-13 (= HOLDOUT). **Every DEV+VAL trade is recon.**
The recon-vs-live check the README mandates *is* the holdout check, and spending it is exactly what
the split rule forbids. This is a structural limitation of the panel, not something to work around.

### Concentration, and the month-by-month record

```
prop: 25-10:+0.3(2)  25-11:-1.4(6)  25-12:-1.1(8)  26-01:+2.3(10)  26-02:+3.2(4)
      26-03:+1.6(8)  26-04:-0.4(12) 26-05:+10.4(14) 26-06:-1.6(16)
ship: 25-10:+0.8(2)  25-11:-0.6(6)  25-12:-2.9(8)  26-01:-2.0(10)  26-02:+1.2(4)
      26-03:+3.2(8)  26-04:-7.4(12) 26-05:+5.5(14)  26-06:+1.0(16)
```

WARNING: **May 2026 is +10.4R of the +13.4R total.** Ex-May the proposal is +3.0R over 66 trades
(+0.046/trade) — barely positive. That is the biggest caveat in this document.

The mitigation is that the *improvement* is broad even where the *level* is not: over the same
ex-May 66 trades the shipped bracket is **-6.6R**, so the change is worth +9.6R outside its best
month. The proposal beats the shipped bracket in 5 of 9 months and by +18.6R against -3.9R in
aggregate.

Concentration within trades is not a problem, because a fixed target caps every winner at +1.54R:
best trade +1.51R, worst -1.20R. Remove the three best trades and it is still +8.9R over 77; remove
the five best and +5.9R over 75. **This is not a tail-driven result** — which is unusual and good
for a strategy on a population whose MFE distribution has a mean of 1.9R and a median of 0.5R.

### It is not an artefact of the capacity cap

    1/day: shipped +0.082 (+5.4R)   proposal +0.200 (+13.2R)
    2/day: shipped -0.014 (-1.1R)   proposal +0.168 (+13.4R)
    3/day: shipped -0.040 (-3.3R)   proposal +0.169 (+13.8R)

---

## 5. Does it survive a different selection? (the important caveat)

Agent A may replace selection entirely, so this was tested on a ladder of seven selections
(`step6_selection.py`). Net R per trade, DEV+VAL:

| selection | n | shipped bracket | proposal (1.30, 2.00) | delta | that selection's own best (m, t) |
|---|---|---|---|---|---|
| SHIPPED | 80 | -0.014 | **+0.168** | **+0.182** | (1.25-1.3, 2.0) — the same place |
| SHIPPED, stop_pct floor rescaled to 2.0% | 88 | -0.134 | -0.021 | +0.114 | (1.25, 2.0) |
| `passed` only | 178 | -0.552 | -0.235 | +0.317 | (3.0, 1.0) -> -0.168 |
| SHIPPED minus `passed` | 311 | -0.294 | -0.228 | +0.066 | (3.0, 1.0) -> -0.018 |
| price / time / stop_pct only | 323 | -0.297 | -0.258 | +0.039 | (3.0, 1.0) -> -0.024 |
| stop_pct >= 2.5% only | 332 | -0.504 | -0.351 | +0.153 | (3.0, 1.0) -> +0.004 |
| RAW POOL | 332 | -0.497 | -0.319 | +0.178 | (3.0, 1.0) -> +0.004 |

Three things fall out of this table, and they should be read in this order:

1. **The direction is universal.** The proposal beats the shipped bracket on *every* selection
   tested, by between +0.039 and +0.317 R/trade. "The shipped stop is too tight" is not conditional
   on the shipped rules.
2. **The magnitude is not.** Looser selections want m ~ 2-3, not 1.3. If selection changes, **m must
   be re-fitted**; t = 1.0-2.0 C is stable but m is not. The tighter the selection, the tighter the
   optimal stop — which makes sense, since a stricter shape gate means the consolidation low is a
   more meaningful level.
3. **Only the shipped selection is positive in level.** The raw pre-market pool's best possible
   simple bracket is **+0.004 net R/trade** (m = 3.0, t = 1.0: 332 trades, +21.7R gross, +1.2R net,
   76.8% win). Bracket geometry takes the raw pool from -0.50 to break-even and no further.
   **Exits cannot rescue this population on their own.** That is Agent A's job, and this result says
   the ceiling on exits alone is roughly zero.

### The one entanglement worth flagging explicitly

SHIPPED filters on `stop_pct >= 0.025`, and `stop_pct` is measured against the **shipped** stop. So
widening the stop to 1.30 C silently raises the effective floor to 3.25% of entry. Lowering the
filter to 2.0% so the *bracket's* stop still clears 2.5% adds 8 trades and takes the result from
**+0.168 to -0.021**. Those 8 marginal narrow-consolidation setups are worth about -1.5R each.

Read the right way round, that is a finding rather than a fragility: **the minimum-stop-width filter
is load-bearing and should be stated against whichever stop is actually used.** But it does mean the
headline number depends on a selection threshold I was told to hold fixed, so it is stated here
rather than buried.

---

## 6. The three exposure questions

### Same-bar (`replay_bracket` books a bar containing both entry and stop as a loss)

| | trades resolved by the assumption | net/trade if all of them had won instead |
|---|---|---|
| shipped bracket | **13 / 80 (16.3%)** | — |
| **proposal** | **4 / 80 (5.0%)** | +0.291 (vs +0.168) |

**The proposal cuts same-bar exposure by two thirds.** This is the direction you want: a wider stop
is hit later, so fewer trades land in the one place where the replay is making an assumption rather
than a measurement. #583 found the conservative reading wrong 38% of the time; applying that to 4
trades moves the result by roughly +0.05 R/trade **in the proposal's favour**, so the assumption is
working against the proposal, not for it. It is not manufacturing the improvement.

(For contrast: a *tighter* stop at m = 0.6 puts 55% of trades into the same-bar case, which is why
no tight-stop number in section 1 should be believed even though they are all clearly negative
anyway.)

### The conservative entry fill (+3 ticks above the trigger, never better than the bar open)

On **3 of 80 trades (3.8%)** the fill lands above the entry bar's entire high — a price that never
printed. Excluding them, net/trade is **+0.152 vs +0.168**: the assumption is worth about -0.016
R/trade, i.e. **the result does not rest on it**. Nothing here tries to fix it; the conservatism is
deliberate and it is currently costing about 10% of the edge.

WARNING: this exposure is *selection-dependent and much larger on looser pools*: 24.4% of raw-pool
trades and 26.5% of the `stop_pct >= 2.5%` pool have a fill above the entry bar's high, because
those pools contain cheap names where 3 ticks is a large move. Any conclusion drawn on the raw pool
— including my own section 5 conclusion that its ceiling is zero — carries that caveat.

### 09:30 — the pre-market trade that is still open when the bell rings

- **4 of 80 trades (5.0%)** are still live at 09:30. Median hold is 3 bars (15 min); 51% resolve
  inside 15 minutes and 84% inside an hour.
- **None of the four gapped through the stop at the open** — the 09:30 opens printed at -0.31R,
  -0.32R, +0.70R and -0.69R against entries whose stops sat at -1.00R. So in this sample the
  overnight-style gap risk did not materialise, but **n = 4 is not evidence of safety**, it is
  evidence that the exposure is small. A pre-market position carried into the open is the one place
  a stop can be filled far worse than it is placed, and 5% of the book is exposed to it.
- Their eventual outcomes were -1.00R, -0.68R, +1.54R, +1.54R.
- **A mechanical flatten at the 09:30 open costs +13.4R -> +7.5R** (+0.168 -> +0.093 per trade). It
  stays positive, so it is available as insurance, but it costs 44% of the edge on this evidence.
  Recommendation: **leave the bracket running.** Revisit if a gap-through ever actually happens —
  one bad one would change this arithmetic quickly.

---

## 7. What the forbidden exits would have been worth (context only, not proposable)

Gross R per trade, same 80 trades, same 1.30 C stop:

```
  proposed simple bracket                  +0.242
  time stop after 60 min                   +0.261     (no real help)
  breakeven stop once +1.0R is seen        +0.376     (+55%)
  scale half out at +0.75R                 +0.399
  breakeven stop once +0.5R is seen        +0.563     (+133%)
  perfect foresight: exit at the MFE       +1.971     (the ceiling on any exit rule)
```

The simple bracket captures about **12% of the theoretical maximum**. The single highest-value
relaxation of the one-OCA-order constraint would be a **breakeven stop after +0.5R**, worth roughly
+0.32 gross R/trade — more than doubling the edge — because half of these trades show a favourable
excursion and then give it all back. Trailing stops and time stops add nothing. This is stated as
context for a later decision only; **the proposal is a plain bracket.**

---

## 8. Honest summary of what is and is not established

**Established.**
- The shipped stop is too tight, and this is the largest single lever found: the same 80 trades go
  from -1.1R to +13.4R net with no change to selection, capacity, sizing or the target price.
- The effect is mechanical and monotone (10 loser->winner flips, 0 the other way), 86% price path
  and 14% lower cost drag, not tail-driven, and it survives 1/2/3-a-day capacity caps.
- The direction survives every selection tested, including the raw pool.
- The consolidation range is the correct unit for both legs. Percentages of entry, pole multiples
  and clamps are all worse.
- The proposal *reduces* dependence on the two conservative assumptions in the replay.

**Not established.**
- That +0.168/trade is the right number. 80 trades, 2 parameters, and May 2026 is +10.4R of the
  +13.4R. Ex-May it is +0.046/trade. Treat the *direction* as solid and the *level* as a wide band —
  the bootstrap's 90% band is [-0.071, +0.410].
- That m = 1.30 transfers to a different selection. It does not; only the direction does.
- That any of this makes the wider pre-market population tradeable. It does not. The best simple
  bracket on the raw pool is break-even.
- That it works on live data. Live *is* the holdout; that test has not been run and must not be.

**If this is taken forward**, the two numbers to re-fit against whatever selection wins are `m`
(sensitive) and the `stop_pct` floor (load-bearing, and it must be stated against the bracket's
stop, not the consolidation low).

---

## Files

    lab.py                  packed-path harness, Bracket/Geom, vectorised resolve, equivalence check
    step1_baseline.py       reconciles with the published baseline; MFE and stop-width distributions
    step2_surface.py        stop x target sweep, four stop families, two selections
    step3_decoupled.py      the same surface with both axes in units of C (the one to read)
    step4_targets.py        fine target scan + %-of-entry / pole / pre-market-high targets
    step5_robustness.py     walk-forward (fixed and refit), sensitivity, bootstrap, exposures
    step6_selection.py      the seven-selection ladder
    step7_diagnostics.py    mechanism: which trades flip, gross vs net, month by month, cap
    step8_open_and_bounds.py  the 09:30 open + upper bounds for the ruled-out exits
    step9_result.py         emits data/spikes/engine-lab/exits/result.json

Outputs: `data/spikes/engine-lab/exits/` — `result.json` (the proposal), plus `surface.json`,
`decoupled.json`, `targets.json`, `robustness.json`, `selection_robustness.json`,
`diagnostics.json`, `open930_and_bounds.json`, and `pretrigger.parquet` (a cache of each row's
pre-trigger high, built once from the bar store).
