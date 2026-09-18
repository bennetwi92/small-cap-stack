# The analysis brief — the prompt for the end-to-end design pass

**Status:** LIVE (2026-09-18).

The 2-year harvest is complete. This file holds the brief handed to an **opus** agent
(`strategy-analyst`, or an opus session) whose deliverable is the **design of the analysis**, end
to end — not its results.

The brief is deliberately **blind**: it does not tell the analyst what this project has traded,
what it has already searched, or what any of that returned. Those conclusions were drawn on
partial windows by passes that are not being repeated, and an analyst who starts from them is not
designing an analysis — it is rationalising the existing one. Read
[`data-inventory.md`](./data-inventory.md) first; the brief assumes it, and it is a *shape*
document that draws no strategy conclusions.

Why a design pass before an analysis pass: the binding constraint here is not compute or data — it
is **how many hypotheses a 511-session record can support**. That budget has to be allocated
deliberately, in advance, by something expensive enough to think about it. Hence: design first,
pre-register, then execute.

---

## The prompt

> Copy from here down.

---

You are designing an end-to-end data-science analysis of a systematic small-cap momentum strategy.
Your deliverable is **the analysis plan**, not the analysis. You will not fit a model, sweep a
threshold or report a number in this pass. Someone else — probably several agents — will execute
what you write, and the plan is what stops them wasting the only 2-year record this project will
have for another 2 years.

### Work blind — this is a hard requirement

You are designing this analysis **from the data's shape, not from what anyone previously concluded
about it**. The point of the exercise is an unbiased pass, and prior conclusions, prior rule sets
and prior search results would anchor every choice you make downstream — which hypotheses look
worth spending budget on, which features look promising, which thresholds look "reasonable".

**Do not read these, and do not ask another agent to summarise them for you:**

| Do not read | What it would tell you |
|---|---|
| `research/strategy.md` | the rules currently running — the incumbent selection and execution model |
| `research/decisions.md` | every rule ever tried, why it shipped, and what it returned |
| `research/bull-flag.md`, `research/engine-v2.md` | the pattern grammar the incumbent is built on |
| `spikes/README.md` and everything under `spikes/` | every search already run and what it found |
| `src/small_cap_stack/portfolio/`, `src/small_cap_stack/config.py` | the shipped execution model and its tuned values |
| `docs/` (the dashboard) and its published payloads | the incumbent's results |
| `research/tradepilot.md`, `research/entresys_light.md` | prior-repo audits of earlier versions of this strategy |
| git log and issue history | the same material, narrated |

If you need a mechanical fact that only one of those files holds — a column name, a units
question, whether a field is populated — get it from `data-inventory.md`, and if the inventory is
silent or wrong, **say so in the plan** and design around the uncertainty rather than opening the
file.

You may read the code that **produces and stores the raw data** (`capture.py`, `storage.py`,
`harvest/`) where the inventory leaves a shape question open. You are reading it to learn what a
column means, never to learn what was done with it.

**The one exception, and its timing.** An execution harness already exists. It is not described
here and you should not go looking for it, because its structure encodes the prior analysis's
choices. Write your plan against the requirements below; once the plan is **pre-registered**, the
harness will be handed to the execution pass, and whatever of it already satisfies your design
gets reused rather than rebuilt. Design first, inventory the tooling second — never the reverse.

### The objective

Walk away from the executed analysis with **a candidate strategy worth refining in the next
phase**: a specific, mechanically-decidable selection + exit + capacity rule set, with an honest
out-of-sample estimate of what it does, and a stated confidence that distinguishes it from the
null. "Refine in the next phase" means it must be concrete enough to run forward on paper, and
honest enough that running it forward is informative rather than reassuring.

A plan that concludes "the record does not support a profitable rule set, and here is the evidence
and what would" is a **successful** outcome of this design. Do not design an analysis that can
only succeed.

### The account and the trader's constraints

These are declared facts about the operator, not findings about the market. They cost you no
evidence and you should design against them rather than test them:

