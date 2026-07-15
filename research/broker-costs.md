# Broker Costs — IBKR cost model for a small UK cash account

**Date:** 2026-07-15. **Status:** Complete. Answers open question #6 (market-data entitlement) and
the §4 unknown "actual IBKR market-data monthly cost for this exact use case" from
[`findings-index.md`](./findings-index.md).

**Scope:** what it costs to run the live strategy through IBKR from a UK-resident cash account at a
~$500 starting balance, and whether that account size is viable.

> **Sourcing caveat.** IBKR's pricing pages return HTTP 403 to automated fetches, so the rates below
> come from secondary sources that corroborate each other rather than from the primary pages. The two
> figures the whole model pivots on — the **tiered per-order minimum ($0.35)** and the **exchange
> liquidity-removal rate (~$0.0030/share)** — should be eyeballed against Client Portal before any
> money moves. The removal rate in particular is a representative lit-venue figure; it varies by
> venue and SMART routing may occasionally capture a rebate instead.

---

## 1. Account profile modelled

| Parameter | Value |
|---|---|
| Entity | IBUK (Interactive Brokers U.K. Limited), introduced to and carried by IBLLC (US broker) |
| Account type | **Cash** (deliberately — sidesteps PDT, which is a margin-account rule) |
| Balance | ~$500 USD, funded once from GBP, then permanently USD (no recurring FX) |
| Positions | Max 2 concurrent, $250 each, both opened and closed same day |
| Order flow | ~4 orders/day (2 buys + 2 sells) → ~84 orders / ~42 round trips per month (21 trading days) |
| Instruments | US common stock only, $1–20, no ETFs/ETNs |
| Order style | Stop / stop-limit entries above consolidation high → **always liquidity-removing**, never earns add-liquidity rebates |
| Market data | Level 1 (top of book) only |

---

## 2. Verdict

**Use Tiered. Do not change the scanner price floor. The cost structure is fine; the account size is
the problem.**

- **IBKR Lite is not available** — US residents only (plus Singapore). As a UK client you are on
  IBKR Pro, so Tiered vs Fixed is the only commission lever that exists.
- **Tiered wins across essentially the whole traded range** ($1.70–20), costing ~$46/month all-in
  versus ~$84/month on Fixed.
- **At $500 the drag is ~9–13% per month.** That is not a broker problem — it is the per-order
  minimum colliding with small orders. The same strategy at $2,000 drags ~2.9%/month.
- **No UK-accessible alternative can run this strategy.** Commission-free UK brokers lack either the
  API, the 04:00 ET pre-market access, or both. IBKR's cost is the price of being the only venue
  that does all four things this strategy needs.

---

## 3. Commission plan: Tiered vs Fixed

| Plan | Per-share | Per-order min | Cap |
|---|---|---|---|
| Fixed | $0.005 | **$1.00** | 1% of trade value |
| Tiered | $0.0035 | **$0.35** | 0.5% of trade value (secondary sources disagree; see below) |

**The caps never bind.** Tiered's cap would only engage below ~$0.70/share and Fixed's below
~$0.50/share — both under the $1 scanner floor. The 0.5%-vs-1% disagreement between sources is
therefore moot and was not chased.

**The minimum always binds, and that is the whole story.** At a $250 position the per-share rate only
exceeds the $0.35 tiered minimum above 100 shares (i.e. below $2.50/share). Above $2.50/share you pay
a flat $0.35 regardless. So the real choice is between a $0.35 floor and a $1.00 floor.

### Cost formula

Tiered unbundles the pass-throughs that Fixed bundles:

```
tiered_per_order = max($0.35, shares × $0.0035)     # IBKR commission
                 + shares × $0.0030                  # exchange liquidity removal (always, see profile)
                 + shares × $0.0002                  # clearing
                 + shares × $0.000166                # FINRA TAF        — sells only, cap $8.30
                 + value  × 0.0000278                # SEC Section 31   — sells only

fixed_per_order  = max($1.00, shares × $0.005)       # bundles exchange + most regulatory fees
```

### Round-trip cost at a $250 position

| Share price | Shares | Tiered commission | **Tiered RT** | **Fixed RT** | Winner |
|---|---|---|---|---|---|
| $1.50 | 166 | $0.58 | **$2.26** | **$2.00** | Fixed, by $0.26 |
| $1.67 | 150 | $0.53 | **~$2.00** | **$2.00** | *crossover* |
| $1.75 | 142 | $0.50 | **$1.93** | **$2.00** | Tiered |
| $1.99 | 125 | $0.44 | **$1.71** | **$2.00** | Tiered |
| $2.50 | 100 | $0.35 | **$1.36** | **$2.00** | Tiered |
| $10 | 25 | $0.35 | **$0.87** | **$2.00** | Tiered |
| $20 | 12 | $0.35 | **$0.79** | **$2.00** | Tiered |

