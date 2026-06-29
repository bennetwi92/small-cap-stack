# Data Feasibility Assessment — US Small-Cap Momentum Strategy

_Prepared 2026-06-29. Scope: validate that each strategy criterion can be sourced cheaply (prefer free), and flag real-time vs delayed feasibility for an automated system operating in a 4:00am–11:59am ET window (pre-market heavy)._

## Summary feasibility table

| # | Criterion | Best data source(s) | Free? | Real-time vs delayed/polling | Feasibility | Gaps / risks |
|---|-----------|---------------------|-------|------------------------------|-------------|--------------|
| 1 | Price $2–$10 (real-time last) | IBKR L1 quote (US Securities Snapshot Bundle, ~$1.50–$4.50/mo, waived if commissions > fee); Finnhub `/quote` (free, real-time US) | Yes (Finnhub) / near-free (IBKR) | Real-time (IBKR sub, Finnhub free RT US); yfinance ~15min delayed | Easy | yfinance last price is delayed and scrape-flaky; Finnhub free RT is reliable for the quote itself |
| 2 | Float < 20M shares | FMP `shares-float` / `all-shares-float` (free 250/day); SEC-API float; yfinance `.info` floatShares | Yes | Static/daily polling (cache) | Medium | Float vs shares-outstanding definitions differ by source; small-cap/OTC float data is stale or missing; SEC float updated only on filings. _Note: criterion reads "<$20M shares" — treated as 20M **shares**, confirm intent_ |
| 3 | Short interest % | FINRA Equity Short Interest API (free, official); Finnhub; Fintel/SQUEEZR | Yes (FINRA) | Bi-monthly (settlement + 2 biz days lag) | Easy to fetch / Medium to use | Inherently stale — published twice a month, ~1–2 wk old. Cannot be "real-time." Fine as a static filter only |
| 4 | Breaking news on the stock | Finnhub `company-news` (free); StockTitan feed; Benzinga Newswire (paid, low-latency) | Finnhub free / Benzinga paid | Polling (Finnhub minutes-latency); Benzinga true RT | Medium | Free feeds lag the actual print; relevance/dedup scoring is on us; pre-market PR timing (4–7am) coverage varies by source |
| 5 | 5-min volume > 100k (intraday) | IBKR `reqHistoricalData`/`reqRealTimeBars` (needs L1 sub); Finnhub candles; yfinance 5m | Near-free (IBKR) / Finnhub free | IBKR real-time; yfinance ~15min delayed | Medium | yfinance intraday is delayed + rate-limited; volume revisions; pre-market volume coverage is the weak point (see #9) |
| 6 | Change % > 10% vs prior close | Derived: Finnhub `/quote` (`c` current, `pc` prev close); IBKR L1 | Yes | Real-time (Finnhub/IBKR) | Easy | Pre-market % should be computed vs prior official close; ensure source's `pc` is the right reference |
| 7 | Bull-flag pattern detection | Computed locally from clean 5-min OHLCV (IBKR bars preferred) | Free (compute) | Depends on bar feed | Medium → Hard | Entirely dependent on a clean, gap-free, real-time 5-min series **including pre-market**; pattern logic is sensitive to missing/late bars |
| 8 | Candle counts (≤2 green ext., ≤2 red consol.) | Same 5-min OHLCV series; sequential classification | Free (compute) | Depends on bar feed | Medium | Same dependency as #7; thin pre-market bars (sparse trades) distort green/red classification |
| 9 | Pre-market window 4am–11:59am ET | IBKR `reqHistoricalData useRTH=0` + `reqRealTimeBars` (needs L1 sub); Alpha Vantage `extended_hours=true`; yfinance `prepost=True` | IBKR near-free / AV free-but-capped | IBKR real-time pre-market; AV 15-min delayed + 25 req/day; yfinance delayed/flaky | **Hard** | **Single biggest risk.** Free sources either delay pre-market (AV, yfinance) or cap requests below scan needs. IBKR is the only viable real-time pre-market 5-min source, and only with an L1 entitlement |

Feasibility key: Easy = reliable free real-time source exists. Medium = workable but data-quality/latency caveats. Hard = no clean free real-time path; needs paid/entitled feed or accepted compromise.

## Narrative by source

### yfinance
- **Real-time:** No. It scrapes Yahoo web endpoints; quotes are effectively ~15-min delayed and not contractually real-time.
- **Pre-market:** Supported via `history(prepost=True, interval="5m")`, but pre-market bars are delayed and frequently incomplete; before the cash open the day's pre-market series can be empty or partial (long-standing behavior, GH issue #581).
- **Float / shares outstanding:** Available via `Ticker.info` (`floatShares`, `sharesOutstanding`), but values are inconsistent for small-caps and sometimes null.
- **5-min history depth:** Intraday capped — 1m only last ~7 days; any sub-daily interval only ~last 60 days. Adequate for a live intraday strategy, not for deep backtests.
- **Rate-limiting / ToS:** High risk for an automated system. No official API or license; Yahoo tightened limits in 2024 and 429 `YFRateLimitError` / temporary IP bans are common in 2025 (GH #2422, #2125). Heavy polling looks like abuse. **Not safe as the primary feed for an automated scanner.**

### IBKR (TWS / IB Gateway API)
- **Can supply:** real-time L1 last/quote (#1, #6), real-time 5-min bars incl. pre-market via `reqHistoricalData useRTH=0` and `reqRealTimeBars` (#5, #7, #8, #9), and a **server-side scanner** (`reqScannerSubscription`).
- **Entitlement/cost:** API historical/real-time bars require a Level-1 streaming market-data subscription (the API, unlike the TWS GUI, will not build delayed charts for historical requests). The US Securities Snapshot & Futures Value Bundle / US equity top-of-book is roughly $1.50–$4.50/mo and is waived when monthly commissions exceed the fee. Delayed/frozen data is available via `reqMarketDataType` but defeats the 4am real-time goal.
- **Scanner limits:** max 50 results per scan code; only 10 active API scans at once; filters include `usdMarketCapAbove/Below`, price, volume, `TOP_PERC_GAIN`, `HOT_BY_VOLUME`. Pre-market scanner coverage in the API is narrower than the TWS GUI advanced scanner.

### Free alternatives
- **Finnhub (free, 60 calls/min):** real-time US quotes (`/quote`), company news, basic fundamentals, 50-symbol WebSocket. Best free real-time **quote/news** source. Intraday candles and full pre-market depth are limited/paid; not a full pre-market 5-min replacement.
- **Alpha Vantage (free):** `extended_hours=true` returns 4:00am–8:00pm ET intraday at 1/5/15/30/60-min — but free tier is **25 requests/day, 5/min, 15-min delayed**. Useful for occasional pre-market context, useless for live scanning.
- **Polygon.io (free):** 5 calls/min, 15-min delayed, and free tier excludes current-day minute aggregates in practice. Pre-market is on paid tiers.
- **FMP (free, 250/day):** float/shares endpoints (#2) and a biggest-gainers endpoint (regular-hours/EOD-ish) usable as a static filter source.
- **FINRA (free):** official bi-monthly short interest incl. % of float (#3); SQUEEZR/Fintel repackage it.

### The SCANNER problem (hardest architectural gap)
Discovering the candidate universe (volume-spike + %-change, low float, $2–$10) is harder than evaluating any single ticker, because it requires a **whole-market real-time sweep** rather than per-symbol lookups.
- **IBKR scanner API** is the most practical cheap option: `TOP_PERC_GAIN` / `HOT_BY_VOLUME` with price and `usdMarketCap`/`usdPrice` filters, but it returns ≤50 rows per scan, allows ≤10 concurrent scans, and its **pre-market** scan universe/quality is weaker than the GUI. Float and short-interest filters are not native — those must be applied as a post-filter against FMP/FINRA.
- **Free third-party scanners:** StockAnalysis.com pre-market gainers page and StockTitan momentum scanner expose 4am gainers but as web pages, not clean free APIs (scraping = fragility + ToS risk). FMP biggest-gainers is not true pre-market real-time.
- **Net:** there is no clean, free, real-time **pre-market low-float gainer scanner API**. Realistic path = IBKR scanner for the live gapper/volume universe, then enrich each candidate with float (FMP/yfinance cache), short interest (FINRA cache), and news (Finnhub). Accept that pre-market scan breadth is constrained.

### Pre-market data (biggest feasibility risk)
The 4:00am ET start eliminates most free real-time options:
- yfinance pre-market = delayed and often empty pre-open.
- Alpha Vantage has true 4am extended-hours data but 15-min delayed and 25 req/day — not scannable.
- Polygon pre-market is paid.
- **IBKR (`useRTH=0` + real-time bars) is the only realistic real-time pre-market 5-min source in the cheap tier, and only with an L1 entitlement.** This makes IBKR the de-facto backbone for criteria #1, #5, #6, #7, #8, #9 during pre-market.

## Recommended sourcing architecture (cheap path)
- **Real-time price, % change, 5-min bars, pre-market, scanner:** IBKR API + L1 subscription (backbone).
- **Float / shares outstanding:** FMP free (cache daily) with yfinance/SEC-API as cross-check.
- **Short interest %:** FINRA API (cache; accept bi-monthly staleness).
- **Breaking news:** Finnhub company-news (free polling); consider Benzinga if news latency proves decisive.
- **Pattern + candle logic (#7, #8):** computed locally on the IBKR 5-min series.

## Open questions for Phase-1 tracker
1. Confirm criterion #2 intent: "Float < $20M shares" = **20M shares** (not $20M market value)? This changes the filter and which fields matter.
2. Will we commit to an **IBKR L1 market-data subscription** (and IB Gateway uptime/automation) as the real-time + pre-market backbone, or attempt a free-only stack and accept delay?
3. How fresh must short interest (#3) be? If bi-monthly FINRA data is acceptable as a static filter, criterion #3 is Easy; if "real-time" is required, it is infeasible cheaply.
4. What latency is tolerable for "breaking news" (#4) — minutes (Finnhub free) vs sub-second (Benzinga paid)?
5. Define pre-market bar handling for #7/#8 when bars are sparse/missing (gap-fill, skip, or minimum-trade threshold) to avoid false green/red classification.
6. Validate IBKR scanner pre-market behavior empirically (result count, refresh cadence, whether low-float gappers actually surface at 4–7am).
7. Decide universe size vs IBKR scanner's 50-row / 10-scan caps — do we need multiple stacked scans to cover the $2–$10 low-float space?

## Hardest gaps (callout)
- **Pre-market real-time 5-min data (4am ET):** no clean free path; effectively forces IBKR + L1 sub. This gates criteria #1, #5, #6, #7, #8, #9.
- **The pre-market low-float gainer SCANNER:** no free real-time API discovers the candidate universe; IBKR scanner is the only cheap option and is row/scan-capped with weaker pre-market coverage. Float/short-interest must be bolted on as post-filters.
- **yfinance as automation backbone:** unreliable (delayed, rate-limited, ToS-exposed) — usable only as a cache/cross-check, not the live feed.
