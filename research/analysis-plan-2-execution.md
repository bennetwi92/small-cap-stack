# Analysis plan 2 — entry mechanics, exit interiors and the cost floor: *how to enter and exit them*

**Status:** LIVE (2026-09-18). Workstream **W2** of the three-agent analysis of the 2-year Phase-1
record.

> **Read [`analysis-protocol.md`](./analysis-protocol.md) first, in full.** It carries the blind
> list, the operator facts and priors, the thirteen hard constraints, the frozen panel, the splits,
> the objective function, the exit families, the cost identity, the trials ledger, the custodian and
> the single holdout opening. This document carries only what is specific to W2. Where the two
> appear to disagree, the protocol wins and the disagreement is a stop-and-report.

The copy-paste starting prompt for the W2 agent is [§12](#12-the-starting-prompt).

---

## 1. The question

> **Holding selection fixed at the a-priori Filter A, how much net R per session is recoverable by
> changing only how a trade is entered and exited — and which of the four exit families actually
> clears its cost floor at $500?**

Why it is worth 36 of the 120 global trials:

- It attacks the **declared binding constraint**. At $500 with a per-order commission minimum, the
  cost floor decides which exit families exist at all. Protocol §11.2 shows the inventory
  contradicting itself about that floor by nearly **3×**, and the contradiction is the difference
  between the scalp branch needing a 73 % net hit rate and needing 86 % — demanding versus
  impossible. Nothing else in the analysis is worth funding until that number is measured.
- Much of its budget buys **estimation, not search** — fixed-parameter measurements with no free
  parameter to overfit. A trial spent on "what is the achievable fill, really?" buys more certainty
  than a trial spent on another threshold.
- It owns the **largest untouched asset in the record**: `bars_1m`, ~32 M rows of 1-minute
  pre-market tape that nothing in the package has ever read (inventory §2.4). It is the only place a
  question about the microstructure of the break, fill realism or a finer trigger can be asked at
  all.
- Its Stage-0 contribution **gates the other two workstreams** (S0-F), which is why W2's break-even
  derivation is pulled forward into shared Stage 0 rather than living inside W2.

⚠️ **W2 owns execution. It does not own selection (W1) or timing (W3).** See §10.

---

## 2. The population and the panel

**Population:** the **Filter A stream** — the frozen panel restricted by protocol §9: pre-market
triggers, `hits_before_trigger ≥ 1`, `cum_dollar_vol_to_trigger ≥ q50(FIT)`, capacity `N = 1`
earliest by time. Filter A is a priori, frozen, never tuned, and costs no trials. Its constants are
literals published in `panel-v1-spec.md`.

**Why W2 holds selection fixed:** selection is W1's question, and a workstream that varied both
selection and execution would be searching a product space it cannot afford and would produce a
result neither workstream could use. W2's job is to make its answers **transferable** to whatever
selection W1 lands on — which is what §6's stratification requirement is for.

**Two bar grids, two different reaches** (inventory §2.3, §2.4; verified against `harvest/runner.py`
in protocol §11.3):

| grid | window | halves | what W2 uses it for |
|---|---|---|---|
| `bars_1m` | `[04:00, 09:30)` ET, 1-minute | **recon only** | entry mechanics and fill realism |
| `bars` | full session, 5-minute | both | exit interiors, the whole trade path |

⚠️ **This asymmetry is the central structural fact of W2 and must be stated in every result it
produces.** The 1-minute fill-realism measurement exists on the recon half only; its transfer to the
live half is **untested**, and §7.2 says what W2 does about that instead of pretending otherwise.

**Verification is by bytes.** `sha256sum panel-v1.parquet` against `panel-v1-spec.md` on `main`
before anything else. A mismatch is a stop-and-report. Never rebuild the panel.

---

## 3. Stage 0 — W2's own reconnaissance

The shared items S0-A … S0-J (protocol §4.5) run centrally. **W2 is the workstream whose findings
feed S0-E and S0-F**, so its Stage 0 is entangled with the shared one — and the sequencing is
deliberate: S0-F must be published *before any workstream opens a fit split*, so its measurement is
run first, by the Stage-0 builder, with W2 consuming the published result rather than re-deriving
it.

W2 adds four items of its own. Three are free; one is the shared S0-F, named here because W2 is its
principal consumer.

| id | item | outcomes? | decision rule / pre-authorised fallback |
|---|---|---|---|
| **W2-0a** | `bars_1m` coverage audit: rows per symbol-day and sessions covered, across all 453 recon sessions. The dataset is written behind a `harvest_store_minute_bars` switch, so early sessions may predate it. | no | **≥ 95 %** of recon sessions covered → proceed as planned. **80–95 %** → proceed on the covered subset and report the coverage on every 1-minute result. **< 80 %** → the 1-minute items **E1 and E2 are cancelled**, their 15 trials return to the global budget, and W2 continues as a 5-minute-only workstream. (This is S0-E's rule; W2 executes it.) |
| **W2-0b** | **Grid reconciliation** — do the 1-minute bars aggregate to the stored 5-minute bars? Compare `aggregate(bars_1m, 5)` against the stored `bars` over a sample of symbol-days, including IBKR's flat zero-volume filler candles, which `reconstruct.aggregate` synthesises because the engine counts bars, not minutes. | no | Material disagreement (> 1 % of bars differing in OHLC beyond float tolerance) → **stop-and-report**. The two grids disagreeing means one of them is not what it claims, and every W2 result rests on them being the same tape at two resolutions. |
| **W2-0c** | Recon session window: max bar timestamp per recon session on the 5-minute grid (protocol §11.3). | no | Reaching **≥ 15:55 ET on ≥ 95 %** of recon sessions → **F3 and F4 may hold past the bell on recon**. Otherwise every family is additionally evaluated under a **09:30 hard close on recon** and the difference reported; ledger the amendment. (This is S0-E's other rule.) |
| **W2-0d** | Consume the published **S0-F** cost audit: `c` as a distribution over `stop_pct` deciles, and the recomputed break-even table for F1–F4. | shared (2 trials, charged to Stage 0) | **If F1's measured net break-even exceeds 80 %, F1 is cancelled across all three plans** and its trials return to the global budget unspent. Do not fund a branch the arithmetic has already killed. |

**Stage-0 exit gate for W2:** S0-E, S0-F, S0-H and S0-J posted to the ledger; the panel hash
verified; W2-0b reconciliation clean; the surviving exit-family set and the 1-minute coverage tier
both known and ledgered.

**Cancellation condition:** if W2-0b finds the two bar grids materially disagree, **W2 stops
entirely** and the finding is escalated — it is a data-integrity problem that invalidates more than
this workstream. If S0-F cancels F1 *and* S0-H reports F3 and F4 under-powered at W2's allocation,
W2 reduces to F2 alone and its plan is re-scoped by amendment before Stage 1, not during Stage 2.

---

## 4. The hypothesis budget: 36 trials

| stage | what | trials |
|---|---|---|
| **E1 — fill realism** *(estimation, no free parameter)* | for each of the 3 entry mechanics in §5.1, measure the achievable fill distribution on `bars_1m` and the R degradation against the conservative 3-tick 5-minute fill | **3** |
| **E2 — entry × exit** | 3 entry mechanics × 4 exit families | **12** |
| **E3 — F3 interior** | arming threshold ∈ {0.5 R, 1 R, 1.5 R} × trailing reference ∈ {prior bar low, 2-bar low} | **6** |
| **E4 — F4 interior** | scale fraction ∈ {⅓, ½, ⅔} × scale point ∈ {1 R, 1.5 R} | **6** |
| **E5 — selection-dependence** | the 2 best families re-scored under 2 a-priori `stop_pct` strata (below / above the FIT median) | **4** |
| **E6 — walk-forward refits** | 3 folds × 1 candidate specification. ⚠️ **Every refit is a trial** — prior (2). | **3** |
| **E7 — check** | the single candidate scored on CHECK | **1** |
| **reserve** | amendments arising from Stage-0 findings | **1** |
| **total** | | **36** |

⚠️ **If S0-F cancels F1**, E2 becomes 3 × 3 = 9 and **3 trials return to the global budget
unspent**. **If W2-0a lands under 80 % coverage**, E1 and E2 are cancelled and **15 trials return**.
Returned trials are never re-spent inside W2.

**Nulls at matched intensity** (protocol §7.3): the null for E2 re-runs all 12 points against each of
B = 200 block-shuffled records; the final null re-runs the **entire** E1–E5 sequence, because that
sequence is what produced the winner.

⚠️ **E1 is charged even though it is a measurement.** Achievable fill is computed from prices *after*
the trigger, and protocol §5.2 is explicit that post-trigger price is an outcome. Estimation is
cheaper than search in certainty-per-trial, not in ledger entries.

---

## 5. The hypothesis class

### 5.1 Entry mechanics — three, and only three

All three break above the last consolidation candle's high; they differ in what counts as the break
and what fill is assumed.

| id | mechanic | resolution | what it tests |
|---|---|---|---|
| **M1** | the incumbent-shaped baseline: mechanical trigger 1 tick above the consolidation high, R measured against a conservative 3-tick fill (inventory §3.2) | 5-minute | the reference point. Carried so every other mechanic has something to be better than. |
| **M2** | **1-minute confirmation**: trigger only when a *1-minute* bar **closes** above the consolidation high | 1-minute (recon) | whether requiring a close rather than a touch buys enough fewer false breaks to pay for the later entry |
| **M3** | **1-minute achievable fill**: M1's trigger, but the fill taken from the next 1-minute bar's actual traded range rather than assumed at 3 ticks | 1-minute (recon) | whether the 3-tick assumption is generous, conservative, or wrong in a way that varies with `stop_pct` |

**Out of the class, explicitly:** any entry that reads a column outside `RULE_COLUMNS`; any entry
requiring within-day ranking (constraint 1); any entry decidable only after the fact; any limit order
whose fill cannot be established from the tape (an unfillable limit is an opportunity that could not
have been traded, and constraint 2 forbids reporting a statistic on it — including as an upper
bound).

### 5.2 Exit interiors

The four families are defined **once** in protocol §10 and are not redefined here. W2 searches their
interiors:

- **F1 (0.5 R scalp)** and **F2 (2 R base)** have no interior — the target *is* the family. They
  appear in E2 only.
- **F3 (runner)** — the arming threshold and the trailing reference (E3).
- **F4 (hybrid)** — the scale fraction and the scale point (E4).

W1 uses the protocol §10 defaults for F3 and F4 and does **not** vary them; that is the whole
division of labour. If W2's E3/E4 result differs from the defaults, it is reported as an execution
finding for the next phase — **not** back-propagated into W1's frozen candidate, because W1's
candidate was fitted under the defaults and swapping its exit after the fact would make its
validation meaningless.

### 5.3 The cost floor is a measurement, not a hypothesis

Protocol §11.1's identity:

```
c ≈ (2 × commission_per_side) / (BP × stop_pct)  +  2 × slip_pct / stop_pct
```

Both terms scale as `1 / stop_pct`. W2's job is to replace the two assumed quantities —
`commission_per_side` against the broker's real fills, and `slip_pct` against the 1-minute tape —
with measured ones, and to report `c` **as a distribution over `stop_pct` deciles**, never as a
scalar. That is S0-F's output, and W2 is the reason it exists.

---

## 6. The objective function

**Protocol §6, unchanged: mean net R per session**, with the full §6.2 report on every scored block.

Two W2-specific additions, both required on every result:

1. **Every exit conclusion is reported as a function of `stop_pct` decile.** Not as a pooled mean.
   This is what makes W2's answer transferable: W1's candidate will have some `stop_pct`
   distribution, and an execution specification stated per decile can be applied to it, while a
   pooled one cannot. Protocol §11.1 predicts the gross/net gap widens as `stop_pct` narrows — this
   is the reporting that checks the prediction.
2. **The gross/net gap is a headline number, not a footnote.** W2 is the workstream where a gross
   improvement that worsens net is most likely, because every mechanic that enters earlier or exits
   tighter trades R against cost. Constraint 6 decides, always.

---

## 7. Validation design

### 7.1 The usual apparatus

- **Walk-forward with rolling refit**, three expanding-window folds inside FIT (protocol §7.2), with
  **parameter stability** reported — do E3/E4's chosen interiors land in the same neighbourhood
  across folds?
- **Permutation null, block-shuffled by session, B = 200, at matched search intensity** (protocol
  §7.3). `p > 0.05` on FIT and the candidate does not proceed.
- **±20 % sensitivity** on every frozen interior parameter, reported, never used to select.
- **Multiplicity**: the permutation null over the whole E1–E5 sequence is primary; Bonferroni at
  α = 0.05/36 is the cross-check. The cross-workstream correction is the custodian's.

### 7.2 The recon-only problem, handled rather than hidden

E1, M2 and M3 exist on the recon half only, and FIT and CHECK are recon-only anyway — so the
*fitting* is unaffected. The problem is the **holdout**, which is ~65 recon sessions plus all 58 live
ones, and where a 1-minute-derived fill correction has never been tested.

W2 does three things about it, all of them stated in the freeze report:

1. **The frozen candidate must be evaluable on the live half.** If M2 or M3 wins, the candidate ships
   as *M1's trigger with a measured fill correction expressed at 5-minute resolution*, so the
   custodian can score it on live rows. A candidate that can only be evaluated on recon is not a
   candidate — it would make constraint 7's both-halves report impossible.
2. **A 5-minute cross-check of the fill assumption on both halves** (free — it reads only the
   already-charged E1 outputs and the 5-minute grid): compare the realised `entry_price` versus
   `entry_fill` gap distribution on live rows against recon rows. If the two distributions differ
   materially, the 1-minute correction's transfer is **reported as unestablished** and the candidate
   ships with the plain 3-tick fill instead. That is the conservative branch and it is taken by
   default when the evidence is ambiguous.
3. **The limitation is named in the freeze report**, not discovered by the custodian.

---

## 8. The regime treatment (prior 3) and the exit treatment (prior 4)

### 8.1 Regime — W2 does none, by construction

W2 pools across time. Prior (3)'s leak is blocked here by the population being **Filter A** — a
priori, frozen, with its single data-dependent constant fitted on FIT alone — so W2 is never in the
position of having fitted a filter on the whole record and then studied it. W2 may not subset by
time, volatility or market state to improve a result; that is a hard-constraint violation and a
stop-and-report. W3 owns timing.

W2 does report the §6.2 per-session distribution and the deepest cold stretch, because prior (1)
requires every result to carry them.

### 8.2 Exit — the family is fixed, the interior is joint with entry

Prior (4) says selection × exit is joint. W2's slice of that is **entry × exit**, searched as a
crossed design (E2: 3 mechanics × 4 families), because a mechanic that enters earlier helps a runner
and hurts a scalp, and the reverse. The interiors (E3, E4) are searched within family, after E2 has
said which families are alive.

**Selection-dependence is tested, not assumed** (E5): the two best families are re-scored under two
a-priori `stop_pct` strata. If the exit conclusion flips between strata, W2's deliverable becomes a
**per-decile specification** rather than a single spec — which is the honest form and is why §6
requires per-decile reporting in the first place.

**Break-even per family comes from S0-F and is consumed, not re-derived** (protocol §11.2). If S0-F
cancels F1, the crossed design is 3 × 3 and three trials return unspent.

---

## 9. What "done" looks like

### 9.1 The candidate branch

W2 freezes **one execution specification** — an entry mechanic, an exit family, its interior
parameters, and the `stop_pct`-decile qualification — only if all of:

1. `p ≤ 0.05` on the FIT permutation null at matched intensity;
2. `J > 0` and realised throughput ∈ [0.6, 1.0] on FIT, on the Filter-A stream;
3. `J > 0` on CHECK, same sign of effect, scored **once**;
4. interior parameters stable across all three walk-forward folds;
5. `J > 0` throughout the ±20 % sensitivity band;
6. **net J > 0** and the gross/net gap reported per `stop_pct` decile (§6);
7. **evaluable on the live half** (§7.2.1) — or shipped with the plain 3-tick fill.

### 9.2 The viability verdict — a deliverable in its own right

Independent of whether a candidate clears, W2 must deliver the thing the whole analysis is waiting
on:

> **A per-family viability table**: for F1–F4, the measured `c` by `stop_pct` decile, the implied net
> break-even hit rate, the realised hit rate on the Filter-A stream on FIT, and a plain verdict —
> **viable / marginal / dead at $500**.

This is the output that makes the difference between discovering the cost floor in a plan and
discovering it in six sessions of execution. It ships even if §9.1 fails.

### 9.3 The tuning protocol (prior 2)

- **What may be re-tuned:** the interior parameters of the chosen exit family (F3's arming
  threshold, F4's scale fraction and point) and the fill correction. **Not** the entry mechanic, not
  the family.
- **On what window:** a rolling 250-session window. **How often:** quarterly, or whenever the
  measured `c` distribution shifts by more than 20 % at the median `stop_pct` decile — the cost
  model is the thing most likely to move under W2, because it depends on the broker and the account,
  not on the market.
- **Re-tune versus rescue:** a refit inside the ±20 % sensitivity band is a **re-tune** — apply,
  ledger, continue. Outside it is a **rescue** — do not apply; the execution model has stopped
  describing the fills, and that needs a new pre-registration, not a wider band.
- ⚠️ **Every refit is ledgered as a trial**, forward, forever.

### 9.4 The null branch — a success, explicitly

If nothing clears §9.1, W2's deliverable is the §9.2 viability table plus a documented null: every
mechanic and interior searched, its null distribution, the recomputed power table saying what could
have been detected, and — the finding that matters most —

> **whether the binding constraint is the strategy or the account.** If no exit family clears its
> cost floor at $500 under any entry mechanic, then the next phase's question is **capital, not
> rules**: what account size moves F1 or F4 from dead to viable, given `c ∝ 1 / (BP × stop_pct)`.
> That is a directly actionable null and it is worth the whole workstream.

What gets retired on a null: the 3-tick fill assumption, if E1 shows it is wrong. What the next
phase does instead: size the account, or restrict selection to a `stop_pct` band where the cost
floor is survivable — the latter being a **selection** finding handed to a future W1, not applied
here.

**A documented null is a successful outcome of W2.**

---

## 10. What is out of scope for W2

| out of scope | whose it is |
|---|---|
| any selection rule, any feature threshold, any change to Filter A | **W1** |
| time-conditioning, regime gates, market-state features, refit-cadence measurement | **W3** |
| back-propagating an E3/E4 interior into W1's candidate | nobody — see §5.2 |
| the 167 hand reviews (quarantined — protocol §9.2) | Stage 0, once, redacted |
| the holdout, in any form | the custodian, once (protocol §12) |
| reading another workstream's plan, intermediates or candidate | nobody, before the holdout pass |

---

## 11. Execution shape and cost

⚠️ **W2 is the Mac workstream.** `bars_1m` is ~32 M rows and the recon `bars` ~14 M; neither can be
moved into a cloud session against a GitHub branch (inventory §9), and the box is a 2 vCPU / 4 GB
CX23 that a job this shape takes down hard.

| piece | where | tier |
|---|---|---|
| W2-0a, W2-0b, W2-0c — coverage, grid reconciliation, session window | **Mac**, direct store access | `builder` |
| E1 fill realism on `bars_1m` | **Mac** | `spike-runner` — measurement only |
| E2–E5, the nulls (B = 200 × 25 points), the walk-forward | **Mac**; the 5-minute path replays may be exported as a per-setup path bundle and run in a cloud session if the Mac is the bottleneck | `spike-runner` |
| every interpretation step: family viability, which mechanic wins, the freeze report | separate session | `strategy-analyst` (opus) |

If any part must touch the box: `scripts/box-job.sh`, **per date, one at a time**, never `docker
exec` into the app, never `--all`, and watch `free -m`.

**Cost estimate: 6–8 sessions** — ~2 `builder` (Stage 0 and the grid reconciliation), ~3–4
`spike-runner` (E1–E7 and the nulls), ~2 `strategy-analyst` (the viability verdict and the freeze
report). One task per session, then `/clear`.

---

## 12. The starting prompt

> Copy from here down into a fresh session.

---

You are the **W2 agent** in a three-agent analysis of a 2-year, ~511-session record of a systematic
US small-cap momentum strategy. You are executing a plan, not designing one. Three agents are
running concurrently against one record; none of you can ask a question mid-flight, and the controls
below are what stop the three of you burning the only 2-year record this project will have for
another two years.

**Read these two documents in full, in this order, before anything else:**

1. `research/analysis-protocol.md` — the shared contract. Blind list, operator facts and priors, the
   thirteen hard constraints, the frozen panel and its Stage-0 items, the splits, the objective
   function, the four exit families, the cost identity, the trials ledger, the custodian, the single
   holdout opening.
2. `research/analysis-plan-2-execution.md` — this plan. You execute §3 through §9.

Then read `research/data-inventory.md` for what the data is.

**Work blind.** The blind list is the table under `### Work blind — this is a hard requirement` in
`research/analysis-brief.md`. Read it there and obey it exactly. Do not read the incumbent rules, the
decision log, the pattern-grammar specs, `spikes/README.md` or any spike's findings, the shipped
portfolio or config code, the dashboard payloads, the prior-repo audits, or git/issue history — and
do not ask a subagent to summarise any of them for you, or accept such a summary if offered. The one
carve-out is protocol §1.1: `spikes/engine_lab/` is readable **as machinery**; its `SHIPPED` /
`baseline()` constants may be *executed* as a benchmark and may **not** be read into a search. You
may read `capture.py`, `storage.py` and `harvest/` to learn what a column means — never to learn what
was done with it. If you need a mechanical fact only a blinded file holds, take it from the
inventory; if the inventory is silent or wrong, **say so in the ledger** and work around it.

**Your question:** holding selection fixed at the a-priori Filter A, how much net R per session is
recoverable by changing only how a trade is entered and exited — and which of the four exit families
actually clears its cost floor at $500?

**Your deliverables:** (a) the **per-family viability table** of §9.2 — measured cost `c` by
`stop_pct` decile, implied net break-even, realised hit rate, and a plain viable/marginal/dead
verdict. This ships whatever else happens. (b) Exactly **one** frozen execution specification, or a
documented null — both equally successful outcomes. Do not produce a candidate because one was
expected.

**Your budget: 36 trials**, from a global budget of 120 shared with two other agents. A trial is one
(hypothesis, parameterisation) scored against any outcome column **or post-trigger price** — which
means your fill-realism measurements are charged even though they have no free parameter. A k-point
grid is k trials. **Every walk-forward refit is a trial.** A permutation null is not a trial but must
run at matched intensity. A sensitivity band is not a trial and may never be used to *select* a
value. Coverage, nullity and grid-reconciliation checks are free. The allocation is in §4, stage by
stage. **If you would exceed it, stop** — you do not borrow from another workstream.

**Ledger before you open data.** The trials ledger is the GitHub issue named in your workstream
sub-issue. Post your Stage-1 pre-registration comment — hypotheses, grids, objective, success
threshold, stopping rule, trial arithmetic — **before** you open the fit split. Post a batch comment
before each scoring batch with your running total, and every amendment as its own comment before you
act on it. You post and proceed; you do not wait for approval. The custodian verifies your frozen
specification against your ledger entries, and one that does not match is not scored.

**The stage gates. Do not open with the analysis.**

- **Stage 0** — reconnaissance, costs no budget except the shared S0-F. Verify the panel hash; run
  W2-0a (`bars_1m` coverage), W2-0b (**grid reconciliation** — if the 1-minute bars do not aggregate
  to the stored 5-minute bars, stop entirely and escalate; it invalidates more than this
  workstream), W2-0c (recon session window); consume the published S0-F, S0-H and S0-J. Do not start
  Stage 1 until the §3 exit gate holds.
- **Stage 1** — pre-registration, ledgered, before the fit split is opened.
- **Stage 2** — execute E1–E7 on FIT, then the single CHECK score. Measurement and interpretation are
  **separate sessions**: `spike-runner` measures, `strategy-analyst` interprets. Never one step that
  does both.
- **Stage 3** — freeze one specification (or the null) plus the viability table, post to the ledger,
  **stop**. The holdout pass is not yours.

**Where you run.** You are the **Mac** workstream. `bars_1m` is ~32 M rows and the recon `bars` ~14 M
— neither can be moved into a cloud session against a GitHub branch. ⚠️ **The box is a 2 vCPU / 4 GB
CX23 and a heavy job takes it down hard**: if you must touch it, use `scripts/box-job.sh`, per date,
one at a time, never `docker exec` into the app, never `--all`, and watch `free -m`. The frozen panel
is on the `data-export` branch; verify `sha256sum` against `panel-v1-spec.md` on `main`. **Never
rebuild the panel** — a hash mismatch is a stop-and-report.

**Out of scope for you**, because they belong to the other two workstreams: any selection rule, any
feature threshold, any change to Filter A (all **W1**); time-conditioning, regime gates, market-state
features and refit-cadence measurement (all **W3**). Also: do not back-propagate an exit interior
into W1's candidate — its validation was done under the protocol §10 defaults and swapping the exit
afterwards would void it. Out of scope for everyone: the 167 hand reviews (quarantined, protocol
§9.2), the holdout in any form, and any other workstream's plan, intermediates or candidate.

**Escalation.** Every Stage-0 item in §3 carries a pre-authorised fallback — take it and ledger the
amendment. Where none applies — the grids disagreeing, the panel hash mismatching, coverage below
the stated tier, the harness lacking a primitive, your budget about to be exceeded — **stop**. Write
the finding to your sub-issue and the ledger, say exactly what assumption failed and what you would
need, and end the session. Do not substitute a different grid, a different fill assumption, a
different metric or a smaller B.

**Stopping rule.** You stop at the first of: your 36 trials spent; §9.1's seven conditions met on one
specification; §3's cancellation condition firing; or any stop-and-report. **The holdout is not
yours to open**, in any form, for any reason.

**The one thing to get right.** At $500 a gross improvement that worsens net is a failure, and yours
is the workstream where that is most likely — every mechanic that enters earlier or exits tighter
trades R against cost. Constraint 6 decides, always. And measure the cost floor before you fund the
branch it might already have killed: it is far cheaper to discover that in arithmetic than in six
sessions of execution.

> Copy to here.
