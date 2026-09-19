# W2 result — entry mechanics, exit interiors and the cost floor

**Status:** FROZEN (2026-09-18). Workstream **W2** of the three-agent analysis, executed against
[`analysis-plan-2-execution.md`](./analysis-plan-2-execution.md) under
[`analysis-protocol.md`](./analysis-protocol.md). Ledger:
[#735](https://github.com/bennetwi92/small-cap-stack/issues/735); findings:
[#738](https://github.com/bennetwi92/small-cap-stack/issues/738).

> **The outcome is a documented null, and the null is the deliverable.** No execution specification
> is carried to the holdout. W2's candidate slot at the custodian's pass is **empty, deliberately**
> (protocol §12.1 admits "or a documented null"), and the §9.2 viability table below ships in its
> place.

**Budget: 32 of 36 trials spent; 4 returned to the global budget unspent** (E6's 3 and the 1
reserve — amendment W2-A3). Panel `panel-v1`, verified by sha256 against
[`panel-v1-spec.md`](./panel-v1-spec.md) §1, 8 / 8. Harness `spikes/analysis_v1/w2.py`, committed
before anything was scored. Everything below is FIT + CHECK; the holdout was never opened.

---

## 1. The three answers, in the order they matter

### 1.1 Which exit family clears its cost floor at $500? **None. And the account is not why.**

The plan's §9.4 pre-declared what a null would mean: *"If no exit family clears its cost floor at
$500 under any entry mechanic, then the next phase's question is **capital, not rules**."*

**That branch is measured, and it is wrong.** Recomputing `c` on the Filter-A FIT stream at six
account sizes, with §11.1's two terms separated:

| equity | `c_loss` | fee term | **slippage term** | F1 break-even | F2 break-even | F1 break-even at 0 ticks |
|---|---|---|---|---|---|---|
| **$500** | 0.1830 | 0.0816 | **0.1015** | **73.9 %** | 38.1 % | 72.1 % |
| $1,000 | 0.1742 | 0.0727 | **0.1015** | 73.3 % | 37.9 % | 71.5 % |
| $2,500 | 0.1712 | 0.0698 | **0.1015** | 73.1 % | 37.8 % | 71.3 % |
| $5,000 | 0.1710 | 0.0695 | **0.1015** | 73.1 % | 37.8 % | 71.3 % |
| $10,000 | 0.1710 | 0.0695 | **0.1015** | 73.1 % | 37.8 % | 71.3 % |
| $25,000 | 0.1710 | 0.0695 | **0.1015** | 73.1 % | 37.8 % | 71.3 % |

Fifty times the account buys **0.8 percentage points** of F1 break-even. The reason is in the
identity itself:

```
c ≈ (2 × commission_per_side) / (BP × stop_pct)   +   2 × slip_pct / stop_pct
      ↑ shrinks with buying power                      ↑ does not depend on it at all
```

Only the first term sees the account. It is already the *smaller* term at $500 (0.082 R of 0.183 R),
and it is exhausted by ~$2,500 — the point at which the $0.35 per-order minimum stops binding. The
second term is ticks per share against a percentage stop, and no amount of equity moves it. **Even
at $25,000 with slippage set to zero, F1 needs 71.3 % and gets 54.9 %.**

> **The binding constraint is the strategy, not the account.** Recapitalising this system does not
> make it viable, and the next phase should not be a capital question. This is the single most
> load-bearing thing W2 measured, and it is the opposite of what the plan expected to find.

### 1.2 How much net R per session is recoverable by changing only entry and exit? **About 0.10 R, and it is not enough to reach zero.**

Best of 24 scored points: `M2×F1` at **J = −0.1945** net R/session on FIT, against the Filter-A
baseline's best of −0.2981. A real improvement of +0.104 R/session — and the destination is still
deeply negative. **Every one of the 24 points is negative net, and every one is negative gross.**
Costs are not why this loses; it loses before costs are charged.

### 1.3 Does it survive its luck benchmark? **No, and not narrowly.**

Best-of-search permutation null, block-by-session, B = 200, all 24 points re-run per replicate:
**p = 0.1891**. The real best does not reach the null's 95th percentile (−0.1359). §9.1 condition 1
fails, and conditions 2 and 3 fail independently of it.

---

## 2. §9.2 — the per-family viability table

*The deliverable that ships whatever else happens.* Filter-A stream, FIT, 266 trades, full buying
power, 2-tick slippage on non-limit exits. `c` from S0-F; realised hit rates measured here.

| family | target | **net break-even** (S0-F) | **realised net hit rate** | shortfall | mean cost/trade | net R/trade | J | **verdict at $500** |
|---|---|---|---|---|---|---|---|---|
| **F1** scalp (0.5 R) | 0.5 R | **77.0 %** | 54.9 % | **−22.1 pts** | 0.127 R | −0.2981 | −0.2981 | **dead** |
| **F2** base (2 R) | 2 R | **40.4 %** | 28.6 % | **−11.8 pts** | 0.154 R | −0.2994 | −0.2994 | **dead** |
| **F3** runner | — | n/a (expectancy) | 29.3 % | | 0.182 R | −0.3133 | −0.3133 | **dead** |
| **F4** hybrid | — | n/a (expectancy) | 41.7 % | | 0.167 R | −0.4532 | −0.4532 | **dead** |

F3 and F4 have no fixed target, so a target break-even is undefined; their floor is `c` subtracted
from every trade regardless of outcome, and both fall further below zero than F1 or F2.

**Not one family is marginal.** F1 misses by 22 points of hit rate, F2 by 12. Those are not gaps a
better execution model closes — F1 would need to convert more than one loser in five into a winner.

### By `stop_pct` decile — F1 and F2, with S0-F's frozen FIT edges

| decile | median `stop_pct` | F1 cost/trade | F1 net hit | F1 net R/trade | F2 cost/trade | F2 net hit | F2 net R/trade |
|---|---|---|---|---|---|---|---|
| D1 | 1.21 % | **0.286** | 0.588 | −0.3155 | **0.360** | 0.235 | −0.6537 |
| D2 | 2.27 % | 0.137 | 0.632 | −0.1898 | 0.173 | 0.368 | −0.0683 |
| D3 | 3.11 % | 0.174 | 0.250 | −0.7989 | 0.180 | 0.188 | −0.6175 |
| D4 | 4.32 % | 0.149 | 0.435 | −0.4965 | 0.186 | 0.217 | −0.5337 |
| D5 | 5.29 % | 0.169 | 0.600 | −0.2693 | 0.198 | 0.367 | −0.1171 |
| D6 | 6.36 % | 0.125 | 0.568 | −0.2734 | 0.151 | 0.324 | −0.1781 |
| D7 | 7.51 % | 0.115 | 0.600 | −0.2146 | 0.147 | 0.200 | −0.5473 |
| D8 | 9.17 % | 0.106 | 0.556 | −0.2731 | 0.129 | 0.333 | −0.1287 |
| D9 | 11.32 % | 0.069 | 0.583 | −0.1940 | 0.085 | 0.250 | −0.3348 |
| D10 | 19.56 % | **0.032** | 0.552 | −0.2047 | **0.039** | 0.310 | −0.1082 |

**The cost gradient is a factor of nine** — 0.286 R per trade in the tightest decile against 0.032 R
in the widest — and it runs in the direction §11.1 predicted, monotonically, with no exception. **No
decile of any family is net positive.** The cost lever is real and it is large, and it is still not
large enough: even in D10, where costs are almost free, F1 returns −0.20 R per trade.

---

## 3. What was searched, and what each mechanic bought

### E1 — fill realism (3 trials). The 3-tick assumption is right at the median and wrong in the tail.

| | M1 (5-min reference) | M2 (1-min close) | M3 (1-min achievable) |
|---|---|---|---|
| entered, of 266 | 266 | **252** | 266 |
| fill vs consolidation high, ticks q10 / q50 / q90 | 3 / 3 / 3 | 1 / 5 / 24 | **−4 / 2 / 18** |
| mean | 3.0 | 14.8 | 8.3 |
| fill **below** the trigger | 0 % | 7.1 % | **36.1 %** |
| entry delay, minutes q50 (mean) | 0 (0) | 3 (8.7) | 2 (2.3) |
| Δ`r_max` vs M1, mean / median | — | −0.50 / −0.21 | +1.06 / +0.12 |

M1's fill is exactly `consolidation high + 3 ticks` on **266 / 266** setups — a constant, not a
distribution. The achievable fill has a **median of 2 ticks** (the assumption is marginally
*conservative* at the middle) and a **mean of 8.3** with a q90 of 18 (it is badly optimistic in the
tail). More than a third of the time the next minute opens *below* the trigger price.

**The error is not stationary in `stop_pct`.** M3's mean fill error by decile runs
+0.06 / +0.05 / −4.61 / −0.61 / +1.30 / −2.35 / +4.97 / −0.97 / **+54.19** / +3.91 ticks. M2's is
monotone and much worse, from +1.2 ticks in D1 to **+61.9 in D9** — waiting for a one-minute *close*
costs most exactly where the bar is largest.

One M2 setup filled **at or below the stop**: the confirming minute's successor opened through the
consolidation low. There is no risk to measure R against, so it is not a trade — it is recorded as
its own not-taken reason rather than folded into "unaffordable". The other 13 never printed a
1-minute close above the trigger before 09:30.

### E2 — 3 mechanics × 4 families (12 trials). J net (gross in brackets), FIT, 266 sessions

| | F1 (0.5 R) | F2 (2 R) | F3 (runner) | F4 (hybrid) |
|---|---|---|---|---|
| **M1** | −0.2981 (−0.171) | −0.2994 (−0.145) | −0.3133 (−0.132) | −0.4532 (−0.286) |
| **M2** | **−0.1945** (−0.079) | −0.2151 (−0.071) | −0.2892 (−0.114) | −0.3531 (−0.194) |
| **M3** | −0.2009 (**−0.058**) | −0.2345 (−0.056) | −0.3076 (−0.085) | −0.4146 (−0.216) |

Throughput 1.000 (M1, M3) and 0.947 (M2) — all inside [0.6, 1.0].

> ⚠️ **The gross/net ordering is not preserved, and that is W2's central execution finding.** On
> gross, **M3 wins every family**. On net, **M2 wins three of four**. M3 fills lower; a lower fill
> against a fixed consolidation-low stop is a *smaller* `stop_pct`; and `c ∝ 1 / stop_pct` then
> charges it more. Its gross/net gap is the widest in every family (0.143 / 0.179 / 0.223 / 0.199 R
> per session against M1's 0.127 / 0.154 / 0.182 / 0.167). **A better fill bought a worse trade.**
> Constraint 6 decides, and it decides against the mechanic with the better entries.

### E3 / E4 — the interiors (12 trials). The search moved F3 nowhere.

F3's best interior is **protocol §10's own default** (arming 1.0 R, trail the prior bar's low) at
−0.3133; both knobs are monotonically worse in every direction (0.5 R: −0.3144 · 1.5 R: −0.3448 ·
2-bar reference: −0.3269). F4 improves monotonically with the scale fraction — ⅓ −0.5056 → ½
−0.4532 → **⅔ −0.4014** — that is, **F4 gets better the more it is made to resemble F2**, which is a
statement about the hybrid, not a parameter to ship. The scale point is worth ~0.01 R either way.

### E5 — selection-dependence (4 trials). The conclusion holds; its magnitude does not.

Stratum boundary: FIT median `stop_pct` of the Filter-A stream = **0.06616**, 133 setups each side.

| | J | gross | gross/net gap | cost/trade |
|---|---|---|---|---|
| `M2×F1` tight (below median) | −0.2480 | −0.0940 | 0.1540 | 0.164 R |
| `M2×F1` wide (at or above) | **−0.1410** | −0.0639 | 0.0771 | 0.081 R |
| `M2×F2` tight | −0.2794 | −0.0888 | 0.1906 | 0.203 R |
| `M2×F2` wide | −0.1508 | −0.0526 | 0.0982 | 0.103 R |

F1 beats F2 in **both** strata, so W2's answer is a single specification rather than a per-decile
one. But the wide-stop half is worth **+0.107 R/session** (F1) and **+0.129** (F2) over the tight
half, and **cost explains about three quarters of that** — the gross difference is only 0.030 and
0.036 R. This is the largest single effect W2 measured, and it is a **selection** finding, so it is
handed on rather than applied (§5 below).

### E7 — the CHECK score (1 trial). The null is not a fitting artefact.

`M2×F1`, scored **once**, as the confirmation of a null and not as a carried candidate (W2-A3):

| | FIT | CHECK |
|---|---|---|
| sessions | 266 | 125 |
| **J** | −0.1945 | **−0.1579** |
| gross | −0.0789 | −0.0520 |
| throughput | 0.947 | 0.928 |
| net hit rate | 0.607 | 0.629 |
| net R/trade (sd) | −0.2053 (0.793) | −0.1701 (0.793) |
| longest losing 20-session run | 12 of 13 | 3 of 6 |
| deepest drawdown | −58.3 R | −23.3 R |
| end equity from $500 | −$1,320.02 | −$94.74 |

Same sign, same magnitude, on an untouched six-month block.

---

## 4. §9.1 — the seven conditions, against the best point

| # | condition | `M2×F1` | |
|---|---|---|---|
| 1 | `p ≤ 0.05` on the FIT null at matched intensity | p = **0.1891** | ✗ |
| 2 | `J > 0` and throughput ∈ [0.6, 1.0] on FIT | J = **−0.1945**; throughput 0.947 | ✗ |
| 3 | `J > 0` on CHECK, same sign | J = **−0.1579** | ✗ |
| 4 | interiors stable across three folds | not evaluated — no candidate to refit (W2-A3) | — |
| 5 | `J > 0` throughout ±20 % sensitivity | vacuous below zero | — |
| 6 | net `J > 0`, gross/net gap per decile | gap reported; net **< 0 everywhere** | ✗ |
| 7 | evaluable on the live half | M2 is recon-only; §7.2.2's cross-check is not executable (W2-A2) | ✗ |

**Four independent failures.** Nothing is frozen.

---

## 5. What is retired, what is handed on, what the next phase should do

**Retired by this workstream:**

- **The 3-tick fill assumption, as a scalar.** It is accurate at the median and optimistic by tens of
  ticks in the wide-stop deciles. Any future simulation that quotes a single fill offset is quoting a
  number that is wrong where it matters most. What replaces it is a `stop_pct`-conditional
  distribution, and E1's table is the first measurement of one.
- **"The account is the binding constraint."** §1.1 kills it. It was the plan's own leading
  hypothesis for the null branch and it does not survive contact with the identity.
- **F4 as a distinct family.** Its interior search says it improves monotonically towards F2. It is
  not a hybrid worth shipping; it is a worse F2.

**Handed on, not applied here:**

> **To a future W1 — the `stop_pct` selection finding.** Restricting selection to wide-stop setups
> is worth **+0.107 to +0.129 R/session**, and roughly three quarters of that is cost, not edge.
> That is a *selection* rule, and W2 is forbidden from applying one (plan §10). It is the single
> most promising lead W2 produced. ⚠️ It is also **not free**: it is a threshold on a feature, so it
> costs trials, and on this record it moves the result from −0.25 to −0.14 — it closes a little over
> half the remaining gap to zero and does not cross it. It should be funded as a selection
> hypothesis, not adopted as a fact.

**What the next phase should not do:** recapitalise. §1.1 shows fifty times the account buys 0.8
points of break-even. If this strategy is to become viable, the change has to be in what is traded
or in the slippage assumption — and the slippage assumption is, per amendment A3, **still
unaudited**: there are no real fills, because Phase 1 places no orders. That is the cheapest
remaining uncertainty in the whole cost model, and it is retired by paper trading, not by analysis.

---

## 6. Limitations, named here rather than discovered by the custodian

1. **M2 and M3 are recon-only, and their transfer to the live half is unestablished.** `bars_1m`
   exists on the recon half alone. Plan §7.2.2's free cross-check — the realised `entry_price` vs
   `entry_fill` gap, live rows against recon — **cannot be run**: `entry_price` is an outcome column
   and is nulled on all 2,455 HOLDOUT rows, and every live row is inside HOLDOUT (amendment W2-A2,
   ledgered before scoring). The conservative branch was therefore taken a priori. Since no
   candidate is frozen, nothing depends on it — but the gap is real and a future pass inherits it.
2. **Slippage is an assumption, not a measurement** (amendment A3). Every net figure here is at
   2 ticks. S0-F's 0 / 2 / 4-tick band brackets it; §1.1's last column shows the conclusion survives
   even at zero.
3. **The per-decile tables are thin** — 16 to 37 trades per decile. They are reported because §6
   requires them and because the *gradient* is monotone and large; individual decile hit rates are
   not estimates to act on.
4. **Filter A's throughput is 1.000 on FIT**, at the very edge of the [0.6, 1.0] band (S0-D). Its
   condition 3 decides *which* setup is earliest, never *whether* a session trades, so W2's results
   describe a system that takes one trade almost every session.
5. **W2 read W1's intermediate results, unintentionally** — listing #735's comments to establish the
   ledger's running state returned every workstream's entries (amendment W2-A1). W2's hypothesis set
   is enumerated literally in the plan and was pre-registered unchanged; W1's numbers are J values of
   *selection predicates*, which are not an input to anything W2 computes. Recorded so the custodian
   can weigh it rather than discover it. ⚠️ **The protocol's design defect this exposes:** it mandates
   one shared ledger *and* forbids cross-reading it. A future pass needs per-workstream ledger issues.

---

## 7. Reproduction

```bash
# panel + bounded tapes from the data-export branch into data/spikes/panel-v1/publish/
cd data/spikes/panel-v1/publish && sha256sum -c panel-v1.sha256    # 8/8 OK, or stop
cd -
.venv/bin/python spikes/analysis_v1/w2_recon.py                    # W2-0a/0b/0c, free
.venv/bin/python spikes/analysis_v1/w2.py e1                       #  3 trials
.venv/bin/python spikes/analysis_v1/w2.py e2                       # 12 trials
.venv/bin/python spikes/analysis_v1/w2.py e3e4                     # 12 trials
.venv/bin/python spikes/analysis_v1/w2.py e5 M2xF1,M2xF2           #  4 trials
.venv/bin/python spikes/analysis_v1/w2.py null                     #  0 trials, B = 200
.venv/bin/python spikes/analysis_v1/w2.py e7 M2xF1                 #  1 trial
.venv/bin/python spikes/analysis_v1/w2.py viability                #  0 trials
```

Outputs land in `data/spikes/w2/` (gitignored). The harness reproduces S0-H's published
`M1 × F1..F4` J — −0.2981 / −0.2994 / −0.3133 / −0.4532 — to the last digit, and E3/E4's protocol
defaults reproduce E2's `M1×F3` and `M1×F4` exactly. Those three checks are the harness's own
verification and a mismatch on any of them is a stop-and-report.
