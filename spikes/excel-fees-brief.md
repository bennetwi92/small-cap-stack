# Brief: add trading costs to the filter dashboard

Companion to `excel-book-brief.md`. That one builds the dashboard and scores trades in **R**
(multiples of risk). This one makes those numbers net of what trading actually costs.

Read this first, because it determines the whole structure:

> **You cannot express fees in R without sizing the position.**
>
> R is a ratio. Fees are dollars per share and dollars per order. To convert one into the other you
> need a share count, and to get a share count you need an account size. So adding costs is not a
> single extra column — it drags in the position-sizing model whether you wanted it or not.

That is why the sizing section below is mandatory rather than optional, and why it comes first.

---

## 1. Why this matters more than it usually does

This is a **$500 account** taking positions of ~30 shares. Commission has a **$0.35 per-side
minimum**, so a small trade pays the same $0.70 round trip as a much larger one. Measured against
risk, the drag is not a rounding error:

- Mean all-in cost is **$0.90 per trade** against a mean risk of **$16.58**
- That is **6.9% of R gone per trade**, before any slippage
- Add exit slippage and the total drag is about **10% of R per trade**

On the real system's own rules that turns **+11.0R gross into +1.2R net**. The strategy goes from
modestly profitable to roughly break-even. Any dashboard that reports gross R only is telling the
user something that is not true about their money.

So: build gross and net side by side, always both visible. Never replace gross with net — the gap
between them is itself the finding.

---

## 2. Position sizing (required)

Per trade, given the running `equity`:

```excel
riskPerShare = entry_fill - stop
riskQty      = FLOOR(equity * RiskFraction / riskPerShare, 1)
capQty       = FLOOR(equity * PositionFraction / entry_fill, 1)
qty          = MIN(riskQty, capQty)
```

Defaults, all user-editable: `StartEquity = 500`, `RiskFraction = 0.05`, `PositionFraction = 0.50`.

A trade where `qty < 1` is **unaffordable** — drop it and flag it. Track those; on a small account
they are a real constraint, not an edge case.

⚠️ **Which limit binds is the opposite of the intuition.** The notional cap binds on **tight**
stops, not wide ones. At 5%/50% the crossover is a stop 10% from entry: anything tighter is
cap-bound. On this dataset **64 of 100 trades are cap-bound**. A cap-bound trade risks *less* than
the intended 5%, so it also returns less in dollars than its R implies — which is precisely the
distortion that makes R and dollars disagree.

Add a `sized_by` column reading `cap` or `risk`. The user will want to see it.

---

## 3. The cost model

IBKR tiered pricing. Both sides pay commission, exchange removal and clearing; only the sell pays
TAF and SEC.

| component | rate | applies |
|---|---|---|
| Commission | `MAX(0.35, qty * 0.0035)` | **each side** — so ×2 |
| Exchange removal | `qty * 0.003` | each side |
| Clearing | `qty * 0.0002` | each side |
| FINRA TAF | `MIN(qty * 0.000166, 8.30)` | sell only |
| SEC Section 31 | `qty * exitPrice * 0.0000278` | sell only |

As one Excel expression:

```excel
commission = 2 * MAX(CommMin, [@qty] * CommPerShare)
perShare   = 2 * [@qty] * (ExchangeFee + ClearingFee)
taf        = MIN([@qty] * TafPerShare, TafMax)
sec        = [@qty] * [@exitPrice] * SecRate
costUsd    = commission + perShare + taf + sec
```

Put all seven rates on the Controls sheet as named cells. They change, and the user will want to ask
what a different broker looks like.

**Always liquidity-removing.** Entries are stop-triggered and exits are stops or market orders, so
no add-liquidity rebate is ever earned. Do not model one.

**One known omission:** IBKR caps tiered commission at 1% of trade value, and the model above does
not. On cheap names that cap can bind — 100 shares of a $1.20 stock is $1.20 of value against a
$1.00 commission minimum. Implementing it means `MIN(commission, 0.01 * qty * price)` per side. Add
it if you like, but flag it as a change from the reference numbers in §6.

