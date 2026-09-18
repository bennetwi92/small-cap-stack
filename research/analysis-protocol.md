# The analysis protocol — the shared preamble for the three-agent execution pass

**Status:** LIVE (2026-09-18). Output of the design pass briefed by
[`analysis-design-prompt.md`](./analysis-design-prompt.md).

This is the **shared half** of the analysis design. It carries everything all three workstreams
must agree on — the blind list, the operator facts and priors, the hard constraints, the frozen
panel, the splits, the objective function, the exit families, the cost identity, the trials ledger,
the custodian and the single holdout opening — so that the three plans carry only what differs.

The three plans:

- [`analysis-plan-1-selection.md`](./analysis-plan-1-selection.md) — **W1**, *which setups*
- [`analysis-plan-2-execution.md`](./analysis-plan-2-execution.md) — **W2**, *how to enter and exit them*
- [`analysis-plan-3-regime.md`](./analysis-plan-3-regime.md) — **W3**, *when to be trading at all*

Each plan doc ends with a copy-paste starting prompt for a fresh agent. Every one of those prompts
opens by pointing here. Read this first; it is the contract.

Companions: [`data-inventory.md`](./data-inventory.md) is what the data *is*;
[`analysis-brief.md`](./analysis-brief.md) is the prior single-track design brief and the canonical
home of the blind list.

---

## 0. What was designed, and the one thing it is for

The failure mode that kills this project is: search hard, find something excellent on the fitting
half, ship it, watch it collapse. The record has already demonstrated it on itself — a
15,434-combination sweep scored 51.0 on the fitting half against a shuffled-outcome best-of-luck of
43.7, and returned **−0.194 R/session** on sessions it had not seen (inventory §7.8).

Three concurrent agents triple that risk unless the controls are **mechanisms rather than
intentions**. Two of the mechanisms below are physical rather than procedural, and they are the
load-bearing part of this design:

1. **The three agents do not possess the holdout's outcomes.** The panel they are handed has every
   outcome column nulled and every post-trigger bar path absent for holdout-window rows (§4.3). An
   agent cannot peek at a split whose answers are not in the file it holds.
2. **A candidate rule is handed a dataframe that physically lacks the lookahead columns** (§5.1). A
   rule that reaches for `day_high` raises `KeyError`; it does not quietly work.

Everything else — the ledger, the budget, the custodian — is procedure, and procedure is what the
mechanisms fall back on. Both layers are required.

---

## 1. Work blind — inherited verbatim

**The blind list is the table under `### Work blind — this is a hard requirement` in
[`analysis-brief.md`](./analysis-brief.md).** It is canonical there and is deliberately *not*
copied here, so the two cannot drift. Read it there, in full, before opening anything else.

It is in force for all three workstreams, unchanged, with three clarifications:

- **It survives pre-registration.** The brief's "the harness will be handed to the execution pass
  once the plan is pre-registered" is now due — but it is a carve-out for *machinery*, not for
  *findings*. See §1.1.
- **It applies to subagents.** Do not ask a scout, a summariser or any other agent to read a
  blinded surface on your behalf, and do not accept a summary of one if it is offered.
- **It does not cover raw-capture code.** `capture.py`, `storage.py` and `harvest/` are readable,
  to learn what a column *means*. Never to learn what was done with it.

### 1.1 The harness carve-out — machinery yes, findings no

`spikes/` is on the blind list. For the execution pass it is relaxed, narrowly:

| surface | execution pass | why |
|---|---|---|
| [`spikes/engine_lab/`](../spikes/engine_lab) — `load_panel`, `load_paths`, `replay_bracket`, `build_book`, `score`, `walk_forward`, `permutation_pvalue`, `sensitivity`, `assert_no_lookahead` | **readable and reusable** | it is the measurement apparatus; rebuilding it is waste and would give three agents three rulers |
| [`spikes/vix_regime.py`](../spikes/vix_regime.py) — the VIX cache | **readable** (W3 only) | a data loader |
| `SHIPPED` / `baseline()` constants inside the harness | **executable, not readable** | see below |
| `spikes/README.md` and every other spike's harness, output or write-up | **still blind** | these are the prior searches and what they found |

**The `SHIPPED` profile may be run as a benchmark; its parameter values may not be read into a
search.** No grid in any plan is expressed as an absolute threshold. Every grid is expressed as a
**quantile of the fit-split distribution** of the quantity concerned (§5.3). That is both the
anti-anchoring mechanism and good practice — it makes a grid mean the same thing on a split whose
distribution has moved.

---

## 2. Declared operator facts — design against these, do not test them

- **The account is $500**, at a broker with a per-order commission minimum. Fixed costs are a
  material fraction of every unit of risk. **Net of costs is the only number that counts**; a gross
  improvement that worsens net is a failure (inventory §7.10).
- **Target throughput is ~0.8 trades/day.** Capacity is a constraint on the objective, never a free
  parameter to maximise.
- **Strategy quality (in R) stays separate from capital adequacy (in dollars).** A conclusion drawn
  at $500 is partly a conclusion about $500. Both are reported; only the first is optimised.

## 3. Operator priors, and where each one landed

These are the trader's beliefs from years in this market. They are not findings, and they are not
optional colour — each one changed the design. Where it landed is named so it can be checked.