- **The account is $500**, at a broker whose commission carries a per-order minimum. At that size
  fixed costs are a material fraction of every unit of risk, so **net of costs is the only number
  that counts** and a gross improvement that worsens net is a failure.
- **The trader wants roughly 0.8 trades per day.** Capacity is therefore a constraint on the
  objective, not a free parameter to maximise.
- Keep **strategy quality** (in R) separate from **capital adequacy** (in dollars). A conclusion
  drawn at $500 is partly a conclusion about $500.

### Hard constraints on anything you design

These are not preferences. A plan that violates one is wrong.

1. **No lookahead.** A selection rule must be decidable at trigger time. ⚠️ Day aggregates
   (`day_volume`, `day_high`, `n_scanner_hits_day`, `first_rank`, `run_count`) read as context and
   are lookahead. Within-day *ranking* is lookahead too — you cannot rank a day's setups against
   each other at 07:00. Capacity takes the earliest N triggers by time, always. Your plan must say
   how this is **enforced mechanically**, not observed by convention.
2. **Never report a statistic on opportunities that could not have been traded** — not as a
   contrast, not as a "lookahead delta", not as an upper bound.
3. **Pre-register before touching data.** Your plan must name, in advance: the hypotheses, the
   objective function, the success threshold, the trials counter, and the stopping rule. A
   hypothesis not written down before the split is opened does not count.
4. **One look at the holdout.** Split the record into fit / check / holdout. You set the
   boundaries — 511 sessions changes what is affordable — but the one-look property is
   non-negotiable and you must say who is allowed to open the holdout and when.
5. **Every result carries a luck benchmark.** Shuffled-outcome or permutation null, reported
   alongside the real number, at the same search intensity. A result without its null is not a
   result.
6. **Net of costs, at the real account size** (see above).
7. **Both halves separately, always.** Recon and live observe different things (see inventory
   §7.1) — pre-market-restrict both or compare nothing.

### What the plan must contain

Structure it however serves the work, but it must answer all of these concretely:

1. **The population and the panel.** Say exactly how the analysis panel gets built over the full
   511-session record, at what grain, what the expected row count is, and — critically — how the
   build is *verified*, since every number downstream rests on it.
2. **The splits**, with dates and rationale, and the rule for who opens the holdout.
3. **The hypothesis budget.** How many distinct hypotheses the record can support at the power you
   need, how you arrived at that number, and how the budget is divided across the workstreams.
   This is the central design decision of the whole pass. Justify it.
4. **The workstreams**, ordered by expected value per hypothesis spent, with your ordering argued
   rather than assumed. The data assets available to them, from the inventory, include: the
   opportunity spine, the scanner attention series, the 5-minute tape, `bars_1m` (32 M rows of
   1-minute pre-market tape nothing has read), `daily_universe` (the negative-sample universe of
   names that ran but never set up), the 167 hand reviews, and the `skipped` log.
5. **The objective function**, stated once, derived from the constraints above, and defended
   against the alternatives you rejected.
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
- Prefer the inventory's summary to re-deriving a fact from source. If the inventory is wrong about
  something, say so in the plan.
- Where a design choice is genuinely open, present the trade-off and **make a recommendation**.
  A plan that lists options without choosing is not a plan.
- Write the plan into the repo as a research doc, linked from `README.md`
  (`tests/test_research_docs.py` fails on an orphan doc). Open an issue for it on the board and
  label the workstreams as sub-issues if that helps the execution pass.
- Do not publish a report. Reports are for dated findings; this is a plan.

### The one thing to get right

The failure mode that kills a project like this is: search hard, find something excellent on the
fitting half, ship it, watch it collapse. Good intentions and adequate statistics do not prevent
it — a large enough search finds a beautiful result in noise, every time, and the search that
found it always felt disciplined from the inside. Design the analysis so that outcome is
structurally **impossible**, not discouraged, and accept that the price is a smaller number of
questions asked with more discipline.

> Copy to here.
