# Brief: build a filter dashboard over a trading-opportunity dataset

You are building an Excel dashboard on top of a flat table of trading setups. The user wants to
change filter thresholds and immediately see what that does to returns over the whole period.

Everything below is what you need; you do not need to know anything else about the trading system.

---

## 1. The data

One file, one sheet, one row per **setup** (a potential trade). 5,024 rows, 68 columns, covering 197
trading sessions from 2025-10-30 to 2026-08-13. Load it as an Excel Table named `tOpp`.

The columns that matter. Everything else is extra features to filter on.

| column | meaning |
|---|---|
| `dt` | trading session (date) |
| `source` | `live` or `recon` — two halves of the record; see trap 5 |
| `symbol` | ticker |
| `trigger_et_min` | **when the trade would have been entered**, as minutes past midnight US/Eastern. 570 = 09:30. This is the ordering key for everything. |
| `first_hit_et_min` | when the setup first appeared on the scanner, same units |
| `entry_fill` | the planned entry price |
| `stop` | the planned stop price |
| `stop_pct` | risk as a fraction of entry, i.e. `(entry_fill - stop) / entry_fill` |
| `max_r` | **the outcome.** How far the trade went in your favour before hitting the stop, in multiples of the risk taken. 2.0 means it made twice what it risked. |
| `stopped_out` | TRUE if the stop was hit |
| `passed` | TRUE if the setup was well-formed (a shape check) |
| `cons_has_range` | FALSE means the stop is meaningless for this row — see trap 3 |

Filterable features include: `pole_len`, `cons_len`, `retracement`, `score`, `cycle_num`,
`staleness_delay_min`, `float_shares`, `shares_outstanding`, `day_dollar_volume`,
`hits_before_trigger`, `ext_at_trigger`, `rvol_pole`, `vol_share_pole`, `runup_pre_appearance`.

---

## 2. What "the book" is

Filtering alone does not give you returns, because **you cannot take every setup that passes a
filter**. There is a capacity limit. The book is the three-step chain from "setups that pass my
filter" to "R I would actually have made".

### Step 1 — Selection

A row passes if it satisfies every threshold the user has set. Blank threshold = no limit.

### Step 2 — Capacity

Of the rows that pass on a given day, **only the first N are taken**, where N defaults to 2.
"First" means **earliest `trigger_et_min`**.

> ⚠️ **Order by time, never by `max_r` or `score` or any ranking.**
>
> This is the single most important rule in this brief. Picking "the best 2 of the day" requires
> knowing how the day turned out, which you do not know at 07:00 when the first setup fires. Sorting
> by outcome produces a spectacular, completely fake equity curve. Sorting by `score` is subtler but
> the same mistake — you cannot rank a day's setups against each other until the day has produced
> them all.
>
> A filter is decidable at the moment of entry. A ranking is not. Only build the filter.

Optionally also enforce **one entry per symbol per day** (take only that symbol's earliest passing
setup). Default this **off** — the real system has no such rule and will take the same ticker twice.
Make it a toggle so the user can ask whether it should.

### Step 3 — Outcome

Each taken trade returns:

```
R = IF(max_r >= TargetR, TargetR, StopR)
```

with `TargetR = 2.0` and `StopR = -1.0` as user-editable defaults.

Read this carefully: you are **not** summing `max_r`. `max_r` is the best price the trade ever
reached, and nobody sells the exact high. The model is a fixed profit target with a stop: if the
move got to the target you banked the target, otherwise you lost your risk. That is the only
bankable reading of this data. Summing `max_r` will overstate returns by roughly double.

---

## 3. Implementation

Put every threshold on a **Controls** sheet as a named cell — `MinStopPct`, `MaxEntry`, `MaxPerDay`,
`TargetR`, `StopR`, and so on — so the formulas read as English and the user has one place to work.
Blank must mean "no limit".

On the data sheet, add these computed columns in order. These are tested and work as written.

**`Pass`** — 1 if the row satisfies every control. Build it as one `AND(...)` with a pair of terms
per numeric threshold, each blank-tolerant:

```excel
=IF(AND(
    OR(MinStopPct="", [@stop_pct]>=MinStopPct),
    OR(MaxStopPct="", [@stop_pct]<=MaxStopPct),
    ... one pair per filterable column ...
  ),1,0)
```

Column names in every formula here are the file's own headers verbatim (`stop_pct`, `max_r`,
`trigger_et_min`, …). Do not rename the headers to something prettier — or if you do, rename them
everywhere in one pass, because a structured reference to a header that no longer exists gives
`#REF!` in one cell and Excel will open the file without complaint.

Use 1/0, not TRUE/FALSE — `COUNTIFS` criteria are far less fiddly against numbers.

**`FirstForSymbol`** — only needed if the one-per-symbol toggle is on:

```excel
=IF([@Pass]=0,0,IF(COUNTIFS(tOpp[dt],[@dt],tOpp[symbol],[@symbol],
   tOpp[Pass],1,tOpp[trigger_et_min],"<"&[@trigger_et_min])=0,1,0))
```

**`Cand`** — a row eligible for a slot:

```excel
=IF([@Pass]=0,0,IF(OnePerSymbol<>"Yes",1,[@FirstForSymbol]))
```