| prior | where it landed |
|---|---|
| **(1) Expect the strategy to be inconsistent.** Hot and cold stretches are the expected shape, not a defect. | The objective is **mean net R per session**, not a risk-adjusted ratio (§6) — Sharpe would penalise exactly the shape the operator expects. Every result reports the *distribution* of period outcomes, the top-decile share and the deepest cold stretch (§6.2), and no candidate is rejected for losing quarters. The counterweight: burst returns make any single-window fit less informative than it looks, so validation is **rolling-refit walk-forward with parameter-stability reporting** (§7), and the permutation null is **block-shuffled by session** so it preserves the burst structure rather than washing it out (§7.3). |
| **(2) Expect the configuration to need re-tuning.** The deliverable is a rule set *plus* a tuning protocol. | **Every plan must deliver both** — the candidate and the refit rule (what may be re-tuned, on what window, with what guardrails, and how a re-tune is told from a rescue). W3 owns the question of what cadence the record actually supports. ⚠️ **Every refit is a trial and is ledgered as one** (§8.1). |
| **(3) Do not look for regimes in the unfiltered dataset.** Regime structure across every name that gapped is structure in a population nobody will trade. | Regime work runs **only on a filtered trade stream**, and the filter is **Filter A** (§9) — declared *a priori* in this document, before any data was opened, with its one data-dependent element (a median) computed **on the fit split only** and frozen. That is the leak block, and it is mechanical: Filter A's frozen constants are published in the Stage-0 spec and hashed. |
| **(4) Both a scalp branch and a runner branch, probably a hybrid.** Selection × exit is joint, not sequential. | Four exit families are defined **once, here** (§10) so all three agents mean the same thing. W1 searches selection × family jointly as a crossed design. W2 searches the interior of each family under a fixed a-priori selection. ⚠️ **The cost floor decides the scalp branch, and the cost model is inconsistent in the inventory by nearly 3×** (§11) — so the break-even derivation is a **Stage-0 exit gate** (S0-F), published to all three *before* anyone opens a fit split. It is cheaper to kill a branch in arithmetic than in execution. |

---

## 4. Stage 0 — the frozen panel and the shared reconnaissance

### 4.1 What it is

One row per triggered setup, `(date, source, symbol, run)`, over the full ~511-session record,
built by replaying the detector under the harness's `WIDE` settings profile (every fitted threshold
off, the raw quantity each one reads recorded). Pre-market appearance restriction (`trigger_et_min
< 570`) applied to **both halves** — non-negotiable, inventory §7.1.

Expected size: **~9,000–9,500 rows**, up from the 3,639 the stale build holds (inventory §4). If
the realised count is outside 7,500–11,000, that is a Stage-0 stop (§12, S0-A).

### 4.2 Who builds it, and what ships

One `builder` session, on the **Mac** (the box is a 2 vCPU / 4 GB CX23; the full-record panel build
is exactly the shape of job that takes it down — inventory §9). Artefacts:

| artefact | contents |
|---|---|
| `panel-v1.parquet` | the panel, all splits, **holdout outcomes redacted** (§4.3) |
| `paths-v1/` | post-trigger 5-min bar paths per setup (`load_paths` format), **fit and check only** |
| `panel-v1-spec.md` | build command, settings profile, package commit sha, per-split session and row counts, per-column nullity, Filter A's frozen constants, and the sha256 of every artefact |
| `panel-v1.sha256` | the checksums again, standalone |

Distribution: the `data-export` branch (the panel is ~9 k rows — trivially portable). The spec doc
and the checksums are committed to `main` in the same PR that closes Stage 0, so the hashes live
somewhere the agents cannot silently change.

### 4.3 The holdout redaction — the mechanism, not a rule

In the agents' copy, every row whose `date` falls in the **HOLDOUT** window has:

- every column in `OUTCOME_COLS` (`max_r`, `mae_r`, `stopped_out`, `stop_index`, `bars_to_max_r`,
  `max_gain_pct`, `entry_price`, `same_bar_stop`, `fill_above_entry_bar_high`) set to null; and
- **no entry in `paths-v1/`** — a post-trigger bar path *is* the outcome, at higher resolution.

Holdout **features** ship intact, deliberately: an agent must be able to confirm its candidate
produces a sane trigger count on the holdout, and to check feature-distribution drift, without
learning anything about what happened. That is not a look at the holdout; a look at the holdout is
conditioning on an outcome, and the outcome is not in the file.

The custodian keeps the unredacted panel and paths, off the shared branch.

### 4.4 How the other two verify they hold the same bytes

`sha256sum panel-v1.parquet` and compare, character for character, against `panel-v1-spec.md` on
`main`. **Same row count is not verification.** Any mismatch is a stop-and-report (§13), never a
rebuild — an agent that rebuilds the panel has just created a second panel.

### 4.5 The shared Stage-0 items, S0-A … S0-J

Stage 0 is run **once, centrally**, by the Stage-0 `builder` session on the Mac — not three times by
three agents. Each plan adds its own workstream-specific Stage-0 items on top of these; none of them
repeats one of these.

⚠️ **Stage 0 is nearly free because of §5.2's rule**: anything computed without reading an outcome
column or a post-trigger price costs no budget. Exactly two items below read outcomes, and together
they cost the **6 trials** allocated to Stage 0 in §8.2.

