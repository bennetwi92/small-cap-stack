# The analysis design prompt — three plans, three agents, one record

**Status:** LIVE (2026-09-18).

This file holds the prompt handed to a single **opus** session whose deliverable is the *design* of
**three independent analyses** of the 2-year Phase-1 record — three plans, plus three copy-paste
starting prompts that supersede [`analysis-brief.md`](./analysis-brief.md)'s single-track design
pass.

It supersedes nothing else. `analysis-brief.md` stays in place: its blind list, its hard
constraints and its shape are inputs to this pass, and the three prompts it produces inherit them.

---

## The prompt

> Copy from here down.

---

You are a quantitative strategy researcher and a data scientist. You are designing — not running —
an analysis of a systematic US small-cap momentum strategy against a ~511-session, 2-year record.

Your deliverable is **three independent analysis plans and the three starting prompts that launch
them**. You will not fit a model, sweep a threshold, open a split or report a number in this pass.
Three separate agents, running concurrently and unable to ask a question mid-flight, will execute
what you write. The plans are what stop them burning the only 2-year record this project will have
for another 2 years.

### What to read, and what you must not

Read first, in this order:

1. [`research/data-inventory.md`](./data-inventory.md) — what exists, at what grain, where the
   holes are. Every design choice below rests on it.
2. [`research/analysis-brief.md`](./analysis-brief.md) — the previous single-track design brief.
   Read it as **prior art and as a constraints source**, not as a plan to reproduce. It is itself
   blind: it carries no prior strategy conclusions, so reading it does not contaminate you. Its
   blind table, its seven hard constraints and its "what the plan must contain" list are inherited
   by your three plans unless you argue explicitly for a change.

**You inherit `analysis-brief.md`'s blind list verbatim.** Do not read the incumbent rules, the
decision log, the pattern-grammar specs, `spikes/`, the shipped portfolio/config code, the
dashboard payloads, the prior-repo audits, or git/issue history — and do not ask a subagent to
summarise them for you. Prior conclusions anchor every downstream choice: which hypotheses look
worth funding, which features look promising, which thresholds look "reasonable". An analysis
designed from them is a rationalisation of the incumbent, not a fresh look.

If you need a mechanical fact only a blinded file holds — a column name, a unit, whether a field is
populated — take it from the inventory. If the inventory is silent or wrong, **say so in the plan**
and design around the uncertainty rather than opening the file. You may read the code that produces
and stores raw data (`capture.py`, `storage.py`, `harvest/`) to learn what a column *means*, never
to learn what was done with it.

### Declared operator facts — design against these, do not test them

- **The account is $500**, at a broker with a per-order commission minimum. Fixed costs are a
  material fraction of every unit of risk, so **net of costs is the only number that counts**; a
  gross improvement that worsens net is a failure.
- **Target throughput is ~0.8 trades/day.** Capacity is a constraint on the objective, not a free
  parameter to maximise.
- Keep **strategy quality** (in R) separate from **capital adequacy** (in dollars). A conclusion
  drawn at $500 is partly a conclusion about $500.

### Operator priors — convert each of these into a design constraint

These are the trader's stated beliefs from years of trading this market. They are not findings and
they are not optional colour. Each one changes what a competent plan looks like; your plans must
show where each landed.

1. **Expect the strategy to be inconsistent.** Small caps run hot and cold. A rule set that is
   flat-to-negative through stretches and pays in bursts is the *expected* shape, not a defect. So:
   do not design an objective that rewards smoothness for its own sake, do not reject a candidate
   for having losing quarters, and do report the *distribution* of period outcomes (how often a
   cold stretch runs, how deep, what fraction of total R comes from the best decile of sessions)
   rather than a single pooled mean. Conversely: burst-shaped returns make any single-window fit
   less informative than it appears — say how your validation design accounts for that.
2. **Expect the configuration to need re-tuning over time.** A frozen rule set is not the target; a
   rule set *plus a tuning protocol* is. Every plan must deliver both: the candidate, and the rule
   for how and how often it is re-fitted — what may be re-tuned, on what window, with what
   guardrails, and how a re-tune is distinguished from a rescue. Validate with rolling-refit
   walk-forward, and report **parameter stability** (does the refit keep landing in the same
   neighbourhood, or does it wander?). ⚠️ Every refit is a trial and must be counted as one.
