# Brief: model the book properly — risk, sizing, target, and the equity curve

Third and last of three. `excel-book-brief.md` builds the filter dashboard and scores trades in R.
`excel-fees-brief.md` makes those numbers net of costs. This one turns the result into a faithful
simulation of the account: risk budget, position size, profit target, exits, and a running equity
curve where each trade is sized off what the previous ones left you.

Assume the other two are built. This replaces their simple `R = IF(max_r >= 2, 2, -1)` line with the
real thing.

---

## 1. What the book actually does, per day

For each trading session, in order:

1. Take the candidates that pass the user's filter.
2. Sort them by `trigger_et_min`. Keep the first `MaxPerDay` (default 2). **Everything else is
   dropped as `cap`** — but log it, do not delete it; see §5.
3. Size each survivor off the **day's opening equity**.
4. Walk each to its exit. Apply costs. Add the net to equity.
5. Carry the closing equity into the next session.

Sessions must be processed in date order, and trades within a session in `trigger_et_min` order.
The whole model is path-dependent from step 5 onward, so ordering is not cosmetic.

> ⚠️ **Both of a day's positions size off the day's OPENING equity — not sequentially.**
>
> This is the rule most likely to be got wrong, because compounding *within* a day looks more
> natural. It is wrong: both trades are committed before either one resolves, so neither can be
> sized off the other's outcome. Sizing trade 2 off trade 1's result is a look-ahead that quietly
> inflates the curve.
>
> Equity accrues sequentially only for the running balance. It does not feed sizing until the next
> day opens.

---

## 2. Risk and position size

```excel
riskPerShare = [@entry_fill] - [@stop]
riskQty      = FLOOR(openEquity * RiskFraction / riskPerShare, 1)
capQty       = FLOOR(openEquity * PositionFraction / [@entry_fill], 1)
qty          = MIN(riskQty, capQty)
sizedBy      = IF(capQty < riskQty, "cap", "risk")
```

`RiskFraction = 0.05`, `PositionFraction = 0.50`, `StartEquity = 500` — all user-editable.

`riskUsd = qty * riskPerShare`. Note this is usually **less** than `openEquity * RiskFraction`,
because whole-share rounding and the notional cap both only ever reduce it. Show `riskUsd` and
`riskUsd / openEquity` as columns; the gap between intended and actual risk is a real finding on a
small account.

The cap binds on **tight** stops (crossover at a stop 10% from entry, at these defaults), and it
binds on 64% of this book's trades.

---

## 3. The profit target

`TargetR = 2.0` by default. The target is a **resting limit order** at
`entry + TargetR * riskPerShare`, which has three consequences:

- It fills at exactly the target price even if the bar gapped straight past it. Never credit the
  extra — that is a conservative choice and it should stay.
- It never slips. Only stops and market exits slip.
- A trade is a winner **iff `max_r >= TargetR`**, because `max_r` is the best R the trade ever
  reached. That is the test to use.

Make `TargetR` a control and put a small sweep table beside it, because this is the most
consequential single number on the sheet. Measured on the shipped filter, net of costs:

| target | win rate | gross R | net R | net R / trade |
|---|---|---|---|---|
| 1.0 | 48.0% | −4.0 | −13.3 | −0.133 |
| 1.5 | 42.0% | +5.0 | −4.6 | −0.046 |
| **2.0** | **37.0%** | **+11.0** | **+1.2** | **+0.012** |
| 2.5 | 25.0% | −12.5 | −22.9 | −0.229 |
| 3.0 | 20.0% | −20.0 | −30.9 | −0.309 |
| 4.0 | 11.0% | −45.0 | −56.3 | −0.562 |

2R is a genuine peak and the only net-positive setting. A dashboard that lets the user move this
without showing the curve either side of it is hiding the most important sensitivity in the model.

---

## 4. Exits — and the one thing Excel cannot do

The real simulator walks the trade bar by bar:

- **Stop-first intrabar.** A bar that breaches the stop closes the trade before any favourable move
  on that same bar is credited.
- **Gap-through on the stop.** A bar opening below the stop fills at its open, not the stop.
- **Target as a resting limit**, as above.
- **Breakeven** (optional, ships disabled at `0`): once a bar's high reaches `BreakevenR`, the stop
  moves to entry — armed from the *next* bar, never the same one.
- **Mark to close** if none of the above happened by the last bar.

**The spreadsheet does not have the bars.** It has one row per setup with `max_r`, `mae_r`,
`stopped_out` and `bars_to_max_r`. So the exact walk cannot be reproduced. Use this instead:

