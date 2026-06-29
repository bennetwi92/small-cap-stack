# Market-Data Subscriptions — exactly what this system needs

Research date: 2026-06-29. Scope: the **Phase-1 tracker** (`src/small_cap_stack/`) running headless
against IB Gateway. This pins the answer to what the code actually requests, not a generic shopping
list. Supersedes the loose "L1 + streaming + TotalView" line in
[`ibkr-integration.md`](./ibkr-integration.md) §7 — see "Correction" below.

> Pricing below is **non-professional, US** rates. Confirm your **non-pro status** in Client Portal
> (pro fees are multiples higher) and reconfirm dollar figures on IBKR's live pricing page — the
> `*.php` pages block automated fetch, so the numbers here are corroborated from IBKR Campus + KB,
> not scraped from the pricing table.

---

## 1. What the code actually pulls from IBKR market data

Only **two** production code paths touch IBKR market data, plus news:

| Code path | API call | What it needs entitled |
|---|---|---|
| `scanner.py` → `Scanner.scan` | `reqScannerDataAsync` (`TOP_PERC_GAIN` @ `STK.US.MAJOR`, price/`changePerc`/`stVolume5minAbove` filters) | Real-time US equity top-of-book for the listing exchanges (so the scanner ranks on **live** pre-market price/volume, not delayed). |
| `marketdata.py` → `IBKRMarketData._subscribe` | `reqHistoricalDataAsync(barSizeSetting="5 mins", whatToShow="TRADES", useRTH=False, keepUpToDate=True)` | **Same entitlement as streaming top-of-book live data** — IBKR's docs are explicit that historical/`keepUpToDate` data carries the identical subscription requirement as live streaming. `useRTH=False` (pre-market) needs nothing extra. |
| `marketdata.py` → `fetch_news` | `reqHistoricalNewsAsync` (providers `BRFG+DJ-N+DJNL`) | News-provider entitlement — **already covered**, see §4. |

What the code **does not** call (and therefore you do **not** pay for in Phase 1):

- ❌ `reqMktDepth` — no order-book / depth-of-book → **NASDAQ TotalView is NOT needed.** The entry
  trigger ("tick above the high of the last consolidation candle") and stop (flag low) are computed
  from **5-min TRADES bars**, never from the book.
