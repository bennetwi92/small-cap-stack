# Analysis plan 3 — time structure and the re-tuning protocol: *when to be trading at all*

**Status:** LIVE (2026-09-18). Workstream **W3** of the three-agent analysis of the 2-year Phase-1
record.

> **Read [`analysis-protocol.md`](./analysis-protocol.md) first, in full.** It carries the blind
> list, the operator facts and priors, the thirteen hard constraints, the frozen panel, the splits,
> the objective function, the exit families, the cost identity, the trials ledger, the custodian and
> the single holdout opening. This document carries only what is specific to W3. Where the two
> appear to disagree, the protocol wins and the disagreement is a stop-and-report.

The copy-paste starting prompt for the W3 agent is [§12](#12-the-starting-prompt).

---

## 1. The question

> **Does the post-selection trade stream have exploitable time structure — can a session-level,
> trigger-time-decidable state variable tell you to stand aside — and what re-fitting cadence does
> this record actually support?**

Why it is worth 24 of the 120 global trials, and why it is third rather than first:

- **It is the workstream prior (2) asks for.** The deliverable is not a frozen rule set but a rule
  set *plus a tuning protocol*, and without a measured answer to "what refit cadence does this
  record support?" the project re-tunes ad hoc forever. ⚠️ Every ad-hoc re-tune is an **uncounted
  trial** — which is the uncontrolled version of exactly the risk this whole design exists to
  control. W3 is how that gets counted.
- **It is the workstream prior (1) asks for.** The operator expects hot and cold stretches and
  expects a flat-to-negative run not to be a defect. Somebody has to measure the shape — how often a
  cold stretch runs, how deep, what fraction of total R comes from the best decile of sessions — and
  every other result is read against it.
- **It is third because it is the most under-powered**, and it should be read expecting a null. At
  ~200 fit-split trades, session-level conditioning has very little to work with. A documented null
  here — "this record cannot tell you when to stand aside, and here is what it could have detected"
  — is worth 24 trials, because the alternative is an expensive belief held indefinitely.

⚠️ **W3 owns timing. It does not own selection (W1) or execution mechanics (W2).** See §10.

---

## 2. The population and the panel

**Population:** the **Filter A stream** — the frozen panel restricted by protocol §9: pre-market
triggers, `hits_before_trigger ≥ 1`, `cum_dollar_vol_to_trigger ≥ q50(FIT)`, capacity `N = 1`
earliest by time, scored under **F2** unless stated otherwise.

**This is prior (3), and it is the whole reason W3 is shaped this way.** The trader's stated belief
is that regime structure in the *unfiltered* population — every name that gapped and set up — is
structure in a population nobody will ever trade, and is therefore not evidence. So W3 studies the
post-selection stream only.

**The leak prior (3) warns about, and how it is blocked mechanically.** A filter fitted on the whole
record and then studied for regimes has already seen the regimes. Filter A is immune by
construction:

- it was **declared a priori**, in `analysis-protocol.md`, before any data was opened;
- its only data-dependent element is a **median of the FIT split**, computed once by Stage 0 and
  published as a literal constant in `panel-v1-spec.md`;
- it is applied **unchanged** to CHECK and HOLDOUT, and **W3 may not tune it** — not a threshold,
  not a column, not the capacity rule. Any change to Filter A is a stop-and-report;
- it costs **zero trials**, because choosing it read no outcome.

**W3 may not use W1's filter**, even if it were available — W1's filter is fitted on the fit split
with sight of outcomes, and conditioning a regime study on it would reintroduce exactly the leak
prior (3) names. W3 does not read W1's intermediates at all (constraint 11).

**Verification is by bytes.** `sha256sum panel-v1.parquet` against `panel-v1-spec.md` on `main`
before anything else. A mismatch is a stop-and-report. Never rebuild the panel.

---

## 3. Stage 0 — W3's own reconnaissance

The shared items S0-A … S0-J (protocol §4.5) run centrally. W3 adds four, all free under protocol
§5.2 — none reads an outcome.

| id | item | decision rule / pre-authorised fallback |
|---|---|---|
| **W3-0a** | Build the **session-state series** (§5.1) over all ~511 sessions: prior-session opportunity count, prior-session attention intensity, prior VIX close and its 5-day change. Verify each is populated on **both** halves (constraint 12). | A feature not populated on both halves is **dropped and its trials returned** — a regime feature that exists only on recon is untestable on a holdout that is 47 % live. ⚠️ This is why `daily_universe` breadth is **not** a W3 feature: it is recon-only harvest bookkeeping. The both-halves substitute is the prior-session **opportunity** count, which exists on both. |
| **W3-0b** | Stationarity and autocorrelation of each session-state feature across FIT / CHECK / HOLDOUT-features. | A feature whose distribution shifts materially between FIT and HOLDOUT is **flagged in the freeze report**, not dropped — it is a fact about the record, and a gate fitted on a shifted feature failing out of sample is the finding, not an accident to be hidden. |
| **W3-0c** | Feature-feature correlation among the four session-state features on FIT. | \|ρ\| > 0.9 between two features → keep the better-covered one, drop the other, ledger it. Two names for one variable would spend eight trials measuring one thing. |
| **W3-0d** | **Sessions-with-a-trade census** on the Filter-A stream: how many of the ~511 sessions produce a trade, and what the distribution of trades per session is. Free — it reads trigger times, not outcomes. | Fewer than **60 %** of FIT sessions producing a trade → a stand-aside gate has almost nothing to remove, and W3's T1 grid is re-scoped by amendment to two thresholds per feature before Stage 1. Fewer than **40 %** → **W3 is cancelled**, trials returned, written up as a null. |

**Stage-0 exit gate for W3:** S0-F, S0-H and S0-J posted (S0-H also gives W3 its Filter-A **baseline
J** and the per-session distribution it measures everything against); the panel hash verified; at
least **three** session-state features surviving W3-0a and W3-0c; the W3-0d census ledgered.

**Cancellation condition:** fewer than three surviving features, or W3-0d below 40 %, or S0-H's
recomputed power table showing a minimum detectable effect above **0.6 R/trade** on the Filter-A
stream at W3's allocation. The last one is not hypothetical — W3 is the workstream most likely to
trip it, and tripping it is a legitimate, budget-saving outcome.

---

## 4. The hypothesis budget: 24 trials

| stage | what | trials |
|---|---|---|
| **T1 — marginal gates** | 4 session-state features × 3 quantile thresholds, as a **stand-aside gate**, scored under F2 | **12** |
| **T2 — conjunction** | the 2 best features combined, 2 thresholds each | **4** |
| **T3 — exit-family dependence** | the best gate re-scored under F3 and F4 | **2** |
| **T4 — refit cadence** | Filter A's `q50` constant refit on a rolling window ∈ {125, 250} sessions at a cadence ∈ {quarterly, semiannual} — 4 combinations, less one dropped for budget | **3** |
| **T5 — check** | the single candidate scored on CHECK | **1** |
| **reserve** | amendments arising from Stage-0 findings | **2** |
| **total** | | **24** |

⚠️ The **Filter-A baseline** — the stream's J and its full protocol §6.2 report on FIT — is **not**
charged to W3. It comes out of the shared **S0-H** (4 trials, Stage 0), and all three workstreams use
it as their common reference point. W3 consumes it; it does not re-derive it.

⚠️ **T4's refits are trials**, one per (window, cadence) combination, because prior (2) is explicit
that every refit is one. T4 is the measurement that produces the tuning protocol, and it is the one
place in the whole design where refitting is the *object* of study rather than a validation
technique.

**Nulls at matched intensity** (protocol §7.3): the null for T1 re-runs all 12 gates against each of
B = 200 block-shuffled records; the final null re-runs the entire T1+T2+T3 sequence. ⚠️ The shuffle
is **block-by-session**, which matters more here than anywhere else in the design: a global i.i.d.
shuffle would destroy the very burst structure W3 is testing for and would hand W3 a null it could
beat by accident.

---

## 5. The hypothesis class

### 5.1 The four session-state features

All four are **decidable at trigger time** — every one is built from information complete before the
session opens — and all four must be populated on **both** halves (constraint 12).

| id | feature | definition | why |
|---|---|---|---|
| **R1** | **breadth** | prior session's count of distinct opportunities, 5-session mean | how many names were setting up lately: the cheapest proxy for "is the market running". Uses the opportunity spine, which exists on both halves. |
| **R2** | **attention intensity** | prior session's `scanner_hits` rows per opportunity, 5-session mean | how *persistent* the scanner attention was, as distinct from how many names got it. The attention series is the only record of this (inventory §2.2). |
| **R3** | **volatility** | prior VIX close, and its 5-day change | the only external regime input wired, cached by `spikes/vix_regime.py`, complete over the population (inventory §6) |
| **R4** | **stream state** | trailing 20-trade realised **net** R of the Filter-A stream | the most direct test of "hot and cold". ⚠️ It reads outcomes — but only of *closed prior trades*, which are known at trigger time, so it is **not lookahead**. It is charged as a hypothesis like any other. |

⚠️ **`daily_universe` is not a W3 feature.** It is the record's closest thing to a negative-sample
universe and it is tempting as a breadth measure, but it is **recon-only harvest bookkeeping** —
absent on the live half, which is 47 % of the holdout. Constraint 12 rules it out and R1 is the
both-halves substitute. This is a real loss and it is named in protocol §14.

### 5.2 The conditioning form

**One form: a stand-aside gate.** Trade the Filter-A stream normally when the feature is on the
permitted side of a FIT quantile; take no trade that session when it is not. Thresholds are
quantiles of the FIT distribution (protocol §5.3), never absolutes.

**Out of the class, explicitly:** any change to Filter A; any selection feature (W1's); any exit
parameter (W2's); position-size modulation (that is capital adequacy, which protocol §6 keeps
separate from strategy quality and never optimises); any within-day timing rule (a rule about *which
hour* is a selection rule on `trigger_et_min`, and it belongs to W1's S4 family); any learned model;
any feature not decidable before the session opens.

⚠️ **A stand-aside gate cuts throughput, and throughput is a constraint.** A gate that stands aside
on 40 % of sessions takes the Filter-A stream from ~0.9 to ~0.54 trades/session — **outside the
[0.6, 1.0] band, and therefore disqualified** (protocol §6). That is not a nuisance; it is the
operator's declared constraint doing its job, and it is why the T1 grid is three quantiles rather
than a sweep: most aggressive gates disqualify themselves on throughput before their J is even
interesting. W3-0d's census is what makes this predictable in advance.

---

## 6. The objective function

**Protocol §6, unchanged: mean net R per session**, with the full §6.2 report on every scored block.
Sessions on which the gate stands aside contribute **0** — they are not dropped. Dropping them is
how a stand-aside rule flatters itself, and it is the single most likely way for this workstream to
produce a wrong answer.

W3 additionally owns, and reports as a first-class deliverable, the **period-outcome distribution**
that prior (1) demands and that every other result is read against:

1. the distribution of per-session net R across the Filter-A stream, by decile;
2. the **share of total net R contributed by the best decile of sessions**;
3. the **length and depth distribution of cold stretches** — how often a run of 20 / 40 / 60 losing
   sessions occurs, and how deep the cumulative-R drawdown gets;
4. the autocorrelation of session-level net R at lags 1 … 20 — the direct test of whether "hot and
   cold" is a real serial dependence or a pattern the eye imposes on independent bursts.

Item 4 is the cheapest honest answer to W3's question, and it is computed from the S0-H baseline
rather than charged as a trial of its own.

---

## 7. Validation design

- **Walk-forward with rolling refit**, three expanding-window folds inside FIT (protocol §7.2), with
  **parameter stability** reported — do the gate's chosen quantiles land in the same neighbourhood
  across folds? For W3 this is more than a diagnostic: an unstable gate parameter *is* the answer to
  "does this record support re-tuning at this cadence?", and it goes straight into §9.2.
- **Permutation null, block-shuffled by session, B = 200, at matched intensity** (protocol §7.3).
  `p > 0.05` on FIT and the candidate does not proceed.
- **±20 % sensitivity** on the frozen quantile, reported, never used to select.
- **Multiplicity**: the permutation null over T1+T2+T3 is primary; Bonferroni at α = 0.05/24 is the
  cross-check. The cross-workstream correction is the custodian's.
- **Both halves separately** on any block containing both (constraint 7) — which is the holdout, and
  which matters unusually much here: R1 and R2 are built from the opportunity and attention series,
  and those are *observed* on live and *reconstructed* on recon (inventory §7.2). A gate that works
  on recon and not on live may be telling you about the reconstruction, not the market. **That
  possibility is named in the freeze report in advance**, so the custodian reads the holdout split
  with it in mind rather than discovering it afterwards.

---

## 8. The regime treatment (prior 3) and the exit treatment (prior 4)

### 8.1 Regime — this is the whole workstream

Covered in §2: post-selection stream only; Filter A a priori with one FIT-fitted constant; no tuning
of the filter; W1's filter never used. The leak is blocked by construction and by the constant being
published as a literal before Stage 1 opens.

### 8.2 Exit — fixed, with one dependence check

W3 holds the exit fixed at **F2** for T1 and T2, because varying selection, timing *and* exit at
once is a product space this budget cannot buy. **T3 is the honesty check**: the best gate is
re-scored under F3 and F4, and if the gate's usefulness flips across exit families, that is reported
as a finding — it would mean timing and exit interact, which is a result about the market and a
warning about W3's own design.

Break-even per family comes from **S0-F** and is consumed, not re-derived (protocol §11.2). If S0-F
cancels F1, W3 is unaffected — F1 never appears in W3's grid.

---

## 9. What "done" looks like

### 9.1 The candidate branch

W3 freezes **one** timing overlay — a feature, a threshold and a direction — only if all of:

1. `p ≤ 0.05` on the FIT permutation null at matched intensity;
2. `J > 0` on FIT **and** `J` above the Filter-A baseline from S0-H — a gate that does not beat *not
   gating* is not a candidate;
3. realised throughput ∈ [0.6, 1.0] on FIT after the gate (§5.2's warning);
4. `J > 0` and above baseline on CHECK, same sign of effect, scored **once**;
5. the gate quantile stable across all three walk-forward folds;
6. `J` above baseline throughout the ±20 % sensitivity band;
7. net, not gross (constraint 6).

Condition 2 is W3's specific trap and is called out because it is easy to miss: a gate can raise J by
removing trades on a stream whose J is already negative, which is not evidence of timing skill — it
is evidence the stream is unprofitable. The comparison is always **against the ungated Filter-A
baseline**, never against zero.

### 9.2 The tuning protocol (prior 2) — W3's primary deliverable

This ships **whether or not** a timing overlay clears §9.1, and it is the reason W3 is funded.

> **What the record can and cannot support, in refitting terms.** From T4's four-combination
> measurement and the three-fold parameter-stability tables produced here and — where they are
> published — by W1 and W2:
>
> - **What may be re-tuned** across the whole system: threshold constants only. Never a column set, a
>   predicate form, an exit family or the capacity rule; those need a new pre-registration.
> - **On what window**, and **how often** — chosen from T4's measured combinations, not assumed.
> - **The guardrail:** a refit landing inside the ±20 % sensitivity band established at freeze is a
>   **re-tune** — apply it, ledger it, continue. Outside the band it is a **rescue** — do not apply
>   it. A rescue means the rule has stopped describing the market, and the honest response is a new
>   pre-registration, not a wider band. This distinction is the operational content of prior (2) and
>   it is what stops "we re-tune as needed" from meaning "we search continuously and never count it".
> - **⚠️ Every refit is a trial, ledgered forward, forever.** A tuning protocol that does not count
>   its own refits is an uncounted search running in production, and it will reproduce the collapse
>   this design exists to prevent — just slowly, where nobody is watching for it.
> - **The honest branch:** if T4 shows the refit constant wanders across windows and cadences, the
>   protocol's conclusion is **"this record does not support re-tuning at any cadence it can
>   measure"** — and the correct response is a *fixed* rule set plus forward paper collection, not a
>   refit schedule invented to fill the gap.

### 9.3 The null branch — the likely outcome, and a success

W3 should be read expecting a null. If nothing clears §9.1, the deliverable is:

- every feature and threshold searched, its J against the Filter-A baseline, and its null;
- the recomputed power table saying **what W3 could have detected and did not** — at ~200 fit-split
  trades, a session-level gate would have had to be very good indeed;
- the §6 period-outcome distribution and, in particular, the **session-level autocorrelation**: if it
  is indistinguishable from zero, the honest statement is "the hot-and-cold shape in this record is
  consistent with independent bursts, and this record cannot distinguish it from serial dependence";
- the §9.2 tuning protocol, which ships regardless.

What gets retired on a null: the assumption that standing aside on measurable market state is worth
building. What the next phase does instead: prior (1) is **not** refuted by a null here — burst-shaped
returns remain the expected shape; what is refuted is the idea that the bursts are *predictable from
this record's session-level state*. The next pass should fund the W1 × W3 interaction (protocol §14)
— selection fitted jointly with regime conditioning — which both W1 and W3 are structurally unable to
see, rather than more session-level features on the same ~200 trades.

**A documented null is a successful outcome of W3**, and given the power available it is the outcome
to expect.

---

## 10. What is out of scope for W3

| out of scope | whose it is |
|---|---|
| any selection rule, any setup-level feature, any change to Filter A | **W1** |
| entry mechanics, fill realism, exit interiors, `bars_1m`, anything below 5-minute resolution | **W2** |
| within-day timing rules (`trigger_et_min` is a **selection** feature, W1's S4 family) | **W1** |
| position-size modulation (capital adequacy is reported, never optimised — protocol §6) | nobody |
| `daily_universe` as a regime feature (recon-only; constraint 12) — §5.1 | nobody, this pass |
| the 167 hand reviews (quarantined — protocol §9.2) | Stage 0, once, redacted |
| the holdout, in any form | the custodian, once (protocol §12) |
| reading another workstream's plan, intermediates or candidate | nobody, before the holdout pass |

---

## 11. Execution shape and cost

W3 is the **lightest** workstream: it works on session-level aggregates over the Filter-A stream,
which is a few hundred rows, plus a ~511-row session-state table.

| piece | where | tier |
|---|---|---|
| W3-0a session-state build — needs the `opportunities` and `scanner_hits` counts per session across both stores | **box, via `box-data`** as a per-session **aggregation** (counts, not rows), or on the **Mac** directly. ⚠️ Never transfer the raw hit series: `scanner_hits` is 156 k rows live and far more on recon. Push the `GROUP BY` down. | `builder` |
| VIX series | `spikes/vix_regime.py`'s cache — a data loader, readable under protocol §1.1 | `builder` |
| T1–T5, the nulls (B = 200 × 18 points), the walk-forward, T4's refits | **cloud session** off the exported panel and the session-state table — both trivially portable | `spike-runner` / `builder` — measurement only |
| every interpretation step: which features proceed, whether the gate clears, the tuning protocol, the freeze report | separate session | `strategy-analyst` (opus) |

If any part touches the box: `scripts/box-job.sh`, **per date, one at a time**, never `docker exec`
into the app, never `--all`.

**Cost estimate: 4–5 sessions** — ~1 `builder` (Stage 0 and the session-state build), ~2
`spike-runner` (T1–T5 and the nulls), ~1–2 `strategy-analyst` (the gate verdict and the tuning
protocol, which is the piece that most needs judgement).

---

## 12. The starting prompt

> Copy from here down into a fresh session.

---

You are the **W3 agent** in a three-agent analysis of a 2-year, ~511-session record of a systematic
US small-cap momentum strategy. You are executing a plan, not designing one. Three agents are
running concurrently against one record; none of you can ask a question mid-flight, and the controls
below are what stop the three of you burning the only 2-year record this project will have for
another two years.

**Read these two documents in full, in this order, before anything else:**

1. `research/analysis-protocol.md` — the shared contract. Blind list, operator facts and priors, the
   thirteen hard constraints, the frozen panel and its Stage-0 items, the splits, the objective
   function, the four exit families, **Filter A**, the trials ledger, the custodian, the single
   holdout opening.
2. `research/analysis-plan-3-regime.md` — this plan. You execute §3 through §9.

Then read `research/data-inventory.md` for what the data is.

**Work blind.** The blind list is the table under `### Work blind — this is a hard requirement` in
`research/analysis-brief.md`. Read it there and obey it exactly. Do not read the incumbent rules, the
decision log, the pattern-grammar specs, `spikes/README.md` or any spike's findings, the shipped
portfolio or config code, the dashboard payloads, the prior-repo audits, or git/issue history — and
do not ask a subagent to summarise any of them for you, or accept such a summary if offered. The
carve-out is protocol §1.1: `spikes/engine_lab/` and `spikes/vix_regime.py` are readable **as
machinery**; `SHIPPED` / `baseline()` constants may be *executed* as a benchmark and may **not** be
read into a search — every threshold you run is a quantile of the fit-split distribution, never an
absolute. If you need a mechanical fact only a blinded file holds, take it from the inventory; if the
inventory is silent or wrong, **say so in the ledger** and work around it.

**Your question:** does the post-selection trade stream have exploitable time structure — can a
session-level, trigger-time-decidable state variable tell you to stand aside — and what re-fitting
cadence does this record actually support?

**Your population is the Filter A stream**, defined in protocol §9. It is a priori, frozen, and its
one data-dependent constant was fitted on the fit split alone and published as a literal. ⚠️ **You may
not tune Filter A** — not a threshold, not a column, not the capacity rule. Any change to it is a
stop-and-report. This is what blocks the leak the operator's third prior warns about: a filter fitted
on the whole record and then studied for regimes has already seen the regimes. You also may not use
W1's filter, and you do not read W1's intermediates at all.

**Your deliverables:** (a) the **tuning protocol** of §9.2 — what may be re-tuned, on what window, at
what cadence, with what guardrail, and the re-tune-versus-rescue distinction. This ships whatever else
happens, and it is the reason your workstream is funded. (b) The **period-outcome distribution** of
§6 — cold-stretch length and depth, top-decile share, session-level autocorrelation. (c) Exactly
**one** frozen timing overlay, or a documented null. **Expect the null**: at ~200 fit-split trades a
session-level gate has very little to work with, and a well-evidenced null is a successful outcome
here. Do not produce a candidate because one was expected.

**Your budget: 24 trials**, from a global budget of 120 shared with two other agents. A trial is one
(hypothesis, parameterisation) scored against any outcome column or post-trigger price. A k-point
grid is k trials. **Every refit is a trial** — including the four T4 refit-cadence combinations,
which is the point of T4. A permutation null is not a trial but must run at matched intensity, and
your shuffle is **block-by-session**: a global i.i.d. shuffle would destroy the burst structure you
are testing for and hand you a null you could beat by accident. A sensitivity band is not a trial and
may never be used to *select* a value. Feature builds, coverage checks, correlations and the census
are free. The allocation is in §4. **If you would exceed it, stop** — you do not borrow from another
workstream.

**Ledger before you open data.** The trials ledger is the GitHub issue named in your workstream
sub-issue. Post your Stage-1 pre-registration comment — hypotheses, thresholds as quantiles,
objective, success threshold, stopping rule, trial arithmetic — **before** you open the fit split.
Post a batch comment before each scoring batch with your running total, and every amendment as its
own comment before you act on it. You post and proceed; you do not wait for approval.

**The stage gates. Do not open with the analysis.**

- **Stage 0** — reconnaissance, costs no budget. Verify the panel hash; run W3-0a … W3-0d; consume
  the shared S0-F, **S0-H (which gives you the Filter-A baseline J you measure everything against)**
  and S0-J from the ledger. Do not start Stage 1 until the §3 exit gate holds. If §3's cancellation
  condition fires — fewer than three usable features, the W3-0d census below 40 %, or the recomputed
  minimum detectable effect above 0.6 R/trade — **W3 is cancelled**: write it up, return the trials,
  stop. That is a legitimate and budget-saving outcome, not a failure.
- **Stage 1** — pre-registration, ledgered, before the fit split is opened.
- **Stage 2** — execute T1–T5 on FIT, then the single CHECK score. Measurement and interpretation are
  **separate sessions**: `spike-runner`/`builder` measures, `strategy-analyst` interprets.
- **Stage 3** — freeze one overlay (or the null), plus the tuning protocol and the period-outcome
  distribution, post to the ledger, **stop**. The holdout pass is not yours.

**Your specific trap, stated so you cannot walk into it:** a stand-aside gate can raise J by removing
trades from a stream whose J is already negative. That is not timing skill; it is evidence the stream
is unprofitable. **Every comparison is against the ungated Filter-A baseline, never against zero.**
And sessions on which the gate stands aside contribute **0** to the objective — they are never
dropped from the denominator.

**Where you run.** Cloud sessions, off the exported panel and the ~511-row session-state table — both
trivially portable. Build the session-state series by pushing the `GROUP BY` **down** to the store via
the `box-data` skill or on the Mac; never transfer the raw `scanner_hits` series. ⚠️ **The box is a 2
vCPU / 4 GB CX23 and a heavy job takes it down hard**: `scripts/box-job.sh`, per date, one at a time,
never `docker exec` into the app, never `--all`. Verify `sha256sum panel-v1.parquet` against
`panel-v1-spec.md` on `main`; **never rebuild the panel**.

**Out of scope for you**, because they belong to the other two workstreams: any selection rule, any
setup-level feature, any change to Filter A, and within-day timing (`trigger_et_min` is a selection
feature) — all **W1**; entry mechanics, fill realism, exit interiors, `bars_1m` and anything below
5-minute resolution — all **W2**. Also out of scope: position-size modulation, `daily_universe` as a
regime feature (recon-only, so untestable on a holdout that is 47 % live), the 167 hand reviews
(quarantined), the holdout in any form, and any other workstream's plan, intermediates or candidate.

**Escalation.** Every Stage-0 item in §3 carries a pre-authorised fallback — take it and ledger the
amendment. Where none applies — a feature missing on one half, the panel hash mismatching, the
census below its floor, your budget about to be exceeded — **stop**. Write the finding to your
sub-issue and the ledger, say exactly what assumption failed and what you would need, and end the
session. Do not substitute a different feature, a different population, a different baseline or a
smaller B.

**Stopping rule.** You stop at the first of: your 24 trials spent; §9.1's seven conditions met on one
overlay; §3's cancellation condition firing; or any stop-and-report. **The holdout is not yours to
open**, in any form, for any reason.

**The one thing to get right.** The operator expects this strategy to run hot and cold, and expects
to re-tune it over time. Both beliefs are reasonable and neither is a licence: burst-shaped returns
make any single-window fit **less** informative than it looks, and "we re-tune as needed" means "we
search continuously and never count it" unless the refits are ledgered like any other trial. Your job
is to turn those two beliefs into a counted, bounded protocol — and, if the record cannot support
one, to say so with the evidence.

> Copy to here.
