# W4 — Stage-0 interpretation and the Stage-A pre-registration (2026-09-19)

**Status:** INTERPRETED. Workstream **W4** ([#748](https://github.com/bennetwi92/small-cap-stack/issues/748)),
plan [`analysis-plan-4-joint.md`](./analysis-plan-4-joint.md), protocol
[`analysis-protocol.md`](./analysis-protocol.md) as amended by §16.

This is the **interpretation session**'s record (protocol §13.4). It **measures nothing**. Every
number below is either transcribed from [`analysis-w4-stage0.md`](./analysis-w4-stage0.md) — the
measurement session's record of the 8 charged Stage-0 trials — or is arithmetic on those published
numbers, which §5.2 makes free because it reads no outcome column the ledger has not already paid
for. **No trial is spent here. W4 remains at 8 / 120 (global 106 / 240).** CHECK and HOLDOUT are
untouched; no null is due until Stage A closes.

Ledger entries on [#735](https://github.com/bennetwi92/small-cap-stack/issues/735), posted in this
order: the fork reading and amendment **W4-A1**; the Stage-0 exit gate and the power reading; the
Stage-A pre-registration with amendments **W4-A2** and **W4-A3**.

⚠️ `C` is a **ceiling** — the oracle-exit R net of costs on a trigger-time-safe stream (amendment
P-1). It appears here only beside `J` and `κ`. **A candidate freezes on `J`, never on `C` or `κ`.**

---

## 1. The §5.2 fork — the row is **capture**, and the stop-and-report does not apply

### 1.1 The inputs, unambiguous

| input | measured | fork column |
|---|---|---|
| `C_raw` — ceiling of the unfiltered `N = 1` FIT stream | **+2.1152 R / session** | **> 0** |
| `κ = J / C_raw`, best family (F1 scalp) | **−0.183** (`J` = −0.3880) | **not high** |
| `κ`, the other three | F2 −0.207 · F3 −0.215 · F4 −0.260 | all negative |

Plan §5.2 has three rows. `C_raw ≤ 0` is false, so row 1 (*selection*) is out on its inputs. Row 3
(*> 0 / high κ*) is defined by its consequence — "the unfiltered stream is already viable, which
would **contradict** W1's 42 negative-gross points" — and requires `J > 0`, i.e. `κ > 0`. Every
measured `κ` is negative and every family's `J` is negative, which **agrees** with W1 rather than
contradicting it. **No two measurements disagree. The row-3 stop-and-report does not apply, and
W4 does not stop.**

> **The row is row 2: `C_raw` > 0, `κ` low → the deficit is *capture*, and the budget is amended
> toward Stage B before Stage 1 (amendment W4-A1, §1.4).**

### 1.2 The pool is real — it is concentrated, but it is not one trade and it is not one decile

The heavy-tail facts could make `C_raw` > 0 an artefact, so they are tested against the published
numbers before the row is accepted.

| test | arithmetic | reading |
|---|---|---|
| **Does `C_raw` > 0 depend on the 83 R trade?** | Drop the single largest trade (83.162 R): `C` = 1.803 R/session (published); on the remaining 265 trades mean 1.809, **sd 4.966** (from 7.032), se 0.3051, **t = 5.9** — *up* from 4.9 | **No.** Removing the biggest observation removes more variance than mean and makes the ceiling *more* clearly positive. |
| **Does it depend on the top decile of sessions?** | Top-decile sessions hold 66.4 % of the positive pool, so the other ~239 sessions hold 33.6 % ≥ 0.336 × 562.6 = **189.0 R**. Worst case every one of the 84 trades with pool ≤ 0 sits outside the top decile, each at the observed minimum −0.872 → ≥ **−73.3 R**. Net ≥ **+115.8 R** over the full 266-session denominator | **No.** **Delete the entire top decile of sessions and the remaining 90 % still offer ≥ +0.44 R/session of oracle ceiling.** Concentrated, not singular. |
| **Is the median trade carrying it?** | Median per-trade pool **+0.313 R**; 31.6 % of trades have pool ≤ 0; median `max_r` over all 4,516 FIT rows **0.500 R** | **No** — and this is the problem, not a reassurance. The typical trade's *entire* oracle ceiling is ~0.3 R, and half of all rows never travel further than F1's 0.5 R target. |

So the §3.5 admissibility screen is safe: the tape did offer a positive net pool to an unselective
stream, it is not a single lucky print, and Stage B is not being funded against an artefact.

### 1.3 ⚠️ But the *label* on row 2 is wrong for this record, and that governs how much is moved

Row 2's gloss is "**the pool is there and the exits are leaving it on the table.**" The first half is
established above. **The second half is not supported by the measurements, and the executing sessions
must not read it as "tune the exits".**

**The decisive evidence is the invariance of `κ` across the four families.** F1–F4 span essentially
the whole space of exit shapes — a 0.5 R scalp with no tail exposure at all, a 2 R bracket, an
*unbounded* structural trailer, and a scale-out with a break-even stop:

| | `J` R/session | `κ` |
|---|---|---|
| F1 scalp 0.5 R | −0.3880 | −0.183 |
| F2 base 2 R | −0.4379 | −0.207 |
| F3 runner (unbounded) | −0.4540 | −0.215 |
| F4 hybrid (scale-out) | −0.5491 | −0.260 |

- The deficit from the ceiling to the **best** family is `C_raw − J(F1)` = **2.5032 R/session**.
- The **entire spread across the four families** is `J(F1) − J(F4)` = **0.1611 R/session**.
- **Exit family therefore accounts for at most 6.4 % of the gap** (0.1611 / 2.5032).
- And the ordering is **anti-tail**: the family that cannot touch the tail (F1, capped at 0.5 R) is
  the *best*; the only family with unbounded upside (F3) is third; the scale-out (F4) is worst.

If the deficit were a capture problem in the ordinary sense — money sitting in the tape that a
better-shaped exit collects — the family that reaches the tail would separate sharply from the one
that caps at 0.5 R. It does the opposite. **On this stream the exit design is close to irrelevant to
the outcome, which means the exit is not the binding constraint.**

*Indicative, not a measurement (all 4,516 FIT rows, not the `N = 1` stream; no new trial).* A pure
bracket at target `T` with a −1 R stop and cost `c ≈ 0.19` breaks even at a reach rate
`p = (1+c)/(1+T)`. Against the published `max_r` deciles the realised reach rate is **49–64 % of
break-even at every target from 0.5 R to 4.9 R** — a shortfall that is roughly flat in `T`. A
scale-free shortfall is not a symptom a target choice fixes.

**Why the oracle still scores +2.115, then:** the ceiling's positive mass lives in the oracle's
foreknowledge of **which trade**, not of **when to leave a trade already known to be good**. 66.4 %
of the positive pool is in ~27 of 266 sessions and one trade alone is 0.313 R/session of the 2.115.
Knowing in advance which session is a tail session is **not an exit policy** — it is a
**session-state gate**, i.e. **axes A-R1…A-R6, which are Stage A's**.

### 1.4 What this does to the budget — amendment **W4-A1**

Pre-registration binds: the fork was written before the numbers were seen and it says row 2 amends
toward Stage B. That is done. The evidence in §1.3 decides **how much and where**, not *whether*.

**The shift is +8 trials, bounded deliberately**, and is aimed at the one structural region the
invariance evidence does *not* cover — F1–F4 are four fixed shapes at frozen parameters, and their
flatness bounds what *those four* buy, not what their interiors do.

| | before | after | why |
|---|---|---|---|
| **B2** F3's interior (arming × trailing reference) | 6 | **8** | F3 is the only unbounded family and its arming threshold is an inherited constant (1 R, protocol §10). 4 arming × 2 trailing reference. The 4th arming point is **pool-derived** — the stream's own FIT median `max_r` — so the trail arms where the median trade actually lives. |
| **B4** pool-derived fixed targets | 4 | **6** | As budgeted, q40/q50/q60/q70 of the stream's own `max_r` all sit at or below ~1.5 R on the FIT row distribution — inside the region F1 and F2 already show fails. **Add q80 and q90**, which is where the pool is (top-decile rows hold 59.4 % of Σ max(`max_r`, 0)). Without this, B4 samples only known-dead ground. |
| **B7** *(new)* partial exit **without** a break-even stop | — | **4** | F4 is the only scale-out in the frozen set and it is the worst family; W2's E4 already showed F4's *fixed-target* interior degenerates toward F2. Its untested defect here is the **break-even stop on the remainder**, which caps the tail at the moment the tail starts. B7 = {half off at 0.5 R, half off at 1 R} × {stop held at the consolidation low, stop on F3's trailing rule}, remainder always trailing, **no break-even stop**. This is the one cell F1–F4 do not contain, and the pool's shape (median 0.5 R, 66 % in the top session decile) names it directly. |
| **Stage B total** | 24 | **32** | |
| **reserve** | 6 | **0** | the reserve exists for exactly this |
| **A5** stand-aside depth sweep | 3 | **1** | A5 sweeps *deeper* fractions on an already-chosen gate. It tests no new hypothesis (A2/A3 carry the state hypothesis), the deeper fractions are the ones W4-0d already drops on throughput, and the differences it would resolve are two orders below the MDE on `C` (§2). Keeps the single deepest **in-band** fraction. |

⚠️ **B7 is a policy, not a fifth exit family.** Protocol §10 defines F1–F4 once so the three
workstream plans compose; that pass is closed, F1–F4 are still scored at B1 as the reference, and
B2/B3/B4 already search points outside F1–F4's literal definitions. B7 is of a piece with those.

**Deliberately *not* done:** Stage A is not cut beyond A5, and A1/A2/A3/A4 — the entire joint
hypothesis, which is the reason W4 exists (plan §1) — are untouched. §1.3 says the return to exit
design on this stream is small; moving 30 trials into Stage B on a row whose label the evidence
contradicts would be the rescue this plan was written to prevent.

**Revised allocation (total unchanged at 120; global authorisation unchanged at 240):**

| stage | trials |
|---|---|
| S4-0 (spent) | 8 |
| A1 15 · A2 18 · A3 36 · A4 3 · **A5 1** → **Stage A** | **73** |
| B1 4 · **B2 8** · B3 6 · **B4 6** · B5 3 · B6 1 · **B7 4** → **Stage B** | **32** |
| Stage C (C1 2 · C2 2 · C3 2 · C4 1) | 7 |
| reserve | **0** |
| **total** | **120** |

Gate spends restate to: **G1 23** (unchanged) · **G2 41** (unchanged — G2 still defunds A3's 36) ·
**G3 ≈ 81** · **G4 ≈ 112**. **The reserve is now zero:** any further amendment must be funded by a
named stage or by a request against the global ledger, and an agent that would exceed its
allocation **stops** (plan §13).

### 1.5 What Stage A still owes

Row 2 does **not** demote Stage A. Given §1.3 it raises what Stage A must deliver:

1. **G3 will not discriminate as written.** `C_raw` is already +2.115, so almost any in-band cell
   clears "best joint cell has `C` > 0". Clearing G3 is necessary and nearly free; it is not
   evidence.
2. **Stage A owes a stream whose ceiling is *reachable*, not merely large** — because a ceiling that
   lives in 10 % of sessions is one no causal exit collects. That has to be measured, not assumed:
   **amendment W4-A2** (§3.6) adds free per-cell pool-shape diagnostics and a pre-registered
   tie-break.
3. **Stage A owes the state-gate test its full budget**, because §1.3's argument is that "which
   session" is where the recoverable structure would have to be, and that is A2/A3's question.
   ⚠️ This is a *prior*, declared here before any cell is scored — it is **not** a prediction the
   nulls are relaxed for. N-AB at `p ≤ 0.01` is unchanged and remains the only null a freeze may
   cite.
4. **Stage A owes A-R4's real throughput.** W4-0d's A-R4 pair throughput is **nominal**
   (selection throughput × (1 − fraction)); it is re-measured free when A-R4 is built (§3.3).
5. **Stage A owes one reconciliation** (§2.4): the in-band grid count.

---

## 2. The Stage-0 exit gate, and what this record can actually resolve

### 2.1 The gate — **confirmed, not amended**

| §5.3 requirement | status |
|---|---|
| W4-0a–0f complete and ledgered | ✅ #735, 2026-09-19 07:25 |
| the eight charged trials posted | ✅ #735, 07:27 (8 trials, W4 8 / 120, global 106 / 240) |
| the §7.2 power table recomputed from `sd(pool_i)` | ✅ S4-0k, sd = 7.032 |
| ≥ 4 selection axes **and** ≥ 4 state axes surviving W4-0c/0e | ✅ **5** and **6** (nothing dropped; no pair |ρ| > 0.9) |
| throughput-calibrated grid with ≥ 24 scoreable joint points | ✅ **201** in band of 270 |
| **G1** (`C_raw ≤ 0` **and** top-decile share < 0.25) | **does not fire** — +2.115 and 0.594 |
| **Power cancellation** (per-session MDE on `C` > \|`C_raw`\|) | **does not fire** — 1.474 < 2.115 |

**The Stage-0 exit gate is met. Stage A is authorised.** Both cancellation conditions are read
exactly as pre-registered; neither is reinterpreted.

### 2.2 The power table — arithmetic confirmed, **reading amended**

Recomputed independently from the published sd: δ = (z₀.₀₀₅ + z₀.₂₀)·σ/√n = 3.4174 · 7.032/√n.

| FIT trades | 266 (1.00/s) | 221 (0.83) | 154 (0.58) | 120 (0.45) |
|---|---|---|---|---|
| **MDE on `C`, per trade** | **1.474 R** | 1.617 R | 1.937 R | 2.194 R |

**Arithmetic confirmed at all four points.** Three amendments to how it is *read* — none change a
number, all three stop a number being over-read:

- **(a) It is normal theory on a distribution that violates normality severely** (sd 7.03, median
  pool 0.313, max 83.16, 31.6 % of trades ≤ 0, skew dominated by one observation). The normal
  approximation to the sample mean's sampling distribution is poor at this skew. **1.474 R is a
  floor on the honest MDE, not a point value.** `C`-differences are to be read against a permutation
  reference — which the design already supplies at freeze — and **never against ±1 se**.
- **(b) The resolving power is hostage to one observation.** Excluding the single 83.162 R trade,
  sd falls **7.032 → 4.966 (−29 %)** and the per-trade MDE falls **1.474 → 1.043 R**. One of 266
  trades sets a third of what this record can detect.
- **(c) ⚠️ G2's bar is not the MDE and must never be quoted as one.** G2 fires on a lift of **≥ 1 se**
  ≈ 0.431 R/trade at 1.00 trades/session (≈ 0.567 at 0.58, ≈ 0.642 at 0.45) — roughly **a third** of
  the MDE, and a ~68 % one-sided screen rather than a test. **A cell passing G2 is not a detected
  effect.** G2 authorises A3's funding and nothing else; it may not be cited as a finding.

### 2.3 What size of cell effect on `C` this record can resolve — stated plainly

> **At α = 0.01 two-sided and 80 % power, Stage A can resolve a difference in the per-trade ceiling
> of ≥ 1.474 R at full throughput, rising to ≥ 2.194 R at the 0.45 band floor. The unfiltered
> stream's per-trade ceiling is 2.115 R. So the smallest cell effect this record can resolve is
> ~70 % of the entire baseline ceiling at best, and ~104 % of it at the floor: a selection rule
> would have to roughly *double* the available pool per trade — or destroy most of it — before this
> record could tell it apart from the unfiltered stream.**

Two concrete calibrations of how coarse that is:

- The **published Filter-A reference** stream differs from the unfiltered stream by
  `C_A − C_raw` = **+0.0547 R/session** — a real difference between two real streams, and **~27×
  below the MDE**. (Reference only; P-4 forbids conditioning anything on it.)
- W1's best point sat **0.21 R/trade** from break-even on `J`. That was already below the `J`-MDE at
  every throughput (plan §7.3), and the `C`-MDE is five times coarser still.

**Consequences, declared now:**

1. **`C` is a screen (§3.5) and a denominator (§3.4). It is not a test, and Stage A may not rank
   cells on `C` alone** — hence amendment W4-A2's pre-registered tie-break (§3.6).
2. **A null Stage A means "no large cell effect", never "no effect"** — plan §7.3's wording, and it
   binds `C` at least as hard as it binds `J`.
3. Nothing here re-opens the power cancellation: it compares the MDE to \|`C_raw`\| and it does not
   fire. The cancellation licenses that Stage A *could* detect an effect of the size of the whole
   ceiling. It **does not** license resolving differences *between* cells, and the gate was never
   written to.

### 2.4 One reconciliation item, carried into Stage A

W4-0d publishes **270 grid points, 201 in band, 69 dropped**. Reconstructing the in-band test from
the published (rounded) throughputs — `selection throughput × (1 − realised stand-aside share) ≥
0.45` — gives **68** dropped, not 69:

| selection level | dropped pairs | why |
|---|---|---|
| A-S1 q95 (0.515) | 12 | in band only against the 10 % tier |
| A-S2 q95 (0.519) | 12 | as above |
| A-S3 q95 (0.560) | 8 | 20 % tier survives against R1/R2/R5/R6, drops against R3 (19.9 %) and A-R4 (20 % nominal) |
| A-S4 q95 (0.455) | 18 | at the floor alone |
| A-S5 q95 (0.466) | 18 | at the floor alone |
| | **68** | |

The one-point difference is almost certainly a boundary pair sensitive to the 3-dp rounding in the
transcribed table. **It changes no decision** — 201 or 200 both clear the ≥ 24 floor by an order of
magnitude, and the disputed point is at the throughput floor where A-R4's nominal pairing is
re-measured anyway. **Stage A confirms the exact dropped list against `w4-stage0-free.json` before
A1 scores, and ledgers the boundary point.** This is a bookkeeping check, not a stop-and-report: no
measurement contradicts another.

---

## 3. Stage A — pre-registration

Posted to #735 **before any outcome column is opened for Stage A**. Protocol §5 constraint 3.
Plan §7.1 (A1–A5), §6.3 (axes and directions), §6.4 (leak block), §8 (nulls).
**Seed 20260919. B = 200. α = 0.01. N-AB primary.**

### 3.1 The objective, fixed

For a candidate `(S, G)` on a block with session universe `Σ`:

- **Permitted sessions**: those `G` allows. A session `G` stands aside on takes **no trade and
  contributes 0**, and is **never dropped from `|Σ|`**.
- **The taken trade**: in each permitted session, the **earliest** setup by `trigger_et_min` passing
  `S`. **Capacity `N = 1`. No ranking, ever.**
- `pool_i = max(max_r_i, −1) − c_i`, `max_r` the panel's **stop-truncated** value (W4-0b verified:
  6,740 / 6,740 reproduced, 0 mismatches, walk stop-armed).
- `c_i` = `stage0.price_trade` at **$500 full buying power**, pinned tiered schedule, the oracle exit
  priced as **one non-limit leg at `entry + r·risk`, `r = max(max_r, −1)`** — both commissions plus
  **2-tick slippage on the exit** (the `c_trail` floor). A row the account cannot afford contributes
  **0** and is counted.
- **`C(S, G) = Σ pool_i / |Σ|`.**

⚠️ **W2's `R_max` (raw path maximum, no stop) is not read, not computed, and not approximated
anywhere in Stage A. Doing so is a stop-and-report** (plan §3.2). ⚠️ **The within-session-best
("oracle selection") ceiling is forbidden outright** and may not be computed, quoted or estimated
(plan §3.6, §6.2). ⚠️ **Every reported `C` carries the word *ceiling*, the realised `J` and `κ`**
(P-1). A candidate freezes on `J`.

### 3.2 The five selection axes — directions fixed a priori, never searched both ways

| id | column | op | q80 → trades/s | q90 | q95 |
|---|---|---|---|---|---|
| **A-S1** | `hits_before_trigger` | ≥ | 25 → 0.947 | 35 → 0.816 | 46 → 0.515 |
| **A-S2** | `cum_dollar_vol_pre_trigger` | ≥ | 15,802,035 → 0.932 | 27,166,815 → 0.748 | 43,653,285 → 0.519 |
| **A-S3** | `retracement` | ≤ | 0.5714 → 0.970 | 0.44755 → 0.793 | 0.3636 → 0.560 |
| **A-S4** | `stop_pct` | ≥ | 0.10329 → 0.910 | 0.14265 → 0.707 | 0.18445 → 0.455 |
| **A-S5** | `ext_at_trigger` | ≤ | −0.00008 → 0.914 | −0.06216 → 0.722 | −0.11988 → 0.466 |

Levels are **FIT quantiles** keeping 20 / 10 / 5 % of rows (protocol §5.3), thresholds frozen at
W4-0d and **not re-derived**. Anything looser than q80 is throughput-degenerate and **is not
scored** (plan §6.1 — this is the correction W1's whole grid failed). Predicates are submitted over
the frozen `RULE_COLUMNS` subset; the runner subsets **before** calling, so a reach for `max_r` or a
day aggregate raises `KeyError`. `assert_no_lookahead()` runs on the subset as the belt to that
brace.

### 3.3 The six state axes — decidable before the session's first trigger

| id | series | stand aside | 10 % | 20 % | 30 % |
|---|---|---|---|---|---|
| **A-R1** | prior-session distinct triggered symbols, 5-session mean | **below** | 8.0 (7.9 %) | 9.2 (18.4 %) | 11.0 (29.3 %) |
| **A-R2** | prior-session mean `hits_before_trigger`, 5-session mean | **below** | 13.371 (9.8 %) | 14.185 (19.5 %) | 14.766 (29.3 %) |
| **A-R3** | prior VIX close | **above** | 24.13 (10.2 %) | 20.87 (19.9 %) | 19.27 (30.1 %) |
| **A-R4** | trailing 20-trade pool of the candidate's own stream | **below** | quantile of its own FIT series, built in-stage | | |
| **A-R5** | A-R1 reversed | **above** | 20.8 (9.8 %) | 17.6 (19.2 %) | 16.0 (29.3 %) |
| **A-R6** | A-R3 reversed | **below** | 14.775 (10.2 %) | 15.37 (19.5 %) | 16.03 (30.1 %) |

⚠️ **A-R2 carries W3-A1's caveat**: it tracks the plan's original all-day attention feature at only
ρ = 0.385, and W3's headline rested on it. ⚠️ **A-R5 / A-R6 are paid for as their own hypotheses,
not flipped**: W3 found R1 and R3 raised `J` while making every retained trade worse, and flipping
silently after reading that is selection-of-maximum bias. ⚠️ **Prior on A-R4 is low**: W3 refuted
session-level serial dependence on the Filter-A stream (Ljung–Box Q(20) = 12.92 vs 31.41; runs
z = 0.215). That is a fact about *that* stream and is re-measured on W4's own — but it lowers the
prior and the executing session should expect it to.

**Amendment W4-A3 — A-R4 is defined in pool terms at Stage A.** Plan §6.3 says "trailing 20-trade
**net R** of the candidate's own stream", but Stage A has **no exit family frozen** — its currency is
`pool`, and picking a family to define the gate would re-introduce exactly the selection × bracket
confound §3.3 says `C` exists to remove. Therefore, declared before any A-R4 cell is scored:

- At Stage A, A-R4 = the **trailing 20-trade mean `pool_i`** of the candidate's own stream, over
  **closed prior sessions only** (not lookahead: every input is a trade that already finished before
  the gated session's first trigger).
- The cut is a FIT quantile of **its own** series at 10 / 20 / 30 %; **its realised pair throughput
  is re-measured free when the series is built** and any pair falling below 0.45 is dropped with its
  trials returned (W4-0d's nominal figures do not bind).
- The first 20 trades are undefined; **an undefined state trades** (W3's convention, as for R1/R2's
  5-session warm-up).
- Whatever A-R4 cut is frozen at Stage A is **carried verbatim into Stage B and is not refitted**
  when an exit family is chosen.
- ⚠️ **A-R4 is self-referential and is rebuilt inside every null replicate**, on that replicate's own
  permuted pools (W3's construction). On HOLDOUT it is computable by the custodian only.

### 3.4 The grid

**270 joint points** (15 selection levels × 18 state gates), **201 in band**, **69 dropped before
Stage 1** and their trials returned — the dropped list is `w4-stage0-free.json` and is reconciled
per §2.4 before A1 scores. Band **[0.45, 1.0]** average trades/session (amendment P-2). ⚠️ **A
frozen candidate below 0.6 trades/session ships flagged as a throughput departure** the operator
must accept explicitly; the declared target is 0.8/day. A point outside the band is
**disqualified, not penalised** (plan §3.3).

### 3.5 The five stages, and what selects between them

| id | what | trials | pre-registered rule |
|---|---|---|---|
| **A1** | selection marginals on `C`: 5 axes × 3 levels, no state gate | **15** | — |
| **G2** | after A1 | — | **stop** if no selection axis at its tightest in-band level lifts per-trade `C` by ≥ 1 se over the unfiltered `N = 1` stream → A3's 36 are not funded, spend 41. ⚠️ Passing G2 is a funding screen, **not** a detected effect (§2.2c). |
| **A2** | state marginals on `C`, on A1's best selection: 6 axes × 3 fractions | **18** | "A1's best selection" = the (axis, level) pair ranked first by §3.6 |
| **A3** | the joint cells: 2 selection survivors × 3 levels × 2 state survivors × 3 fractions, in-band only | **36** | **survivors are the top two by §3.6** — selection from A1, state from A2. Out-of-band cells are skipped and their trials returned. |
| **A4** | selection conjunction (A-Sᵢ ∧ A-Sⱼ, the two survivors) under the winning state gate × 3 levels | **3** | levels chosen so the **conjunction's** throughput stays in band; throughput of a conjunction is a free calculation (§5.2). Fewer than 3 in-band levels → unspent trials return. |
| **A5** | stand-aside depth on the winning cell — **1 point** after W4-A1 | **1** | the **deepest** fraction in {40 %, 50 %, 60 %} whose realised throughput on the winning cell is ≥ 0.45. None in band → not scored, trial returns. |
| **G3** | after Stage A | — | **stop** if the best joint cell has **`C` ≤ 0** → §3.5, no exit can rescue it, **Stage B never runs**, spend ≈ 81 |
| | **Stage A total** | **73** | |

A **batch comment is posted to #735 before every scoring batch**, naming the points and the running
trial count. Every point is one trial; a k-point grid is k trials.

### 3.6 Amendment **W4-A2** — how a cell is chosen, declared before any cell is seen

§2.3 establishes that cell-to-cell differences in `C` will sit far below the MDE. Ranking on `C`
alone would therefore be ranking on noise, and improvising a tie-break *after* seeing the ranking is
the classic sin. So both are fixed now.

**(a) Free per-cell diagnostics.** Every scored Stage-A cell reports, beside its `C`, four figures
that are **pure arithmetic on rows already scored for `C`** and cost **no additional trial**:

1. **median per-trade `pool`** — the typical trade's ceiling (unfiltered reference: +0.313 R);
2. **share of trades with `pool` ≤ 0** (unfiltered reference: 31.6 %);
3. **top-decile sessions' share of positive pool** (unfiltered reference: 66.4 %);
4. **`C` recomputed with the cell's single largest trade removed** (unfiltered reference:
   2.115 → 1.803).

Without these, Stage B is handed a stream it cannot reason about, and §1.3's question — is this
ceiling *reachable* — has no answer at the moment the stream is frozen.

**(b) The pre-registered ranking rule.** Among cells that are **in band** and pass the stage's gate,
rank by:

1. **`C` per trade**, descending; ties — and **any two cells within 0.30 R per trade**, which §2.3
   shows is well inside noise — fall to:
2. **median per-trade `pool`**, descending; ties within 0.05 R fall to:
3. **share of trades with `pool` > 0**, descending; ties fall to:
4. **the looser selection level** (higher throughput → more trades → better power downstream, and
   further from P-2's 0.6 flag).

⚠️ **This is a tie-break for choosing which stream to hand Stage B. It is not a second objective and
it does not become a freeze criterion. A candidate still freezes on `J > 0` with `p ≤ 0.01` on
N-AB — never on `C`, never on `κ`, never on any figure in (a).**

### 3.7 The leak block (plan §6.4)

1. **Selection and state are fitted on FIT only** — 266 sessions. **CHECK is opened once**, for one
   composed candidate, at B6, and only if G4 passes. **HOLDOUT is not W4's, in any form, for any
   reason**; its outcomes are physically absent from the panel (protocol §4.3), so this is a
   property of the bytes.
2. **The permutation null runs at matched intensity over the entire joint sequence** — A1 → A5 re-run
   in full on every replicate, with A-R4 rebuilt inside each.
3. **α = 0.01** (P-3), because 98 trials were already spent against this same fit split.
4. **The splits are not re-drawn.** The panel is not rebuilt (`sha256sum -c panel-v1.sha256` = 8 / 8,
   W4-0a).

### 3.8 The nulls (plan §8) — **B = 200, seed 20260919**

| id | construction | controls |
|---|---|---|
| **N-A** | within each session, permute which setup received which **price path**; the donor's exit legs move, the recipient keeps its **own** entry, stop, size and cost (W1's construction, so A-S4's cost lever cannot masquerade as signal) | the **selection** search |
| **N-B** | circular block permutation of the per-session outcome series against a fixed session-state series, **block length 20 sessions** (W3's construction, which preserves the burst structure prior (1) declares real) | the **state** search |
| **N-AB** | both, composed | **the joint search — primary, and the only one a freeze may cite** |

- `p` = share of replicates whose **best-of-sequence** statistic ≥ the real best-of-sequence.
- **Threshold `p ≤ 0.01`.** Bonferroni cross-check at **0.05 / 120** reported beside it.
- **A-R4 is rebuilt inside every replicate.** A null that reuses the real A-R4 series is invalid.
- A permutation null is **not** a trial (§5.2); it is the control for the trial set.
- **Sensitivity** (±20 % on every frozen literal, one at a time, on FIT) is free, is **reported**,
  and **may never select a value**. If a sensitivity band changes a chosen parameter, the new value
  is a **new trial**.

### 3.9 Drift, carried into the freeze report (W4-0f) — flagged, never used to drop an axis

Share of rows a **FIT** q90 threshold keeps on each block:

| column | FIT | CHECK | HOLDOUT recon | HOLDOUT live |
|---|---|---|---|---|
| `cum_dollar_vol_pre_trigger` | 10.0 % | 11.4 % | **17.7 %** | 8.7 % |
| `ext_at_trigger` | 10.0 % | 13.3 % | 11.1 % | **18.0 %** |
| `stop_pct` | 10.0 % | 8.8 % | **14.5 %** | 11.4 % |
| `hits_before_trigger` | 10.8 % | 10.3 % | 12.8 % | 12.8 % |
| `retracement` | 10.0 % | 8.5 % | 10.9 % | 7.7 % |

State means — R1 breadth **14.1 / 14.6 / 17.4 / 17.6**, R2 attention 16.0 / 15.3 / 17.2 / 17.0,
R3 VIX 18.8 / 19.0 / 18.5 / **16.2** (FIT / CHECK / HOLDOUT recon / live).

⚠️ **Any frozen candidate reading `cum_dollar_vol_pre_trigger`, `ext_at_trigger` or `stop_pct` at a
tight level carries its drift row in the freeze report**, and so does a candidate gating on R1 or R3.
Quantile thresholds absorb part of this by construction (protocol §5.3) and re-quantiling on a later
block is *not* permitted without a trial. **Drift is a fact about the record; hiding it makes the
holdout unreadable, and it is never a reason to drop an axis.**

### 3.10 What Stage A is not

Not opening CHECK. Not opening HOLDOUT. Not re-drawing the splits. Not rebuilding the panel. Not
re-deriving the cost identity or re-opening the capital question (settled: W2). Not reading
`daily_universe`, the hand reviews, `news`, `float_shares` or `first_rank` (constraint 12). Not
ranking within a session. Not computing `R_max` or the oracle-selection ceiling. Not publishing a
report — this pass produces a research doc.

---

## 4. Handover

**No trial spent by this session. W4 stands at 8 / 120; global 106 / 240.** No stop-and-report is
open. CHECK and HOLDOUT untouched.

Measurement resumes in a **fresh session** (protocol §13.4) from the three #735 ledger entries and
this document. Its first acts, in order:

1. Reconcile the in-band grid count against `w4-stage0-free.json` and ledger the boundary point
   (§2.4) — free.
2. Build A-R4 per **W4-A3** and re-measure its pair throughput — free.
3. Post the **A1 batch comment** to #735, then score A1's 15 points.
