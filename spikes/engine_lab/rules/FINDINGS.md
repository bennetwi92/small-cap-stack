# Agent A — selection rules. Which pre-market setups should we take?

**Verdict: no selection rule clears the bar.** One candidate is worth a single holdout look in the
synthesis step, clearly labelled unproven. Everything else in this population is noise, and two of
the panel's most promising-looking features turn out to be lookahead.

Population: the 3,639-row combined pre-market panel, minus HOLDOUT — **2,989 rows over 166
sessions**, all of it `recon`. Exits fixed at 2R/-1R (`fixed_target_r`), capacity fixed at 2/day
earliest-first (`build_book`), sizing and costs at the `common` defaults. HOLDOUT was never loaded
into any evaluation; `lab.no_holdout()` strips it at the top of every script.

---

## 0. Three things that change how the whole lab should read its data

### 0.1 ⚠️ Two columns in `TRIGGER_TIME_SAFE` are lookahead on this population

Both sit at the top of any naive feature ranking, which is exactly what a leak looks like.

| column | why it is not decidable at trigger time |
|---|---|
| **`first_rank`** | In `recon` — 100% of DEV and VAL — rank comes from `harvest/prefilter.py::_ranked`, which sorts the day's candidates by `day_change_pct = day_high / prev_close - 1` **off the daily bar**. Rank 1 means "biggest mover of the whole session". Measured within-day rank correlation against `day_high/day_open` is **-0.59**. It is also not the same quantity as live `first_rank` (IBKR's intraday `TOP_PERC_GAIN` position at appearance), so it could not transfer even if it were causal. Its top decile shows **+0.195R gross** against -0.53R in the bottom — the strongest "signal" in the panel, and entirely fake. |
| **`n_scanner_hits`** | `spikes/regime_panel.py` sets it to `osub.height` — every scanner hit for that opportunity across the **whole capture window**, including hits after the trigger. The causal cousin `hits_before_trigger` is in the panel and carries far less (top decile +0.115R gross vs -0.065R). |

`common.py` is deliberately **not** edited — three agents share it and adding these to
`OUTCOME_COLS` would start raising inside a sibling's run mid-flight. They are excluded by name in
`rules_def.LEAKY` instead. **The other two agents should check whether they used either.**

### 0.2 ⚠️ `source` is perfectly confounded with `split`

    dev      2,012 rows   125 sessions   100% recon
    val        977 rows    41 sessions   100% recon
    holdout    650 rows    31 sessions   100% live

The README requires that any rule "work on both the `recon` and `live` halves". **That check is
impossible without spending the holdout**, because the live half *is* the holdout. Nothing below has
been validated against live data, and no rule from this lab can be, until the holdout is opened.
This is a structural limit on the whole exercise, not a shortcoming of this agent's work.

### 0.3 Selection has very little leverage under a 2-a-day, earliest-first cap

With ~18 candidates a session and a cap of 2, a filter only changes the book when it removes an
*early* row. Overlap between the filtered book and the completely unfiltered book:

| filter | rows kept | booked trades | share of book identical to no filter |
|---|---|---|---|
| `stop_pct >= p50` | 50% | 329 | 56% |
| `pole_pct >= p50` | 50% | 330 | 51% |
| `pole_pct >= p90` | 10% | 187 | 17% |
| `passed` | 7% | 178 | 17% |
| `SHIPPED` | 3% | 80 | 15% |

A filter has to cut to **under ~10% of rows** before it is really choosing anything. That is why
every mild threshold in §2 is worthless, and it also means **selection and capacity cannot be
tuned independently** — the risk agent's cap choice changes how much leverage any rule has.

---

## 1. What carries signal, and what does not

Row-level mean R at the fixed 2R bracket, by decile, DEV and VAL reported separately
(`step1_characterise.py`, full tables in `data/spikes/engine-lab/rules/step1_bands.json`).
Base rate on DEV+VAL: **-0.249R gross, -0.530R net, 25.0% win**.

**Carries something (gross R, so cost-free), all consistent in direction across DEV and VAL:**

- **The "already moved" family** — `pole_pct`, `ext_at_peak`, `runup_pre_appearance`,
  `ext_at_trigger`. Roughly monotone: the bottom decile is around -0.40R gross, the top around
  -0.15R. They are near-duplicates of each other (rank correlations 0.63-0.85) and of `stop_pct`
  (0.48-0.72), so they are one feature, not four. **None of them ever reaches positive.**
- **`shares_outstanding`** — the top decile (>244M shares) is -0.46R gross against -0.08R for the
  bottom. Null on 685 of 2,989 rows, which makes it unusable as a hard gate.
