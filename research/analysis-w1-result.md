# W1 result — selection × exit: **a documented null**

**Status:** LIVE (2026-09-19). Stage-3 of workstream **W1** of the three-agent analysis of the
2-year Phase-1 record. Issue [#737](https://github.com/bennetwi92/small-cap-stack/issues/737);
ledger [#735](https://github.com/bennetwi92/small-cap-stack/issues/735).

Plan: [`analysis-plan-1-selection.md`](./analysis-plan-1-selection.md) — this document executes its
**§9** gate against the evidence produced under §3–§8. Shared contract:
[`analysis-protocol.md`](./analysis-protocol.md). Prompt for this session:
[`analysis-w1-interpretation-prompt.md`](./analysis-w1-interpretation-prompt.md).

> This session measured nothing (protocol §13.4). Every figure below is either read off the ledger
> or is arithmetic on numbers already scored — free under protocol §5.2. **No trial was spent here,
> CHECK was never opened, and the holdout was never touched.**

---

## 1. The verdict

**Branch B. No candidate is frozen. W1's deliverable is a documented null.**

Across the 42 charged trials — four selection families × three quantile levels, crossed with four
exit families, plus one conjunction — **every scored point is negative, net *and* gross**, on the
FIT split. The best of them, `stop_pct ≥ q70` under the 2 R bracket, returns **−0.203 net R per
session** against an a-priori reference of **−0.299**, and the matched-intensity permutation null
puts that at **p = 0.378**: a search of this shape finds a result this good in shuffled outcomes
about two times in five.

Plan §9.1 requires all six conditions and admits no partial pass. **Four of the six fail on measured
evidence.** The remaining two were deferred by amendment W1-A2 and are unreachable — CHECK is a
confirmation, not a rescue (§9.1's stated asymmetry), and a walk-forward refit cannot make a
negative J positive.

This is the outcome protocol §7.2 named as the likely one and §12.3 names as a success. It is not a
failed search; it is a search that returned an answer.

---

## 2. What was executed, and what it cost

| stage | what | trials | state |
|---|---|---|---|
| W1-0a … W1-0e | panel hash, family assignment, viability, throughput feasibility, correlation and drift | 0 | done ([ledger](https://github.com/bennetwi92/small-cap-stack/issues/735#issuecomment-5735259968)) |
| Stage 1 | pre-registration | 0 | done ([ledger](https://github.com/bennetwi92/small-cap-stack/issues/735#issuecomment-5735263978)) |
| 2a | 4 families × L/M/T under F2 | 12 | done |
| 2b | 2 survivors × L/M/T × F1–F4 | 24 | done |
| 2c | the conjunction × L/M/T × the 2 best exits | 6 | done ([ledger](https://github.com/bennetwi92/small-cap-stack/issues/735#issuecomment-5735280909)) |
| 2d | 3 walk-forward folds × 2 carried candidates | 6 | **deferred (W1-A2), now returned unspent** |
| 2e | the single CHECK score | 1 | **deferred (W1-A2), now returned unspent** |
| reserve | amendments | 5 | **unused, returned** |
| | | **42 of 54 spent · 12 returned** | |

The permutation null (B = 200) is not a trial. The ±20 % sensitivity band is not a trial and was
bundled into the deferred `w1.py 2d`, so it was not reported either — §5 below shows why it is moot.

Population: `panel-v1.parquet`, sha256-verified, FIT split only — **266 recon sessions, 4,516
rows**. Capacity N = 1, earliest trigger per session by time, never ranked. Money: full buying power
of the $500 account, per-leg tiered fees, 2-tick slippage on non-limit exits.

---

## 3. The §9.1 gate, condition by condition

| # | condition | verdict | the evidence |
|---|---|---|---|
| 1 | `p ≤ 0.05` on the FIT permutation null at matched intensity | **FAIL** | p = **0.378** on the whole-sequence null. Even at the narrower 2a/2b intensities the same real value gives p = 0.070 — still above 0.05. |
| 2 | `J > 0` **and** throughput ∈ [0.6, 1.0] on FIT | **FAIL** | Throughput holds (0.744–1.000 across all 42 points; 0.981 for the best). `J > 0` fails on **all 42 points**; the best is −0.203. |
| 3 | `J > 0` on CHECK, same sign of effect | **not measured — unreachable** | Deferred by W1-A2. §9.1 scores CHECK once, for one candidate, as a confirmation; there is no candidate that passed FIT to confirm. |
| 4 | refit parameters stable across all three folds | **not measured — unreachable** | Deferred by W1-A2. A stability table describes a candidate; it cannot create one. |
| 5 | `J > 0` throughout the ±20 % sensitivity band | **FAIL (by arithmetic, no measurement needed)** | The band is centred on J = −0.203. The two adjacent pre-registered levels of the same column are M −0.302 and (beyond T) nothing better; no point in the whole 42 is positive. A band around a negative centre, bounded by negative neighbours, cannot be positive throughout. |
| 6 | **net** J > 0, not merely gross | **FAIL** | Net J < 0 everywhere — and so is **gross** J, on every one of the 42 points. Best gross: −0.096 R/session. |

**Four measured failures. No partial pass. Branch B.**

### Why this is not Branch C

Branch C exists for the case where conditions 1, 2 and 6 leave a candidate genuinely live and the
deferred stages would decide it. They do not. Conditions 1, 2 and 6 all fail, and the prompt's own
⚠️ is explicit: *"If conditions 1 and 2 have already failed, spending 2e does not change the verdict
— it only spends the check split."* Sending 2d back would buy a stability table for a candidate that
has already failed four conditions, at a cost of 6 trials and one more opening of the fit split.
**The 12 remaining trials are returned unspent.**

---

## 4. §9.3 deliverable — every family searched, its grid, its best J, and its null

### 4.1 The grids

Pre-registered as FIT quantiles (protocol §5.3), with the literal FIT values. **L** keeps ~70 % of
rows, **M** ~50 %, **T** ~30 %.

| family | column | direction | L | M | T |
|---|---|---|---|---|---|
| **S1** attention | `hits_before_trigger` | ≥ | 6 | 11 | 19 |
| **S2** participation | `cum_dollar_vol_pre_trigger` | ≥ | 2,237,637.89 | 4,857,910.10 | 10,388,863.58 |
| **S3** shape | `retracement` | ≤ | 1.07595 | 0.86655 | 0.67460 |
| **S4** geometry/cost | `stop_pct` | ≥ | 0.038241 | 0.056816 | 0.082407 |

### 4.2 Stage 2a — the marginal screen (F2, 12 trials)

FIT J, net R/session:

| family | L | M | T | **family score** |
|---|---|---|---|---|
| S1 attention | −0.520 | −0.483 | −0.610 | −0.483 |
| S2 participation | −0.437 | −0.299 | −0.299 | −0.299 |
| S3 shape | −0.531 | −0.422 | −0.297 | **−0.297** → survives |
| S4 geometry/cost | −0.394 | −0.302 | **−0.203** | **−0.203** → survives |

⚠️ **S3 survived over S2 by 0.002 R/session.** The standard error on a point of this size is ≈ 0.086
R/session, so the gate that chose which family entered the 24-trial crossed design acted on a
difference about **1/40th of one standard error**. The gate was mechanical and pre-registered, which
is exactly right, and the quantity it ranked on was noise. That the whole downstream search hung on
a coin-flip is itself part of the null's evidence, and it is why §9.1 leans on the permutation null
rather than on the ordering of best-J.

📌 A useful internal check: **S2:M reproduces Filter A almost exactly.** Filter A's frozen condition 3
is `cum_dollar_vol_pre_trigger ≥ 4,857,910.10055` — S2's M threshold to the cent — and S2:M scores
−0.299 against S0-H's independently-measured Filter-A F2 baseline of −0.2994. Two separate code
paths agree. S2:T scoring identically to S2:M says the rest: at N = 1 the earliest pre-market
trigger of the day is already a high-dollar-volume name, so tightening that column buys no
selection at all.

### 4.3 Stage 2b — the crossed design (24 trials)

FIT J, net R/session, L / M / T:

| | F1 (0.5 R) | F2 (2 R) | F3 (runner) | F4 (hybrid) |
|---|---|---|---|---|
| **S4** | −0.344 / −0.294 / −0.218 | −0.394 / −0.302 / **−0.203** | −0.365 / −0.268 / −0.257 | −0.481 / −0.412 / −0.350 |
| **S3** | −0.399 / −0.375 / −0.264 | −0.531 / −0.422 / −0.297 | −0.503 / −0.449 / −0.348 | −0.581 / −0.523 / −0.457 |

Exit scores (max over 6 points): **F2 −0.203 · F1 −0.218 · F3 −0.257 · F4 −0.350.** Top two: F2, F1.

**On plan §8.2's screen assumption** — that a family's usefulness under F2 is informative about its
usefulness under F1/F3/F4. It holds on this record: S3 and S4 rank the four exits the same way, and
S4 beats S3 in **all twelve** matched cells. The screen did not distort the result. That is worth
recording because it was an assumption stated to be checked; it is not evidence for anything else.

### 4.4 Stage 2c — the conjunction (6 trials)

S3 ∧ S4, at the same level for both, under the top two exits:

| | L | M | T |
|---|---|---|---|
| F2 | −0.452 | −0.331 | −0.290 |
| F1 | −0.356 | −0.276 | −0.233 |

**The conjunction is worse than S4 alone in five of six matched cells**, and better in the sixth
(F1:M, −0.276 vs −0.294) by 0.018 — about one fifth of a standard error. Adding shape to geometry
does not add value.

### 4.5 The null distribution each was measured against

B = 200, seed 20260919. Each replicate permutes **which setup of a session got which price path**,
re-prices the outcome at the recipient's own entry, stop and size, and **re-runs the entire adaptive
2a → 2b → 2c sequence with its own gates**. The cost-preserving convention matters: it stops S4's
cost advantage from being credited to the null as signal, which makes this a fair test of S4
specifically.

| statistic | real | null q05 / q50 / q95 | **p** |
|---|---|---|---|
| 2a best of 12 | −0.203 | −0.402 / −0.305 / −0.198 | 0.070 |
| 2b best of 24 | −0.203 | −0.359 / −0.292 / −0.198 | 0.070 |
| **whole-sequence best** (42 charged trials, 36 distinct points) | **−0.203** | −0.324 / **−0.225** / −0.124 | **0.378** |

Bonferroni at α = 0.05/54: not approached.

> ### The single most instructive number in this workstream
>
> **The real result never changes — −0.203 at every intensity — and its p-value moves from 0.070 to
> 0.378 purely because the search got wider.** The null's *median* best-of-sequence over 42 trials is
> −0.225, within 0.022 of the real best. Widening a search does not make a result better; it makes
> luck better, and it does so faster than the result improves.
>
> This is protocol §0's thesis demonstrated on the record a second time, at 42 trials rather than
> 15,434, and it is the strongest argument in this document against funding a wider selection search
> on this data.

### 4.6 The contrast that makes the point

S0-H measured the **a-priori** Filter-A stream against its own single-hypothesis null and found
p = 0.005 (F1), 0.0199 (F2), 0.0149 (F3), 0.01 (F4): a fixed, outcome-blind filter picks
**significantly better than a random draw from the same session's pool**. W1's 42-trial search found
a point that scores *better than Filter A* (−0.203 vs −0.299) and **cannot clear its own null at
p = 0.378**.

The two nulls are differently built — S0-H draws an outcome from the session pool for one fixed
filter; W1 permutes price paths and re-runs the whole adaptive search — so this is a qualitative
contrast, not a formal test. But the shape of it is the lesson: *there is a little real
within-session ordering structure in this record* (the earliest, most-participated pre-market
trigger is a better-than-random pick), **and a 42-point search over it cannot be distinguished from
luck.** The structure is real and an order of magnitude too small to trade.

---

## 5. §9.3 deliverable — the recomputed power table: what W1 could have detected, and did not

S0-J assumed **213** FIT trades (0.8 × 266 sessions, the protocol's throughput target). W1's
realised throughput was higher — **261 trades** for the carried candidate (0.981/session), 226–266
across the search — so W1 was *better* powered than S0-J projected, by a factor of ×0.90 on the
minimum detectable effect. Recomputed at the realised counts and the realised per-trade sd
(protocol §7.2; free arithmetic under §5.2):

| family | sd (S0-H) | S0-J, n = 213, Bonf-54 | **realised n = 261, 1 candidate** | **realised n = 261, Bonf-54** | the same, per session |
|---|---|---|---|---|---|
| F1 | 0.805 | 0.229 | **0.140** | **0.207** | 0.203 |
| F2 | 1.408 | 0.401 | **0.244** | **0.362** | 0.355 |
| F3 | 1.776 | 0.505 | **0.308** | **0.457** | 0.448 |
| F4 | 0.900 | 0.256 | **0.156** | **0.231** | 0.227 |

At the sd the carried candidate actually realised (S4:T:F2, sd 1.391, n 261): **MDE = 0.241 R/trade
for one candidate, 0.358 R/trade at W1's 54-trial allocation.**

Now measure the search against its own floor:

| quantity | R/trade | as a share of the 1-candidate floor (0.241) | of the 54-trial floor (0.358) |
|---|---|---|---|
| **observed** improvement of the best point over the a-priori reference (−0.207 vs −0.2994) | **+0.092** | 38 % | 26 % |
| improvement that would have been **required** to reach `J = 0` | **+0.207** | 86 % | 58 % |

Two consequences, and both are load-bearing for how the rest of this project reads W1:

1. **The improvement W1 found is a quarter to a third of the smallest improvement it could have
   certified.** It is exactly the size of thing a 42-point search produces by selecting a maximum,
   and the permutation null says so directly (p = 0.378).
2. ⚠️ **Even a rule that merely reached break-even would not have been certifiable at this search
   intensity.** Reaching J = 0 from the reference needs +0.207 R/trade; the 54-trial detection floor
   is 0.358. **This record cannot certify a break-even selection rule — only a substantially
   profitable one.** Protocol §7.2 warned of this in the abstract ("the record can certify a large
   edge or nothing"); here it is in the realised numbers.

For completeness, the 95 % CI on the best point's J, unadjusted for the search: **[−0.369, −0.037]**.
The best of 42 searched points is significantly *below* zero even before any multiplicity
correction.

---

## 6. §9.3 deliverable — the cost floor, or the absence of signal?

Protocol §11.1 predicted that S4 — the geometry-and-cost family, thresholded on `stop_pct` — is
where gross and net diverge most, because cost in R is inversely proportional to percentage stop
distance. **The prediction is confirmed, precisely, and the confirmation is what rules the cost
floor out as the cause of the null.**

| point | `stop_pct` q50 of taken trades | gross R/session | net R/session | cost, R/trade |
|---|---|---|---|---|
| **S4:T:F2** (`stop_pct ≥ q70`) | **11.1 %** | −0.096 | −0.203 | **0.109** |
| S4:T:F1 | 11.1 % | −0.132 | −0.218 | **0.088** |
| S3∧S4:T:F1 | 11.5 % | −0.162 | −0.233 | 0.084 |
| S4:T:F3 | 11.1 % | −0.127 | −0.257 | 0.132 |
| S3:T:F1 | 6.9 % | −0.120 | −0.264 | **0.144** |
| *ref: Filter-A F2* | *6.6 %* | *−0.145* | *−0.299* | ***0.154*** |

Read the F1 rows against each other: S4:T and S3:T take trades with median stops of 11.1 % and
6.9 %, and pay **0.088** and **0.144** R per trade in costs. That 0.056 R/trade difference is bought
by stop distance alone, exactly as the cost identity says it must be, and it matches S0-F's decile
table (cost of a loss runs 0.47 R in the tightest decile to 0.07 R in the widest).

**So where did S4's win come from?** Its total improvement over the Filter-A F2 reference is
0.096 R/session. Decomposed:

- **gross** improvement: −0.096 vs −0.145 = **+0.049 R/session** (51 %)
- **cost** relief from selecting wider stops: 0.154 − 0.109 = **+0.045 R/trade ≈ +0.044 R/session** (47 %)

⚠️ **About half of the best candidate's apparent edge is not selection at all. It is the cost lever.**
A reader who saw only net J would have concluded that `stop_pct` selects good setups. It does not;
it selects *cheap* setups. This is precisely the failure mode §11.1 was written to catch, and it is
the single most transferable finding W1 produced.

### The answer to the question §9.3 asks

> **Selection was defeated by the absence of signal, not by the cost floor.**

The cost floor is real, expensive and worth attacking — but it is not what killed this. The
decisive evidence is one line: **gross R per session is negative on all 42 points.** Gross is
measured before a cent of commission or slippage. Set every cost in this system to zero and every
candidate still loses money.

Stated in the units of the break-even table, as an approximation (an idealised ±2 R / −1 R reading
of F2, so treat the hit rates as indicative rather than exact):

| | implied gross hit rate |
|---|---|
| a-priori Filter-A stream | ~28.5 % |
| **the best of 42 searched points** | **~30.1 %** |
| gross break-even at a 2 R target | 33.3 % |
| **net** break-even, measured (S0-F) | **40.4 %** |

The entire 42-trial search moved the hit rate about **1.6 points**, when it needed ~4.8 to stop
losing gross and ~11.9 to stop losing net. It closed roughly a third of the distance to *gross*
break-even and about an eighth of the distance to *net*.

📌 **The strategy verdict does not depend on the $500 account.** Gross R is capital-independent, and
gross is negative. The account size determines *how much worse* net is (S0-H: on the Filter-A
stream at fixed $500 sizing, the account is ruined inside the FIT window on every exit family), but
it does not create the deficit. Operator fact 3's separation holds cleanly here: this is a strategy
result, not a capital-adequacy result.

---

## 7. §9.3 deliverable — the verdict on S3, the shape grammar

**This is the load-bearing verdict, and the honest answer is negative.**

### What was actually tested

W1's pre-registered S3 predicate is **one column — `retracement`, thresholded at three FIT
quantiles, direction "shallower is better"** — the grammar's core claim about consolidation depth.
Two things must be said plainly before the verdict, because they bound it:

- ⚠️ **It is 266 FIT sessions, not 511.** The plan and the interpretation prompt both describe this
  verdict as "re-measured on 511 sessions rather than 197". The panel spans 508 sessions, but W1
  only ever opened FIT: **266 recon sessions, 4,516 rows.** That is still a substantially larger and
  cleaner population than the 197-session, 271-passing-row basis of inventory §7.9 — but it is not
  511, and saying 511 would overstate the evidence. CHECK and HOLDOUT remain unopened.
- ⚠️ **`passed` — the shape-gate composite itself — was never scored.** It sits in S3's assigned
  column set (W1-0b) but the pre-registered predicate is `retracement`. Nor were several shape
  columns plan §5.1 names (`cons_tightness`, `cons_strictness`, `vol_ratio`,
  `pole_vol_concentration`, `holds_base`): W1-0b found they are not in panel-v1 at all. So this is a
  verdict on the grammar's central claim as W1 pre-registered it, not on every shape feature ever
  computed.

### The evidence

1. **S3 never beats S4, anywhere.** In all **12** matched cells of the crossed design (3 levels × 4
   exits), the geometry family beats the shape family. Not once does shape rank first.
2. **S3's best point is indistinguishable from a fixed non-shape filter.** S3's best under F2 is
   −0.297; the a-priori Filter-A F2 baseline is −0.2994. A difference of **0.002 R/session** against
   a standard error of ~0.086. Under F1, S3's best (−0.264) beats Filter A's (−0.298) by 0.034 —
   **0.4 of one standard error**.
3. **S3 only survived stage 2a at all by 0.002 R/session over S2** (§4.2). Its place in the crossed
   design was a coin flip.
4. **Adding shape to geometry destroys value.** The conjunction S3 ∧ S4 is worse than S4 alone in
   five of six matched cells (§4.4). Conditioning on a well-formed flag on top of a cost-favourable
   one makes the book worse, not better.
5. **Direction, at least, is the grammar's.** S3 improves monotonically as it tightens
   (L −0.531 → M −0.422 → T −0.297): shallower consolidations really are less bad than deep ones.
   The sign of the grammar's claim is right. Its magnitude is nowhere near enough to matter, and the
   whole L→T span (0.234 R/session) sits inside the null's best-of-sequence spread.

### The verdict

> **On 266 FIT sessions, with a real cost model and capacity N = 1, the shape grammar — tested
> through its central claim, consolidation depth — does not select better than a fixed, outcome-blind
> non-shape filter, and it subtracts value when conjoined with one.**
>
> Inventory §7.9 recorded the same negative on a 197-session population and 271 passing rows. **This
> record does not merely fail to replicate a positive; it replicates the negative**, on a larger
> population, under a stricter protocol, with a pre-registered predicate and a matched-intensity
> null. The grammar's *direction* survives; its *selectivity* does not.

The one thing this verdict must not be read as: evidence that the shape grammar is worthless as
**machinery**. It is what produces the setup population at all — it defines what a row *is*. The
finding is that, having produced the population, thresholding its depth does not tell you which rows
to take.

---

## 8. What the null retires, and what it does not

**Retired — the assumption that the shape grammar is an axiom rather than a candidate.** It has now
been tested as a candidate, pre-registered, twice, on two different populations, and it has not
selected either time. Anything downstream that treats a shape gate as load-bearing selection is
resting on an assumption this record twice declines to support. Treat the grammar as the thing that
defines the population, and treat selection as unsolved.

**Also retired — the hope that selection alone closes the gap.** The deficit to gross break-even is
~4.8 points of hit rate; the best of four pre-registered families crossed with four exits moved it
~1.6, and could not be distinguished from luck. A better filter over these 35 columns is not where
the missing edge is.

**Not retired:**

- **That there is *some* real within-session structure.** S0-H's a-priori filter clears its own null
  at p ≈ 0.005–0.02 (§4.6). Something about being the earliest, most-participated pre-market trigger
  is better than random. It is far too small to trade and it is already captured by Filter A.
- **That `stop_pct` matters.** It does — through cost, not selection (§6). That is a real, specific,
  transferable finding, and it points at execution, not at filtering.
- **Anything about CHECK or HOLDOUT.** Neither was opened. This is a FIT-split verdict.
- **Anything about the other two workstreams.** W1 was blind to them by design and remains so.

---

## 9. What the next pass should fund

⚠️ **Explicitly not: a wider selection search on this record.** Inventory §7.8 and §10 already said
the record cannot support one, and §4.5 above has now demonstrated it a second time at 42 trials —
the same real result decayed from p = 0.070 to p = 0.378 purely by widening the search. More grid
points will produce a better-looking winner and a worse p-value. This is the recommendation the plan
asked for in advance, and the evidence agrees with it.

In priority order, from W1's own evidence:

1. **The W1 × W3 interaction** (protocol §14) — *conditional on W3's result, which is the
   custodian's to see, not W1's.* Protocol §14 names this as the first thing to fund if W1 and W3
   both return nulls, because a selection rule that works only in some regimes is missed by W1
   (which pools across all of them) and by W3 (which holds selection fixed). W1 has now returned its
   null. Whether the condition is met is for the custodian to determine.

2. **The recon-window annotation programme** (protocol §14) — the cheap fix, and the one W1's result
   argues for most directly. W1 searched 35 mechanical columns and found nothing; the one signal
   source the record holds and has never been able to use is **the trader's own judgement**, and the
   167 existing reviews are stranded in the holdout window. **300–500 annotations spread across
   2024-09 → 2026-03 would move that asset into FIT** and make a supervised workstream fundable next
   pass, at the cost of chart time and nothing else. Every month this is not started is a month it
   cannot pay off in. *Start it now, before the next analysis is designed, not when it is.*

3. **Attack the cost floor rather than the filter** — from §6's decomposition, not from the plan.
   The one lever W1 found that genuinely moved net R was `stop_pct`, and it worked through cost. The
   cost identity (`c ∝ 1/stop_pct`, plus a per-order fixed minimum) says the same relief is
   available from the *cost* side without distorting selection at all: order type, sizing, the
   fixed-fee minimum, and account size. **A 0.045 R/trade improvement — the size of what selecting
   wide stops bought — is available from cost without touching the filter.** That is W2's territory
   and is named here only as a pointer, since W1 must not read W2's work.

4. **`passed`, and the shape columns panel-v1 does not carry** (§7's caveats) — named as a known
   gap, *not* recommended. Testing them would be exactly the wider selection search item 0 forbids.
   If the shape grammar is ever re-opened, it should be as a new pre-registration on new evidence,
   not as more points on this grid.

---

## 10. There is no §9.2 tuning protocol, and why

Plan §9.2 makes the tuning protocol part of the freeze deliverable, so its absence is stated rather
than left to be noticed: **there is no rule set to re-tune.** A tuning protocol specifies what may be
re-fitted, on what window, how often, and how a re-tune is told from a rescue. All four presuppose a
frozen candidate. W1 froze none.

The one piece of prior (2) that survives is its warning, and it is worth carrying forward
independently of any candidate: **every refit is a trial and must be ledgered as one, forward,
forever.** A tuning protocol that does not count its own refits is an uncounted search running in
production — which is the same failure this whole design exists to prevent, just slower.

---

## 11. Caveats an honest reader needs

1. **FIT only.** 266 recon sessions. CHECK (125) and HOLDOUT (117) were never opened. Nothing here
   is an out-of-sample result, and the null is not a substitute for one.
2. **Recon only, therefore reconstructed.** FIT carries no live rows, so constraint 7's recon/live
   split is vacuous here and the recon→live transfer question is entirely untested. Appearance times
   on the recon half are reconstructed from minute bars plus the previous close, not observed.
3. **Slippage is an assumption, not a measurement.** S0-F is explicit: Phase 1 places no orders, so
   there are no real fills. The 2-tick figure is pinned, not audited. At 4 ticks every net number
   here gets worse and F1's break-even reaches 78.9 %.
4. **The conclusion rests on gross being negative**, which is the most robust part of the result —
   it is independent of the cost model, the account size and the slippage assumption alike.
5. **A narrow hypothesis class, by design.** One column per family, one direction fixed a priori,
   three quantile levels, one conjunction. No interactions beyond that, no learned models, no
   ranking. This is what 54 trials buys, and the alternative is the 15,434-combination search that
   already destroyed this record once. **The null is a null *about this hypothesis class*** — it says
   the record does not support finding a rule this way, not that no rule exists.
6. **One drift flag, from W1-0e:** `ext_at_trigger` (S4) has its HOLDOUT-live median at FIT q34.9.
   It is carried here as the plan requires — flagged, never dropped. It is not in any scored
   predicate (S4's predicate is `stop_pct`), so it does not affect this verdict; it is recorded
   because the holdout result would be unreadable without it.
7. **Plan §5.1 named seven S3/S4 columns that panel-v1 does not contain** (W1-0b). Nearest analogues
   were assigned. The pre-registered predicates were unaffected — `retracement` and `stop_pct` are
   both present — but the shape family is thinner than the plan imagined.
8. **W1 pooled across the whole fit window and never segmented it** (plan §8.1). A selection rule
   that works only in some regimes would be invisible to this design. That is the stated blind spot,
   not an oversight.

---

## 12. The audit of the measurement — why 2d was not sent back

Amendment W1-A2 left one door open: 2d runs if this session finds an error in the measurement. I
read `spikes/analysis_v1/w1.py` and `stage0.py` as machinery (protocol §1.1) and found none. What
was checked:

- **The predicate never sees more than it may.** Every candidate runs through `stage0.run_rule`,
  which hands it `df.select(RULE_COLUMNS)` — 35 columns, none of the seven lookahead columns, the
  `*_to_trigger` variants already removed by amendment A1. A rule reaching further raises `KeyError`.
- **Capacity is time-ordered, never ranked.** `earliest_one` sorts by `(dt, trigger_et_min, symbol,
  run)` and takes the first per session. Constraint 1 holds.
- **The null preserves the burst structure and the cost.** It permutes *within* session (protocol
  §7.3's block requirement, prior (1)), moves the price path rather than net R, and re-prices at the
  recipient's own entry, stop and size. Without that convention S4's cost advantage would have leaked
  into the null as signal and p would be *understated* — the convention makes the test harder on the
  candidate, which is the right direction.
- **Matched intensity is genuine.** Each replicate re-runs the whole adaptive sequence including its
  own survivor and top-exit gates, not just the winning point.
- **The pricing matrix reproduces the shared cost model.** `cmd_stage` asserts the diagonal of the
  donor/recipient matrix equals `stage0.family_outcomes` to 1e-9 on 200 rows.
- **Flat sessions count as zero.** J divides by session count, not trade count (protocol §6).
- **Three independent consistency checks pass.** S2:M reproduces Filter A's frozen constant to the
  cent and its J to 0.0004. Every published J equals its per-trade mean × trades ÷ 266 sessions. And
  the implied gross hit rate of the Filter-A stream, ~28.5 %, matches the record's independently
  known 29.5 % on `books_all` (inventory §8).

One presentational imprecision, recorded because it is in the ledger: the whole-sequence null is
described as over "42 points". The 42 is the **charged trial count**; because stage 2b re-scores the
six survivor × F2 points already scored in 2a, the search covers **36 distinct scored points**. The
plan charges the repeats deliberately (plan §4) and the null re-runs the identical sequence, so the
intensity match is correct and no number changes. Recorded for the custodian's specification check.

**No stop-and-report is open.**

---

## 13. Trials, final

| | |
|---|---|
| allocated | 54 |
| spent | **42** (2a 12 · 2b 24 · 2c 6) |
| **returned unspent** | **12** (2d 6 · 2e 1 · reserve 5) |
| spent by this interpretation session | **0** |
| CHECK | **never opened** |
| HOLDOUT | **never opened** |

The 12 returned trials go back to the global budget and are **not** re-spent inside W1.

---

## 14. Status for the custodian

W1's Stage-3 freeze is the **documented null** above — protocol §12.1 condition 1 is met by a
documented null exactly as it is by a candidate. There is no W1 specification to verify against the
Stage-1 ledger entry, because nothing was frozen; what should be verified instead is that the 42
charged trials match the pre-registered grid, which §4 above sets out point for point.

W1 has no open stop-and-report. No result in this document is final until the custodian's single
holdout pass, and W1 contributes no candidate to it.