**Crossover is at ~150 shares = $1.67/share** on a $250 position. Below that, Tiered's per-share
exchange fees overtake Fixed's bundled $1.00 minimum.

### Why the price floor should NOT be raised

An earlier draft of this analysis recommended raising the scanner floor to ~$2 on cost grounds. **That
recommendation was wrong and is withdrawn**, for two reasons:

1. **It doesn't apply where it was aimed.** Stocks "just under $2" ($1.70–1.99) are still in Tiered's
   winning range. Only sub-$1.67 favours Fixed, and only by ~$0.26/trade ≈ $11/month — noise.
2. **It optimises the wrong term.** The extra cost of a $1.50 stock over a $10 stock is $1.39/round
   trip. Against 1R = $12.50 (a $250 position with a ~5% stop to the consolidation low) that is
   **0.11R**. If low-priced low-float runners carry even 0.15R more expectancy than $10 names — which
   is essentially the Warrior thesis — they are worth trading and it is not close. Excluding a cohort
   of setups to save 0.11R is a bad trade.

**Keep the $1 floor (#126).** Let the tracker's R-metrics decide where the edge lives; costs are the
smaller term and should not drive universe selection.

*(One genuine edge case: for stocks under $1.00, exchange fees switch from per-share to
percentage-of-value (~0.30%), which is materially worse. The $1 floor already excludes this.)*

### Monthly commission totals (42 round trips)

| Traded price | Tiered all-in | Fixed all-in |
|---|---|---|
| $1.50 stocks | $95 | $84 |
| $2.50 stocks | $57 | $84 |
| $10 stocks | **$37** | $84 |

---

## 4. Market data

**US Securities Snapshot and Futures Value Bundle — $10/month**, non-professional. Covers the Level 1
need: Tapes A/B/C for NBBO (NYSE, NYSE American, NASDAQ) plus OTC top-of-book. No Level 2 required.

**Waived if the account generates ≥$30/month in commissions** (per user subscribed).

There is a mild irony here: at $2.50+ stocks the tiered commission is exactly 84 × $0.35 = **$29.40**,
missing the waiver by 60 cents. Two extra orders (86 × $0.35 = $30.10) cost $0.70 and save $10. Worth
knowing, though it is unclear whether the waiver counts IBKR commission only or all-in fees — if
all-in, the threshold clears comfortably and this is moot. **Verify in Client Portal.**

### ⚠️ Professional classification — the tail risk

IBKR **defaults every user to Professional**; non-professional status is self-certified via a
questionnaire that must be re-affirmed annually.

Automating via the API does **not** by itself make you Professional. Professional status hinges on
trading for an entity, being a registered security/investment advisor, or using data for more than
personal investment purposes. A sole individual trading their own money through their own automation
remains non-professional.

**However**, the questionnaire explicitly asks users who submit API orders without manually approving
each one to declare the automated system, disclose the software used, and name who manages order
submission. Answer it accurately.

**If ever reclassified Professional, US market data goes from ~$10 to ~$100+/month** — over 20% of a
$500 account per month. This single line item would end the experiment. It is the largest tail risk in
the cost model.

---

## 5. Other fees

- **Inactivity / minimum-activity fees:** abolished ~July 2021. Not a factor. *(Worth a one-line
  confirmation for IBUK specifically.)*
- **One-off GBP→USD conversion (~$500):**
  - IDEALPRO manual spot: 0.20bp rate = $0.01, but **$2.00 minimum commission** → **$2.00**
  - Automatic conversion: 0.03% spread adjustment → **$0.15**
  - **Use auto-conversion.** It wins by $1.85. A rounding error, but it is a single decision made
    once. Note spot FX settles **T+2** vs the stock leg's T+1 — relevant only at initial funding,
    since the account stays in USD permanently thereafter.

---

## 6. Settlement mechanics — a hard constraint on the sizer

US equities settle **T+1** (since 28 May 2024). In a cash account, purchases require **settled** cash;
IBKR extends no credit.

**The daily cycle works, and supports exactly one full rotation per day:**

| Day | Settled cash AM | Action | Legal? |
|---|---|---|---|
| Mon | $500 | Buy 2 × $250 from settled cash; sell both same day | ✅ Paid with settled funds; selling what you've paid for is never a violation |
| Mon PM | $0 | Proceeds ~$500 **unsettled** | — |
| Tue | $500 | Monday's proceeds settled overnight; repeat | ✅ |

**The trap is sequential reuse within a day.** If position 1 closes at 09:30 and that same $250 is
redeployed into position 2 at 10:15 and sold before the close, position 2 was bought with *unsettled*
proceeds and sold before settlement → **good-faith violation**. Three in 12 months restricts the
account to settled-cash-only for 90 days. A **free-riding** violation (paying for a purchase with the
proceeds of selling that same security) triggers the same 90-day restriction after just **one**
occurrence.

> **Rule to encode in the position sizer:** *total daily buy notional must not exceed the settled cash
> balance at the start of the day.* $500 settled → at most $500 of buys, whether that is two
> concurrent $250s or one $500. It explicitly does **not** mean "$250 recycled twice."

This is a correctness constraint, not a guideline — the failure mode is a restriction letter, not a
bad fill.

---

## 7. Why $500 specifically hurts

**Costs scale with order count and share count. They do not scale with capital.**

| Account | Position | Shares @ $10 | RT cost | Monthly (42 RT) | % of account |
|---|---|---|---|---|---|
| $500 | $250 | 25 | $0.87 | $46 | **9.3%** |
| $2,000 | $1,000 | 100 | $1.36 | $67 | **2.9%** |
| $5,000 | $2,500 | 250 | $3.30 | $149 | **2.8%** |

The 9.3% → 2.9% cliff is the **$0.35 minimum**. At 25 shares you pay $0.35 for what the headline rate
prices at $0.09 — **~4× the advertised commission rate**. The minimum is designed for ~100-share
orders; a $500 account never reaches them, so it structurally overpays on every fill. Past ~100 shares
the drag converges to a stable ~2.8–2.9%/month.

**In R terms** (1R = $12.50 on a $250 position with a ~5% stop): costs run **0.07R/trade at $10 stocks,
0.11R at $2.50, 0.18R at $1.50** — roughly **3–8R of drag per month**. Survivable if the edge is real,
but measured expectancy must clear ~+0.1R/trade before the account nets a penny.

**Levers, in order of leverage:**

1. **More capital.** $500 → $2,000 takes the drag from 9.3% to 2.9%/month. By far the biggest lever.
2. **Fewer, better trades.** Costs are per-order. 1 position/day instead of 2 halves the bill to
   ~$28/month without touching capital.
3. ~~Raise the price floor~~ — withdrawn, see §3.

---

## 8. Alternatives — and why there aren't any

The strategy needs four things simultaneously: **commission-free or cheap**, **API access**,
**04:00 ET pre-market**, and **US small-cap coverage**. No UK-accessible broker has all four.

| Broker | Pre-market | API | Verdict |
|---|---|---|---|
| Trading 212 | ✗ meaningful US pre-market | ✗ | Dead |
| Freetrade | ✗ | ✗ | Dead |
| Lightyear | ✗ | ✗ | Dead |
| **Webull UK** | ✅ 09:00 GMT = **04:00 ET** — genuinely covers the window | ✗ no retail API found | **Near-miss** — the only one that clears the pre-market bar, but cannot drive `ib_async`-shaped automation |
| Alpaca / Public | ✅ | ✅ | US-resident brokers — cannot open from the UK |
| **IBKR** | ✅ 04:00–20:00 ET | ✅ | **The only viable option** |

Webull UK is the one worth re-checking if it ever ships a retail API. Everything else fails on
pre-market access alone.

---

## 9. Recommendation

1. **Switch to Tiered** (Client Portal; first three switches process daily, thereafter quarterly).
2. **Subscribe to the US Securities Snapshot and Futures Value Bundle** ($10/mo) and complete the
   non-professional questionnaire accurately, declaring the automated system.
3. **Fund once via automatic FX conversion**, not IDEALPRO.
4. **Encode the settled-cash constraint in the position sizer** (§6) before any live order flow.
5. **Leave the $1 scanner floor alone.**
6. **Treat the $500 account as plumbing validation, not strategy validation.** A 9–13%/month cost
   floor will swamp any edge signal. Order routing, fills and settlement behaviour are what this
   account can honestly test; keep judging the strategy on the tracker's R-metrics, where costs do not
   distort the measurement.

---

## 10. Open questions

- **Verify against Client Portal:** tiered per-order minimum ($0.35), exchange liquidity-removal rate
  (~$0.0030/share), and whether the $30 data waiver counts IBKR commission only or all-in fees.
- **Confirm IBUK has no inactivity fee** (believed abolished ~2021).
- **Confirm whether Fixed bundles SEC Section 31 / FINRA TAF** for US stocks, or passes them through.
  Does not change the verdict (Tiered wins on the minimum alone) but would sharpen the Fixed column.
- **Re-check Webull UK** if it ever ships a retail API — it is the only UK alternative that clears the
  pre-market requirement.
</content>