- **`cons_vol_reducing`** — True is -0.218R gross, False -0.310R. Small but consistent.
- **`cons_len`** — shorter is better, -0.24R at 1-2 bars against -0.30R at 6+.

**Carries nothing.** `score` (the shipped composite: bottom decile -0.368R, top decile -0.237R,
no ordering in between), `retracement`, `pole_len`, `vol_share_pole`, `range_before_pole_pct`,
`cum_volume_to_trigger`, `bars_before_pole`, `first_hit_et_min`, `pole_has_big_green`,
`halted_consolidation`. `cycle_num` and `untraded_cons_bars` are degenerate on the pre-market cut.

**Carries something that is really a cost effect.** `planned_risk` and `stop_pct` have the largest
correlations with *net* R in the whole panel (+0.66 and +0.48) and almost none with gross. That is
arithmetic, not edge — see §3.

**Time of day** is a weak U: 07:05-07:30 and 08:15 onward are around -0.18R gross, the 07:05-08:15
middle around -0.40R. It does not survive as a threshold.

## 2. `passed` is worse than useless, and now we know which gate does the damage

`failing_gates` names seven shape gates. For each, the difference between rows that pass it and
rows that fail it (positive = the gate earns its place):

| gate | rows failing | gross R (fail) | gross R (pass) | value of the gate | DEV | VAL |
|---|---|---|---|---|---|---|
| **`pole_height`** | 133 | -0.391 | -0.243 | **+0.148** | +0.197 | +0.110 |
| **`cons_len`** | 594 | -0.313 | -0.233 | **+0.080** | +0.055 | +0.137 |
| `vol_peak_gt_cons` | 670 | -0.252 | -0.248 | +0.004 | +0.002 | +0.007 |
| `peak_green` | 949 | -0.248 | -0.250 | -0.002 | +0.062 | -0.109 |
| `cons_retracement` | 2,586 | -0.246 | -0.270 | -0.025 | -0.041 | -0.001 |
| `wick_peak` | 1,182 | -0.223 | -0.266 | -0.043 | -0.023 | -0.100 |
| `cons_holds_base` | 1,173 | -0.220 | -0.268 | -0.048 | +0.019 | -0.216 |

Only **`pole_height`** and **`cons_len`** are worth anything, and only `cons_len` is selective
enough to matter. `cons_retracement` — which eliminates **87% of the pool on its own** — is worth
-0.03R, and `wick_peak` and `cons_holds_base` are actively negative in both splits. And the count of
gates failed is flat (0 failed: -0.271R; 5 failed: -0.132R), so "more malformed" does not mean
"worse trade".

That is the whole explanation for why `passed` is worse than the raw pool: it is dominated by a gate
that shreds the sample without discriminating, plus two that point the wrong way.

**Recommendation: do not use `passed` for selection.** If a shape gate is wanted, use
`pole_height` and `cons_len` and drop the other five. As a *filter*, though, even those two are
worthless — `pole_height AND cons_len` books 250 DEV trades at -0.502 net R per trade, because per
§0.3 they are nowhere near selective enough to change the book. Their value is as components
inside a much tighter rule.

## 3. Net R is mostly a stop-width decision, not a setup decision

Cost drag as a fraction of R, by stop width, on the $500/5%/50% account:

| stop % | rows | cap-bound | median $ risk | cost (R/trade) | gross R | net R |
|---|---|---|---|---|---|---|
| < 2% | 387 | 100% | $3.06 | **0.543** | -0.271 | -0.814 |
| 2-4% | 660 | 100% | $7.47 | 0.379 | -0.355 | -0.733 |
| 4-6% | 636 | 100% | $12.18 | 0.306 | -0.302 | -0.608 |
| 6-8% | 364 | 100% | $17.20 | 0.207 | -0.184 | -0.391 |
| 8-10% | 277 | 97% | $22.23 | 0.160 | -0.134 | -0.293 |
| 10-13% | 252 | 0% | $24.89 | 0.123 | -0.036 | -0.159 |
| 13-16% | 147 | 0% | $24.86 | 0.101 | -0.143 | -0.244 |
| 16-22% | 152 | 0% | $24.79 | 0.076 | -0.270 | -0.345 |
| > 22% | 114 | 0% | $24.64 | 0.058 | -0.342 | -0.400 |

The crossover at ~10% is where the 50% notional cap stops binding and dollar risk finally reaches
the intended $25. **Below it, a $0.35/side commission minimum eats 15-54% of every R.** Any rule
that improves net without improving gross is a sizing decision wearing a selection costume — worth
having, but it belongs to the risk agent, and it must not be counted twice.