3. **Do not look for hot and cold markets in the unfiltered dataset.** The raw population — every
   name that gapped and set up — will never be profitable at any exit policy, and regime structure
   measured across it is structure in a population nobody will ever trade. Regime work is therefore
   only legitimate **on the post-selection trade stream**: filter first, then study the
   time-variation of what survives the filter. ⚠️ The obvious leak here: a filter fitted on the
   whole record, then studied for regimes, has already seen the regimes. The filter used for any
   regime analysis must be either *a priori* or fitted **only on the fit split**, and your plan must
   say which and enforce it mechanically.
4. **Both a scalp branch and a runner branch, and probably a hybrid.** This market has paid the
   trader both ways in different periods: letting winners run, and clipping high-probability ~0.5R
   scalps. Exit policy is therefore **not** a parameter tacked on after selection — the plans must
   treat selection × exit as a **joint** search space, because the setups worth taking for a 0.5R
   clip are not necessarily the ones worth holding for a 5R runner, and a selection rule fitted
   against one exit family is not evidence for the other. Cover at minimum: a high-probability
   fixed-target branch, a let-it-run branch (trailing/structural exits), and a scale-out hybrid
   (part off early, remainder runs). ⚠️ **The scalp branch is the one the cost floor kills.** At a
   2R target the inventory puts break-even at 33.3 % gross and ~42.9 % net; the same arithmetic at a
   0.5R target pushes the required net hit rate north of 70 %. **Derive the exact break-even for
   each exit family from the pinned cost model, in the plan, before funding the workstream** — that
   hurdle, not the idea, decides whether the branch is viable, and it is cheaper to discover it in
   the plan than in the execution.

### Hard constraints — a plan that violates one is wrong

Inherited from `analysis-brief.md` (restated so your plans can be checked against them without
opening it):

1. **No lookahead.** A selection rule must be decidable at trigger time. Day aggregates
   (`day_volume`, `day_high`, `n_scanner_hits_day`, `first_rank`, `run_count`) read as context and
   are lookahead; within-day *ranking* is lookahead. Capacity takes the earliest N triggers by time.
   Say how this is enforced **mechanically**, not by convention.
2. **Never report a statistic on opportunities that could not have been traded** — not as a
   contrast, not as a "lookahead delta", not as an upper bound.
3. **Pre-register before touching outcome data**: hypotheses, objective function, success
   threshold, trials counter, stopping rule.
4. **One look at the holdout**, with a named custodian and a named condition.
5. **Every result carries a luck benchmark** — permutation or shuffled-outcome null at the same
   search intensity. A result without its null is not a result.
6. **Net of costs, at the real account size.**
7. **Both halves separately, always** — recon and live observe different windows (inventory §7.1);
   pre-market-restrict both or compare nothing.

And four that exist **because there are now three agents instead of one**:

8. **One panel, built once, frozen.** Three agents rebuilding the analysis panel independently is
   three subtly different panels and three incomparable results. The panel build is a shared Stage-0
   artefact: built once, verified once, content-hashed, and consumed identically by all three. Name
   who builds it and how the other two verify they hold the same bytes.
9. **One set of split boundaries**, identical across all three. Different splits make the three
   results impossible to correct jointly.
10. **One global trials ledger.** Three "independent" searches against one record multiply exactly
    like one big search — the multiplicity is global whether or not the agents talk to each other.
    Carve each plan's hypothesis budget out of a single global budget, and require every agent to
    append its trials to a shared ledger (a file on a branch, or an issue) *before* it opens data.
    State the global budget, the per-plan allocation, and the arithmetic that got you there.
11. **One holdout opening, covering all three.** The holdout is opened once, by the custodian, after
    all three workstreams have each frozen **one** candidate, and all three are scored in the same
    pass with a multiplicity correction across the three. No agent opens it. No agent reads another
    workstream's plan, intermediate results or candidate before that pass.