- ❌ `reqTickByTick`, `reqRealTimeBars` — not used.
- ❌ `reqMktData` L1 streaming — not used in production. (`transport.py` has a generic
  subscribe-replay path, but nothing registers an L1 line today; the spike tradability check uses a
  one-off `reqMktData(snapshot=True)`, covered by the value bundle's snapshot allowance.)
- ❌ OTC Markets (Pink/Grey) L1 — the universe is `STK.US.MAJOR` (exchange-listed NASDAQ/NYSE/NYSE
  American). Sub-exchange OTC runners are out of strategy scope (price $2–10, exchange-listed).
- ❌ IBKR fundamentals — float/shares/short% come from **yfinance** (account is unentitled for
  Reuters fundamentals, error 10358). See `decisions.md`.

---

## 2. The subscriptions to buy (the answer)

Small-cap momentum names list across all three US consolidated tapes — NASDAQ (most $2–10 runners),
NYSE American, and NYSE — so you need live top-of-book for **all three networks**. That is exactly
what this pair delivers, and it is the **whole requirement** for the Phase-1 tracker:

| # | Subscription | ~Non-pro / mo | Why you need it |
|---|---|---|---|
| 1 | **US Securities Snapshot and Futures Value Bundle** | **$10.00** (waived if ≥ **$30** commissions/mo) | **Prerequisite** for #2. On its own gives calculated NBBO **snapshots** across CTA A/B/C. |
| 2 | **US Equity and Options Add-On Streaming Bundle** | **$4.50** (**not** commission-waivable) | Upgrades #1 to **real-time streaming L1** for **NYSE (Network A/CTA)**, **NYSE American/AMEX (Network B/CTA)**, and **NASDAQ (Network C/UTP)** — the entitlement the scanner ranking and the `keepUpToDate` 5-min TRADES stream both ride on. |

**Net cost: ~$14.50/mo, dropping to ~$4.50/mo once you trade ≥ $30 commissions in a month** (the
$10 value bundle waives; the $4.50 add-on never waives). You must hold **both** — the $4.50 add-on
cannot be subscribed without the $10 value bundle underneath it.

> Why not the add-on alone? IBKR sells the streaming upgrade only on top of the snapshot/value
> bundle; the value bundle is the base entitlement and the add-on flips it from snapshot to
> streaming. Buying #2 requires #1.

---

## 3. Explicitly NOT needed for Phase 1 (don't pay for these yet)

- **NASDAQ TotalView-OpenView (~$1.50 non-pro)** — depth-of-book. No `reqMktDepth` in the code.
  Revisit only if **P2/P3 execution** wants book-based fills/queue position. *Not Phase 1.*
- **Quote Booster Packs (+100 lines each)** — the account's default **100 market-data lines** is
  ample: the strategy acts on the **top 1–3 scanner rows** (`decisions.md` scope note), and the
  5-min streams are a handful of symbols at a time (`marketdata.py` warns at 45 concurrent streams,
  well under any cap). *Not needed.*
- **OTC Markets L1** — out of universe (see §1). *Not needed.*
- **Paid news (Benzinga Pro ~$35, etc.)** — see §4; start on the included feed.

---

## 4. News — already covered, nothing to buy yet

`fetch_news` requests providers **`BRFG+DJ-N+DJNL`**:
- **BRFG** (Briefing.com general) and **DJNL** (Dow Jones Newsletters) are **complimentary**.
- **DJ-N** (Dow Jones per-symbol headlines + bodies + halt notices) is among the **8 providers the
  account is already entitled to** (`decisions.md` risk C → GREEN).

Decision stands: **start on the included IBKR feed, measure headline timeliness in Phase 1**, and only
budget for **Benzinga Pro (~$35/mo, via `reqMktData mdoff,292:BZ`)** if the free feed proves too slow
for breaking small-cap news. No news subscription purchase is required to run the tracker.

---

## 5. Correction to prior research

`ibkr-integration.md` §7 / recommendations list **"L1 bundle (~$10) + streaming add-on (~$4.50) +
NASDAQ TotalView (~$1.50)"** as the data stack. For the **Phase-1 tracker that line over-buys**:
TotalView (depth) is unused because nothing calls `reqMktDepth`. Keep TotalView as a **P2/P3
execution** consideration, not a Phase-1 line item. The L1 value bundle + streaming add-on pairing is
correct and is the minimum that makes the scanner and the 5-min bar stream return **live** data.

---

## 6. Operational reminders (already in `ibkr-integration.md`, repeated for provisioning)

- Set `reqMarketDataType(1)` (Live). Missing entitlement surfaces as **error 354** ("not
  subscribed") or **10089/10090** — alert on these; they mean a bundle lapsed (e.g. commission-waiver
  threshold logic changed your billing).
- Entitlements must be **enabled in Client Portal**; the API can't subscribe you. Allow for IBKR's
  market-data activation lead time before relying on live pre-market data.
- Pre-market needs **no extra subscription** — `useRTH=False` on the same L1 entitlement covers
  04:00 ET onward (`decisions.md` risk B → GREEN).

### Sources
- IBKR Campus — Market Data Subscriptions: https://www.interactivebrokers.com/campus/ibkr-api-page/market-data-subscriptions/
- IBKR Market Data pricing: https://www.interactivebrokers.com/en/pricing/market-data-pricing.php , https://www.interactivebrokers.com/en/pricing/research-news-marketdata.php
- US market-data subscription considerations (KB): https://www.ibkrguides.com/kb/subscription-consideration-us-market-data.htm
- Historical data == live entitlement; `keepUpToDate`: https://interactivebrokers.github.io/tws-api/historical_bars.html , https://interactivebrokers.github.io/tws-api/historical_data.html
- Bundle contents / prereq corroboration: https://app.loopedin.io/optrabot/kb/brokerage/market-data-subscriptions