Gross R is *not* monotone in stop width: it peaks at 10-13% and falls away again. So "just take
wide stops" is not the answer either.

## 4. The search, and why almost all of it is noise

### 4.1 No single threshold works

421 univariate cuts (every decile, both directions, every candidate feature, plus the booleans and
the seven gates), fitted on DEV and booked at 2/day: **zero are net-positive on DEV.** The best is
-0.328R per trade. `step4_sweep.py`.

### 4.2 A free four-clause search finds beautiful rules that are not real

Greedy forward selection over ~200 candidate clauses, <=4 clauses, maximising booked net R per trade
on DEV subject to a minimum trade frequency, produced e.g.

    planned_risk >= 0.19 AND hits_before_trigger <= 2 AND cons_len <= 3 AND rvol_pole >= 0.47
    DEV  45 trades, +0.274 net R/trade      VAL  21 trades, +0.768 net R/trade

Both splits positive, a clean sensitivity table, a permutation p-value of 0.003. And it is not real:

- **Walk-forward of the procedure** — re-run the whole search on each expanding training block and
  trade the next one — is **-0.13 net R per trade, 2 of 6 blocks positive**. Every clause budget
  fails: 1 clause -0.498 (0/6), 2 clauses -0.219 (2/6), 3 clauses -0.148 (2/6), 4 clauses -0.130
  (2/6). *Out-of-sample performance improves as the budget grows*, which is the opposite of what a
  real edge does and is the signature of a search fitting the training block ever more tightly.