| id | item | outcomes? | decision rule, and the pre-authorised fallback |
|---|---|---|---|
| **S0-A** | Rebuild the panel over all ~511 sessions (§4.1). Publish realised row and session counts per split and per half. | no | Rows outside **7,500–11,000** → stop-and-report. Any split more than **15 %** thinner than §7.1 states → stop-and-report: the boundaries are protocol, and moving them is not a workstream's call. |
| **S0-B** | Redact holdout outcomes and paths (§4.3); publish artefacts, `panel-v1-spec.md` and the sha256s. | no | If redaction cannot be verified row-for-row against the HOLDOUT date range, nothing ships. |
| **S0-C** | Audit `RULE_COLUMNS` against the seven lookahead columns named in constraint 1. | no | Any of the seven inside the harness whitelist → **remove, re-freeze, ledger the amendment**. Where `assert_no_lookahead()` and the inventory's list disagree, **the inventory's list wins** — it is the wider one, and the cost of being wrong is asymmetric. |
| **S0-D** | Per-column coverage, nullity and stationarity, per split and per half. Realised throughput of Filter A. | no | A column **> 20 % null** on FIT, or on either half of HOLDOUT, is dropped from `RULE_COLUMNS` and the drop is ledgered. Filter A throughput outside **[0.6, 1.0]** → stop-and-report; the protocol is amended centrally, not by whoever noticed. |
| **S0-E** | Verify the recon session window (§11.3) and `bars_1m` coverage across the 453 recon sessions. | no | Recon `bars` reaching ≥ 15:55 ET on ≥ 95 % of sessions → **F3/F4 may hold past the bell on recon**. Otherwise every family is evaluated under a 09:30 hard close on recon and the difference reported. `bars_1m` present on ≥ 95 % of recon sessions → W2 proceeds; **80–95 %** → W2 proceeds on the covered subset and reports the coverage; **< 80 %** → W2's 1-minute items are cancelled and their trials return to the global budget. |
| **S0-F** | **Audit the cost model against the broker's real fills.** Measure `c` as a *distribution over `stop_pct` deciles*, not a scalar. Recompute §11.2's break-even table. | **yes — 2 trials** | If F1's measured net break-even exceeds **80 %**, **F1 is cancelled across all three plans** and its trials return unspent. Publish before any workstream opens a fit split — this is an **absolute exit gate**. Correct `data-inventory.md` §6/§8 in the same PR. |
| **S0-G** | Review geometry audit through the redacting loader (§9.2). | no (redacted) | Publish the agreement rate with both caveats attached. No workstream may condition a rule on it. If the redacting loader cannot be written such that `note` and `annotations.max_r` are dropped at parse time, **the item is cancelled** — it is not worth a manual promise. |
| **S0-H** | Outcome dispersion: mean and sd of per-trade net R on the **Filter-A stream, FIT split, per exit family**. This also produces the Filter-A baseline J and its full §6.2 report, which all three workstreams use as their reference. | **yes — 4 trials** | Recompute §7.2's power table from the measured sd. If the minimum detectable effect for a family exceeds **0.6 R/trade** at the relevant allocation, that family is reported **under-powered** and its share of any crossed design is cut; ledger the amendment. |
| **S0-I** | Inventory the existing harness (§1.1): which primitives each plan needs, which already exist, which do not. | no | A missing primitive that any plan requires → **stop-and-report before Stage 1**. Building it once, centrally, is cheaper and safer than three agents each building their own. |
| **S0-J** | Publish the recomputed power table and the realised split arithmetic to the ledger. | no | This is the Stage-0 exit artefact: no workstream opens its fit split before S0-F, S0-H and S0-J are posted. |

**Stage-0 exit gate, globally:** S0-A through S0-J complete, S0-F and S0-H published to the ledger,
the panel hash on `main`, and no open stop-and-report. Every Stage-0 finding that changes a plan is
written back into the plan doc, and ledgered, **before** Stage 1 opens.

---

## 5. Hard constraints — a plan or an execution that violates one is wrong

Constraints 1–7 are inherited from [`analysis-brief.md`](./analysis-brief.md); 8–11 exist because
there are three agents; 12–13 are added by this design and argued where they appear.

1. **No lookahead.** A selection rule is decidable at trigger time. Day aggregates (`day_volume`,
   `day_dollar_volume`, `day_high`, `day_low`, `n_scanner_hits_day`, `first_rank`, `run_count`) read
   as context and are lookahead. Within-day **ranking** is lookahead — you cannot rank a day's
   setups against each other at 07:00. Capacity takes the **earliest N triggers by time**. Use
   `cum_volume_to_trigger`, `cum_dollar_vol_to_trigger`, `ext_at_trigger`, `hits_before_trigger`.
   Enforced mechanically — §5.1.
2. **Never report a statistic on opportunities that could not have been traded.** Not as a contrast,
   not as a "lookahead delta", not as an upper bound. There is no exception and no framing that
   makes one acceptable.
3. **Pre-register before touching outcome data**: hypotheses, objective, success threshold, trials
   counter, stopping rule — ledgered (§8) before the fit split is opened.
4. **One look at the holdout**, by the custodian, under the named condition (§12).
5. **Every result carries a luck benchmark** — a permutation null at the same search intensity
   (§7.3). A result without its null is not a result.
6. **Net of costs, at the real account size.**
7. **Both halves separately, always.** Recon observes 04:00–09:30 appearances; live scans to 11:59
   (inventory §7.1). Pre-market-restrict both, and report recon rows and live rows separately
   wherever a block contains both.
8. **One panel, built once, frozen** — §4. Verified by sha256, not by row count.
9. **One set of split boundaries**, identical across all three — §7.1.
10. **One global trials ledger**, appended to *before* data is opened — §8.
11. **One holdout opening, covering all three** — §12. No agent opens it. No agent reads another
    workstream's plan, intermediate results or candidate before that pass.
12. **A candidate rule may read only columns populated on both halves.** ⚠️ New. `float_shares` is
    ~83 % null by construction, `short_percent` is not collected, `news` exists on 11 % of sessions
    as unextracted text, and `first_rank` is not recoverable on recon (inventory §7.2–§7.4). A rule
    reading any of them is fittable on 58 sessions and validatable on none — and those 58 sessions
    are the holdout (§7.1). The consequence is deliberate and is named as a blind spot in §14.
13. **Holdout outcomes are withheld physically, not by instruction** — §4.3.

### 5.1 How no-lookahead is enforced mechanically

Three layers, in order of how much they are trusted:

1. **Column subsetting (the real mechanism).** A candidate rule is submitted as a pure predicate
   over a frozen `RULE_COLUMNS` list. The runner subsets the panel to `RULE_COLUMNS` **before**
   calling the predicate. A rule that reaches for `day_high` or `max_r` raises `KeyError`. It cannot
   quietly work, and it cannot work on the author's machine and fail in review.
2. **`assert_no_lookahead()`** from the harness, run on the subset, as a belt to the braces.
3. **Time-ordered capacity.** `build_book` takes the earliest N triggers by time. No ranking step is
   permitted anywhere in a candidate — if a plan appears to need one, that is a stop-and-report.

⚠️ **Stage-0 item S0-C exists because the inventory is ambiguous here.** Inventory §4 says four
columns "feel like trigger-time context and are not" and then lists seven. Whether any of the seven
are inside the harness's `TRIGGER_TIME_SAFE` whitelist is unstated. S0-C checks, removes any that
are, and re-freezes `RULE_COLUMNS` before the panel is published.