**`Seq`** — its position within the day, in trigger-time order:

```excel
=IF([@Cand]=0,"",COUNTIFS(tOpp[dt],[@dt],tOpp[Cand],1,
   tOpp[trigger_et_min],"<"&[@trigger_et_min])+1)
```

**`Taken`** — inside the cap:

```excel
=IF(AND([@Cand]=1,[@Seq]<=MaxPerDay),1,0)
```

**`R`**:

```excel
=IF([@Taken]=0,"",IF([@max_r]>=TargetR,TargetR,StopR))
```

Guard each `COUNTIFS` behind the cheap column before it, exactly as shown — most rows fail the
filter, and skipping the full-table scan on those keeps recalculation quick. Without the guards the
sheet does ~75 million comparisons on every keystroke.

### Daily sheet

One row per `dt`, all 197 sessions pre-listed. `SUMIFS`/`COUNTIFS` against the data table:

- `Taken` — `COUNTIFS(tOpp[dt],$A2,tOpp[Taken],1)`
- `R` — `SUMIFS(tOpp[R],tOpp[dt],$A2,tOpp[Taken],1)`
- `Cum R` — running total of the previous column
- `Peak` — `MAX(previous peak, this Cum R)`
- `Drawdown` — `Cum R - Peak`

Chart `Cum R` against `dt`. That curve is the dashboard's main output.

> Derive your column letters from position in code rather than typing them. Hand-indexing these is
> how you end up with the win rate dividing by the wrong column — the file opens perfectly and
> quietly reports wrong numbers.

### Summary sheet

Trades, total R, **R per trade** (the expectancy — this is the number that matters, break-even is
0), R per session, win rate, stop rate, max drawdown.

At a 2R target, **break-even win rate is 33.3%** before costs. Put that on the sheet as a reference
line, because a filter showing 30% looks decent and is actually losing money.

---

## 4. Five traps that will silently produce wrong numbers

1. **Every row in the file already triggered.** Setups that formed and never fired are not present.
   So a win rate here is per *entry taken*, never per *setup spotted*. Do not describe it as the
   latter.

2. **`passed` is a shape check, not a trade decision.** Only 419 of 5,024 rows have `passed = TRUE`.
   The other 92% are malformed patterns, not trades that were declined. Default the `passed` filter
   **on**, or every statistic will be diluted by noise. Make it a toggle, not a hard-coded filter.

3. **`cons_has_range = FALSE` rows have a fake stop.** For those the entry and stop collapse to the
   same price, so `stop_pct` is an artifact of rounding and `max_r` is meaningless. Default them
   **excluded**.

4. **A blank value must fail a threshold it cannot be checked against.** If the user filters float
   below 20M, a row with no float is not *known* to be under 20M. Write the numeric terms so a blank
   cell fails, and offer one global `Keep rows with unknown values` toggle. This matters a lot:
   `float_shares` is empty on all 3,272 `recon` rows by design.

5. **The two `source` halves are not interchangeable.** `recon` is 166 older sessions rebuilt from
   historical data; `live` is 31 recent sessions recorded as they happened. Give the user a
   `both / live / recon` selector, because a rule that only works on one half is not a rule — it is
   a coincidence. This is the single most useful control on the sheet.

---

## 5. Acceptance test — reproduce these exactly

Two configurations with known answers. If your sheet disagrees, a formula is wrong.

**A. Loose filter** — pre-market only (`first_hit_et_min < 555`), `passed = TRUE`,
`cons_has_range = TRUE`, no other threshold, 2 per day, no symbol dedup, target 2R / stop −1R:

```
trades = 215    total R = -59.00    R per trade = -0.274    sessions traded = 138    win rate = 24.2%
```

**B. The real system's own rules** — as A but adding `cycle_num <= 2`,
`staleness_delay_min <= 30`, `entry_fill` between 3 and 50, `trigger_et_min` between 240 and 555,
`stop_pct >= 0.025`:

```
rows passing = 102
trades = 100    total R = +11.00    R per trade = +0.110    sessions traded = 82    win rate = 37.0%
```

Configuration B is the **baseline any new filter has to beat**. Consider putting it on the Summary
sheet as a fixed second column, computed the same way, so every experiment is scored against
something rather than against zero. If you do, hard-code B's thresholds — it must not move when the
user moves a control.

Note what A vs B shows: loosening the rules more than triples the trade count and turns +11R into
−59R. Filters are doing real work here, and the dashboard's job is to let the user find better ones.

---

## 6. Optional extension: dollars instead of R

Only if asked. R is the honest unit; dollars need a position-sizing model:

- Start equity $500, compounding through the equity curve
- Risk 5% of current equity per trade → `qty = FLOOR(equity * 0.05 / (entry_fill - stop))`
- Cap position notional at 50% of equity → `capQty = FLOOR(equity * 0.5 / entry_fill)`
- Take `MIN` of the two
- Whole shares only, and a trade is skipped if the size rounds to zero

⚠️ The cap binds on **tight** stops, not wide ones — a stop within 10% of entry is cap-bound at
these settings, which means the position risks less than the intended 5% and the trade returns less
than its R suggests. That effect is large enough at $500 to change conclusions, which is exactly why
R is the default unit. Do not present dollar returns without it.