### Independence — say what you mean by it

"Three independent analyses" is a design claim you have to earn. In your cover section, state
precisely:

- **What differs** between the three — the question, the lens, the data assets leaned on, the
  hypothesis class. Three variations on one idea is one analysis with three agents' worth of
  multiplicity and none of the coverage.
- **What is deliberately shared** — panel, splits, cost model, objective function family, ledger,
  custodian — and why sharing each one *increases* rather than compromises independence.
- **What none of the three covers**, and what it would cost to cover it. Name the blind spot; do not
  let it be discovered later as a surprise.
- **Why this three-way split maximises expected value per hypothesis spent.** Argue the ordering;
  do not assume it. The available assets, from the inventory: the opportunity spine, the scanner
  attention series, the 5-minute tape, `bars_1m` (32 M rows of 1-minute pre-market tape nothing has
  read), `daily_universe` (names that ran but never set up), the 167 hand reviews, the `skipped`
  log, VIX, and the `engine_lab` panel with its 46 trigger-safe columns.

### Every plan is multi-stage — do not let an agent open with the analysis

None of the three may begin by modelling. Each plan is staged, with an explicit gate between
stages, because the questions worth asking are not knowable until the cheap groundwork is done.

- **Stage 0 — reconnaissance (costs no hypothesis budget).** Rebuild and *verify* the panel;
  inventory the execution harness that already exists (the previous pass deferred this deliberately
  — design first, tooling second — so it happens here, after the design and before the search);
  audit the cost model against the broker's real fills; check feature coverage, nullity and
  stationarity; audit the 167 hand reviews as a supervision signal; compute the power available at
  the split sizes you chose. Every Stage-0 finding that changes the plan gets written back into it
  before Stage 1 opens.
  ⚠️ **The rule that makes Stage 0 free:** anything computed **without reading an outcome column**
  costs no budget. The moment a probe conditions on `max_r`, `mae_r`, `stopped_out` or any realised
  return, it is a hypothesis and it is charged. State this rule in each prompt; it is the only thing
  keeping "exploratory data analysis" from becoming an uncounted search.
- **Stage 1 — pre-registration.** Hypotheses, objective, thresholds, stopping rule, trials
  allocation, written down and ledgered before the fit split is opened. Name the **Stage-0 exit
  gate**: what Stage 0 must have established before Stage 1 is allowed to start, and what Stage-0
  finding cancels the workstream outright.
- **Stage 2 — execution** on fit, then check. Measurement and interpretation stay separate steps
  (`spike-runner`/`builder` measures; `strategy-analyst` interprets) — never one step that does
  both.
- **Stage 3 — freeze one candidate**, hand it to the custodian, stop. The holdout pass is not part
  of any workstream.

Where a workstream genuinely needs a preliminary investigation you cannot specify blind — a feasibility
probe, a data-integrity question, a methodology choice that depends on what Stage 0 finds — build it
into the plan as a named Stage-0 item with a decision rule attached, rather than leaving the
executing agent to improvise. **An agent that cannot ask a question must be given the answer to
"what do I do if X turns out to be false?" in advance.**

### What each of the three plans must contain

Structure it as serves the work, but answer all of these concretely:

1. **The question** this workstream owns, in one sentence, and why it is worth its share of the
   budget.
2. **The population and the panel** it uses — grain, expected row count, and how the build is
   *verified*, since everything downstream rests on it.
3. **Stage 0**, item by item, each with its decision rule and its exit gate.
4. **The hypothesis budget** allocated to it, and how it is spent across the stages. Include the
   refit trials from prior (2).
5. **The hypothesis class** — selection, exit, capacity, regime-conditioning — stated tightly
   enough that "did we test this?" has a yes/no answer.
6. **The objective function**, stated once, derived from the constraints above, defended against the
   alternatives rejected. Net R per session at the throughput constraint is the obvious starting
   point; if you depart from it, say why.
7. **Validation design** — walk-forward with rolling refit vs fixed split, permutation nulls,
   sensitivity bands (does it survive ±20 % on each threshold?), parameter-stability reporting, and
   multiplicity control appropriate to the *global* budget.
