# Analysis plan 4 — the joint pass: selection × regime on the R pool, then execution on the capture

**Status:** LIVE (2026-09-19). Workstream **W4**. It **supersedes the three-way split** —
[`analysis-plan-1-selection.md`](./analysis-plan-1-selection.md),
[`analysis-plan-2-execution.md`](./analysis-plan-2-execution.md) and
[`analysis-plan-3-regime.md`](./analysis-plan-3-regime.md) are marked SUPERSEDED as *designs*. Their
*results* stand, are quoted below, and are inputs to this plan.

> Read [`analysis-protocol.md`](./analysis-protocol.md) first, in full, **including §16**, which
> carries the five amendments this plan requires. Where this plan and the protocol appear to
> disagree, the protocol as amended wins and the disagreement is a stop-and-report.

The copy-paste starting prompt is [§13](#13-the-starting-prompt).

---

## 1. Why this exists — the defect in the three-way split

The previous pass ran three analyses against one record and got three nulls. The nulls are sound.
**The design that produced them was not**, and the reason is structural rather than statistical.

### 1.1 Two of the three workstreams studied a stream nobody would trade

W2 (execution) and W3 (regime) both held selection fixed at **Filter A** — the a-priori placeholder
declared in protocol §9. Measured at Stage 0, that stream is **gross-negative: −0.1450 R/session
before a cent of cost**, and it retains a setup on **266 of 266 FIT sessions at 1.000 trades per
session**.

So Filter A is not a filter. It is a tie-break rule that keeps essentially every session and then
picks the earliest setup in it. And the two questions asked of it were:

- *W2:* which execution harvests this stream? — asked of a stream with **nothing to harvest**;
- *W3:* when is this stream hot? — asked of a stream with **nothing to time**.

Both correctly returned nulls. Both nulls are close to uninformative about the question the operator
actually has. W3's own agent said so at freeze, and said it first:

> *"The Filter-A stream is **gross-negative** (−0.1450 R/session before costs), so neither timing nor
> execution can rescue it — conditioning redistributes an edge, it cannot manufacture one."*

### 1.2 Operator prior (3) was honoured in letter and broken in spirit

Prior (3) is *"do not look for hot and cold markets in the unfiltered dataset — filter first, then
study the time-variation of what survives the filter."* The protocol implemented it with Filter A,
chosen *a priori* so that no leak could enter through a fitted filter.

The leak block worked. **The filter did not.** A filter that keeps 100 % of sessions is, for the
purposes of prior (3), the unfiltered dataset with extra steps — which is precisely the population
the prior forbids studying for regimes. The correct reading of prior (3), which the three-way split
missed, is stronger than its implementation:

> **The filter a regime study conditions on must be the *real* filter. Which means selection and
> regime are one fit, not two — there is no order in which to do them sequentially that is honest.**

Fitting them jointly reintroduces the leak the a-priori filter was defending against. §6.3 says how
this plan blocks it instead: both are fitted on FIT only, and the permutation null is run at matched
intensity over the **entire joint search sequence**, which is the control a joint fit requires and an
a-priori filter is a poor substitute for.

### 1.3 Nothing in the design would ever have composed a system

Each workstream was to freeze one candidate; the custodian was to open the holdout once and score
all three, with a multiplicity correction across them. **There was no step, anywhere, that assembled
selection + regime + execution into the thing that would actually be traded.** Three candidates,
each valid only under the others' placeholder assumptions, scored side by side and never together.
That is the defect that renders the pass unusable as it stands, and §9 (Stage C) is the direct
answer to it.

### 1.4 The burst shape the operator believes in was inexpressible

Capacity was fixed a priori at **N = 1 earliest-by-time, not a search dimension**, and throughput had
to stay in **[0.6, 1.0]** trades/session. Together those cap a candidate at *standing aside on at
most 40 % of sessions* and *never taking more than one trade on a strong day*. The expressible
hot-to-cold dynamic range is **1.67 : 1**.

Operator prior (1) says this market pays in bursts. A 1.67 : 1 range cannot express that hypothesis,
so W3 was not merely underpowered to find regime structure — it was **constrained out of the region
where the operator says the structure lives**. W3 hit the wall from its own side and reported it:
the throughput band permitted a stand-aside fraction of at most 40 %, which is why its grid ran
10/20/30 % instead of 30/50/70 %.

Amendment **P-2** relaxes this. Capacity stays at **N = 1** — concentration without amplification,
matching the book — and the throughput band widens to **[0.45, 1.0]** with the power cost stated in
§7.2 rather than hidden.

### 1.5 What was *not* wrong, and must not be wished away

⚠️ **W1 did search selection on the unfiltered panel**, crossed with all four exit families, 42
scored points. The result is the load-bearing fact in this whole document and the executing agent
must hold it in view:

| | |
|---|---|
| points scored on FIT | 42 |
| points with net J > 0 | **0** |
| points with **gross** R/session > 0 | **0** |
| best point (`stop_pct ≥ q70`, F2) | **J = −0.203** net, **−0.096 gross** |
| whole-sequence permutation null | **p = 0.378** |

**The pooled marginal edge is not there, and it is not a cost problem.** It loses before costs are
charged. A joint search can only win if the edge lives in a *cell* — a (setup subset × market state)
combination — that pooling averages away. That is a harder claim than "the analysis was done wrong",
and this plan is designed to test it and to report a null honestly if it is false.

**The prior odds are not favourable. Design accordingly, and do not design an analysis that can only
succeed.**

### 1.6 The design knew, and deferred it for budget

⚠️ This is not hindsight. Protocol **§14** — *"what none of the three covers"* — names the gap
exactly, and pre-authorises the remedy:

> *"**The W1 × W3 interaction** — a selection rule fitted **jointly** with regime conditioning.
> Deliberately unfunded. The joint space multiplies the trials count past what §8.2 allows […]
> ⚠️ **The cost is real and specific: a selection rule that works only in some regimes will be missed
> by both W1 (which pools across regimes) and W3 (which holds selection fixed).** If W1 and W3 both
> return nulls, this interaction is the first thing the next pass should fund."*

**All three returned nulls.** The condition the protocol set for funding the interaction has been
met, in the protocol's own words, and W4 is that funding. What the protocol did not anticipate is
that the joint fit also needs a **different objective** (§3) and a **much tighter selection grid**
(§6.1) — those two are this plan's own contribution, and without them a joint search would re-run
W1's degenerate grid in two dimensions instead of one.

---

## 2. The question W4 owns

> **Is there a (selection rule × market-state gate) pair, both decidable at trigger time, whose
> stream carries a harvestable R pool large enough that some execution policy takes home positive
> net R per session at the operator's throughput — and does the whole composed system survive a
> permutation null run at the intensity of the search that found it?**

One question, two stages, in the order the operator named:

1. **Stage A — the pool.** Find the (selection, state) pair that maximises the **ceiling**: the R
   that the tape made available to a stream we could actually have taken. Execution-agnostic.
2. **Stage B — the capture.** Find the execution policy that takes home the largest **net** share of
   that ceiling. Pool-aware.
3. **Stage C — the composition.** Verify the two stages compose, i.e. that Stage B's winner does not
   reorder Stage A's ranking. This is the step the three-way split never had.

---

## 3. The objective function

### 3.1 Stage A — the ceiling, `C`

For a candidate pair `(S, G)` — selection predicate `S` over `RULE_COLUMNS`, session-state gate `G`
decidable before the session's first trigger — on a block with session universe `Σ`:

- **Permitted sessions**: those `G` allows. A session `G` stands aside on takes no trade and
  contributes **0** — never dropped from `|Σ|`.
- **The taken trade**: in each permitted session, the **earliest** setup by `trigger_et_min` passing
  `S`. Capacity `N = 1`. No ranking, ever (constraint 1).
- **Per-trade ceiling**: `pool_i = max(max_r_i, −1) − c_i`

  where `max_r_i` is the panel's `max_r` and `c_i` is the per-trade cost in R from S0-F's identity at
  $500 full buying power.

- **The objective**: `C(S, G) = ( Σ_i pool_i ) / |Σ|` — **mean ceiling net R per session.**

`max(max_r, −1)` is the realised R of an oracle exit: an exit that could not do better than the
excursion the tape offered, and could not do worse than the stop. Subtracting `c_i` keeps the
operator's constraint 6 in force — **a ceiling that ignores costs is not a ceiling, it is a
brochure.**

### 3.2 ⚠️ `max_r` is stop-truncated in this panel, and the repo contains a trap

This is the single most important mechanical fact in the plan and the executing agent must verify it
at Stage 0 before anything else.

`replay_bracket(target_r=None)` in `spikes/engine_lab/common.py` **walks the path with the stop
armed** and halts at the stop, returning the maximum favourable excursion *up to that point*. A
same-bar stop credits **zero** excursion. So the panel's `max_r` is a **reachable** quantity: some
exit policy could, in principle, have taken it.

**The trap:** W2's E1 reports a different quantity under a confusingly similar name —
`R_max = (max path high after entry − entry) / (entry − stop)`, the raw path maximum with **no stop
applied**. Its FIT mean is 5.72 R and its median 3.01 R on the Filter-A stream. Those numbers are
**not harvestable** — much of that excursion occurs after the stop was hit — and a Stage A that
optimised them would be optimising a fantasy that Stage B could never cash.

> **Stage A scores the panel's stop-truncated `max_r`. It never scores W2's `R_max`. A harness that
> computes the raw path maximum anywhere in Stage A is a stop-and-report.**

The Stage-0 check that enforces it: `stage0.py`'s integrity item already reproduces every panel row's
`max_r` from `replay_bracket(target=None)` with **0 mismatches** over 9,195 rows. Re-run it, and
confirm the reproduction is the stop-armed walk.

### 3.3 Why `C` and not `J`, and what capacity normalisation it needs

The three-way split optimised `J` = mean net R/session **under a chosen exit family**. That
confounds "these are good setups" with "this bracket happens to fit these setups", and it cost the
previous pass real coverage: W1's family gate ran under **F2 only**, so a selection axis producing
5R runners that first wobble to the stop books −1R under a 2R bracket and is **eliminated at the
first gate**, before any exit family ever sees it.

`C` asks a different question of the same rows — *where does the available excursion live* — and it
is the better instrument for three reasons:

1. **It decouples.** A selection rule is scored by the opportunity it delivers, not by an arbitrary
   bracket's fit to it.
2. **It carries more information per trade.** The bracketed outcome is near-binary (−1 or +2);
   `max_r` is continuous and keeps the path information the bracket throws away.
3. **It is the operator's own decomposition** — find where the moves are, then decide how to take
   them — and it honours prior (4)'s "selection × exit is a joint space" more faithfully than
   crossing two small grids does.

**Capacity normalisation, which the naive form lacks.** "Sum of max R" is monotone in trade count:
the rule that maximises it is *take everything*. `C` is normalised per **session** with capacity
pinned at `N = 1`, so the count is fixed by the throughput band and cannot be traded for pool size.
A candidate outside the band (§7.2) is **disqualified, not penalised**.

### 3.4 Stage B — the capture ratio, `κ`

For an execution policy `E` on the frozen Stage-A stream:

- `J(S, G, E)` = **mean net R per session** — protocol §6, unchanged, at $500 full BP with the pinned
  cost model and 2-tick slippage.
- `κ = J / C` — **the capture ratio**: the share of the available ceiling the policy takes home.

**A candidate freezes on `J > 0`. Never on `C`, and never on `κ`.** `C` is a screen and a
denominator; `κ` is a diagnostic that says *whether the remaining deficit is a selection problem or a
capture problem* — which is exactly the question the three-way split could not answer, because it
never measured the denominator.

### 3.5 The admissibility screen that makes the whole design cheap

> **If `C(S, G) ≤ 0`, no execution policy can produce a profitable stream, because `J ≤ C` by
> construction. The cell is dead. Stage B never runs on it.**

W2 spent **32 trials** discovering by exhaustion — 24 scored points, all negative gross — something
one `C` computation on the same stream would have shown for one. That screen is the cheapest
diagnostic in this design and it runs at Stage 0 (**S4-0h**) before any search opens.

### 3.6 What is deliberately *not* in the objective

| rejected | why |
|---|---|
| raw sum of `max_r`, unnormalised | monotone in trade count; maximised by taking everything (§3.3) |
| W2's un-stopped `R_max` | not harvestable (§3.2) |
| the within-session-best ceiling ("oracle selection") | ⚠️ **forbidden.** Choosing the best of a session's ~17 setups requires ranking them, which constraint 1 forbids and constraint 2 makes unreportable. See §6.2 — this is where the permitted and forbidden kinds of lookahead part company, and the line is not negotiable. |
| Sharpe or any return/volatility ratio | penalises the burst shape prior (1) declares expected (protocol §6.1) |
| `κ` as the freeze criterion | a stream with `C = 0.02` and `κ = 0.9` is a rounding error with a good ratio |

---

## 4. What carries over unchanged

W4 is a re-plan, not a restart. **Nothing about the data, the splits or the money changes**, because
changing them after the record has been looked at is how a re-plan becomes a rescue.

| carried unchanged | where |
|---|---|
| **`panel-v1`, frozen, verified by sha256** — 9,195 rows, never rebuilt | protocol §4, `panel-v1-spec.md` |
| **The splits** — FIT 266 sessions / CHECK 125 / HOLDOUT sealed | protocol §7.1 |
| **The cost model** — S0-F's identity, $500 full BP, 2-tick slippage | protocol §11 |
| **The four exit families** F1–F4 | protocol §10 |
| **No-lookahead enforcement** — `RULE_COLUMNS` subsetting, `assert_no_lookahead()`, earliest-by-time capacity | protocol §5.1 |
| **The trials ledger**, the custodian, the single holdout opening | protocol §8, §12 |
| **Measurement and interpretation in separate sessions** | protocol §13.4 |
| **The blind list** — the incumbent's rules, decisions, spikes and dashboard stay unread | protocol §1 |
| **Constraint 12** — no live-only column (`float_shares`, `news`, `first_rank`) | protocol §5 |

⚠️ **The splits are not re-drawn.** FIT has now been searched at 98 trials. Re-splitting to buy fresh
fitting data after seeing that result is the classic sin, and the cost of not re-splitting is paid
instead in §7.3's tighter gate.

### 4.1 What W4 *may* read that the three agents could not

Constraints 8–11 existed because three agents ran concurrently. **That pass is closed and all three
are frozen**, so W4 reads all of it — W1's, W2's and W3's plans, ledger entries and results. They are
this pass's own output, not the incumbent's record, so the blind list is untouched.

Four findings are carried as **inputs**, saving W4 the trials of re-deriving them:

1. **The account is not the binding constraint** (W2). `c ≈ 2·commission/(BP·stop_pct) + 2·slip_pct/stop_pct`;
   only the first term sees the account and it is exhausted by ~$2,500. Fifty times the account buys
   0.8 pts of F1 break-even. **W4 does not re-open the capital question.**
2. **F4 is a worse F2, not a hybrid** (W2 E4): its interior improves monotonically toward F2. W4
   therefore funds **F3's** interior and not F4's, and F4 is carried only as a single B1 point.
3. **Wide `stop_pct` is worth +0.107 to +0.129 R/session, and ~¾ of it is cost, not edge** (W2 E5).
   It enters as a Stage-A **selection axis** (A-S4), charged as a hypothesis — not adopted as a fact.
4. **Session-level serial dependence was refuted on the Filter-A stream** (W3): Ljung–Box Q(20) =
   12.92 against a 31.41 critical value, runs test z = 0.215. ⚠️ That is a fact about *that* stream.
   It must be **re-measured on W4's own stream**, because a selected stream can have structure a
   near-unfiltered one does not — but it lowers the prior on axis A-R4 and the executing agent should
   expect it to.

### 4.2 What is retired

- **Filter A as an analysis population.** It survives only as a published reference baseline, so W4's
  numbers stay commensurable with W1/W2/W3's. Nothing is fitted on it and nothing is conditioned on
  it. (Amendment **P-4**.)
- **The three frozen candidates.** All three workstreams froze documented nulls. There is nothing to
  carry to the holdout from the previous pass, and W4 inherits an empty candidate slate.
- **The independence machinery** (constraints 8–11's cross-reading ban). One agent, one search.

---

## 5. Stage 0 — reconnaissance, and the fork it resolves

The shared items **S0-A … S0-J** are already run and published (`panel-v1-spec.md`); they are **not**
repeated. W4 adds six free items and **eight charged trials**. Free means it reads no outcome column
and no post-trigger price (protocol §5.2).

### 5.1 Free items

| id | item | decision rule |
|---|---|---|
| **W4-0a** | `sha256sum -c panel-v1.sha256` — 8 / 8 — before anything else. | Mismatch → **stop-and-report**. Never rebuild the panel. |
| **W4-0b** | Confirm §3.2: re-run the `max_r` reproduction check and read `replay_bracket` to confirm the walk is **stop-armed**. | A raw-path-max reproduction → **stop-and-report**. The whole of Stage A rests on this. |
| **W4-0c** | Build the six session-state series (§6.3) over all 511 sessions from **panel-v1 columns only**, and verify each is populated on **both** halves (constraint 12). | A feature not populated on both halves is **dropped and its trials returned** (W3-A1's precedent). |
| **W4-0d** | **Throughput calibration of the whole grid.** For every selection level and every stand-aside fraction, and every pair of them, record realised trades/session on FIT. | Grid points outside **[0.45, 1.0]** are **dropped before Stage 1** and their trials returned. This is the cheapest way to shrink a search: a disqualified point costs nothing if it is never scored. |
| **W4-0e** | Feature–feature correlation among the five selection columns and among the six state series, on FIT. | \|ρ\| > 0.9 → keep the better-covered one, drop the other, ledger it. |
| **W4-0f** | Per-split feature-distribution drift, FIT vs CHECK vs HOLDOUT-features. | Material drift is **flagged in the freeze report**, never used to drop an axis. Drift is a fact about the record; hiding it makes the holdout unreadable. |

### 5.2 The eight charged trials — and the fork they resolve

| id | trial(s) | what it establishes |
|---|---|---|
| **S4-0g** | **1** | The **stop-truncated `max_r` distribution** over all 4,516 FIT panel rows: mean, median, deciles, the share with `max_r ≤ 0`, and the **top-decile share of total pool R**. |
| **S4-0h** | **1** | **`C_raw`** — the ceiling of the **unfiltered** `N = 1` earliest-by-time FIT stream. |
| **S4-0i** | **1** | **`C_A`** — the ceiling of the **Filter-A** stream, so W4 stays commensurable with W1/W2/W3. |
| **S4-0j** | **4** | **`κ` of each of F1–F4** on the unfiltered `N = 1` FIT stream — how much of `C_raw` each family actually takes home. |
| **S4-0k** | **1** | **`sd(pool_i)`** per trade, which sets the MDE table for every Stage-A comparison (§7.2). |

> **These eight trials are the highest-value spend in the plan, and they resolve a fork the previous
> pass never even framed:**
>
> | `C_raw` | `κ` of the best family | what the deficit *is* | what W4 does |
> |---|---|---|---|
> | **≤ 0** | any | **selection.** The tape did not offer a profitable pool to an unselective stream at any exit. | Stage A is the whole game. Proceed as written. |
> | **> 0** | **low** | **capture.** The pool is there and the exits are leaving it on the table. | Amend the budget toward Stage B before Stage 1, and ledger the amendment. |
> | **> 0** | **high** | neither — the unfiltered stream is already viable, which would contradict W1's 42 negative-gross points. | **Stop-and-report.** Two measurements disagree and one of them is wrong. |

### 5.3 The Stage-0 exit gate and the cancellation condition

**Exit gate:** W4-0a–0f complete and ledgered; the eight charged trials posted; the §7.2 power table
recomputed from `sd(pool_i)`; **≥ 4 selection axes and ≥ 4 state axes surviving W4-0c/0e**; the
throughput-calibrated grid containing **≥ 24 scoreable joint points**.

**Cancellation — G1.** If `C_raw ≤ 0` **and** the top-decile share of total pool R from S4-0g is
**below 25 %** (against 10 % under uniformity), then the available R is both negative in aggregate
and *diffuse* — there is no concentrated pool for a selection rule to find. Stage A is cut to **A1
only**, the remaining trials return to the global budget, and **W4 closes as a documented null at 23
trials.** That is a successful outcome, and it costs 23 of 120 rather than 120 of 120.

---

## 6. The hypothesis class

### 6.1 ⚠️ The correction that matters most: W1's grid was throughput-degenerate

The panel carries **4,516 FIT rows over 266 sessions ≈ 17 setups per session**, and the book takes
**one**. A selection filter therefore does not reduce the trade count until it is tight enough that
whole sessions run out of qualifying setups. The arithmetic, at ~17 setups/session:

| share of rows kept | ≈ P(session has no qualifying setup) | ≈ realised trades/session |
|---|---|---|
| 70 % (`q30`) | ~0 | 1.00 |
| 50 % (`q50`) | ~0 | 1.00 |
| **30 % (`q70`)** | **~0.003** | **1.00** |
| 20 % (`q80`) | 0.02 | 0.98 |
| **10 % (`q90`)** | **0.17** | **0.83** |
| **5 % (`q95`)** | **0.42** | **0.58** |
| 3.5 % | 0.55 | 0.45 |

**W1's entire grid — `q30` / `q50` / `q70` — sits in the degenerate region.** Its tightest level took
**261 trades on 266 sessions**: the filter removed *five sessions*. W1 therefore never tested a
selective strategy at all. It only varied **which** setup got taken on essentially every session, and
then reported that no such variation was profitable — which is a much narrower finding than it reads
as.

> **W4's selection grid is `q80` / `q90` / `q95` (top 20 % / 10 % / 5 % of rows).** Anything looser is
> throughput-degenerate and is not scored. The levels are FIT quantiles per protocol §5.3, never
> absolutes, and **W4-0d re-calibrates them free before Stage 1** because the setups in a session are
> not independent and the realised throughput will not match the table above exactly.

### 6.2 ⚠️ Where lookahead is permitted and where it is not

This plan reads `max_r`, which is an outcome column, to *score* candidates. That is charged as
trials and is ordinary. What is new is that `C` is an **exit-path oracle**, so the line has to be
drawn explicitly — amendment **P-1** draws it, and it is the one place in this design where a careless
step would invalidate everything:

| | permitted? | |
|---|---|---|
| **Selection lookahead** — choosing *which* setup to take using anything not known at trigger time, including ranking a session's setups against each other | ❌ **never** | constraints 1 and 2. Enforced by `RULE_COLUMNS` subsetting and earliest-by-time capacity, mechanically, not by convention. |
| **Exit lookahead** — `C`, the oracle-exit ceiling on a stream that *was* selected trigger-time-safely | ✅ **as a screen and a denominator only** | Every opportunity in `C` could have been traded. What is oracular is the exit timing, not the choice. |
| **A `C` reported as a result** | ❌ **never** | `C` is published only with `J` and `κ` beside it, and never appears in a report, a dashboard or an issue without the word **ceiling** and the realised number. A candidate freezes on `J`. |
| **The "oracle selection" ceiling** — the best of a session's 17 setups | ❌ **never** | It requires ranking. It is the single most tempting number in this design and it is forbidden outright (§3.6). |

### 6.3 The axes, with directions declared a priori

**Five selection axes**, each one column from `RULE_COLUMNS`, direction fixed now and **not searched
both ways**:

| id | column | direction | rationale, declared before data |
|---|---|---|---|
| **A-S1** | `hits_before_trigger` | ≥ | more scanner attention accumulated before the break |
| **A-S2** | `cum_dollar_vol_pre_trigger` | ≥ | more participation before the break |
| **A-S3** | `retracement` | ≤ | shallower consolidation against the pole — the grammar's core claim |
| **A-S4** | `stop_pct` | ≥ | wider % stop, lower cost floor — W2 E5's lead, charged not adopted (§4.1) |
| **A-S5** | `ext_at_trigger` | ≤ | **new coverage.** A setup already extended from its base has less room above it. Named trigger-safe in constraint 1 and **never tested by W1**. |

**Six state axes**, session-level, decidable before the session's first trigger, built from panel-v1
columns so a candidate is scoreable from the panel alone:

| id | series | stand aside when | note |
|---|---|---|---|
| **A-R1** | prior-session distinct triggered symbols, 5-session mean | **below** the cut | W3's R1; ρ = 0.988 against the raw spine |
| **A-R2** | prior-session mean `hits_before_trigger`, 5-session mean | **below** the cut | ⚠️ W3-A1's substitute. It tracks the plan's original all-day attention feature at only **ρ = 0.385**, and W3's headline rested on it. Carried, with that stated. |
| **A-R3** | prior VIX close and its 5-day change | **above** the cut | the only external regime input wired |
| **A-R4** | trailing 20-trade net R **of the candidate's own stream** | **below** the cut | the direct hot/cold test. Reads only closed prior trades → not lookahead. ⚠️ **Self-referential: it must be rebuilt inside every null replicate** (W3's construction). |
| **A-R5** | A-R1 **reversed** | **above** the cut | ⚠️ paid for, not flipped. W3 found R1 and R3 raised `J` while making every retained trade *worse* — a hint the declared direction was wrong. Flipping silently after reading that is selection-of-maximum bias; **charging the reverse as its own hypothesis is the honest price.** |
| **A-R6** | A-R3 **reversed** | **below** the cut | as A-R5 |

Stand-aside fractions: **{10 %, 20 %, 30 %}**, as FIT quantiles from the declared tail, subject to
W4-0d's throughput screen — a tight selection level and a deep stand-aside together can starve the
book below 0.45, and those pairs are dropped before Stage 1.

### 6.4 The leak block, since selection and state are now fitted together

Prior (3)'s a-priori filter was a workaround for a sequential design. A joint fit needs a different
mechanism, and it is this one:

1. **Both are fitted on FIT only.** CHECK is opened once, for one composed candidate. HOLDOUT is the
   custodian's, once.
2. **The permutation null runs at matched intensity over the entire joint sequence** — A1 → A5,
   re-run in full on every replicate, with A-R4 rebuilt inside each. A joint search's control is a
   joint null; an a-priori filter is a poor substitute for one and was never the real defence.
3. **α is tightened to 0.01** (amendment P-3) because 98 trials were already spent against this same
   fit split.
4. **No candidate ever reads a HOLDOUT outcome** — they are physically absent from the panel
   (protocol §4.3), so this is a property of the bytes, not a promise.

---

## 7. The budget, the gates, and the power

### 7.1 120 trials — an authorisation, not a spending plan

| stage | what | trials |
|---|---|---|
| **S4-0** | the eight charged Stage-0 measurements (§5.2) | **8** |
| **A1** | selection marginals on `C`: 5 axes × 3 levels | **15** |
| **A2** | state marginals on `C`, on A1's best selection: 6 axes × 3 fractions | **18** |
| **A3** | **the joint cells**: 2 selection survivors × 3 levels × 2 state survivors × 3 fractions | **36** |
| **A4** | selection conjunction (A ∧ B) under the winning state gate × 3 levels | **3** |
| **A5** | stand-aside depth sweep on the winning cell, 3 further fractions | **3** |
| **Stage A** | | **75** |
| **B1** | F1–F4 on the frozen Stage-A stream | **4** |
| **B2** | F3's interior: 3 arming × 2 trailing reference (F4's interior is **not** funded — §4.1) | **6** |
| **B3** | 3 entry mechanics (M1/M2/M3) × the 2 best families | **6** |
| **B4** | **pool-derived targets**: fixed target at the q40/q50/q60/q70 of the *stream's own* FIT `max_r` distribution | **4** |
| **B5** | walk-forward refit of the **composed** system, 3 expanding folds (protocol §7.2) | **3** |
| **B6** | CHECK, the single composed candidate, scored **once** | **1** |
| **Stage B** | | **24** |
| **C1** | Stage-A's 2nd- and 3rd-best cells re-scored under Stage-B's winning execution | **2** |
| **C2** | Stage-B's top-2 policies re-scored on Stage-A's runner-up state gate | **2** |
| **C3** | the composed candidate at selection level ±1 grid step | **2** |
| **C4** | composition amendment reserve | **1** |
| **Stage C** | | **7** |
| **reserve** | | **6** |
| **total** | | **120** |

The global ledger goes **120 → 240** (amendment P-3). The 98 already spent are declared as **prior
search intensity**, not forgiven.

### 7.2 The four gates — why the expected spend is far below 120

⚠️ **This is the answer to the obvious objection that 120 new trials on a 266-session fit split is a
sweep with a covering letter.** It is not, because four mechanical gates stand between the cheap
stages and the expensive ones, and each returns its unspent trials to the global budget.

| gate | fires after | condition to **stop** | spend if it fires |
|---|---|---|---|
| **G1** | Stage 0 | `C_raw ≤ 0` **and** top-decile pool share < 25 % | **23** |
| **G2** | A1 | no selection axis at its tightest in-band level lifts per-trade `C` by **≥ 1 se** over the unfiltered `N = 1` stream → **A3's 36 trials are not funded** | **41** |
| **G3** | Stage A | best joint cell has **`C ≤ 0`** → §3.5: no exit can rescue it, **Stage B never runs** | **~75** |
| **G4** | Stage B | best composed system has `J ≤ 0` on FIT **or** `p > 0.01` → Stage C does not run and **CHECK is never opened** | **~99** |

On W1's evidence, **G2 or G3 firing is the modal outcome**, and the modal spend is 41–75.

### 7.3 Power — stated, not assumed

At sd ≈ 1.4 R/trade (S0-H's measured dispersion) and α = 0.01 two-sided at 80 % power, the minimum
detectable effect on **net R per trade** is:

| trades/session | FIT trades | MDE |
|---|---|---|
| 1.00 | 266 | 0.294 R |
| 0.83 (`q90`) | 221 | 0.322 R |
| 0.58 (`q95`) | 154 | 0.386 R |
| 0.45 (floor) | 120 | **0.437 R** |

The gap from W1's best point (−0.207 R/trade) to break-even is **~0.21 R** — **below the MDE at every
throughput.** Stated plainly:

> **This record cannot resolve a marginal improvement. It can only resolve a large cell effect
> (≳ 0.33 R/trade), and a null here means "no large effect", never "no effect".** That is the honest
> reach of 266 sessions and it is why the answer to a disappointing result is forward paper data, not
> a wider search.

The MDE on **`C`** is different and is unknown until **S4-0k** measures `sd(pool_i)`. The table above
is recomputed from it at the Stage-0 exit gate. ⚠️ **Cancellation:** if the recomputed MDE on `C`
exceeds the `C`-difference between the unfiltered stream and zero, Stage A cannot detect what it is
looking for and W4 stops at Stage 0 with a documented null.

---

## 8. Validation

**Three nulls, one primary.** B = 200, seed published with the harness, the **entire** A1 → A5
sequence re-run on every replicate with A-R4 rebuilt inside each.

| id | construction | what it controls |
|---|---|---|
| **N-A** | within each session, permute which setup received which **price path** — the donor's exit legs move, the recipient keeps its **own** entry, stop, size and cost (W1's construction, so S4's cost lever cannot masquerade as signal) | the **selection** search |
| **N-B** | circular block permutation of the per-session outcome series against a fixed session-state series, block length 20 sessions (W3's construction, which preserves the burst structure prior (1) declares real) | the **state** search |
| **N-AB** | both, composed | **the joint search — this is the primary, and the only one a freeze may cite** |

- `p` = share of replicates whose **best-of-sequence** statistic ≥ the real best-of-sequence.
- **Threshold: `p ≤ 0.01`** (amendment P-3). Bonferroni cross-check at 0.05/120 reported beside it.
- **Walk-forward** (B5): protocol §7.2's three expanding folds, refitting the **composed** system —
  selection level, state cut and execution parameters together. Stability = every refit landing
  within **one grid step**. Every refit is one trial.
- **Sensitivity** (§7.4): ±20 % on every frozen literal, one at a time, on FIT. Free, reported, and
  **may never select a value**.
- **Both halves separately** (constraint 7) wherever a block contains both. FIT and CHECK are
  recon-only, so this binds at the holdout pass and is the custodian's.

---

## 9. Stage C — the composition check, which the three-way split had no place for

This is the direct answer to §1.3, and it is cheap.

A two-stage optimisation is only valid if the stages **compose** — if Stage B's winning execution
does not reorder Stage A's ranking of cells. They can fail to compose: a cell whose pool is shaped as
a few large runners loses its ranking under a 0.5 R scalp, and a cell of many small excursions gains
it. If the ordering flips, the composed system is **not** the optimum of either stage and the plan
must say so rather than ship the illusion.

| | test | pass condition |
|---|---|---|
| **C1** | Stage-A's 2nd- and 3rd-best cells re-scored under Stage-B's winner | the Stage-A ranking **holds** under the realised execution |
| **C2** | Stage-B's top-2 policies re-scored on Stage-A's runner-up state gate | the Stage-B ranking **holds** under a neighbouring stream |
| **C3** | the composed candidate at selection level ±1 grid step | `J > 0` throughout |

⚠️ **If C1 or C2 flips the ordering, the freeze is blocked** and the finding — *that selection and
execution do not separate on this record* — is written up as the pass's headline result. It would be
a more valuable finding than a marginal candidate, and it is one the previous design was structurally
incapable of producing.

---

## 10. What "done" looks like

### 10.1 The freeze conditions — all nine, no partial pass

One composed candidate `(S, G, E)` freezes **only if every one of these holds**:

1. **`C > 0`** on FIT — the ceiling exists (§3.5).
2. **`J > 0`** on FIT — net, at $500, with the pinned cost model.
3. **Throughput ∈ [0.45, 1.0]** on FIT. ⚠️ Below **0.6** the candidate ships **flagged as a
   throughput departure** the operator must accept explicitly — it is below the declared 0.8/day.
4. **`p ≤ 0.01`** on **N-AB** at matched intensity over the whole sequence.
5. **Walk-forward stable** — every refit within one grid step, all three folds (B5).
6. **`J > 0` throughout the ±20 % sensitivity band** on every frozen literal.
7. **`J > 0` on CHECK**, same sign, scored **once** (B6).
8. **Stage C composes** — C1 and C2 do not flip the ordering (§9).
9. **Net, not gross**, with `C`, `J` and `κ` published together and the gross/net gap per `stop_pct`
   decile.

Anything less is a **documented null**. There is no "promising, carry it anyway".

### 10.2 The null branch — written now, so it is not written in disappointment

If W4 freezes no candidate, the deliverable is a research doc containing, at minimum:

- **The `C` / `κ` decomposition** — the number the previous pass could not produce. *Is the deficit
  selection (the pool is not there) or capture (the pool is there and the exits miss it)?* This
  answers the most important open question in the project regardless of which branch fires.
- **The per-cell `C` table** across the joint grid, with the nulls, so the next pass knows which
  region of the space is exhausted rather than merely unvisited.
- **The power statement** (§7.3) restated against what was measured: what size of effect this record
  *could* have found, so the null's reach is not overstated.
- **What is retired**, explicitly.
- **What would change the answer** — and on the evidence in hand this is likely to be *forward paper
  data and more hand annotations*, not a wider search. 511 sessions is the constraint; W3's
  recommendation of **300–500 hand annotations across the recon window** costs chart time and no
  vendor spend and attacks selection, where this record says the deficit lives.

**And the phase-2 consequence, stated in advance:** if the null holds, the next phase is not a
re-search of this record. It is either (a) paper trading the incumbent to generate forward data with
real fills — which is also the only thing that retires the 2-tick slippage assumption — or (b)
revisiting the **detector**, which is the one input no analysis of this panel can question (§11).

---

## 11. What W4 still does not cover

Named here so it is not discovered later as a surprise.

| blind spot | why it is out of scope | what would cover it |
|---|---|---|
| **The detector itself** | Everything here is conditioned on what `detect_day` flags. If the edge is in setups the detector never surfaces, no analysis of this panel finds it. | `daily_universe` — the negative-sample universe of names that ran but never set up. Unused by all four workstreams. **This is the largest blind spot in the project.** |
| **Post-open behaviour** | The panel is pre-market triggers only. | a different panel |
| **`news`, `float_shares`, `first_rank`** | constraint 12 — fittable on 58 sessions, validatable on none, and those 58 are the holdout | a longer live record |
| **The 167 hand reviews** | quarantined; the live window is inside the holdout | 300–500 new annotations on the **recon** window (§10.2) |
| **Slippage** | assumed at 2 ticks; Phase 1 places no orders, so there are no real fills | **paper trading only.** No analysis retires this. |
| **The reverse direction of the five selection axes** | directions are declared a priori and not searched both ways; only the two *state* reversals are funded (A-R5, A-R6) | a new pre-registration, never a re-read of these tables |

---

## 12. Execution shape and cost

| stage | where | agent tier |
|---|---|---|
| W4-0a…0f, S4-0g…0k | **cloud session** off the published `panel-v1/publish/` artefacts (W2's amendment A4 established this works), or the Mac | `builder` |
| Stage A, Stage B, Stage C measurement | cloud or Mac off the frozen panel — **never a `--all` job on the box** | `spike-runner` / `builder` |
| M2 / M3 entry mechanics (B3) | needs the 1-minute tapes; **recon-only and FIT+CHECK-bounded** by amendment A4 | `spike-runner` |
| Every interpretation step | a **separate session** from the measurement that produced it (protocol §13.4) | `strategy-analyst` |
| The holdout pass | **the custodian, once.** Not W4's, in any form. | per protocol §12 |

⚠️ **Measurement and interpretation never share a session.** The previous pass held this and it is
the reason its nulls are trustworthy.

**Cost estimate:** 6–8 sessions — 1 Stage 0, 2–3 Stage A (measure / interpret / amend), 1–2 Stage B,
1 Stage C, 1 write-up. On the modal path (G2 or G3 firing) it is **3–4**. Harness reuse is high:
`stage0.py`, `w1.py` and `w2.py` already provide `run_rule`, `build_book`, `price_trade`,
`replay_bracket`, `folds_v1` and both null constructions. **`C` is the only genuinely new primitive.**

---

## 13. The starting prompt

> Copy from here down.

---

You are executing **workstream W4** of a systematic analysis of a 2-year small-cap momentum record.
You are one agent running one search. Your deliverable is **one frozen composed candidate or a
documented null** — both are successful outcomes and must be written with equal care.

**Read first, in this order, in full:** `research/analysis-protocol.md` (including **§16**, the
amendments), then `research/analysis-plan-4-joint.md` (this plan). Where they disagree, the protocol
as amended wins and the disagreement is a **stop-and-report**.

### Work blind — inherited verbatim

Do not read, and do not ask another agent to summarise: `research/strategy.md`,
`research/decisions.md`, `research/bull-flag.md`, `research/engine-v2.md`, `spikes/README.md` and
everything under `spikes/` **except** `spikes/analysis_v1/` (your own harness lineage),
`src/small_cap_stack/portfolio/`, `src/small_cap_stack/config.py`, `docs/`,
`research/tradepilot.md`, `research/entresys_light.md`, and the git/issue history of the incumbent's
rules. If you need a mechanical fact only one of those holds, take it from
`research/data-inventory.md` or `research/panel-v1-spec.md`, and if both are silent, **say so in the
write-up and design around the uncertainty**.

⚠️ **You *may* read W1's, W2's and W3's plans, ledger entries and results** (issues #735, #737, #738,
#739; `research/analysis-w2-result.md`). That pass is closed and frozen; it is this analysis's own
output, not the incumbent's record. Plan §4.1 lists the four findings you inherit so you do not
re-derive them.

### The job, in one paragraph

Find the **(selection rule × market-state gate)** pair whose stream carries the largest harvestable
**R pool** — `C`, the oracle-exit ceiling net of costs, defined in plan §3.1 — then find the
**execution policy** that takes home the largest net share of it, then **verify the two compose**.
Selection and state are fitted **together**, because the previous pass fitted them apart and got
three results that could never be assembled into a system (plan §1).

### Non-negotiables

1. **`max_r` is stop-truncated. Verify it at W4-0b before anything else.** W2's `R_max` is the raw
   path maximum and is **not harvestable** — scoring it anywhere in Stage A is a stop-and-report
   (plan §3.2).
2. **Selection lookahead is forbidden absolutely.** No day aggregates, no within-session ranking,
   earliest-by-time capacity at `N = 1`. Enforced by `RULE_COLUMNS` subsetting, not by care.
   **Exit lookahead is permitted only as `C`**, only as a screen and a denominator, and every `C`
   is published with `J` and `κ` beside it. The oracle-**selection** ceiling is forbidden outright
   (plan §6.2).
3. **The selection grid is `q80` / `q90` / `q95`.** Anything looser is throughput-degenerate — W1's
   whole grid was, and that is why it never tested a selective strategy (plan §6.1).
4. **Pre-register in the ledger (#735) before opening the fit split**, and post a batch comment
   before every scoring batch. Every walk-forward refit is a trial.
5. **Every result carries its null**, at matched intensity, over the **whole sequence**, with A-R4
   rebuilt inside each replicate. **N-AB is the primary and the only one a freeze may cite.**
   **`p ≤ 0.01`**, not 0.05 — 98 trials were already spent against this fit split.
6. **Net of costs at $500**, full buying power, the pinned cost model, 2-tick slippage.
7. **The holdout is not yours to open, in any form, for any reason.** CHECK is opened **once**, for
   one composed candidate, and only if gate G4 passes.
8. **Measurement and interpretation are separate sessions.** Do not do both in one.
9. **The panel is frozen.** `sha256sum -c panel-v1.sha256` must be 8 / 8. **Never rebuild it.**

### Your budget

**120 trials**, allocated in plan §7.1. **It is an authorisation, not a spending plan** — four
mechanical gates (§7.2) stand between the cheap stages and the expensive ones, and on the previous
pass's evidence the modal spend is **41–75**. Trials returned by a gate go back to the global
budget and are **never re-spent inside W4**. An agent that would exceed its allocation **stops**.

### The escalation rule

When a design assumption turns out false — a hash mismatch, a feature not populated on both halves,
a measurement contradicting another — **stop and report it on the issue with the evidence. Do not
improvise.** Every Stage-0 item in §5 carries a pre-authorised fallback precisely so you never have
to ask a question you cannot ask. Ledger every amendment **before** acting on it.

### What you are not doing

Not re-opening the capital question (settled: W2). Not re-deriving the cost identity. Not touching
`daily_universe`, the hand reviews, `news`, `float_shares` or `first_rank`. Not re-drawing the
splits. Not rebuilding the panel. Not opening the holdout. Not publishing a report — this pass
produces a research doc, and reports are dated findings.

### The one thing to get right

The previous pass searched hard, found nothing, and its nulls are sound — but two of its three
workstreams asked their question of a stream with a **gross-negative expectancy**, so their nulls
answered almost nothing. Your job is to make the opposite error impossible: **a large enough joint
search finds a beautiful cell in noise every time, and the search that found it always felt
disciplined from the inside.** The gates, the whole-sequence null at α = 0.01, and the single CHECK
score are the mechanisms. Use them as mechanisms, not as intentions.

> Copy to here.