---

## 4. Exit slippage

Separate from fees and roughly a third as large again. Model it as ticks against you on the exit:

- **Losing trades** exit on a stop: `ExitSlippageTicks = 2` (i.e. $0.02/share worse than the stop)
- **Winning trades** exit on a limit at the target: **0 ticks**

That asymmetry is the point — you get filled at your limit when you win and slip through your stop
when you lose. A model that slips both sides equally understates the damage.

```excel
slipUsd = IF([@Taken]=0, "", IF([@max_r]>=TargetR, 0, [@qty] * ExitSlipTicks * TickSize))
```

with `TickSize = 0.01`. Mean slippage on this dataset is **2.9% of R**, but it is skewed — the worst
trade loses 25% of its R to slippage alone, because a tight stop and a fixed 2-tick slip is a
brutal combination. Show the distribution, not just the mean.

---

## 5. Putting it together

Per trade:

```excel
riskUsd   = [@qty] * ([@entry_fill] - [@stop])
grossUsd  = IF([@max_r] >= TargetR, TargetR * riskUsd, -riskUsd)
netUsd    = grossUsd - [@costUsd] - [@slipUsd]
grossR    = IF([@max_r] >= TargetR, TargetR, StopR)
netR      = netUsd / riskUsd
```

`netR` is the column the dashboard should headline. It is directly comparable to the gross R the
other brief produces, so the user can see the drag as a number rather than infer it.

On the **Summary** sheet, show gross and net as two columns for: total R, R per trade, win rate,
and the break-even win rate.

**Break-even win rate is the metric costs actually move**, and it is the one to make prominent:

```excel
breakevenWinRate = (1 + meanCostR + meanSlipR) / (TargetR + 1)
```

At a 2R target that is 33.3% with no costs and **36.6%** with them, on this data. The book's actual
win rate is 37.0%. The entire margin of profitability is 0.4 points wide. A user who cannot see that
will happily "improve" a filter into a loss.

### Compounding

If you run the equity curve rather than a flat $500, size each trade off the equity *before* it and
apply `netUsd` after. Process trades in date then `trigger_et_min` order. Note that this makes
sizing path-dependent, so the sheet needs a running equity column and the order must be stable.

Offer a **flat vs compounding** toggle. Flat is easier to reason about when comparing two filters;
compounding is what actually happens.

---

## 6. Acceptance test

Using the real system's own rules — the configuration B from the companion brief: `passed`,
`cons_has_range`, `cycle_num <= 2`, `staleness_delay_min <= 30`, `entry_fill` 3–50,
`trigger_et_min` 240–555, `stop_pct >= 0.025`, 2 trades/day by trigger time, target 2R / stop −1R —
at a **flat $500 equity**, 5% risk, 50% notional cap:

```
trades sized          = 100      (0 unaffordable)
cap-bound             = 64 of 100
mean quantity         = 29.9 shares
mean risk per trade   = $16.58
mean all-in cost      = $0.90 per trade
median cost as R      = 0.057
mean cost as R        = 0.069
mean slippage as R    = 0.029

gross R = +11.00   ->   net R = +1.20
break-even win rate: 33.3% gross  ->  36.6% net   (actual win rate 37.0%)
```

If your sheet reproduces those, the cost model is right.

---

## 7. Three traps

1. **Do not apply costs to gross R directly.** `netR = grossR - 0.069` is wrong — the drag varies
   from 3% to 19% of R across trades, driven by share count and stop width. It has to be computed
   per trade from the actual quantity. A flat haircut will flatter tight-stop trades and punish wide
   ones, which is backwards.

2. **The minimum commission makes small positions disproportionately expensive.** As the user
   tightens filters and trade count falls, cost *per trade* does not fall with it. Watch for a
   filter that looks better gross and worse net — that is the interesting case, and the dashboard
   exists to surface it.

3. **Costs make filters harder to beat, not easier.** Every rule the user tries now has to clear
   36.6% rather than 33.3%. Put the break-even line on the Summary sheet next to the actual win
   rate so the comparison is one glance, not arithmetic.
