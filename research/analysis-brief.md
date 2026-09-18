# The analysis brief — the prompt for the end-to-end design pass

**Status:** LIVE (2026-09-18).

The 2-year harvest is complete and the prior reports were cleared (#729) so the analysis starts
from the data rather than from conclusions drawn on partial windows. This file holds the brief
handed to an **opus** agent (`strategy-analyst`, or an opus session) whose deliverable is the
**design of the analysis**, end to end — not its results.

Read [`data-inventory.md`](./data-inventory.md) first; the brief assumes it.

Why a design pass before an analysis pass: this project has run ≥150 threshold variants, a
15,434-combination search and three parallel engine-lab investigations, and the two rules that
shipped out of them (§D-39, §D-40) both collapsed out of sample. The binding constraint is not
compute or data — it is **how many hypotheses the record can support**. That budget has to be
allocated deliberately, in advance, by something expensive enough to think about it. Hence: design
first, pre-register, then execute.

---

## The prompt

> Copy from here down.

---

You are designing an end-to-end data-science analysis of a systematic small-cap momentum strategy.
Your deliverable is **the analysis plan**, not the analysis. You will not fit a model, sweep a
threshold or report a number in this pass. Someone else — probably several agents — will execute
what you write, and the plan is what stops them wasting the only 2-year record this project will
have for another 2 years.

### The objective

Walk away from the executed analysis with **a candidate strategy worth refining in the next
phase**: a specific, mechanically-decidable selection + exit + capacity rule set, with an honest
out-of-sample estimate of what it does, and a stated confidence that distinguishes it from the
null. "Refine in the next phase" means it must be concrete enough to run forward on paper, and
honest enough that running it forward is informative rather than reassuring.

A plan that concludes "the record does not support a profitable rule set, and here is the evidence
and what would" is a **successful** outcome of this design. Do not design an analysis that can only
succeed.

### Start here, in this order

1. `research/data-inventory.md` — what exists, at what grain, and §7 "Known biases, holes and
   traps". Every trap there has already cost this project something.
2. `research/strategy.md` — the canonical spec (the three stages: scan / engine / book, and the
   selection-vs-execution dividing line). Do not restate its numbers anywhere.
3. `spikes/engine_lab/README.md` and `spikes/engine_lab/common.py` — the existing harness. It
   already implements the population, the splits, no-lookahead enforcement, book construction,
   net-of-cost scoring, walk-forward, permutation testing and sensitivity. **Do not fork it.**
4. `spikes/README.md` §`rule_sweep.py`, §`vix_regime.py`, §`adaptive_book_sweep.py`,
   §`regime_*` — what has already been searched and what it returned. `grep` the headings; the
   file is ~14,600 tokens, never read it whole.
5. `research/decisions.md` — read the index, then only the `§D-nn` you need. §D-35 (engine
   selects, book executes), §D-38, §D-39, §D-40, §D-44 (three shape gates removed), §D-45, §D-47
   are the live ones for this. ~38,000 tokens whole — `sed -n` the range.

### What is already known, so you do not re-derive it

State these as priors in your plan and design *around* them:

- **The shape gates select no better than taking everything.** On 197 sessions, `passed` kept 271
  rows at a 0.247 hit rate against a 0.249 base rate. Three gates were removed on that evidence.
  The bull-flag grammar is a *segmentation device* that defines entry and stop; treat its gates as
  one candidate filter among many, never as an axiom.
- **Threshold sweeps overfit this record, measurably.** 15,434 combinations searched against a
  25 % base rate; the same search on shuffled outcomes found 36.7/100 by luck alone (43.7
  best-of-200). Real data scored 51.0 on the fitting half and **−0.194 R/session** on unseen
  sessions. 19 of the top 20 systems were negative out of sample.
- **The stable findings that survived both halves** are worth building on, cautiously: *freshness*
  (scanner attention before the break — `≤1 hit` gave +0.059 over base, appearing in 18 of the top
  20 combinations), stop distance, and price floor. A 2.0R target was chosen 29 times in the top
  50 systems and agrees with two independent investigations.
- **Costs decide.** At $500, the commission minimum eats ~7 % of every R before slippage and ~10 %
  after. Gross improvements that worsen net are common here. Net is the only number that counts.
- **Capacity is the objective's denominator.** R per session, not hit rate — hit rate cannot
  compare a 2R filter against a 4R one. The trader wants ~0.8 trades/day; that constraint is
  pre-declared and therefore costs no evidence.
- **Selection, not pattern, is where edge has been found** — in this record twice, and in the one
  large-sample intraday study that found a durable edge (same breakout rule, filtered to abnormal
  opening volume: 29 % → ~1,637 % at Sharpe 2.81; the filter was the entire result).

### Hard constraints on anything you design

These are not preferences. A plan that violates one is wrong.

1. **No lookahead.** A selection rule must be decidable at trigger time. `TRIGGER_TIME_SAFE` lists
   what is readable; `assert_no_lookahead()` enforces it. ⚠️ Day aggregates (`day_volume`,
   `day_high`, `n_scanner_hits_day`, `first_rank`, `run_count`) read as context and are lookahead.
   Within-day *ranking* is lookahead too — you cannot rank a day's setups against each other at
   07:00. Capacity takes the earliest N triggers by time, always.
2. **Never report a statistic on opportunities that could not have been traded** — not as a
   contrast, not as a "lookahead delta", not as an upper bound.
3. **Pre-register before touching data.** Your plan must name, in advance: the hypotheses, the
   objective function, the success threshold, the trials counter, and the stopping rule. A
   hypothesis not written down before the split is opened does not count.
4. **One look at the holdout.** The existing convention is DEV (fit) / VAL (check freely) /
   HOLDOUT (one look, at the end). You may redesign the split boundaries for the larger record —
   you should, 511 sessions changes what is affordable — but the one-look property is
   non-negotiable and you must say who is allowed to open it and when.
5. **Every result carries a luck benchmark.** Shuffled-outcome or permutation null, reported
   alongside the real number, at the same search intensity. A result without its null is not a
   result.
6. **Net of costs, at the real account size.** And keep strategy quality (in R) separate from
   capital adequacy (in dollars) — a conclusion drawn at $500 is partly a conclusion about $500.
7. **Both halves separately, always.** Recon and live observe different things (see inventory §7.1)
   — pre-market-restrict both or compare nothing.

### What the plan must contain

Structure it however serves the work, but it must answer all of these concretely:

1. **The population and the panel.** The `engine_lab` panel is stale — it predates 287 recon and
   27 live sessions. Say exactly how it gets rebuilt over the full 511-session record, what the
   `WIDE` profile switches off, what the expected row count is, and how the rebuild is *verified*
   (the existing `--verify` reproduces the shipped `takeable` verdict from the wide panel's
   columns on every row — keep that property).
2. **The splits**, with dates and rationale, and the rule for who opens the holdout.
3. **The hypothesis budget.** How many distinct hypotheses the record can support at the power you
   need, how you arrived at that number, and how the budget is divided across the workstreams.
   This is the central design decision of the whole pass. Justify it.
4. **The workstreams**, ordered by expected value per hypothesis spent. At minimum consider:
   *selection* (the highest-prior area — and specifically **ranking rather than gating**, which
   this project has never tried), *exits and targets* (`load_paths` makes this cheap and it has
   the one stable finding), *capacity and sizing*, *session-level regime*, and the **unexplored
   assets** — `bars_1m` (32 M rows of 1-minute pre-market tape nothing has read), `daily_universe`
   (the negative-sample universe of names that ran but never set up), the 167 hand reviews, and
   the `skipped` log.
5. **The objective function**, stated once, and why. Net R per session at a declared capacity is
   the incumbent; argue for it or against it.
6. **Validation design.** Walk-forward vs fixed split, permutation nulls, sensitivity bands
   (does it survive ±20 % on each threshold?), and multiplicity control appropriate to the budget
   in (3).
7. **What "done" looks like.** The decision rule that turns the executed analysis into either a
   candidate strategy or a documented null. Include the null branch explicitly: what gets written
   down, what gets retired, and what the next phase does instead.
8. **Execution shape.** Which parts run on the box (2 vCPU / 4 GB — per-date, never `--all`),
   which on the Mac, which can run in a cloud session off an exported panel. Which agent tier does
   each piece: mechanical measurement is `spike-runner`/`builder`; interpretation is
   `strategy-analyst`. ⚠️ Measurement and interpretation are deliberately split — do not design a
   step that does both.
9. **The cost estimate.** Roughly how many sessions and of what tier. The subscription is Pro;
   context is a budget.

### How to work

- `grep`/`sed -n` the large files; the inventory names the expensive ones and their token cost.
- You are designing, so you may read broadly — but prefer the inventory's summary to re-deriving a
  fact from source. If the inventory is wrong about something, say so in the plan.
- Where a design choice is genuinely open, present the trade-off and **make a recommendation**.
  A plan that lists options without choosing is not a plan.
- Write the plan into the repo as a research doc, linked from `README.md`
  (`tests/test_research_docs.py` fails on an orphan doc). Open an issue for it on the board and
  label the workstreams as sub-issues if that helps the execution pass.
- Do not publish a report. Reports are for dated findings; this is a plan.

### The one thing to get right

The failure mode this project has demonstrated three times is: search hard, find something that
looks excellent on the fitting half, ship it, watch it collapse. Every previous pass had good
intentions and adequate statistics and still did this. Design the analysis so that outcome is
structurally impossible — not discouraged, impossible — and accept that the price is a smaller
number of questions asked with more discipline.

> Copy to here.
