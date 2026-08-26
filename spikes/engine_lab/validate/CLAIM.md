# The claim under test

Three agents test **one claim**, independently, by different methods, and each returns its own
verdict. The point is triangulation, not division of labour: if the claim is real, three different
attacks should converge on it; if it is an artefact, at least one should break it.

**Nothing below is established.** It is a claim with some supporting numbers attached, produced in
one exploratory session. Every number in this file was computed by a single unreviewed script and
**must be re-derived from the raw panel before it is relied on**. If your re-derivation disagrees
with a number here, your number wins and the disagreement is a finding — say so loudly.

---

## The rule

```
SELECTION  = the shipped rules  (common.SHIPPED: passed, cycle_num<=2, staleness<=30,
                                 entry_fill in [3,50], stop_pct>=0.025, trigger in [240,555])
IN PLAY    = runup_pre_appearance >= 0.15     # the stock moved 15%+ before it hit the scanner
             AND rvol_pole        >= 2.0      # pole volume vs its own baseline
             AND shares_outstanding <= 50e6   # a small company
BOOK       = both of the above, 2 per day, earliest trigger first
EXIT       = 2R target / -1R stop, off the shipped consolidation-low stop
MONEY      = $500, 5% risk, 50% notional cap, IBKR costs (common.Sizing / common.Costs)
```

## What was observed (UNVERIFIED — re-derive it)

| | trades | /session | net R | net R/trade |
|---|---|---|---|---|
| shipped only | 122 | 0.62 | +7.1 | +0.058 |
| in play only, no shape gates | 242 | 1.23 | −20.3 | −0.084 |
| **shipped + in play** | **35** | **0.18** | **+16.7** | **+0.478** |

Per period: dev +4.8R, val +10.6R, holdout +1.3R. Claimed error bar +0.50 ± 0.43R/trade.

Supporting observations, all equally unverified:

- The **intermediate** signal is on much larger samples than the book: the rate of setups that move
  50%+ after entry roughly doubles under the in-play filter, in **all three** periods
  (dev 4.5%→8.1% on 433 rows, val 9.4%→14.1% on 263, holdout 5.2%→7.8% on 230).
- Both stock features are **monotone across the whole distribution** against a 50%+ move:
  `runup_pre_appearance` 1.8%→13.7% across quintiles, `shares_outstanding` 8.2%→1.5% (inverse).
- **Sensitivity looked like a plateau**: moving any one threshold ±(a lot) kept net R/trade between
  +0.25 and +0.63, with no sign flip.
- `rvol_pole` may be doing **no work** — 1.0 and 5.0 gave nearly identical results — but dropping it
  entirely moved the holdout from +1.3R to −0.3R and made the val period carry everything
  (11 trades, 91% win rate).
- Stop placement was tested on the in-play subset: tighter than the consolidation low is
  catastrophic (−1.01R/trade at 0.4x the range), the consolidation low (1.0x) is the peak.

## Three reasons to be suspicious before you start

1. **35 trades.** Everything downstream of the book is a 35-sample statement.
2. **⚠️ The holdout is spent.** The live period (2026-07-01 → 08-13) was queried many times during
   the exploratory session that produced this rule. It is **no longer an out-of-sample test** and a
   verdict resting on it is worthless. The burden falls on walk-forward and on null tests.
3. **The thresholds were chosen after looking at the quintile table** for these same features on
   this same data. That is selection, however round the numbers look.

## The verdict every agent must return

Same scale, so three answers can be compared:

- `REAL` — the effect survives your attack; you would act on it
- `PROMISING` — survives, but the evidence cannot distinguish it from luck at this sample size
- `ARTEFACT` — you broke it; here is what it was really measuring

Plus: your confidence, **the single strongest piece of evidence FOR**, **the single strongest
piece AGAINST**, and what would change your mind. If your methods disagree with each other
internally, report that rather than averaging it away.
