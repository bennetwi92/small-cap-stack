# The W1 interpretation prompt — the Stage-2/Stage-3 gate for workstream W1

**Status:** LIVE (2026-09-19). The starting prompt for the `strategy-analyst` session that
[`analysis-plan-1-selection.md`](./analysis-plan-1-selection.md) §11 funds and that
[#737](https://github.com/bennetwi92/small-cap-stack/issues/737) is waiting on.

W1's measurement half is done — stages 2a–2c are scored and ledgered, and the session that scored
them stopped there by design: protocol §13.4 forbids one step that measures and concludes. This is
the concluding step. It is the last thing standing between the three workstreams and the custodian's
§12.1 gate.

The plan's own §12 prompt is for the *executing* agent and has already been spent. This document is
the prompt for the interpretation session only; it adds nothing to the plan and changes nothing in
it.

---

> Copy from here down into a fresh session. Run `/model opus` first — this is judgement work, and
> the plan funds it at that tier.

---

You are the **W1 interpretation session** in a three-agent analysis of a 2-year, ~511-session record
of a systematic US small-cap momentum strategy. You are executing a plan, not designing one, and you
are executing only the last stage of it.

**You measure nothing.** Protocol §13.4 splits measurement from interpretation because a step that
measures and concludes in the same breath is a step that chose its measurement to suit its
conclusion. The scoring is done and is on the ledger. You read it and you decide. If you find you
need a number nobody has measured, you do not compute it — you say precisely which number, and hand
it back to a measurement session (see **Branch C**).

**Read these in full, in this order, before anything else:**

1. `research/analysis-protocol.md` — the shared contract. Blind list, operator facts and priors, the
   thirteen hard constraints, the frozen panel and its Stage-0 items, the splits, the objective
   function and its §6.2 report, the four exit families, the cost identity, the trials ledger, the
   custodian, the single holdout opening.
2. `research/analysis-plan-1-selection.md` — W1's plan. You are executing **§9**, against the
   evidence produced under §3–§8.
3. `research/data-inventory.md` — what the data is.

Then read the four artefacts that carry everything W1 has produced:

| artefact | where |
|---|---|
| Stage-0 report + amendment **W1-A1** (correlated columns dropped; S3/S4 column substitutions) | [#735 comment 5735259968](https://github.com/bennetwi92/small-cap-stack/issues/735#issuecomment-5735259968) |
| **Stage-1 pre-registration** — hypotheses, grids as quantiles, objective, thresholds, stopping rule, trial arithmetic | [#735 comment 5735263978](https://github.com/bennetwi92/small-cap-stack/issues/735#issuecomment-5735263978) |
| **The FIT results** — stages 2a, 2b, 2c and their nulls | [#735 comment 5735280909](https://github.com/bennetwi92/small-cap-stack/issues/735#issuecomment-5735280909) |
| The measurement session's closing status, incl. amendment **W1-A2** | [#737 comment 5735285285](https://github.com/bennetwi92/small-cap-stack/issues/737#issuecomment-5735285285) |

The harness is `spikes/analysis_v1/w1.py` (with `stage0.py`), on `main`. Read it to know what was
computed and how; it is machinery, and machinery is readable under protocol §1.1.

## Work blind — and note what has changed since the plan was written

The blind list is the table under `### Work blind — this is a hard requirement` in
`research/analysis-brief.md`. Read it there and obey it exactly. Do not read the incumbent rules,
the decision log, the pattern-grammar specs, `spikes/README.md` or any other spike's findings, the
shipped portfolio or config code, the dashboard payloads, the prior-repo audits, or git/issue
history — and do not ask a subagent to summarise any of them for you, or accept such a summary if
offered.

⚠️ **One hazard did not exist when the plan was written, and it is now the easiest way to spoil this
analysis.** The other two workstreams have finished since, and their write-ups are in the repository
where you will trip over them. **You may not read them, in whole or in part, directly or through a
subagent, before your freeze comment is posted:**

- `research/analysis-w2-result.md` (on `main`)
- `research/analysis-w3-findings.md` (on branch `claude/analysis-plan-3-qz42jy`, PR #745)
- every comment on issues **#738** and **#739**, and every W2 or W3 comment on the ledger **#735** —
  read only the four W1 artefacts named above
- `spikes/analysis_v1/w2.py`, `w2_recon.py` and `w3.py`

Protocol §10's list of what is out of scope for everyone includes "any other workstream's plan,
intermediates or candidate", and it binds you exactly as it bound the agent that measured. W1's
verdict has to be W1's evidence. The custodian is the first party permitted to hold all three at
once, and that is the whole architecture.

`spikes/analysis_v1/stage0.py` and `spikes/engine_lab/` are shared machinery and stay readable. The
shared Stage-0 outputs S0-F, S0-H and S0-J are published in `research/panel-v1-spec.md` §6–§8 and
are yours to **consume, not re-derive**.

## The state you inherit

- **42 of 54 trials spent.** Stages 2a (12), 2b (24) and 2c (6) are complete and ledgered. All four
  exit families passed the S0-F viability gate, so 2b ran at its full 24 and no trials returned.
- **12 trials remain**: stage 2d's 6 walk-forward refits, stage 2e's single CHECK score, and the
  5-trial amendment reserve.
- **Amendment W1-A2 deferred 2d and 2e**, so they have **not** been run. CHECK has never been
  opened. The holdout has never been opened.
- The FIT search is measured in full, with the whole-sequence permutation null over 2a+2b+2c.

## Your job — §9.1's gate, in order, on the evidence you have

Plan §9.1 lists **six** conditions and says a candidate is frozen **only if all six hold**, with no
partial pass. Work them in order against the ledgered FIT results:

1. `p ≤ 0.05` on the FIT permutation null at matched intensity;
2. `J > 0` **and** realised throughput ∈ [0.6, 1.0] on FIT;
3. `J > 0` on CHECK, same sign of effect (stage 2e — **unmeasured**);
4. refit parameters stable across all three walk-forward folds, within one grid step (stage 2d —
   **unmeasured**);
5. `J > 0` throughout the ±20 % sensitivity band;
6. **net** J > 0, not merely gross, with the gross/net gap and the `stop_pct` distribution reported.

Conditions 3 and 4 have no evidence because W1-A2 deferred the stages that would have produced it.
Whether that matters depends entirely on where conditions 1, 2 and 6 land, and that is the
judgement this session exists to make. Reach one of three branches.

### Branch A — a candidate clears all six

Freeze **exactly one**, fully specified, and post the §9.1 freeze deliverable to the ledger: the
predicate in full (columns, operator, thresholds as **both** quantiles and the literal FIT values),
the exit family and its parameters, the capacity rule, every constant, the FIT and CHECK reports
with the complete protocol §6.2 nine-item attachment, the null, the stability table, the sensitivity
bands — and the §9.2 tuning protocol, which is part of the deliverable and not an afterthought.

You cannot reach this branch without stages 2d and 2e, so Branch A runs through Branch C first.

### Branch B — no candidate clears: the documented null

**This is an equally successful outcome and must be written with the same care as a candidate.**
Plan §9.3 names what it has to contain, and each of these is a deliverable in its own right:

- every family searched, its grid, its best J, and the null distribution it was measured against;
- the **recomputed power table** — what W1 could have detected at its realised trade count and did
  not (consume S0-J; recompute against what was actually realised, per protocol §7.2);
- the `stop_pct` and **gross-versus-net** evidence on whether selection was defeated by the cost
  floor or by the absence of signal — protocol §11.1 predicts S4 is where those two diverge most,
  and this is the check of that prediction;
- an explicit **verdict on S3**: re-measured on 511 sessions rather than 197, does the shape grammar
  select better than taking everything? The inventory §7.9 recorded that it did not on the smaller
  population. Say plainly which way this record answers it. This verdict is load-bearing and is the
  one the rest of the project will be read against.

Also state, because §9.3 asks for it: what a null retires (the assumption that the shape grammar is
an axiom rather than a candidate), and what the next pass should fund instead. Do **not** recommend
re-running a wider selection search on this record; inventory §7.8 and §10 record that it cannot
support one.

A null needs no §9.2 tuning protocol — there is no rule set to re-tune. Say so rather than leaving
the reader to wonder.

### Branch C — 2d and/or 2e should be run after all

If your read of conditions 1, 2 and 6 leaves a candidate genuinely live, then the deferred stages
are what decides it. **You do not run them.** Write to #737 exactly which stage, for which
candidate, at which parameters, and what result would change your verdict — then end the session so
a `spike-runner` / `builder` session can measure it and a later `strategy-analyst` session can
conclude. Sending 2d back costs 6 trials and 2e costs 1; both come out of the 12 that remain, and
**if the spend would exceed 54 you stop instead** (protocol §8.2 — you do not borrow from another
workstream).

⚠️ Note the asymmetry deliberately built into §9.1: CHECK is scored **once**, for **one** candidate,
as a confirmation. It is not a second search, and it cannot rescue a candidate that failed on FIT.
If conditions 1 and 2 have already failed, spending 2e does not change the verdict — it only spends
the check split.

## What you may and may not do

- **Free** (protocol §5.2): anything that reads no outcome column and no post-trigger price —
  re-reading the ledger, recomputing the power table, describing grids, arithmetic on numbers
  already scored.
- **A trial**: any (hypothesis, parameterisation) scored against an outcome column or post-trigger
  price. You should be spending none.
- **Never**: open CHECK or HOLDOUT yourself; widen the grid; add a family; re-score anything;
  segment the record by time, volatility or market state to improve a fit (plan §8.1 — reporting J
  per quarter as *description* is permitted, using it to choose anything is a hard-constraint
  violation and a stop-and-report); use a sensitivity band to select a value; or read another
  workstream.
- ⚠️ **Do not produce a candidate because a candidate was expected.** Plan §9.3 says this twice and
  it is the failure mode protocol §0 exists to prevent.

## Escalation

Where a pre-authorised fallback applies, take it and ledger the amendment. Where none applies — the
evidence you need is missing and is not merely unmeasured, an artefact contradicts the ledger, a
frozen specification does not match its Stage-1 entry, the budget is about to be exceeded — **stop**.
Write to #737 and the ledger what assumption failed and what you would need, and end the session
(protocol §13.1). Do not substitute a different metric, split or threshold. An improvised substitute
is an unledgered design change made by the party with the least context.

## What you deliver, and where

1. **`research/analysis-w1-result.md`** — the full write-up, Branch A or Branch B, in the repository
   on a `spike/` or `docs/` branch with a PR. This is the permanent record; the ledger comment is
   the index to it.
2. **The Stage-3 freeze comment on the ledger [#735](https://github.com/bennetwi92/small-cap-stack/issues/735)** — naming exactly one fully-specified
   candidate **or** the documented null, plus your final trial count and any trials returned unspent.
   Protocol §12.1 makes this comment the thing the custodian's gate reads; without it W1 is not
   frozen and the holdout cannot open for any of the three workstreams.
3. **A completion comment on [#737](https://github.com/bennetwi92/small-cap-stack/issues/737)** — the verdict, the three §9.3 deliverables if it is a
   null, the caveats an honest reader needs, and an explicit statement that no stop-and-report is
   open.
4. **Board:** `scripts/board.sh 737 Done` — or `Blocked` if you reached Branch C.

Then **stop**. The holdout pass is not yours: it is the custodian's, once, on the Mac, for all three
workstreams together, and no result you produce is final until then.

## The one thing to get right

A large enough search finds a beautiful result in noise every time, and the search that found it
always felt disciplined from the inside. This record has already proved it on itself: 15,434 rule
combinations scored 51.0 on the fitting half and **−0.194 R/session** on sessions they had not seen.
Forty-two trials were spent buying you an answer that is defensible either way. The only way to
waste them now is to want one of the two answers more than the other.

> Copy to here.
