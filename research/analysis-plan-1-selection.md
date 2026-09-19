# Analysis plan 1 — selection × exit: *which setups*

**Status:** EXECUTED (2026-09-18); **SUPERSEDED as a design (2026-09-19)**. Workstream **W1** of
the three-agent analysis of the 2-year Phase-1 record. **The result is a documented null:** 42 of 54
trials spent, all 42 FIT points negative **net and gross**, whole-sequence null `p = 0.378`; 2d/2e
deferred by amendment W1-A2 and CHECK never opened. **SUPERSEDED as a design (2026-09-19)** by [`analysis-plan-4-joint.md`](./analysis-plan-4-joint.md), which fits selection and market state **jointly** — see its §1 for why the marginal split could not compose a system, and `analysis-protocol.md` §16 for the amendments. **The results below stand and are inputs to W4.**

> **Read [`analysis-protocol.md`](./analysis-protocol.md) first, in full.** It carries the blind
> list, the operator facts and priors, the thirteen hard constraints, the frozen panel, the splits,
> the objective function, the exit families, the cost identity, the trials ledger, the custodian and
> the single holdout opening. This document carries only what is specific to W1. Where the two
> appear to disagree, the protocol wins and the disagreement is a stop-and-report.

The copy-paste starting prompt for the W1 agent is [§12](#12-the-starting-prompt).

---

## 1. The question

> **Is there a mechanically-decidable selection rule over trigger-time-safe features which, crossed
> with an exit family, delivers positive net R per session at ~0.8 trades/day — and which survives a
> permutation null run at the same search intensity?**

Why it is worth 54 of the 120 global trials — the largest share:

- It is the **only** workstream that can produce the artefact the objective names: a selection +
  exit + capacity rule set concrete enough to run forward on paper.
- It works on the only asset that is already modelling-ready — the frozen panel: ~9,000–9,500 rows,
  46 trigger-safe columns, a causal boundary the detector has been measured against (2018/2018
  exact prefix stability, inventory §3), and a contiguous time-ordered split.
- It is also the workstream most exposed to the collapse failure mode (protocol §0). The budget is
  where the discipline is spent, not only where the searching is.

⚠️ **W1 owns selection. It does not own execution mechanics (W2) or time-conditioning (W3).** See
§10 for the boundary.

---

## 2. The population and the panel

**Population:** every triggered setup in the frozen panel `panel-v1.parquet` — one row per
`(date, source, symbol, run)`, pre-market appearances on **both** halves (protocol §4.1). Expected
~9,000–9,500 rows across ~511 sessions.

**W1 does not apply Filter A.** Filter A is the a-priori stream W2 and W3 study (protocol §9); W1's
entire job is to find a better filter, so it searches over the unfiltered panel and is the only
workstream permitted to fit one.

**Verification is by bytes, not by inspection.** Before anything else: `sha256sum
panel-v1.parquet` and compare against `panel-v1-spec.md` on `main`. A mismatch is a stop-and-report
(protocol §13.1). **Never rebuild the panel** — an agent that rebuilds it has created a second
panel and destroyed the one property that makes the three results comparable.

---

## 3. Stage 0 — W1's own reconnaissance

The shared items S0-A … S0-J (protocol §4.5) are run centrally and are **not** repeated here. W1
adds five, all of them free under protocol §5.2 because none reads an outcome.

| id | item | decision rule / pre-authorised fallback |
|---|---|---|
| **W1-0a** | Confirm the panel hash and the per-split row counts against `panel-v1-spec.md`. | Mismatch → **stop-and-report**. No rebuild, no "close enough". |
| **W1-0b** | Take the frozen `RULE_COLUMNS` from S0-C/S0-D and assign each column to one of the four **selection families** in §5.1. Publish the assignment to the ledger before Stage 1. | A column fitting no family is **unassigned and unusable** — it may not be pulled in later, because "the feature that didn't fit a family" is how a pre-registration becomes a sweep. |
| **W1-0c** | Family viability screen: each family must retain **≥ 2** usable columns after S0-D's nullity drop. | **< 3 of the 4 families viable → stop-and-report.** A two-family crossed design is not what this budget was allocated for. |
| **W1-0d** | Throughput feasibility (free — no outcome read): for each family, apply its q30/q50/q70 cut with capacity `N = 1` and record the **realised trades per session on FIT**. | Grid points whose throughput falls outside **[0.6, 1.0]** are **dropped from the grid before Stage 1** and their trials returned to W1's allocation. This is the cheapest possible way to shrink the search: a disqualified candidate costs nothing if it is never scored. |
| **W1-0e** | Feature-feature correlation within and across families on FIT, plus per-split feature-distribution drift (FIT vs CHECK vs HOLDOUT-features). | Two columns correlating **\|ρ\| > 0.9** → keep the one with better coverage, drop the other, ledger it. A family whose FIT distribution has drifted materially by HOLDOUT is **flagged in the freeze report**, not dropped — drift is a fact about the record, and hiding it would make the holdout result unreadable. |

**Stage-0 exit gate for W1:** shared S0-F, S0-H and S0-J posted to the ledger; panel hash verified;
≥ 3 families viable; the grid after W1-0d contains ≥ 18 scoreable points; the §7.2 power table
recomputed from S0-H.

**Cancellation condition:** if S0-A reports fewer than 7,500 panel rows, or W1-0c leaves fewer than
three viable families, or S0-H's recomputed minimum detectable effect on FIT exceeds **0.6 R/trade**
for every exit family — **W1 is cancelled**, its unspent trials return to the global budget, and the
finding is written up as a documented null under §9.2. That last condition is the important one: a
workstream that cannot detect what it is looking for should not spend 54 trials proving it.

---

## 4. The hypothesis budget: 54 trials

| stage | what | trials |
|---|---|---|
| **2a — marginal screen** | 4 selection families × 3 quantile grid points, scored under **F2 only** (the reference family). Gate: the **two** families with the highest FIT J proceed. | **12** |
| **2b — crossed design** | the 2 surviving families × 3 grid points × 4 exit families | **24** |
| **2c — one conjunction** | the two surviving families combined as a conjunction, 3 grid points × the 2 best exit families | **6** |
| **2d — walk-forward refits** | 3 folds × the 2 carried candidates, refit per fold (protocol §7.2). ⚠️ **Every refit is a trial** — prior (2). | **6** |
| **2e — check** | the single candidate scored on CHECK | **1** |
| **reserve** | amendments arising from Stage-0 findings | **5** |
| **total** | | **54** |

⚠️ **If S0-F cancels F1** (protocol §11.2), stage 2b becomes 2 × 3 × 3 = **18** and stage 2c's
family pair is chosen from three exits, not four: **6 trials return to the global budget unspent.**
They are not re-spent inside W1 — the whole point of a global budget is that a workstream cannot
quietly widen its own search with someone else's savings.

**Permutation nulls are not trials** (protocol §5.2) but they must be run at matched intensity: the
null for stage 2a re-runs all 12 points against each of B = 200 block-shuffled records; the null for
2b re-runs all 24; and the final null re-runs the **entire** 2a+2b+2c sequence, because that whole
sequence is what produced the winner.

---

## 5. The hypothesis class

Stated tightly enough that "did we test this?" has a yes/no answer.

### 5.1 The four selection families

Each family is a **single predicate form**: one or two columns from that family, thresholded at a
quantile of the **FIT** distribution (protocol §5.3), combined by conjunction if two. No other form
is in scope — no interactions across families except the one conjunction in stage 2c, no learned
models, no ranking (constraint 1).

| id | family | the columns it draws on | why it is a family |
|---|---|---|---|
| **S1** | **Attention** | `hits_before_trigger`, time since first hit at trigger (staleness), hit density before the break | the scanner-attention series is the only record of *how much attention* a name had and *when it started* (inventory §2.2) |
| **S2** | **Participation** | `cum_volume_pre_trigger`, `cum_dollar_vol_pre_trigger` | the trigger-safe liquidity quantities, the safe substitutes for the day aggregates. ⚠️ Stage-0 amendment A1: the `*_to_trigger` versions include the trigger bar's full volume and are out of `RULE_COLUMNS` (`panel-v1-spec.md` §4) |
| **S3** | **Shape** | the FeatureVector's SHAPE / VOL / WICK / CONS fields — `retracement`, `cons_tightness`, `cons_strictness`, `vol_ratio`, `pole_vol_concentration`, `cons_len`, `pole_len`, `holds_base` | the pattern grammar. ⚠️ Carried as **one candidate filter, never as an axiom** — the inventory (§7.9) records that the shape gates selected no better than taking everything on a 197-session population. If S3 fails again on 511 sessions, that is a result worth having. |
| **S4** | **Geometry and cost** | `stop_pct` (stop distance as a percent of price), `ext_at_trigger`, `pole_height_pct`, `pole_extension_atr`, `trigger_et_min` | protocol §11.1: **percentage stop distance is a cost lever**, so this is the family whose gross and net behaviour should diverge most. It is in the design precisely to be checked against §11.1's prediction. |

**Out of the hypothesis class, explicitly:** any live-only column (constraint 12 — `float_shares`,
`short_percent`, news, `first_rank`); any of the seven lookahead columns (constraint 1); any
within-day ranking; any learned model (a gradient-boosted tree over 46 columns on ~200 fit trades is
a search of unbounded intensity wearing one trial's clothing); any temporal conditioning (W3's
territory, §10).

### 5.2 Capacity

Capacity is **fixed a priori at `N = 1`, earliest trigger per session by time** (constraint 1). It
is not a search dimension. A candidate whose realised throughput falls outside [0.6, 1.0] is
**disqualified, not penalised** (protocol §6) — which means a filter tight enough to starve the book
disqualifies itself, and that is the correct behaviour under the operator's declared constraint.

---

## 6. The objective function

**Protocol §6, unchanged: mean net R per session**, with the full §6.2 report attached to every
scored block, including the per-session distribution, the top-decile share, the deepest cold
stretch, gross beside net, recon and live separately, and the capital-adequacy view.

W1 departs from it in no way. The one W1-specific addition, from protocol §11.1:

> Every W1 candidate additionally reports the **distribution of `stop_pct` across its taken
> trades**. A candidate that improves J by selecting tight-stop setups has bought a structurally
> worse cost floor, and the gross/net gap in the §6.2 report is where that shows up.

---

## 7. Validation design

- **Walk-forward with rolling refit inside FIT**, three expanding-window folds (protocol §7.2), with
  **parameter stability** reported as a first-class result: do the refit quantiles land within one
  grid step of each other across folds, or wander? A candidate whose refit parameters wander is a
  candidate whose fit is noise, whatever its mean J.
- **Permutation null, block-shuffled by session, B = 200, at matched search intensity** (protocol
  §7.3). A candidate with `p > 0.05` on FIT does not proceed. No exceptions and no economic-story
  override — the record has already produced a beautiful fit that was noise (inventory §7.8).
- **Sensitivity at ±20 %** on every frozen threshold (protocol §7.4), reported, never used to select.
- **Multiplicity**: within W1, the permutation null over the whole 2a+2b+2c sequence is the primary
  control; Bonferroni at α = 0.05/54 is reported as a cross-check. Across the three workstreams, the
  correction is the custodian's, at the single holdout opening (protocol §12.2).
- **Both halves separately** on every block containing both (constraint 7). FIT and CHECK are
  recon-only, so this binds only on the holdout — which is exactly why the holdout was drawn to span
  the boundary (protocol §7.1).

---

## 8. The regime treatment (prior 3) and the exit treatment (prior 4)

### 8.1 Regime — W1 pools, and says so

W1 does **no** regime work. It pools across the whole fit window and reports the per-session
distribution (protocol §6.2) as a description of what pooling concealed.

⚠️ **W1 may not segment the record by time, volatility or market state to improve a fit.** Reporting
J per calendar quarter is permitted as *description* in the freeze report; using it to choose a
threshold, a family or a subperiod is a hard-constraint violation and a stop-and-report. Prior (3)'s
leak — a filter fitted on the whole record and then studied for regimes — is blocked here by W1
never taking the second step, and in W3 by its filter being a priori (protocol §9).

**The named cost of this division:** a selection rule that works only in some regimes will be missed
by W1 (which pools) *and* by W3 (which holds selection fixed). That is the design's largest
deliberate blind spot; it is stated in protocol §14 and it is the first thing the next pass should
fund if W1 and W3 both return nulls.

### 8.2 Exit — crossed, not sequential

Selection × exit is searched **jointly, as a crossed design** (stage 2b), because the setups worth
clipping for 0.5 R are not necessarily the ones worth holding for 5 R, and a selection rule fitted
against one exit family is not evidence for the other.

The joint space is kept from exploding by the **screen-then-cross** shape: stage 2a screens four
families against one reference exit (12 trials) and stage 2b crosses only the two survivors against
all four exits (24). The full 4 × 3 × 4 cross would be 48 trials for 12 more points of coverage —
a bad trade at this budget, and the screen's cost is one assumption, stated here so it can be
checked: **that a family's marginal usefulness under F2 is informative about its usefulness under
F1/F3/F4.** If stage 2b shows the two survivors ranking differently under different exits, that
assumption is wrong for this record and it goes in the freeze report as a finding.

**Net break-even per family comes from S0-F** (protocol §11.2) and is consumed, not re-derived. If
S0-F cancels F1, the crossed design is 2 × 3 × 3.

---

## 9. What "done" looks like

### 9.1 The candidate branch

A single candidate is frozen and handed to the custodian **only if all of**:

1. `p ≤ 0.05` on the FIT permutation null at matched intensity;
2. `J > 0` and realised throughput ∈ [0.6, 1.0] on FIT;
3. `J > 0` on CHECK, with the same sign of effect — CHECK is a confirmation, not a second search,
   and it is scored **once**, for one candidate (that is what the 1 trial in stage 2e buys);
4. refit parameters stable across all three walk-forward folds (within one grid step);
5. `J > 0` throughout the ±20 % sensitivity band;
6. net J > 0, not merely gross (constraint 6) — and the gross/net gap and the `stop_pct`
   distribution reported (§6).

Failing **any** of the six, the candidate is not frozen. There is no partial pass.

**The freeze deliverable** is a ledger comment naming: the predicate in full (columns, operator,
thresholds as both quantiles and the literal FIT values), the exit family and its parameters, the
capacity rule, every constant, the FIT and CHECK reports, the null, the stability table, the
sensitivity bands — **and the tuning protocol below**.

### 9.2 The tuning protocol (prior 2) — part of the deliverable, not an afterthought

A frozen rule set is not the target; a rule set *plus* a re-fitting rule is. W1's candidate ships
with:

- **What may be re-tuned:** the quantile thresholds, and nothing else. The column set, the predicate
  form and the exit family are frozen. Changing any of those is a new analysis with a new
  pre-registration, not a re-tune.
- **On what window:** a rolling 250-session window, ending at the refit date.
- **How often:** quarterly, or on the stated trigger — whichever comes first.
- **The guardrail, and the distinction that matters:** a refit landing **inside** the ±20 %
  sensitivity band established at freeze is a **re-tune** — apply it, ledger it, continue. A refit
  landing **outside** that band is a **rescue** — do not apply it. A rescue means the rule has
  stopped describing the market, and the honest response is a new pre-registration, not a wider
  band. ⚠️ **Every refit is a trial and is ledgered as one**, forward, forever; a tuning protocol
  that does not count its own refits is an uncounted search running in production.
- **The evidence it rests on:** the three-fold parameter-stability table. If the parameters already
  wandered across three folds of the fit window, say so — the protocol then reads "this rule needs
  refitting more often than the record can justify", which is itself a reason not to ship it.

### 9.3 The null branch — a success, explicitly

If no candidate clears §9.1, W1's deliverable is a documented null containing: every family
searched, its grid, its best J and the null distribution it was measured against; the recomputed
power table saying **what W1 could have detected and did not**; the `stop_pct` and gross/net
evidence on whether selection was defeated by the cost floor rather than by the absence of signal;
and an explicit verdict on **S3** — whether the shape grammar, re-measured on 511 sessions rather
than 197, selects better than taking everything.

What gets retired on a null: the assumption that the shape grammar is an axiom rather than a
candidate. What the next phase does instead: fund the W1 × W3 interaction (protocol §14) or the
recon-window annotation programme, rather than re-running a wider selection search on the same
record — which the record has already shown it cannot support (inventory §7.8, §10).

**A documented null is a successful outcome of W1.** Do not produce a candidate because a candidate
was expected.

---

## 10. What is out of scope for W1

| out of scope | whose it is |
|---|---|
| entry mechanics, trigger definition, fill realism, sub-5-minute anything, `bars_1m` | **W2** |
| the interior parameters of an exit family (F3's arming threshold, F4's scale fraction and point) — W1 uses the protocol §10 defaults and does not vary them | **W2** |
| time-conditioning, regime gates, market-state features, refit-cadence measurement | **W3** |
| the 167 hand reviews (quarantined — protocol §9.2) | Stage 0, once, redacted |
| the holdout, in any form | the custodian, once (protocol §12) |
| reading another workstream's plan, intermediates or candidate | nobody, before the holdout pass |

---

## 11. Execution shape and cost

| piece | where | tier |
|---|---|---|
| panel fetch + hash verification, Stage-0 W1-0a…e | **cloud session** off the `data-export` branch (the panel is ~9 k rows — trivially portable) | `builder` |
| stages 2a–2e, the permutation nulls (B = 200 × 42 points), the walk-forward | **cloud session**; if the null runs exceed what a cloud session can hold, push to the **Mac**. ⚠️ **Never the box** — a 2 vCPU / 4 GB CX23 and a heavy job takes it down hard | `spike-runner` / `builder` — measurement only |
| every interpretation step: which families proceed, whether a candidate clears, the freeze report | separate session | `strategy-analyst` (opus) |

⚠️ **Measurement and interpretation are separate sessions, always** (protocol §13.4). A step that
measures and concludes in one breath is a step that chose its measurement to suit its conclusion.

**Cost estimate: 6–8 sessions** — ~2 `builder` (Stage 0 and the panel), ~3–4 `spike-runner`
(stages 2a–2e and the nulls), ~2 `strategy-analyst` (the two gates and the freeze report). One task
per session, then `/clear`.

---

## 12. The starting prompt

> Copy from here down into a fresh session.

---

You are the **W1 agent** in a three-agent analysis of a 2-year, ~511-session record of a systematic
US small-cap momentum strategy. You are executing a plan, not designing one. Three agents are
running concurrently against one record; none of you can ask a question mid-flight, and the controls
below are what stop the three of you burning the only 2-year record this project will have for
another two years.

**Read these two documents in full, in this order, before anything else:**

1. `research/analysis-protocol.md` — the shared contract. Blind list, operator facts and priors,
   the thirteen hard constraints, the frozen panel and its Stage-0 items, the splits, the objective
   function, the four exit families, the cost identity, the trials ledger, the custodian, the single
   holdout opening.
2. `research/analysis-plan-1-selection.md` — this plan. You execute §3 through §9.

Then read `research/data-inventory.md` for what the data is.

**Work blind.** The blind list is the table under `### Work blind — this is a hard requirement` in
`research/analysis-brief.md`. Read it there and obey it exactly. Do not read the incumbent rules,
the decision log, the pattern-grammar specs, `spikes/README.md` or any spike's findings, the shipped
portfolio or config code, the dashboard payloads, the prior-repo audits, or git/issue history — and
do not ask a subagent to summarise any of them for you, or accept such a summary if offered. The one
carve-out is protocol §1.1: `spikes/engine_lab/` is readable **as machinery**. Its `SHIPPED` /
`baseline()` constants may be *executed* as a benchmark and may **not** be read into a search — every
grid you run is expressed as a quantile of the fit-split distribution, never as an absolute
threshold. If you need a mechanical fact only a blinded file holds, take it from the inventory; if
the inventory is silent or wrong, **say so in the ledger** and work around the uncertainty rather
than opening the file.

**Your question:** is there a mechanically-decidable selection rule over trigger-time-safe features
which, crossed with an exit family, delivers positive net R per session at ~0.8 trades/day and
survives a permutation null at the same search intensity?

**Your deliverable:** exactly **one** frozen candidate — or a documented null, which is an equally
successful outcome and must be written with the same care. Do not produce a candidate because one
was expected.

**Your budget: 54 trials**, from a global budget of 120 shared with two other agents. A trial is one
(hypothesis, parameterisation) scored against any outcome column or post-trigger price. A k-point
grid is k trials. **Every walk-forward refit is a trial.** A permutation null is not a trial but must
run at matched intensity. A sensitivity band is not a trial and may never be used to *select* a
value. Anything computed without reading an outcome or a post-trigger price is free. The allocation
is in §4 of your plan, stage by stage. **If you would exceed it, stop** — you do not borrow from
another workstream.

**Ledger before you open data.** The trials ledger is the GitHub issue named in your workstream
sub-issue. Post your Stage-1 pre-registration comment — hypotheses, grids as quantiles, objective,
success threshold, stopping rule, trial arithmetic — **before** you open the fit split. Post a batch
comment before each scoring batch, with your running total. Post every amendment as its own comment
before you act on it. You post and proceed; you do not wait for approval. The custodian will verify
your frozen candidate against your ledger entries, and a candidate that does not match is not
scored.

**The stage gates. Do not open with the analysis.**

- **Stage 0** — reconnaissance, costs no budget. Verify the panel hash; run W1-0a … W1-0e; consume
  the shared S0-F (cost model and break-even table), S0-H (outcome dispersion, Filter-A baseline)
  and S0-J (power table) from the ledger. Do not start Stage 1 until the §3 exit gate holds. If the
  §3 cancellation condition fires, W1 is cancelled — write it up and stop.
- **Stage 1** — pre-registration, ledgered, before the fit split is opened.
- **Stage 2** — execute stages 2a–2e on FIT, then the single CHECK score. Measurement and
  interpretation are **separate sessions**: `spike-runner`/`builder` measures, `strategy-analyst`
  interprets. Never one step that does both.
- **Stage 3** — freeze one candidate (or the null), post it to the ledger, **stop**. The holdout pass
  is not yours and is not part of this workstream.

**How to reach the data.** The frozen panel `panel-v1.parquet` and its `panel-v1-spec.md` are on the
`data-export` branch; verify `sha256sum` against the spec committed to `main`. From a cloud session
use the `box-data` skill. On the box use `scripts/box-job.sh`, **per date, one at a time** — never
`docker exec` into the app, never `--all`; it is a 2 vCPU / 4 GB host and a heavy job takes it down
hard. Bar-level work belongs on the Mac. **Never rebuild the panel** — a hash mismatch is a
stop-and-report, not a rebuild.

**Out of scope for you**, because they belong to the other two workstreams: entry mechanics, trigger
definition, fill realism, `bars_1m` and anything below 5-minute resolution, and the interior
parameters of the exit families (all **W2**); time-conditioning, regime gates, market-state features
and refit-cadence measurement (all **W3**). Also out of scope for everyone: the 167 hand reviews
(quarantined, protocol §9.2), the holdout in any form, and any other workstream's plan,
intermediates or candidate.

**Escalation.** Every Stage-0 item in §3 carries a pre-authorised fallback — where one applies, take
it and ledger the amendment. Where none applies — a column missing, the panel hash mismatching, a
split materially thinner than the protocol states, the harness lacking a primitive, your budget
about to be exceeded — **stop**. Write the finding to your sub-issue and the ledger, say exactly what
assumption failed and what you would need, and end the session. Do not substitute a different
column, split, metric or a smaller B. An improvised substitute is an unledgered design change made
by the party with the least context.

**Stopping rule.** You stop at the first of: your 54 trials spent; §9.1's six conditions all met on
one candidate; §3's cancellation condition firing; or any stop-and-report. **The holdout is not
yours to open**, in any form, for any reason, and no result you produce is final until the custodian
has opened it once for all three workstreams together.

**The one thing to get right.** A large enough search finds a beautiful result in noise every time,
and the search that found it always felt disciplined from the inside. This record has already proved
it on itself: 15,434 rule combinations scored 51.0 on the fitting half and **−0.194 R/session** on
sessions they had not seen. Your 54 trials, your ledger and the null are what stand between this
analysis and that outcome. Spend fewer questions with more discipline.

> Copy to here.