- **Shuffled-outcome null.** Permute outcomes within each session — keeping the calendar, the pool
  and the capacity cap, destroying only the setup-to-outcome pairing — and re-run the identical
  search. It invents **+0.080 net R per trade on average, +0.250 at the 90th percentile, +0.381 at
  worst**. The observed +0.274 sits inside that band. Twenty null draws are in `step6_null.py`'s
  output; they read exactly like plausible trading rules ("`score >= 0.61` and
  `bars_before_pole >= 47` and `range_before_pole_pct <= 0.21`").

**DEV-and-VAL-both-positive is not evidence here.** That is the single most important methodological
result of this investigation, and it applies to the other two agents' searches too.

### 4.3 What does not immediately die

Two things push the other way and are why §5 exists rather than a flat negative:

- Of 334 **random** 3-clause conjunctions, **none** was net-positive on DEV, and a rule's DEV net R
  correlates with its VAL net R at **+0.44**. The surface is not pure noise.
- If the *feature set* is fixed first and only the thresholds are refit, the shuffled-outcome null
  collapses: mean -0.305, 99th percentile +0.003, max +0.007 over 100 draws. Against an observed
  +0.187 on DEV, that is a p-value below 0.01 — **but it is not a valid one**, because the feature
  set was itself picked by the unrestricted greedy on the same DEV data. It is quoted here as the
  best case for the candidate, not as proof.

---

## 5. The one candidate, and exactly how much to believe it

    hits_before_trigger <= 2   AND   planned_risk >= $0.19   AND   cons_len <= 3

Three thresholds. In words: *take the break only if the name has been surfaced by the scanner at
most twice before it triggers, the stop is at least 19 cents per share away, and the consolidation
is three bars or shorter.*

| | trades | /session | gross R | net R | net/trade | win | max DD (net R) |
|---|---|---|---|---|---|---|---|
| **DEV** | 48 | 0.38 | +15.0 | +9.0 | **+0.187** | 43.8% | -6.7 |
| **VAL** | 27 | 0.66 | +18.0 | +15.5 | **+0.575** | 55.6% | -3.3 |
| **DEV+VAL** | 75 | 0.45 | +33.0 | +24.5 | **+0.327** | 48.0% | -7.2 |
| *SHIPPED, same window* | 80 | 0.48 | — | -0.8 | -0.014 | 36.3% | -12.6 |

**Complexity budget: 3 of the allowed 5.** `hits_before_trigger <= 2` cuts the pool to 13% and is
the clause that gives the rule any leverage at all (§0.3). `planned_risk >= 0.19` is a cost rule —
it removes the sub-$0.19 stops where commission eats 25%+ of R (§3). `cons_len <= 3` is the one
shape gate from §2 with positive value in both splits.

### Anti-overfit battery, in full

| test | result | passes? |
|---|---|---|
| **Walk-forward, procedure refit** (`walk_forward` + greedy) | -0.055 net R/trade, 2/6 blocks positive | FAIL |
| **Walk-forward, thresholds held fixed** | +0.309 net R/trade, 4/6 blocks positive — but four of the six blocks are inside the DEV the thresholds were fitted on, so this is not out of sample | not evidence |
| **Six calendar blocks over DEV+VAL, fixed** | 6/6 positive: +5.5(11) +1.9(9) +1.2(12) +1.0(10) +3.7(13) +11.3(20) | PASS |
| **Monthly** | 7 of 9 months positive; +15.5R of the +24.5R total comes from May and June 2026 | concentrated |
| **Sensitivity ±20%** (`sensitivity`) | net R/trade stays +0.40 to +0.48 on every leg | **vacuous** — ±20% on an integer threshold of 2 rounds back to 1 and 2 |
| **Real sensitivity** (the 100-cell threshold surface) | `hits<=1`: 15/20 cells positive. `hits<=2`: 15/19. `hits<=3`: ~8/20. **`hits<=4`: 0/20. `hits<=6`: 1/20.** | FAIL — a cliff, not a plateau |
| **Permutation** (`permutation_pvalue`, same trade count, same days) | p = 0.003 | PASS |
| **Shuffled-outcome null, restricted search** | p < 0.01 (100 draws, null max +0.007) | PASS but see §4.3 — invalid, the features were chosen on the same data |
| **Shuffled-outcome null, honest unrestricted search** | observed +0.19 inside a null band that reaches +0.38 | FAIL |
| **Session bootstrap, 4,000 draws** | DEV+VAL +0.327, 95% CI **[+0.013, +0.647]**, P(<=0) = 2.2% | only just excludes zero |
| **Per-source (recon vs live)** | **impossible** — see §0.2 | cannot be run |
| **Frequency** | 0.45 trades/session against a 0.5 target | marginal |

### The reason not to believe it

**There is no main effect.** `hits_before_trigger` on its own is worthless — net -0.529 R per trade,
statistically identical to the pool — and by exact value it does not order at all:

| hits before trigger | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8+ |
|---|---|---|---|---|---|---|---|---|
| n | 215 | 161 | 133 | 162 | 214 | 143 | 120 | 1,841 |
| net R/trade | -0.45 | -0.64 | -0.48 | -0.68 | -0.42 | -0.36 | -0.62 | -0.54 |

`planned_risk >= 0.19 AND cons_len <= 3` on its own is -0.259 net R per trade over 1,010 rows. The
edge appears **only** in the three-way conjunction, over **78 of 2,989 rows**. The brief said to be
very sceptical of interactions at this sample size, and this is a pure interaction with no main
effects at all. It is the archetype of a lucky cell.

### The reason not to dismiss it outright

The threshold break reproduces **independently in each split**, at the same place:

| hits, within `risk >= 0.19 AND cons_len <= 3` | 1 | 2 | 3 | 4 | 5+ |
|---|---|---|---|---|---|
| DEV net R/trade (n) | +0.17 (21) | +0.16 (28) | -0.56 (20) | -0.47 (30) | -0.40 (533) |
| VAL net R/trade (n) | +0.56 (20) | +0.57 (9) | +0.03 (13) | -0.39 (16) | -0.13 (320) |

Both splits are positive at 1 and 2 and negative from 4 on, having been fitted only on DEV. The cell
counts are 9 to 30, so this is suggestive and nothing more — but it is not the pattern a single
fluke produces.

There is also a coherent trading story, which matters when the statistics are this thin: a name that
breaks out within a minute or two of first hitting the scanner is being caught on its *first* move,
whereas a name that has been on the scanner for twenty scans is one whose move you are late to.
Live scans every 60s (`tick_interval_sec = 60`) and recon emits hits per qualifying minute bar, so
the feature is at least measured on a comparable scale in both stores — which is more than can be
said for `first_rank`.

### Dependence on the other two agents' variables

- **Capacity: none.** Net R per trade is 0.333 / 0.327 / 0.310 / 0.310 at caps of 1 / 2 / 3 / 5.
  The rule is selective enough that the cap barely binds. The risk agent can move the cap freely.
- **Exit: moderate, and worth flagging.** Net R per trade by fixed target: 1R +0.146, 1.5R +0.155,
  **2R +0.327**, 2.5R +0.186, 3R +0.211, 4R -0.265. Positive across 1R-3R, so it is not an artefact
  of one exit — but it peaks exactly at the 2R it was fitted at, and dies at 4R. **If the exits agent
  lands on a target outside 1R-3R, this rule must be re-checked, not assumed.**

### The frequency / quality curve

| rule | trades | /session | net R/trade | DEV | VAL |
|---|---|---|---|---|---|
| `hits<=1 & risk>=0.15 & cons<=3` | 52 | 0.31 | **+0.433** | +0.419 | +0.448 |
| `hits<=2 & risk>=0.19 & cons<=3` **(proposed)** | 75 | 0.45 | +0.327 | +0.187 | +0.575 |
| `hits<=2 & risk>=0.15 & cons<=3` | 93 | 0.56 | +0.315 | +0.174 | +0.560 |
| `hits<=2 & risk>=0.15 & cons<=4` | 108 | 0.65 | +0.162 | -0.041 | +0.535 |
| `hits<=2 & risk>=0.10 & cons<=3` | 124 | 0.75 | +0.143 | -0.044 | +0.481 |
| `hits<=3 & risk>=0.10 & cons<=3` | 166 | 1.00 | +0.120 | -0.104 | +0.586 |
| SHIPPED | 80 | 0.48 | -0.014 | -0.153 | +0.217 |
| `hits<=2` alone | 234 | 1.41 | -0.555 | -0.679 | -0.268 |

The proposed point is chosen for frequency, not for its DEV number. `hits<=1 & risk>=0.15` is the
most *consistent* variant (+0.42 DEV, +0.45 VAL — the only one where the two splits agree closely)
but trades only 0.31/session. `hits<=2 & risk>=0.15 & cons<=3` at 0.56/session is the natural pick
if the synthesis wants to clear 0.5 trades/session; it costs about 0.01 R/trade against the
proposal and is a slightly looser threshold, which is usually the safer side to err on.

---

## 6. Where this is fragile — read this before shipping anything

1. **It is one interaction over 78 rows.** No main effect anywhere. That is the single biggest
   reason to disbelieve it.
2. **The honest walk-forward loses money.** Refitting the rule as the record accumulates is -0.055
   R/trade. The thresholds are not identified: fold by fold the search picks `hits<=5`, `hits<=4`,
   then `hits<=2` three times, and `planned_risk` between 0.14 and 0.20.
3. **`hits<=2` is a cliff, not a plateau.** One step out to `hits<=3` roughly halves the edge; two
   steps to `hits<=4` kills it entirely. The `sensitivity()` battery *passes* only because ±20% on
   an integer 2 is a no-op — do not read that as a plateau.
4. **Nothing here has ever seen live data.** DEV and VAL are 100% recon. Whether `hits_before_trigger`
   behaves the same way when the hits come from a real IBKR scanner poll rather than a reconstructed
   minute tape is untested and untestable before the holdout.
5. **Half the total R comes from the last two months.** May and June 2026 contribute +15.5 of +24.5.
6. **Frequency is 0.45/session against a 0.5 target.** Use the `risk >= 0.15` variant if the
   frequency has to be met.
7. **VAL was consulted about four times** (the greedy output, the fixed-rule ladder, the six-block
   table, the final curve). Not tuned against, but not untouched either.

**Recommendation to synthesis:** carry `hits_before_trigger <= 2 AND planned_risk >= 0.19 AND
cons_len <= 3` (or the `>= 0.15` variant for frequency) into the one composed holdout evaluation,
labelled as a low-confidence candidate. Do **not** ship it on this evidence, and do not treat its
DEV+VAL numbers as an expectation. Drop `passed` regardless of what happens to the rest — that
finding is solid and independent of the candidate.

---

## Files

    lab.py                  helpers on top of common.py; per-row net R, splits, bands
    rules_def.py            the feature vocabulary + the two leaky columns, documented
    search.py               the greedy procedure (written as a procedure so it can be walk-forwarded)
    step1_characterise.py   every trigger-time feature by decile, DEV vs VAL
    step2_passed.py         decomposition of `passed` into its seven gates
    step3_costs.py          where the cost drag lives; the stop-width / notional-cap crossover
    step4_sweep.py          421 univariate booked thresholds
    step5_greedy.py         the free search + its walk-forward
    step6_null.py           shuffled-outcome null; clause-budget walk-forward
    step7_battery.py        random-conjunction test; book leverage; sensitivity/permutation
    step8_restricted.py     fixed-feature-set procedure; the fixed-rule ladder
    step9_verdict.py        100-draw null; fold-by-fold thresholds; bootstrap; threshold surface
    step10_final.py         interpretation; target and cap dependence; the frequency curve
    emit_result.py          writes data/spikes/engine-lab/rules/result.json

Rerun in order; each writes its own JSON to `data/spikes/engine-lab/rules/`. Total runtime is about
twenty minutes, almost all of it in the null tests.
