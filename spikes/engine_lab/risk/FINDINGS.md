# Agent B — risk: how much, on how many, and when to stop

Population: `common.load_panel()` filtered to **DEV+VAL only — 2,989 rows over 166 sessions**.
**HOLDOUT was never loaded.** Selection held at `common.SHIPPED`, with the whole pre-market pool
(`pool`) run alongside everything so no conclusion is conditional on a selection Agent A may
replace. Exits held at `common.fixed_target_r(2.0)`.

The harness is `sim.py`. Its money math is **proved identical to `common.score()`** — 103/103 trades
match to `max_abs_diff = 0.0` under the default config (`python sim.py`). `common.py` was not
forked or edited.

The bar on this working population (shipped rules, 5% / 50% / 2-a-day):
**80 trades, 0.48/session, gross +7.0R, net -1.1R, +$4.02, drag 10.2%, 70% cap-bound.**

---

## 0. Two structural facts that shape every answer below

**(a) The settled-cash invariant is a hard constraint, not a preference.**
`config.py:498` and `decisions.md` (2026-07-15, amended #237) fix this as a **UK cash account**:
`position_fraction x max_trades_per_day <= 1.0`, pinned by `test_settled_cash_invariant`. Recycling
the same cash twice intraday is a good-faith violation. So capacity and the notional cap are **one
decision, not two**: 1/day allows a 100% cap, 2/day allows 50%, 4/day allows 25%. Every capacity
number below is reported at its legal cap as well as at a fixed 50%.

**(b) A recon-vs-live split is impossible outside HOLDOUT.**
`recon` covers 2025-10-30..2026-06-30 and `live` covers 2026-07-01..2026-08-13 — exactly the
DEV+VAL / HOLDOUT boundary. DEV+VAL is **100% recon**. The README's per-source requirement cannot
be met without opening HOLDOUT, so I substituted **DEV vs VAL** and **odd vs even session index**
and report both. This is a limitation of the design, not a choice; the synthesis step should know
it applies to all three agents.

---

## 1. The cost model, derived rather than fitted

For qty < 100 (nearly all trades), with `rps = entry - stop`:

```
cost_usd ~= 0.70                 2 x $0.35 commission minimum   -- FIXED, size-independent
          + qty x 0.0064         exchange + clearing, both sides
          + qty x 0.02           2-tick stop slippage           -- LOSERS only

cost_R    = 0.70 / risk_usd  +  0.0064 / rps  [+ 0.02 / rps on a loser]
```

**Two drags with two different cures, and only one of them is an account-size problem.**

| | driver | cure | dies with a bigger account? |
|---|---|---|---|
| fixed $0.70 | deployed dollar risk | deploy more dollars | **yes** |
| 2-tick slippage | per-share stop distance | a wider dollar stop | **no — it is proportional** |

On the raw pool the split is **8.8% fixed / 19.2% per-share** of R. The brief framed this as a
commission problem; it is mostly a **slippage-on-tight-dollar-stops** problem. The pool's median
per-share stop is **$0.16**, so two ticks is 12.5% of R on every loser before anything else.

The cap crossover falls straight out: `cap_qty < risk_qty <=> stop_pct < risk_fraction /
position_fraction = 10%`. A cap-bound trade deploys `~= 250 x stop_pct` of risk, not the intended
$25 — at the shipped 2.5% stop floor that is **$6.25, so the fixed $0.70 alone is 11% of R**.

**The cost ledger, in dollars, is the single most important number in this study:**

| book | gross | fees | slippage | net | costs as % of gross |
|---|---|---|---|---|---|
| shipped, 2/day cap 50% | **+$107.99** | $72.45 | $31.52 | **+$4.02** | **96%** |
| proposed, 1/day cap 75% | +$213.26 | $65.59 | $34.38 | +$113.30 | 47% |
| pool, 2/day cap 50% | -$1,376.50 | $488.73 | $481.86 | -$2,347.09 | 71% |

---

## 2. Sizing — the notional cap is the distortion, and it is fixable for free

At 5% / 50% the book deploys a mean **$16.14 of the $25.00 it intends to risk (65%)**, on 70% of
trades. The R the system reports and the dollars it earns are therefore measuring different
strategies.

Raising the cap is the cleanest lever available, and at 1 trade/day it is legal all the way to 100%:

| 1/day, notional cap | trades | net $ | drag | cap-bound | walk-forward |
|---|---|---|---|---|---|
| 50% | 66 | +$56.00 | 10.0% | 70% | 4/6 blocks, +$182.48 |
| 60% | 66 | +$86.18 | 9.3% | 61% | 4/6, +$204.09 |
| **75%** | 66 | **+$113.30** | 8.6% | 50% | **5/6, +$223.21** |
| 90% | 66 | +$121.54 | 8.3% | 38% | 5/6, +$225.02 |
| 100% | 66 | +$130.67 | 8.2% | 33% | 5/6, +$228.61 |

A monotone plateau from 75% up, driven by arithmetic (less cap-binding -> less proportional drag)
rather than by a fitted threshold. **I propose 0.75 rather than the optimum 1.00**: it captures 87%
of the benefit, sits mid-plateau instead of on its edge, and leaves a quarter of the account
uncommitted against halt/gap risk, which a stop-based R model does not price at all.

**Raising the cap amplifies whatever edge you have — it does not create one.** On the pool
(negative expectancy) the same change makes things *worse*: -$923.80 at cap 50% -> -$1,249.23 at
cap 75% -> -$1,459.50 at cap 100%. So this recommendation is **conditional on Agent A's selection
having positive expectancy.** If it does not, the 50% cap is a shield, exactly as `decisions.md`
D-37 found on the live month.

`risk_fraction` itself is nearly inert: 3% -> 20% at cap 50% gives **identical gross R** (R is
size-independent) and net dollars that wander in a $60 band with no trend, because past ~8% the cap
binds on everything. Below 5% it just shrinks the account. **Leave it at 5%.**

---

## 3. Cost drag as a selection input

**The rule: skip a trade whose round-trip cost, assuming it loses, exceeds X% of the money it puts
at risk.** `RiskConfig.worst_case_cost_r(entry, stop, qty)` — deterministic at entry, reads no
outcome, needs no assumed win rate, one threshold.

Plain translation at $500: **`cost <= 10%` is approximately "the stop must be at least 35-40c per
share below the entry"** (82% agreement on shipped candidates, 92% on the pool). Kept candidates
have a median per-share stop of $1.21 on a $10.96 name; excluded ones a median $0.275 stop on a
$6.57 name at 38 shares and $9.04 of deployed risk.

Sorted into cost bands, with capacity off so every candidate is measured:

**pool (n=2,989):**

| worst-case cost | n | gross R | drag | net R |
|---|---|---|---|---|
| <=10% | 518 | -0.160 | 6% | -0.220 |
| 10-15% | 350 | -0.109 | 11% | -0.214 |
| 15-25% | 617 | -0.173 | 17% | -0.343 |
| 25-40% | 640 | -0.212 | 28% | -0.491 |
| >40% | 864 | -0.441 | 56% | **-1.005** |

Gross R is roughly flat to 40% and then collapses. **So the exclusion is roughly half cost-saving
and half selection**: removing the >40% band buys 0.441R of gross *and* 0.564R of avoided cost.
The selection half is Agent A's territory and I do not claim it.

**How much it is worth depends entirely on the selection, and that is the headline caveat:**

| | shipped selection | raw pool |
|---|---|---|
| drag without the rule (1/day cap 75%) | 8.6% | 20.7% |
| drag with `cost <= 10%` | 5.3% | 5.9% |
| net $ change | +$113.30 -> +$45.54 (**worse**) | -$1,249 -> -$873 (**+$376**) |
| walk-forward | 5/6 -> 4/6 blocks, +$223 -> +$163 | 0/6 -> 1/6, -$1,174 -> -$539 |

**Verdict: the cost rule is a guardrail, not an alpha source.** The shipped selection's $3 price
floor and 2.5% stop floor already remove most high-drag names, so on the current selection the rule
costs about $70 and a third of the trade count. On anything looser it is worth several hundred
dollars over 166 sessions and is the difference between a 24% drag and a 6% one.
**If Agent A loosens the price floor or the stop-width floor, this rule becomes mandatory.**

The 24 shipped candidates the rule removes are **gross +6.0R but net -$12.72, having paid $61.08 in
costs** — i.e. the rule is doing exactly what it says: it removes a group whose R is fine and whose
dollars are not. It is the *substitution* that costs money (below), not the removal.

**At 1 slot a day a filter substitutes rather than removes.** Skipping the first trigger frees
the slot for the next one, which can be worse. That is why the guardrail's effect is non-monotone at
1/day and cleaner at 2/day, and it is a mechanism worth carrying into the synthesis.

---

## 4. Capacity — 1 a day, and the reason is mechanical

| capacity (at its settled-cash-legal cap) | shipped: trades / net $ | pool: trades / net $ |
|---|---|---|
| 1/day, cap 100% | 66 / **+$130.67** | 166 / -$1,459.50 |
| 2/day, cap 50% (shipped) | 80 / +$4.02 | 332 / -$2,347.09 |
| 3/day, cap 33% | 82 / -$41.69 | 498 / -$2,397.65 |
| 4/day, cap 25% | 82 / -$67.59 | 664 / -$2,602.84 |
| 6/day, cap 17% | 82 / -$78.74 | 996 / -$3,224.74 |
| unlimited | not available on a cash account at any cap | |

Monotone down from 1 on both selections. **3, 4 and unlimited are rejected outright.**

**But be careful about *why* 1 beats 2.** Slot quality (capacity off, ordered by time, never ranked):

| slot | shipped n / gross R | pool n / gross R |
|---|---|---|
| 1st trigger of the day | 66 / +0.182 | 166 / -0.151 |
| 2nd | 14 / -0.357 | 166 / -0.386 |
| 3rd | 2 / -1.000 | 166 / -0.241 |
| 4th | — | 166 / -0.277 |
| 5th+ | — | 2,325 / -0.245 |

On the shipped selection the "second trades are bad" claim rests on **14 trades**. On the pool,
slot 1 is better than the rest by 0.09R against a per-trade standard error near 0.11 — suggestive,
not established, and slot 2 rather than slot 3+ is the worst, which is not a clean gradient.

**So the defensible reason to run 1 a day is not that later triggers are worse — it is that one
slot lets the notional cap rise to 75-100% inside the settled-cash invariant, so the intended 5%
risk actually gets deployed.** Holding the cap at 50%, 1/day still beats 2/day (+$56.00 vs +$4.02)
but on evidence I would not defend.

**`one_per_symbol`: reject.** The book took the same ticker twice on **1 day** (shipped) and **3
days** (pool). Shipped: +$4.02 -> -$13.04. Pool: -$2,347 -> -$2,282. No measurable effect either way;
adding a rule that fires three times in 166 sessions is not worth the complexity.

---

## 5. Loss limits and streaks — reject both

**Daily stop.** Implemented honestly: a loss only counts once its stop has actually been hit, with
exit times read from the bars (`load_work()` adds `exit_et_min`). Trade 1's outcome is known before
trade 2 triggers on only **50% of shipped days (7 of 14)** and **32% of pool days (53 of 166)** — so
most of the time the rule cannot fire at all.

- shipped, stop after 1 resolved loser: +$4.02 -> +$65.45, but it touches **7 trades**.
- pool, same rule: -$2,347 -> -$2,048 in total, but net R **per trade gets worse** (-0.510 -> -0.525).

That is the signature of *fewer trades*, not a better book. And at 1 trade/day it is a **no-op by
construction**. Reject.

**Risk ladder.** A ladder cannot change R expectancy at all — it only reweights the dollars on
trades you were going to take anyway. On a book whose expectancy is near zero, that is noise, and
here it is negative noise: streak>=2 -> half risk gives +$4.02 -> -$19.94 on shipped and no per-trade
improvement on the pool. The "stand down" variants cut the book to 7-8 trades over 166 sessions and
still lose money. Reject.

---

## 6. Is $500 enough?

**PROPOSED config (1/day, cap 75%, risk 5%), shipped selection, DEV+VAL, by account size:**

| equity | net R / trade | drag | net $ | return on equity |
|---|---|---|---|---|
| $250 | +0.054 | 12.7% | +$34.64 | +13.9% |
| **$500** | **+0.096** | **8.6%** | **+$113.30** | **+22.7%** |
| $1,000 | +0.111 | 7.0% | +$255.31 | +25.5% |
| $2,000 | +0.116 | 6.6% | +$537.35 | +26.9% |
| $5,000 | +0.118 | 6.4% | +$1,364.23 | +27.3% |
| infinite | +0.118 | **6.4%** | — | +27.4% |

- **$500 costs about a fifth of the achievable edge** (+0.096 vs the +0.118 plateau). $1,000
  recovers 94% of it; **$2,000 recovers 98%**. Above $2,000 nothing improves.
- **The drag floor is 6.4%, not zero.** The fixed commission dies; the 2-tick slippage never does.
  Against a gross edge of +0.182R/trade on this book, **costs take a third of the edge at any
  account size**.
- On the *shipped* risk config (2/day, cap 50%) the picture is far worse: gross edge +0.087R/trade
  against a 6.3% floor, so the best net achievable **at infinite equity is +0.025R/trade**.

**So: $500 is a genuine handicap but it is not the binding constraint.** The binding constraint is
that the gross edge is small enough that costs consume a third of it at any size. Fixing the account
size fixes the fixed-commission half of the drag and nothing else. If the trader wants the account
size to stop mattering, **$2,000 is the number** — but that changes +0.096R/trade into +0.118R/trade
and nothing about whether the strategy works.

**What it feels like at $500, compounded on day-open equity (no intraday lookahead):**

| config | $500 -> | max drawdown | trades |
|---|---|---|---|
| shipped 2/day cap 50% | **$435.01 (-13.0%)** | 41% | 80 |
| PROPOSED 1/day cap 75% | $551.52 (+10.3%) | 32% | 66 |
| GUARDED 1/day cap 75% + cost<=10% | $556.68 (+11.3%) | 25% | 39 |
| TWO_SLOT 2/day cap 50% + cost<=10% | $499.65 (-0.1%) | 30% | 38 |

Eight months of work for +10%, through a 32% drawdown. That is the honest shape of it.

---

## 7. The proposal, and the anti-overfit evidence

```
risk_fraction      0.05    unchanged, user-fixed, not a free parameter
max_per_day        1       was 2                                     <- fitted (1 threshold)
position_fraction  0.75    was 0.50; ceiling is 1/max_per_day = 1.00  <- determined, mid-plateau
max_cost_r         0.10    OPTIONAL guardrail                         <- 1 threshold, conditional
daily_loss_limit   none    rejected
ladder             none    rejected
one_per_symbol     false   rejected (unchanged from the real system)
```

**Complexity budget: 1 genuinely free threshold (capacity), +1 if the guardrail is adopted.**
`position_fraction` is not free — the settled-cash invariant pins it to `1/max_per_day`, and 0.75
was chosen for the concentration buffer, not for the number. Well inside the README's budget of 5.

**Scorecards (DEV+VAL, shipped selection, 166 sessions):**

| | trades | /session | gross R | net R | net R/trade | net $ | drag | cap-bound | max dd |
|---|---|---|---|---|---|---|---|---|---|
| baseline 2/day cap50 | 80 | 0.48 | +7.0 | -1.1 | -0.014 | +$4.02 | 10.2% | 70% | -12.6R |
| **PROPOSED** | 66 | 0.40 | +12.0 | **+6.3** | **+0.096** | **+$113.30** | 8.6% | 50% | -7.5R |
| GUARDED (+cost<=10%) | 42 | 0.25 | +3.0 | +0.8 | +0.018 | +$45.54 | 5.3% | 31% | -8.7R |
| TWO_SLOT (2/day + cost<=10%) | 44 | 0.27 | +10.0 | +7.6 | +0.172 | +$97.61 | 5.5% | 46% | -7.6R |

**Walk-forward** (expanding window, 6 blocks; no threshold is fitted in the PROPOSED config, so
this is a pure out-of-block evaluation):

| config | shipped | pool |
|---|---|---|
| baseline 2/day cap50 | 4/6 blocks +$, **+$165.16**, +4.26R | 0/6, -$1,896.18 |
| **PROPOSED 1/day cap75** | **5/6 blocks +$, +$223.21, +7.99R** | 0/6, -$1,174.06 |
| GUARDED | 4/6, +$163.04, +6.60R | **1/6, -$539.02** |
| TWO_SLOT | 4/6, +$223.47, **+12.35R** | 1/6, -$662.73 |

**Sensitivity.** The notional cap is a plateau: 75/90/100% all give 5/6 blocks and
+$223...+$229. `risk_fraction` +/-20% moves nothing. The **`max_cost_r` threshold is *not* on a
plateau on the shipped selection** — +/-20/40% gives +$41 / +$171 / +$65 / +$39 / +$67 at
0.06/0.08/0.10/0.12/0.14, which is noise on 27-52 trades. It *is* plateau-like on the pool
(-$1,164 / -$782 / -$831 / -$1,164 around 0.10). This is why it is a guardrail whose level is
justified by arithmetic, not a fitted edge.

**Permutation** (200 random books, same per-day trade count, same days, drawn from the same day's
eligible pool):

| config | observed net $ | random books beat it |
|---|---|---|
| baseline | +$4.02 | 13.9% |
| PROPOSED | +$113.30 | **5.5%** |
| GUARDED | +$45.54 | 5.5% |
| TWO_SLOT | +$97.61 | 9.0% |

Better than the baseline, but 5.5% on 66 trades is not strong evidence.

**Split stability — and this is the part to take seriously:**

| config | DEV | VAL | odd sessions | even sessions | positive |
|---|---|---|---|---|---|
| baseline | -$204.95 | +$208.97 | -$172.25 | +$176.27 | 2/4 |
| PROPOSED | -$56.55 | +$169.85 | -$190.97 | +$304.27 | 2/4 |
| GUARDED | -$85.83 | +$131.37 | -$42.57 | +$88.12 | 2/4 |
| TWO_SLOT | -$134.08 | +$231.69 | +$34.17 | +$63.44 | 3/4 |

**Every configuration is DEV-negative and VAL-positive, and every one except TWO_SLOT flips sign
between odd and even sessions.** No risk setting makes DEV profitable; the best any of them does is
take DEV from -$205 to -$57. The whole positive result lives in the 41 VAL sessions, and an
arbitrary alternating-session split of the same book is a coin toss. **On the current selection,
the risk configuration is a real improvement in cost efficiency and is not evidence of an edge.**

---

## 8. Cross-dependencies for the synthesis

1. **The cost guardrail's value is entirely conditional on Agent A.** On the shipped selection it
   costs ~$70 and a third of the trades; on the raw pool it is worth ~$376-650. Any loosening of
   the price floor or the stop-width floor makes it mandatory. Wire it in either way — it is cheap
   insurance and it fails safe.
2. **Raising the notional cap is a bet on positive expectancy.** It multiplies the deployed dollars
   on the same trades. Positive selection -> +$75 on this record; negative selection (the pool) ->
   -$536. **If Agent A's rules do not clear positive net R on their own, keep the cap at 50%.**
3. **The entire cost picture is re-priced by Agent C's exit.** The 2-tick slip is charged once per
   loser, so a wider stop cuts the slip's share of R proportionally and a target that resolves more
   often multiplies the fixed $0.70. Every drag number here is stated against a 2R / -1R bracket.
4. **At 1 slot a day, filters substitute rather than remove** — the skipped name frees the slot for
   the next trigger, which may be worse. Any rule Agent A adds interacts with capacity this way.
5. **No per-source validation is possible outside HOLDOUT** (see section 0b). This applies to all
   three agents.

## Files

    sim.py       the simulator (verified identical to common.score()) + RiskConfig + load_work()
    drag.py      cost-drag anatomy: the algebra and the banded tables
    study.py     sections: base / exclude / sizing / capacity / limits / equity / robust
    combo.py     slot quality, the drag floor, combined configs, the account-size curve
    verdict.py   stability, walk-forward, cost ledger, and writes result.json

    python sim.py            # verification + baselines
    python drag.py           # cost anatomy
    python study.py capacity # any single section
    python verdict.py        # the final tables and data/spikes/engine-lab/risk/result.json