```excel
realizedR = IF([@max_r] >= TargetR, TargetR, IF([@stopped_out], -1, "UNKNOWN"))
```

That approximation is **exact on this dataset for any target up to 2.0R** — I checked: at target 1.0
and 2.0, every one of the 100 book trades resolves as either target-hit or stopped, with zero
mark-to-close cases. It stays near-exact at 2.5R (2 unknown of 100) and 3.0R (5 of 100).

So: compute an `UNKNOWN` count, display it prominently, and let the user see it grow as they raise
the target past 2R. Do not silently treat unknowns as zero — exclude them and say how many were
excluded. Above about 3R the approximation stops being trustworthy and the sheet should say so.

**Breakeven cannot be modelled at all from this data** — arming depends on the order bars arrived
in. If the user wants it, that has to come from the source system as a precomputed column.

---

## 5. Five doors, and the invariant that keeps the sheet honest

Every candidate the day was handed must leave through exactly one of:

| door | meaning |
|---|---|
| `taken` | sized to at least one share and traded |
| `cap` | passed the filter but was past the first N by trigger time |
| `unaffordable` | selected, but `qty` rounded to 0 at full risk |
| `throttled` | a risk-ladder rung reduced the risk fraction so it sized to 0 |
| `day_stopped` | the daily loss limit had already been hit |

**`taken + skipped = candidates`, every day, always.** Build that as an assertion row on the sheet.
It matters: in the real system a class of setups once vanished from the book entirely with nothing
on the page explaining where they went, and this invariant is what caught it.

`throttled` and `day_stopped` both ship **disabled** (`DailyLossLimitR = 0`, no ladder), so with
default settings only the first three doors are used. Build the columns anyway — an `unaffordable`
count is genuinely informative at $500, and a user who tightens `RiskFraction` will start seeing
them.

Keep `skipped` trades' outcomes too. "What did the cap cost me" is a question the user will ask
within ten minutes of having this sheet, and it is answered by summing the R of everything dropped
as `cap`.

---

## 6. The equity curve

Per trade, in order:

```excel
grossUsd = qty * (exitPrice - entry_fill)
costUsd  = (from the fees brief)
netUsd   = grossUsd - costUsd - slipUsd
equityAfter = equityBefore + netUsd
```

where `exitPrice` is `entry_fill + TargetR * riskPerShare` on a win and `stop - ExitSlipTicks *
0.01` on a loss.

Then a **Daily** sheet with opening equity, closing equity, trades taken, net R, net dollars. Chart
closing equity against date — that curve is the dashboard's headline output, and it should sit
beside the flat-$500 R curve rather than replacing it.

Report drawdown in **percent of peak equity**, not in R, once compounding is on. They diverge, and
percent is the one that describes the experience.

Give the user a **flat vs compounding** toggle. Flat $500 sizing makes two filters directly
comparable; compounding is what actually happens. Both are worth seeing, and a filter that looks
better only under compounding is usually just one that got lucky early.

---

## 7. Acceptance test

Shipped filter (`passed`, `cons_has_range`, `cycle_num <= 2`, `staleness_delay_min <= 30`,
`entry_fill` 3–50, `trigger_et_min` 240–555, `stop_pct >= 0.025`), 2 per day by trigger time,
target 2R, stop −1R, **flat $500 equity**, 5% risk, 50% cap, costs and 2-tick exit slippage on:

```
candidates passing filter = 102
taken                     = 100      cap = 2      unaffordable = 0      UNKNOWN exits = 0
cap-bound sizing          = 64 of 100
mean quantity             = 29.9 shares
mean risk per trade       = $16.58   (vs a $25.00 budget — rounding and the cap)
gross R = +11.00    net R = +1.20    net R per trade = +0.012
win rate 37.0%      break-even after costs 36.6%
```

---

## 8. Four traps

1. **Do not compound within a day.** Both positions size off the opening equity. See §1.

2. **Do not use `max_r` as the return.** It is the best price the trade ever touched, not what you
   banked. It is only ever the *test* for whether the target was reached.

3. **Do not let the target sweep hide its own uncertainty.** Above 2R the `UNKNOWN` count starts
   climbing, and those are exactly the trades that ran a long way without stopping — the ones a high
   target most depends on. Excluding them silently biases the high-target rows *pessimistically*,
   which happens to flatter the 2R conclusion. Show the count.

4. **Whole-share rounding is not noise at this size.** A mean position of 30 shares means one share
   is 3% of the position. Always `FLOOR`, never round, and never allow a fractional share — the
   result would be an account that cannot exist.