8. **The regime treatment**, obeying prior (3): which filter conditions the regime study, where that
   filter was fitted, and how the leak is blocked.
9. **The exit treatment**, obeying prior (4): which exit families are in scope, the net break-even
   each implies, and how selection × exit is searched jointly without the joint space exploding the
   trials count.
10. **What "done" looks like** — the decision rule that turns the executed work into either one
    frozen candidate or a documented null. **Include the null branch explicitly**: what gets written
    down, what gets retired, what the next phase does instead. A plan that concludes "the record
    does not support a profitable rule set, and here is the evidence, and here is what would" is a
    **successful** outcome. Do not design an analysis that can only succeed.
11. **Execution shape** — which parts run on the box (2 vCPU / 4 GB, per-date, never `--all`), which
    on the Mac, which in a cloud session off an exported panel; which agent tier does each piece.
12. **The cost estimate** — roughly how many sessions, of what tier. The subscription is Pro;
    context is a budget.

### What each starting prompt must contain

Each prompt launches a **fresh agent with no context**. It is closed-form: the agent cannot come
back and ask. Each one must carry, or point unambiguously at:

- the role, the deliverable and the stage gates;
- the blind list (inherited verbatim — the agent must not read the incumbent's conclusions either);
- the declared operator facts and the four operator priors;
- the hard constraints, including the four multi-agent ones;
- its own budget, its slice of the ledger, and the instruction to ledger before opening data;
- how to reach the data (the `box-data` skill from a cloud session; `scripts/box-job.sh` per-date on
  the box; direct access on the Mac) and the frozen panel's identity;
- what is explicitly **out of scope** for it, i.e. the other two workstreams' territory;
- the escalation rule: what to do when a design assumption turns out false — stop and report, not
  improvise;
- the stopping rule, and the instruction that the holdout is not theirs to open.

Avoid triplicating the shared material into three drifting copies. The shape I'd recommend, unless
you have a better one: keep **`research/analysis-brief.md`** as the shared preamble — blind list,
hard constraints, the shared protocol (frozen panel, splits, ledger, custodian, holdout) — and add
one **`research/analysis-plan-<n>-<slug>.md`** per workstream carrying that plan and its
copy-paste starting prompt, each opening with "read the shared preamble first". Decide for yourself,
but keep it DRY and keep the repo green.

### Repo mechanics — these are checked by tests

- `research/analysis-brief.md` must keep the heading `### Work blind — this is a hard requirement`
  and its table of backticked paths, all of which must exist —
  [`tests/test_analysis_blind.py`](../tests/test_analysis_blind.py) fails otherwise.
- Every new `research/*.md` needs an inbound link or
  [`tests/test_research_docs.py`](../tests/test_research_docs.py) reports it as an orphan. Link the
  new docs from `README.md`.
- Each doc carries a `**Status:**` line.
- Run `make check` before pushing. Branch `docs/…`, conventional commit prefix, PR body links the
  epic (`Refs #1`).
- Open a board issue for the design pass and one sub-issue per workstream (`board-keeper` holds the
  procedure) — three concurrent agents need three places to record findings.
- **Do not publish a report.** Reports are dated findings; this is a plan.

### How to work

- `grep`/`sed -n` the large files; the inventory names the expensive ones and their token cost.
- Prefer the inventory's summary to re-deriving a fact from source. If the inventory is wrong, say
  so in the plan.
- Where a design choice is genuinely open, present the trade-off and **make a recommendation**. A
  plan that lists options without choosing is not a plan.

### The one thing to get right

The failure mode that kills a project like this is: search hard, find something excellent on the
fitting half, ship it, watch it collapse. Good intentions and adequate statistics do not prevent it
— a large enough search finds a beautiful result in noise every time, and the search that found it
always felt disciplined from the inside. Running **three** analyses instead of one triples the
search unless the global budget, the shared ledger and the single holdout opening are real
mechanisms rather than good intentions. Design the collapse outcome to be structurally
**impossible**, not discouraged, and accept that the price is fewer questions asked with more
discipline.

> Copy to here.