### 5.2 What counts as a trial

**A trial is one (hypothesis, parameterisation) scored against any quantity derived from an outcome
column or from post-trigger price, on any split.**

- A k-point grid is **k trials**, not one.
- A walk-forward refit is **1 trial per refit window** (prior 2's ⚠️, enforced).
- A permutation null is **not** a trial — it is the control for a trial set, and it must be run at
  matched intensity (§7.3).
- A **sensitivity band** (±20 % around an already-charged, already-frozen parameter) is **not** a
  trial. ⚠️ It is a robustness report on a frozen value and **may never be used to select one** —
  otherwise "sensitivity" is a free three-point grid wearing a disguise. If a sensitivity band
  changes the chosen parameter, the new value is a new trial.
- Anything computed **without reading an outcome column or a post-trigger price** costs nothing.
  Feature coverage, nullity, stationarity, row counts, feature-feature correlation, trigger-time
  distributions: all free. This is the only rule keeping "exploratory data analysis" from becoming
  an uncounted search, and it is why Stage 0 is nearly free.

⚠️ **Post-trigger price is an outcome.** Measuring achievable fill quality on the minute tape reads
prices after the trigger; it is charged. W2's budget is sized for that (§8.2).

### 5.3 Grids are quantiles, never absolutes

Every threshold grid in every plan is stated as a quantile of the **fit-split** distribution of the
quantity — `q30 / q50 / q70`, not `$2.50 / $5.00 / $10.00`. Two reasons: it is the anti-anchoring
mechanism that makes the `SHIPPED` carve-out safe (§1.1), and a quantile means the same thing on a
split whose distribution has moved, which an absolute threshold does not.

---

## 6. The objective function

**J = mean net R per session**, over every session in the evaluated block, with a session that
produced no trade contributing **0** (not dropped — dropping flat sessions is how a low-throughput
rule flatters itself).

Net R is the harness `score()`'s net figure: gross R less commission and slippage, at $500 with the
sizing the account actually uses.

Subject to, as **constraints rather than terms**:

- **Throughput**: realised trades per session over the block ∈ **[0.6, 1.0]**. Outside the band the
  candidate is **disqualified**, not penalised. The operator declared 0.8/day as a constraint; a
  penalty term would let a search trade it away.
- **Sign**: `J > 0` on the block. A candidate that is less negative than the alternative is not a
  candidate.
- **Constraint 12**: no live-only column.

### 6.1 Why not the alternatives

| rejected | why |
|---|---|
| Sharpe, t-statistic, any return/volatility ratio | penalises the burst shape prior (1) declares **expected**. A rule set that pays in bursts is the target, not a defect to be optimised away. |
| win rate, profit factor | cost-blind, and at $500 the cost drag is the whole question. |
| total R over the record | throughput-blind — it rewards trading more, which is the opposite of the declared constraint. |
| net dollars from $500 | conflates strategy quality with capital adequacy (operator fact 3). It is **reported** as the capital-adequacy view — the number the operator lives on — and never optimised. |
| expectancy per trade | the power-relevant unit, and it **is** reported (§7.2 needs it). But J is per *session* because throughput is a constraint and the session is the unit the operator experiences. |

### 6.2 What is reported alongside J, always

Non-negotiable, on every scored block, because prior (1) says the mean alone will mislead:

1. the per-session net-R distribution, by decile;
2. the **share of total net R contributed by the best decile of sessions** — if it is ~all of it,
   say so;
3. the longest run of consecutive losing 20-session blocks, and the deepest peak-to-trough in
   cumulative R;
4. **gross R per session beside net** — a gross-up/net-down candidate is a failure (inventory §7.10);
5. **recon rows and live rows separately** (constraint 7);
6. realised throughput, and the count of trades the capacity cap turned away;
7. the permutation p-value at matched search intensity (§7.3);
8. mean and sd of **net R per trade** — the power-relevant unit (§7.2);
9. the capital-adequacy view: end equity from $500, max drawdown %, and the count of trades that
   were **unaffordable** or **cap-bound** (inventory §8 found 51 and 49 of 78 — at $500 the account
   is frequently the binding constraint, and a candidate can be good and unaffordable).

---

## 7. Splits, validation and the null

### 7.1 The splits — one set, all three, time-ordered, no gap

The record is contiguous across the recon→live boundary, which is its single most valuable
structural property (inventory §1). The splits use it.

| split | dates | half | approx sessions | approx taken trades at 0.8/day |
|---|---|---|---|---|
| **FIT** | 2024-09-09 → 2025-09-30 | recon | ~245–265 | ~200–210 |
| **CHECK** | 2025-10-01 → 2026-03-31 | recon | ~110–125 | ~90–100 |
| **HOLDOUT** | 2026-04-01 → 2026-09-17 | recon to 2026-06-30, **live** from 2026-07-01 | ~115–125 | ~95–100 |

Session counts are estimates from the inventory's 453 recon (of 501 in window) + 58 live. **Stage 0
publishes the realised counts** and S0-A carries the decision rule if a split lands materially thin.

**Why these boundaries:**

- **The holdout is the most recent block**, which is the only honest direction. Fitting on data that
  post-dates the test block is the worst available leak.
- **The holdout spans both halves.** ~65 recon sessions and all 58 live ones. This is the design's
  best property: the single holdout pass tests, in one shot, whether the candidate survives the
  change in observation regime — and constraint 7 makes that report mandatory. A recon-only holdout
  would have left the recon→live transfer question permanently unanswered.
- **The live window is 58 sessions ≈ 46 trades.** As a holdout on its own it can distinguish
  essentially nothing. Extending back to 2026-04-01 roughly doubles it.
- **CHECK is a full six months**, so a candidate must survive a *stretch*, not a good month —
  prior (1) again.
- **FIT is ~13 months**, long enough to contain both hot and cold stretches and to support three
  walk-forward folds (§7.2).
- Boundaries land on month ends, so no split cuts a week in half.

⚠️ **The cost of this choice, stated plainly:** the live window is entirely inside the holdout.
Therefore news, float, `first_rank` and the 167 hand reviews are all in the holdout region. Constraint
12 already rules the first three out of any candidate; §9.2 and §14 handle the reviews.

### 7.2 Walk-forward inside FIT

Expanding-window, rolling refit, **three folds**: train on FIT sessions `1..k`, test on `k+1..k+50`,
for `k` at roughly 95 / 145 / 195 (exact indices set by S0-A's realised counts). Reported per fold:
J on the test block, and **parameter stability** — do the refit parameters land in the same
neighbourhood across folds, or wander? A candidate whose refit parameters wander is a candidate
whose fit is noise, whatever its mean J. That report is a first-class deliverable, not a footnote.

**The power table, and the hard truth in it.** Taking per-trade net-R sd ≈ 1.4 R (a 2R-target
bracket at a ~30 % hit rate implies sd ≈ 1.38), the minimum detectable per-trade effect at 80 %
power is:

| block | ~trades | 1 candidate | 20 trials (Bonferroni) | 120 trials |
|---|---|---|---|---|
| FIT (~205) | 205 | ~0.27 R | ~0.38 R | ~0.42 R |
| CHECK (~95) | 95 | ~0.40 R | — (≤3 candidates carried) ~0.48 R | — |
| HOLDOUT (~98) | 98 | ~0.40 R | 3 candidates: ~0.43 R | — |

Two things follow, and both are load-bearing:

- **Bonferroni's penalty grows slowly** — going from 20 to 120 trials moves the detectable effect
  from ~0.38 R to ~0.42 R. The multiplicity risk here is **not** the z-score; it is the
  selection-of-maximum bias, which Bonferroni does not touch and the permutation null does. This is
  why §7.3 is a hard constraint and the budget is sized on other grounds (§8.2).
- **The record can certify a large edge or nothing.** At ~98 holdout trades and three candidates, a
  true edge below roughly **+0.4 R per trade** will not clear the bar. At 0.8 trades/session that is
  ~+0.32 R/session — a very large effect. **Design and read the whole analysis knowing this.** A
  documented null is the *likely* outcome and is a success (§12.3); a candidate that scrapes past on
  the holdout by a hair should be treated as unconfirmed, not as shipped.

⚠️ sd = 1.4 R is an assumption, and it is larger for the runner family and smaller for the hybrid.
**S0-H measures it** — the one chargeable Stage-0 item — and the table is recomputed before any
Stage-1 pre-registration is finalised.

### 7.3 The permutation null

Constraint 5's mechanism, and the control the record itself has shown to matter (inventory §7.8).

- **Shuffle outcomes within session, in blocks** — permute which setup of a session got which
  outcome, keeping the session's trigger times, its trade count and its place in the sequence. Prior
  (1) says the burst structure is real; a global i.i.d. shuffle would destroy it and produce a null
  that is too easy to beat. Block-by-session preserves it.
- **Re-run the entire search** — every trial in the set, not the winner — against each shuffled
  record. `B = 200` replicates.
- **p = the fraction of null searches whose best-of-search J exceeds the real best-of-search J.**
  That is the number that answers "would a search this hard have found something this good in
  noise?", which is the question the record has already answered *yes* to once.
- A candidate with `p > 0.05` on the fit split does not proceed. No exceptions, no "but the
  economics make sense".

### 7.4 Sensitivity

Every frozen parameter is reported at ±20 %, and the J at each. Per §5.2 this is a robustness
report, never a selection device. A candidate whose J collapses within ±20 % of a threshold is
reported as fragile and, unless the plan says otherwise, is not the one that gets frozen.

---

## 8. The trials ledger and the global budget

### 8.1 The ledger — where, and how it cannot be quietly rewritten

**A dedicated GitHub issue**, "Analysis trials ledger", one comment per entry. Chosen over a file on
a branch because comments are append-only, server-timestamped, attributable to their author, and
carry a visible edit history — three concurrent agents writing to one file on one branch is a merge
conflict pretending to be an audit trail.

Every agent, before opening any split:

1. posts its **Stage-1 pre-registration** comment — hypotheses, grids (as quantiles), objective,
   success threshold, stopping rule, and its trial count arithmetic;
2. posts a **batch comment** before each Stage-2 batch is scored, naming the trials it is about to
   spend and its running total;
3. posts every **amendment** (a Stage-0 finding that changed the plan) as its own comment, before
   acting on it.

The agent posts and proceeds — it does not wait for approval, because it cannot ask questions
mid-flight. The ledger is a record, not a gate. **The gate is the custodian**, who at the holdout
pass verifies that each frozen candidate's specification matches what was ledgered at Stage 1 plus
its ledgered amendments. A candidate that does not match its ledger entry is not scored.

At the holdout pass the custodian transcribes the full ledger into `research/analysis-ledger.md` as
the permanent archive. The issue is the live log; the doc is the record.

### 8.2 The global budget: 120 trials

| allocation | trials | |
|---|---|---|
| Stage 0 (shared) | **6** | **S0-F** the cost-model audit (2) + **S0-H** the outcome-dispersion estimate and Filter-A baseline (4) — the only two Stage-0 items that read outcomes (§4.5) |
| **W1** — selection × exit | **54** | |
| **W2** — execution and the cost floor | **36** | |
| **W3** — time structure and the refit protocol | **24** | |
| **total** | **120** | |

**How 120 was arrived at.** Four arguments, in descending weight:

1. **The empirical precedent.** 15,434 combinations destroyed this record once (inventory §7.8).
   Two orders of magnitude below that is ~150. Three agents multiply exactly like one big search, so
   the number is set globally and then *cut* for the three-way split, not multiplied by it. → ~120.
2. **What the check split can adjudicate.** At ~95 check trades, se ≈ 0.14 R/trade, and each
   candidate carried to check is a multiplicity on it. Three candidates per workstream is already
   nine on a 95-trade block. The budget has to bottom out at **one frozen candidate per workstream**
   (§12.1), which caps how wide the search above it can usefully be.
3. **What the z-score costs.** Very little (§7.2) — so multiplicity control is *not* the binding
   argument, and pretending it is would license a much larger budget than (1) and (2) allow.
4. **What an agent can actually pre-register and defend.** A 500-trial plan is not a plan; it is a
   sweep with a covering letter.

The split 54 / 36 / 24 follows the expected-value ordering argued in §13.2. Each plan's §"hypothesis
budget" shows its own arithmetic down to the individual grid.

**Overrun rule.** An agent that would exceed its allocation **stops** (§13.1). It does not borrow
from another workstream — the allocations are not fungible, because the whole point of a global
budget is that three agents cannot negotiate it upward between themselves.

---

## 9. Filter A — the a-priori filter

Prior (3) forbids studying regimes in the unfiltered population, and forbids conditioning a regime
study on a filter fitted with sight of the whole record. So the filter used by W2 and W3 is declared
here, **before any data was opened**, and its one data-dependent element is fitted on FIT alone.

**Filter A**, applied to the frozen panel:

1. `trigger_et_min < 570` — pre-market, both halves (forced by constraint 7; not a filter choice).
2. `hits_before_trigger >= 1` — the name was on the attention series before it broke.
3. `cum_dollar_vol_to_trigger >= q50(FIT)` — the median of the **fit split only**, computed once by
   Stage 0, published as a literal constant in `panel-v1-spec.md`, and frozen thereafter. It is
   applied unchanged to CHECK and HOLDOUT.
4. Capacity: the **earliest 1 trigger per session by trigger time**. No ranking (constraint 1).

Filter A is **never tuned**. It costs no trials, because it was chosen without reading an outcome.
Its robustness is reported at q40 and q60 as a sensitivity band (§7.4), which per §5.2 may not be
used to change it.

Expected realised throughput: with ~18.5 setups/session before filtering, Filter A should leave a
survivor on most sessions, so throughput should land near 0.85–0.95 trades/session — inside the
[0.6, 1.0] band. **S0-D reports the realised figure.** If it lands outside the band, that is a
stop-and-report and the protocol is amended centrally, not by the workstream that noticed.

### 9.1 What Filter A is not

It is **not** a candidate, not a recommendation and not the incumbent. It is a fixed, defensible,
outcome-blind way of producing a trade stream so that W2 and W3 have something to study that is not
the raw population. W1 does not use it — W1's whole job is to find a better one, and it is the only
workstream permitted to fit a filter.

### 9.2 The 167 hand reviews — quarantined, with one narrow exception

The reviews cover the live window, which is inside the HOLDOUT. They also carry an outcome
(`annotations.max_r`) and a free-text note written after the fact.

**No workstream may open them.** The one permitted use is a Stage-0 item (S0-G), run by the Stage-0
builder, through a **redacting loader** that drops `note` and `annotations.max_r` at parse time and
exposes only `pole`, `consolidation`, `entry`, `stop`, `entry_t` and `no_trigger`. Its single output
is a **detector-geometry agreement rate**: how often the detector places the consolidation where a
human did. That reads human-drawn geometry and bars, and conditions on no outcome.

⚠️ Two honest caveats, both of which go in the Stage-0 report: the trader chose *which* 167 days to
review, and that choice is outcome-informed — so the agreement rate is conditional on being
reviewed and **must not be read as a population statistic**. And the residual leak is not zero: a
human's taste in geometry correlates with what happened next. That is why the output is a single
diagnostic number and why **no workstream may condition a rule on a review**.

---

## 10. The four exit families — defined once, here

Defined centrally so that "the scalp branch" means the same thing in all three plans and the three
results compose. Every family stops at the consolidation low and measures R against the
conservative 3-tick fill, not the 1-tick mechanical trigger (inventory §3.2).

| id | family | definition |
|---|---|---|
| **F1** | **scalp** | bracket, fixed target **0.5 R**, no scale, no trail |
| **F2** | **base** | bracket, fixed target **2 R**, no scale, no trail — the inventory's reference target, carried so this analysis is commensurable with the record's stated state (§8) |
| **F3** | **runner** | no fixed target; structural trailing exit — exit on the first close below the prior bar's low once the trade has been ≥ 1 R in favour; unresolved trades marked to the last visible bar |
| **F4** | **hybrid** | half off at **1 R**, stop to break-even on the remainder, remainder on F3's trailing rule |

F3's trailing rule has one free parameter (the arming threshold, here 1 R). It is **fixed at 1 R for
W1 and W3**, and is W2's to search — that is precisely the selection/execution division of labour.

⚠️ **F3 and F4 hold past 09:30 on the recon half. This is legitimate**, and the inventory is
ambiguous about it — see §11.2.

---

## 11. The cost model — and the inconsistency that gates everything

### 11.1 The cost identity

At $500 with full-buying-power sizing (inventory §8), shares ≈ `BP / price`, so dollar risk ≈
`BP × stop_pct`. Therefore the round-trip cost expressed **in R** is:

```
c ≈ (2 × commission_per_side) / (BP × stop_pct)   +   (2 × slip_per_share) / (price × stop_pct)
  = (2 × commission_per_side) / (BP × stop_pct)   +   2 × slip_pct / stop_pct
```

Both terms are **inversely proportional to the percentage stop distance**, and neither depends on
the price level once sizing is buying-power-bound. The consequence is the single most useful thing
this design derived, and it belongs to all three plans:

> ⚠️ **Percentage stop distance is a cost lever, not only a risk lever.** A selection rule that
> favours tight-stop setups buys itself a structurally worse cost floor, and will look better gross
> than net. Every candidate reports the **distribution of `stop_pct`** across its taken trades, and
> W2 reports every exit conclusion **as a function of `stop_pct` decile** so that it transfers to
> whatever selection W1 lands on.

Break-even hit rate for a fixed target `T` (stop at −1 R): gross `p = 1/(1+T)`; net
`p = (1+c)/(1+T)`.

### 11.2 The inconsistency, and why it is an exit gate

⚠️ **The inventory disagrees with itself about `c` by nearly 3×**, and it is the number that decides
whether the scalp branch exists:

- **§6** states costs eat "~7 % of every R before slippage, ~10 % after" → `c ≈ 0.10 R`.
- **§8** states net break-even at a 2 R target is "~42.9 %" → that is exactly 3/7, which back-solves
  to `c ≈ 0.286 R`.

`c = 0.10` gives a 2 R net break-even of **36.7 %**, not 42.9 %. The two cannot both be right. What
it does to the exit families:

| family | gross break-even | net at `c = 0.10` | net at `c = 0.286` |
|---|---|---|---|
| F1 (0.5 R) | 66.7 % | **73.3 %** | **85.7 %** |
| F2 (2 R) | 33.3 % | 36.7 % | 42.9 % |
| F3 / F4 | n/a — expectancy, with `c` subtracted from **every** trade regardless of outcome | | |

At `c = 0.10` the scalp branch needs a ~73 % net hit rate — demanding. At `c = 0.286` it needs
~86 %, which is not a hurdle, it is a wall. **The design cannot tell you which, and must not
guess.**

Hence **S0-F is an absolute Stage-0 exit gate**: the cost model is audited against the broker's real
fills, `c` is measured as a *distribution over `stop_pct` deciles* rather than a scalar, the
break-even table above is recomputed from the measured values, and the result is published to all
three workstreams **before any of them opens a fit split**. If the measured `c` puts F1's net
break-even above 80 %, **F1 is cancelled across all three plans** and its trials return to the
global budget unspent. Killing a branch in arithmetic costs one Stage-0 item; killing it in
execution costs a third of W1's budget and half of W2's.

This inconsistency should also be corrected in `data-inventory.md` once S0-F resolves it.

### 11.3 The recon session window — resolved, and the inventory should say so

The inventory reads ambiguously: §1 gives recon's observation window as "04:00 – 09:30 ET only" and
§10 lists "post-09:30 behaviour on the recon half" under *does not support*, while §2.3 says the
recon `bars` dataset stores the **full session** on purpose.

**Read against `harvest/runner.py` (permitted — it is raw-capture code), §2.3 is right and the other
two are loosely worded.** The harvest trims to `PREMARKET = (04:00, 09:30)` only for the
*appearance reconstruction* and for `bars_1m`; the 5-minute `bars` dataset is written from
`trim_session(all_bars, chart_start, capture_end)` and aggregated across the whole session,
explicitly so the exit walk is not truncated at 09:30 and does not close every still-open 09:10
entry at 09:25.

So: on the recon half, **appearances are pre-market only; price is full-session**. F3 and F4 may
hold past the bell on recon. S0-E verifies this in the data (max bar timestamp per recon session)
rather than taking the code's word for it, and carries the fallback if it turns out otherwise.

---

## 12. The custodian and the single holdout opening

### 12.1 The condition

The holdout is opened **once**, when all three of these hold:

1. all three workstreams have posted a **Stage-3 freeze** comment to the ledger, each naming
   **exactly one** candidate — fully specified (selection predicate, exit family and its parameters,
   capacity rule, every constant) — **or** a documented null;
2. each frozen candidate's specification matches its Stage-1 ledger entry plus its ledgered
   amendments;
3. no workstream has an open stop-and-report (§13.1).

"One candidate per workstream" is not a stylistic preference. It is what the check split's power
allows (§8.2), and it is the number that makes the three-way multiplicity correction tractable.

### 12.2 The custodian

**The operator is the custodian.** The pass is executed on the **Mac** by a fresh `strategy-analyst`
(opus) session that has read **none** of the three workstreams' intermediate results — only the
three frozen candidate specifications and this protocol. That session holds the unredacted panel and
paths; no agent does.

The pass: score all three candidates on HOLDOUT in one run, with

- the objective and the full §6.2 report for each;
- **recon rows and live rows separately** (constraint 7) — this is the recon→live transfer test the
  split was designed to make possible;
- a multiplicity correction across the three — a single permutation null (§7.3) run over the three
  candidates jointly, reporting the probability that the best of three does this well by luck, with
  Bonferroni at α = 0.0167 one-sided as the cross-check;
- the §7.2 power table recomputed on the realised holdout trade count, so the report states what it
  could and could not have detected.

Then it stops. There is no second look, no "one more variant", and no re-opening if the answer is
disliked. The result is written to `research/analysis-ledger.md` alongside the transcribed ledger,
and published as a **dated report** — the holdout result is a finding, and findings are dated and
correctable (`publish-report`). The plans are not.

### 12.3 What a null looks like, and why it is a success

If no candidate clears, the deliverable is a report that states: what was searched, at what
intensity, what the null said, what the power table says could have been detected, and **what would
change the answer** — which of the named blind spots (§14) is worth funding, and what it costs.

That is a successful outcome of this design. The alternative — a candidate shipped because the
analysis was expected to produce one — is the failure mode §0 exists to prevent. Read §7.2 again:
the record can certify a large edge or nothing, and "nothing" is the more likely reading of a record
whose shipped configuration is below break-even and whose shape gates already measured no better
than taking everything (inventory §7.9, §8).

---

## 13. Working rules for the three agents

### 13.1 The escalation rule — stop, do not improvise

An agent cannot ask a question mid-flight, so every Stage-0 item in every plan carries a
**pre-authorised fallback**. Where one applies, take it and ledger the amendment.

Where none applies — a column missing, the panel hash mismatching, a split materially thinner than
§7.1 states, the harness lacking a primitive the plan assumed, the budget about to be exceeded —
**stop**. Write the finding to the workstream's sub-issue and to the ledger, say precisely what
assumption failed and what you would need, and end the session. Do not substitute a different
column, a different split, a different metric or a smaller B. An improvised substitute is an
unledgered design change made by the party with the least context, and it is how three plans become
three different analyses.

### 13.2 Why the three-way split is ordered this way

Argued, not assumed.

1. **W1 first (54)** — it is the only workstream that can produce the thing the objective names, and
   it works on the only asset that is already modelling-ready (the panel: ~9 k rows, 46 trigger-safe
   columns, a clean causal boundary). Its marginal trial buys the most, and it is also the workstream
   most exposed to the collapse failure — which is where the discipline is worth spending.
2. **W2 second (36)** — it attacks the *declared binding constraint*: at $500 the cost floor decides
   which exit families exist at all (§11). A large share of its trials are **estimations with no free
   parameter** rather than searches, so each one buys more certainty than a W1 grid point. It also
   owns the largest untouched asset in the record, `bars_1m` (32 M rows, nothing has ever read it) —
   the highest-novelty-per-trial work available. And its Stage-0 contribution (S0-F) *gates the other
   two*: if F1 is arithmetically dead, W1 saves a third of its crossed design before spending it.
3. **W3 third (24)** — it is the most under-powered, and a null is its likeliest honest result. It
   earns its allocation anyway, for two reasons. Prior (2) asks for a **tuning protocol**, and
   without a measured answer to "what refit cadence does this record support?" the project re-tunes
   ad hoc forever — and every ad-hoc re-tune is an uncounted trial, which is the uncontrolled version
   of exactly the risk this whole design exists to control. And prior (1) asks for the **distribution
   of period outcomes**, which is W3's to measure and which every other result is read against.

### 13.3 Getting at the data

| from | how |
|---|---|
| the **Mac** | direct `data/live` + `data/recon`; the `review-analysis` skill. **Bar-level work lives here.** |
| the **box** | [`scripts/box-job.sh`](../scripts/box-job.sh), **per date, one at a time**. ⚠️ never `docker exec` into the app; never `--all`. It is a 2 vCPU / 4 GB CX23 and a heavy job takes it down hard. |
| a **cloud session** | the `box-data` skill → `data-export.yml` → the `data-export` branch. The frozen panel (~9 k rows) is trivially portable this way; **the recon `bars` and `bars_1m` datasets are not** — tens of millions of rows against a GitHub branch. Push bar-level work down as an aggregation, never a transfer. |

### 13.4 Measurement and interpretation stay separate

`spike-runner` / `builder` measures. `strategy-analyst` interprets. Never one step that does both —
a step that measures and concludes in the same breath is a step that chose its measurement to suit
its conclusion.

---

## 14. What none of the three covers

Named here so it is a known gap rather than a later surprise. None of these is funded.

| blind spot | why it is out | what it would cost to cover |
|---|---|---|
| **The universe question** — should we be looking at a different population? | The record contains only names that already gapped and ran (inventory §7.5). `daily_universe` holds names that ran but never set up, but there are **no intraday bars** for them, so nothing outside the opportunity spine can be traded in simulation — and constraint 2 forbids reporting a statistic on what could not have been traded. W3 uses it only as *prior-day* session breadth. | A second harvest: intraday bars for the ~217 daily-universe names per session. On the observed rate (82,705 vendor calls bought 453 sessions of the spine) this is a multiple of the original spend and ~10× the storage. Roughly 3–6 months of harvest nights. |
| **News and float** | Live-window only — 58 sessions, and that window is the holdout (§7.1). Constraint 12 rules them out of any candidate. News is also unextracted text; nothing has parsed a headline. | 6–12 more months of live collection before there is anything to validate on, or a vendor historical news feed. Float is worse: recon has no float at all (EDGAR `shares_outstanding` only). |
| **Supervised learning from the trader's taste** | The 167 reviews sit in the holdout window, and 167 labels against ~9,000 setups is a supervision signal, not a training set (inventory §5). Only the geometry audit runs (§9.2). | **The cheap fix, and it should be started now rather than costed later: annotate recon-window days.** ~300–500 reviews spread across 2024-09 → 2026-03 would move the asset into FIT and make a supervised workstream fundable in the next pass, at the cost of the trader's chart time and nothing else. |
| **Cross-sectional ranking** — "take the best two of today's setups" | Forbidden as lookahead (constraint 1): you cannot rank a day's setups at 07:00. Capacity is earliest-N, always. | Not coverable from this record. It would need a live forward test where the ranking is *actually decided* in real time — i.e. Phase 2, not analysis. |
| **The W1 × W3 interaction** — a selection rule fitted *jointly* with regime conditioning | Deliberately unfunded. The joint space multiplies the trials count past what §8.2 allows, and W3 must use an a-priori filter anyway (prior 3's leak block, §9). | ⚠️ **The cost is real and specific: a selection rule that works only in some regimes will be missed by both W1 (which pools across regimes) and W3 (which holds selection fixed).** If W1 and W3 both return nulls, this interaction is the first thing the next pass should fund. |
| **Post-09:30 *appearances* on the recon half** | The recon scanner is reconstructed pre-market only (§11.3). A setup that first appeared at 10:30 exists on 58 live sessions and nowhere else. | Reconstructing regular-session appearances would need the full-session minute tape for the whole universe — the same purchase as the universe question. |

---

## 15. Cost estimate for the execution pass

| piece | where | tier | sessions |
|---|---|---|---|
| Stage 0 — panel build, redaction, S0-A…S0-J | Mac | `builder` (sonnet) | 2–3 |
| S0-F cost audit + S0-H dispersion | Mac | `builder`, interpreted by `strategy-analyst` | 1 + 1 |
| W1 | cloud, off the exported panel | `builder`/`spike-runner` measuring, `strategy-analyst` interpreting | 6–8 |
| W2 | **Mac** (32 M rows of `bars_1m` cannot leave the box/Mac) | `spike-runner` measuring, `strategy-analyst` interpreting | 6–8 |
| W3 | cloud, off exported session-level aggregates | `builder`/`spike-runner`, `strategy-analyst` | 4–5 |
| Holdout pass + report | Mac | `strategy-analyst` (opus), custodian | 1–2 |
| Board and ledger hygiene throughout | — | `board-keeper` (haiku) | as needed |

**Total ≈ 21–28 sessions**, of which ~5–6 are opus (interpretation and the holdout pass) and the
rest sonnet or haiku. The subscription is Pro: one task per session, then `/clear`.
