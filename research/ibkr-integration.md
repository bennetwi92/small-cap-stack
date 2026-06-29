# IBKR (Interactive Brokers) Integration for an Unsupervised, Headless Trading System

Research date: 2026-06-29. Target: a momentum-scalping bot on US small-cap equities, running unattended on a Linux VPS.

> Scope note: This document covers the TWS/Gateway socket API path (IB Gateway + IBC + a Python client). The separate IBKR Client Portal Web API (REST/OAuth) is an alternative for some institutional flows but is not the focus here, as the socket API is the standard for low-latency automated trading.

---

## 1. Headless unattended operation: IB Gateway vs TWS

**Use IB Gateway, not TWS.** From the API client's perspective the two are functionally identical — both expose a socket the client connects to after authentication — but IB Gateway is a minimal, API-only application with no charts, market-data windows, or manual order entry. ([IBKR TWS API doc](https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/))

- **Footprint:** IB Gateway uses roughly **40% fewer resources than TWS**. A single-strategy Gateway deployment runs comfortably on about **4 GB RAM**; multi-strategy/multi-account setups want **8 GB+**. ([VPS hosting guide](https://tradingfxvps.com/best-vps-for-interactive-brokers-tws-gateway-hosting/))
- **Native Linux build** exists and is the preferred VPS target.
- **No true GUI-less mode:** Both TWS and Gateway are Java GUI apps and were deliberately designed to require a display for secure authentication. On a headless VPS you must run a **virtual framebuffer (Xvfb)** plus a window manager (or a Docker image that bundles one). This is why launch scripts set a `DISPLAY` variable. ([IBC user guide](https://github.com/IbcAlpha/IBC/blob/master/userguide.md))

### IBC (IbcAlpha/IBC) for automated login/restart

IBC is a Java application that wraps TWS or IB Gateway and watches for the GUI events that would otherwise need a human (login dialog, connection-accept prompts, restart dialogs), handling them automatically. It is the de-facto standard for unattended IB Gateway. ([GitHub IbcAlpha/IBC](https://github.com/IbcAlpha/IBC))

- **Latest version:** 3.24.0, released 2026-06-19. ([repo](https://github.com/IbcAlpha/IBC))
- **CRITICAL:** IBC requires the **offline / standalone installer** of TWS/Gateway. It does **not** work with the self-updating version. Download the offline build.
- **Login automation** via `config.ini`: `IbLoginId`, `IbPassword`, `TradingMode` (`live`/`paper`). Store credentials in `config.ini` (preferred over CLI args) in a protected/encrypted folder.
- **Connection dialogs:** `AcceptIncomingConnectionAction` (`accept`/`reject`/`manual`) — IBC recommends `reject` plus IP whitelisting in the Gateway API config. `ExistingSessionDetectedAction` controls behavior when the account is logged in elsewhere.
- **Launch scripts:** Linux `gatewaystart.sh` (via crontab + Xvfb `DISPLAY`); Windows `StartGateway.bat` (Task Scheduler); macOS `gatewaystartmacos.sh` (launchd). Use `TWS_SETTINGS_PATH`, not the deprecated `IbDir` — the guide warns **auto-restart breaks** if you use the deprecated approach.
- **Dockerized options** bundle IBC + Gateway + Xvfb/VNC, e.g. [gnzsnz/ib-gateway-docker](https://github.com/gnzsnz/ib-gateway-docker), [hartza-capital/docker-ib-gateway](https://github.com/hartza-capital/docker-ib-gateway). QuantConnect maintains [IBAutomater](https://github.com/QuantConnect/IBAutomater) for the same purpose.

### The mandatory daily restart / weekly reset

IBKR forces every TWS/Gateway session to terminate at least once per day. Configure this in the **Lock and Exit** section of Global Configuration: ([IBKR Auto Restart Considerations](https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm))

- **Auto restart** (preferred): at the configured time the platform shuts down and **restarts automatically without re-authentication**, keeping the session alive across weekdays. Introduced in TWS **974/975**. IBC drives this via `AutoRestartTime` (`HH:MM AM/PM`).
- **Auto logoff** (avoid): fully shuts down and forces a re-login (and possibly 2FA).
- `ClosedownAt` (`<hh:mm>` daily or `<dayOfWeek hh:mm>` weekly) tidily shuts the platform after Friday's close.
- **The weekly wall (unavoidable):** security tokens are invalidated **every Sunday at ~01:00 ET**. The first login of the week after that point **requires a manual 2FA approval**. You cannot bypass this with IBC's PAUSE/restart — IBKR forces a full shutdown when credentials expire.

**Recommended setup:** IB Gateway (offline build) on Linux under Xvfb, wrapped by IBC 3.24.0 launched from `gatewaystart.sh` via crontab using `TWS_SETTINGS_PATH`; set **Auto restart** for the daily cycle, `ClosedownAt` for Friday evening, and plan for one authentication per week after Sunday 01:00 ET.

---

## 2. API client libraries: ib_insync vs ib_async vs ibapi

### ib_insync — DEPRECATED / unmaintained
Original author **Ewald de Wit (erdewit)** passed away **March 11, 2024**. The project is frozen. Snyk classifies maintenance as "Inactive"; last release was **0.9.86 (Dec 11, 2023)**. Both the old `erdewit/ib_insync` and interim `mattsta/ib_insync` repos now redirect users to the successor. **Do not start new projects on it.** ([ib_async repo](https://github.com/ib-api-reloaded/ib_async), [mattsta/ib_insync](https://github.com/mattsta/ib_insync), [Snyk](https://snyk.io/advisor/python/ib-insync))

### ib_async — the maintained successor (RECOMMENDED)
- Repo: https://github.com/ib-api-reloaded/ib_async — "Python sync/async framework for Interactive Brokers API (replaces ib_insync)."
- Maintained by **Matt Stancliff (mattsta)** under the `ib-api-reloaded` org. Near drop-in migration (mostly an import rename).
- **Active cadence:** v2.0.1 (2025-06-22), **v2.1.0 (2025-12-08, current latest)**. Python **3.10–3.14**. ([PyPI](https://pypi.org/project/ib_async/))
- **Async model:** built on asyncio + eventkit; replaces the blocking `EClient.run()` loop with the asyncio event loop. **Dual API** — every request has a blocking form (`reqHistoricalData()`) and an awaitable form (`reqHistoricalDataAsync()`). Event subscriptions (`ib.orderStatusEvent += ...`, `ticker.updateEvent += ...`) instead of callback boilerplate. **Automatic state sync** keeps orders, executions, positions, tickers, and account values current with no manual `EWrapper` bookkeeping. ([docs](https://ib-api-reloaded.github.io/ib_async/))
- **Connection lifecycle:** `ib.connect(host, port, clientId)` blocks until ready; `connectAsync(...)` is awaitable; `disconnect()` for teardown. The built-in **`Watchdog`** (`ib_async.ibcontroller`) starts/connects/monitors Gateway, probes liveness after `appTimeout`, and **auto-restarts + reconnects** a dead/hung app. In ib_async, `Watchdog.start()` is non-blocking and no longer needs `patchAsyncio`. ([ibcontroller source](https://ib-api-reloaded.github.io/ib_async/_modules/ib_async/ibcontroller.html))

### Official ibapi — first-party but low-level
Callback-based `EClient` (outgoing) + `EWrapper` (you override every incoming-message method); you run `EClient.run()` yourself and manually correlate requests via reqId. No state caching, no async ergonomics, no reconnect helper. **The PyPI `ibapi` is a stale third-party upload (9.81.1.post1, Dec 2020)** — the real API ships only with the TWS API installer (10.x). ([client/wrapper docs](https://interactivebrokers.github.io/tws-api/client_wrapper.html), [PyPI ibapi](https://pypi.org/project/ibapi/))

### Recommendation
**Use `ib_async` (v2.1.0).** It is the actively maintained successor, fits a long-running asyncio daemon, gives automatic state sync, and ships the `Watchdog` reconnect/restart helper — exactly the headless requirements here. Reserve official `ibapi` only for a brand-new TWS field not yet in ib_async, or a zero-third-party-dependency mandate.

**Practical stack:** `ib_async` (logic + reconnect) → `Watchdog` → **IBC** (unattended login/restart) → **IB Gateway** (offline build) under Docker/Xvfb.

---

## 3. Auth & session keep-alive

- **2FA is mandatory and cannot be disabled.** There is no account toggle to turn it off. ([2FA FAQ](https://www.ibkrguides.com/securelogin/sls/faq.htm))
- **Paper accounts do NOT escape 2FA.** A paper account logs in through the same credentials/2FA flow as its associated live account; it is not a clean way to avoid 2FA. ([ib-gateway-docker discussion](https://github.com/gnzsnz/ib-gateway-docker/discussions/126))
- **Automatable second factor:** only **IBKR Mobile (IB Key)** push approval. Security cards / SMS challenge codes cannot be automated. ([IB Key](https://www.interactivebrokers.com/campus/trading-lessons/ib-key-two-factor-authentication-iphone/))
- **Weekly reality:** tokens reset Sunday ~01:00 ET → first login of the week needs a manual phone tap. During the rest of the week, daily auto-restarts re-establish the session **without** 2FA. Any cold restart (crash, VPS reboot, OS update, network/disk failure) re-prompts 2FA. ([Auto Restart Considerations](https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm))
- **Second Factor Authentication Settling Time:** a Global Config > API field — a delay TWS waits after a 2FA login completes before accepting API socket connections, so the client doesn't connect before the session has settled. Increase it if reconnects immediately after a restart fail. ([API settings panel](https://ibkrguides.com/tws/usersguidebook/configuretws/apisettings.htm))
- **Relevant IBC 2FA config keys** (`config.ini`): `SecondFactorAuthenticationTimeout=180`, `SecondFactorAuthenticationExitInterval=60`, `ReloginAfterSecondFactorAuthenticationTimeout=no` (set to `yes` for unattended retry — IBC re-initiates login when 2FA times out). ([config.ini](https://github.com/IbcAlpha/IBC/blob/master/resources/config.ini))

**Bottom line:** there is no supported zero-touch path. Plan for one manual phone approval per week (after Sunday 01:00 ET) plus one on any cold restart.

---

## 4. Reconnection: detecting disconnects & rebuilding state

Authoritative codes: [TWS API message codes](https://interactivebrokers.github.io/tws-api/message_codes.html), [error handling](https://interactivebrokers.github.io/tws-api/error_handling.html).

### Detecting disconnects
- A broken client↔TWS socket surfaces via the **`connectionClosed()`** callback. Server-side notifications arrive through `error()` with `reqId = -1`.
- **504** — "Not connected." (request issued with no active socket)
- **1100** — "Connectivity between IB and TWS has been lost." Primary disconnect signal (TWS↔IB backend dropped).
- **1101** — "Connectivity restored — **data lost**." Reconnected, but **market-data subscriptions were lost and MUST be re-requested.**
- **1102** — "Connectivity restored — **data maintained**." Reconnected and subscriptions preserved — **do not** re-subscribe.
- **2110** — "Connectivity between TWS and server is broken. It will be restored automatically." (informational)
- **2103/2105/2110** — market/historical data farm disconnected (usually transient/auto-recovering). **2104/2106** = farm OK. **2107/2108** = farm dormant but available on demand (benign).

The **1101 vs 1102** branch is the critical recovery decision.

### Auto-reconnect behavior
- The **TWS/Gateway↔IB backend** link self-reconnects (that's what 1101/1102 report).
- The **client↔TWS socket does NOT auto-reconnect.** Your client must detect `connectionClosed`/504 and re-run `connect()` in a backoff loop. After the daily auto-restart, the socket drops and the client must reconnect. (`ib_async`'s `Watchdog` automates this.)

### State to rebuild after a fresh socket reconnect
- **Open orders:** TWS Global Config > API "Maintain and resubmit orders when connection is restored" is on by default in TWS **10.28+**. Re-request your view with `reqOpenOrders`/`reqAllOpenOrders` (and `reqAutoOpenOrders` to bind TWS-entered orders). ([open orders](https://interactivebrokers.github.io/tws-api/open_orders.html))
- **Positions:** re-request via `reqPositions` (or `reqAccountUpdates`).
- **Market data subscriptions:** on 1101 re-request all `reqMktData`/`reqRealTimeBars`/`reqTickByTickData`/`reqMktDepth`; on 1102 do nothing. On a full socket reconnect, all streams are gone — re-request regardless.
- **Account/PnL subscriptions** (`reqAccountUpdates`, `reqPnL`, `reqPnLSingle`): re-request.
- **`nextValidId` / order-ID counter:** resync on each connect (delivered on connect / `reqIds`) to avoid duplicate IDs.
- **`clientId`:** reconnect with the **same clientId** to see orders placed under it; `clientId 0` also sees manual TWS orders.

### Recommended reconnect loop
1. On `connectionClosed`/504 → backoff loop calling `connect`.
2. After connect, wait for `nextValidId`; resync order-ID counter.
3. Re-request open orders, positions, account/PnL.
4. Re-subscribe all market data.
5. While connected, on 1100 mark "degraded"; branch on 1101 (re-subscribe) vs 1102 (no-op).
6. Expect a socket drop at the daily auto-restart window; reconnect after it, plus the Second Factor Settling Time buffer.

---

## 5. Order placement (momentum scalping)

Basic types via `Order.orderType`: **MKT, LMT, STP, STP LMT**, placed with `placeOrder(orderId, contract, order)`. ([order submission](https://interactivebrokers.github.io/tws-api/order_submission.html))

### Bracket orders (entry + take-profit + stop-loss)
Three linked orders: a parent entry, a take-profit LMT child (opposite side, higher), and a stop-loss STP child (opposite side, trigger). ([bracket orders](https://interactivebrokers.github.io/tws-api/bracket_order.html))
- **`parentId`:** each child's `Order.parentId` = parent's `orderId`; children are held until the parent fills.
- **`transmit` flag (key safety mechanism):** set `transmit=False` on the parent and TP child, `transmit=True` only on the last child (stop-loss). TWS then transmits the whole group atomically — prevents partial transmission.
- The two children behave as an OCA pair: when one fills, the other cancels.

### OCA / OCO
Assign orders the same `Order.ocaGroup` string; one fill cancels the rest (OCO = a 2-order OCA group). `ocaType`: **1** = cancel-all with block (overfill protection); **2** = proportionately reduce with block; **3** = proportionately reduce, no block. ([OCA](https://interactivebrokers.github.io/tws-api/oca.html))

### ib_async helpers
- `ib.bracketOrder(action, quantity, limitPrice, takeProfitPrice, stopLossPrice)` → `BracketOrder` with transmit flags pre-set correctly; then `placeOrder` each leg.
- `ib.oneCancelsAll(orders, ocaGroup, ocaType)` stamps a list into one OCA group.
- `Order` exposes `ocaGroup`/`ocaType` directly; sync + async variants. ([ib_async API](https://ib-api-reloaded.github.io/ib_async/api.html))

### Scalping notes
- A MKT or marketable-LMT parent with an attached bracket gives instant TP/SL in one atomic transmission.
- Trailing stops supported (`trailStopPrice`, `adjustedOrderType`, `triggerPrice`) — usable as the stop child.
- Manage `orderId` carefully when firing rapidly to avoid **error 103 (Duplicate order ID)**.

---

## 6. News feed via the API

API functions (all in [tws-api/news.html](https://interactivebrokers.github.io/tws-api/news.html)):
- `reqNewsProviders()` → subscribed providers (API v973.02+).
- `reqNewsBulletins()` → IB system/exchange bulletins (not headlines).
- `reqHistoricalNews(reqId, conId, providerCodes, start, end, totalResults)` → cached historical headlines for a contract.
- `reqNewsArticle(reqId, providerCode, articleId)` → full article body.
- `reqMktData(... genericTickList ...)` → streaming headlines via the **`tickNews`** callback.

### Free vs paid providers
- **Free / complimentary (since TWS v966):** **BRFG** (Briefing.com General Market Columns), **BRFUPDN** (Briefing.com Analyst Actions), **DJNL** (Dow Jones Newsletters). These are broad-market **commentary, NOT per-ticker breaking-news wires.**
- **Paid (separate research subscription + sometimes an API entitlement application):** **BZ** (Benzinga Pro), **BRF** (Briefing Trader), **FLY** (Fly on the Wall), plus Midnight Trader, Dow Jones Real-Time, Reuters. Benzinga Pro via API is commonly cited at **~USD $35/month** and is the usual choice for real-time per-ticker small-cap breaking news. ([research/news pricing](https://www.interactivebrokers.com/en/pricing/research-news-services.php))

### Can it deliver breaking news on a specific stock? Yes — if entitled.
1. **Contract-specific:** `reqMktData` on the stock with a news generic tick `mdoff,292:<PROVIDER>` (e.g. `"mdoff,292:BZ"`). Generic tick **292** is the news tick; headlines arrive on `tickNews`. This is the real-time per-symbol path.
2. **BroadTape (whole feed):** subscribe to a NEWS contract like `"BZ:BZ_ALL"`, `"FLY:FLY_ALL"`.

### Constraints
- **Tick-by-tick data carries no news** — `reqTickByTickData` (Last/AllLast/BidAsk/MidPoint) is trades/quotes only. ([tick data](https://interactivebrokers.github.io/tws-api/tick_data.html))
- Providers must be enabled in Global Config > API > News Configuration before the API delivers them.
- The free feeds will not give a reliable real-time per-symbol breaking-news wire; budget for Benzinga Pro for that purpose.

---

## 7. Market data: entitlements, costs, rate limits

### Data type selector — `reqMarketDataType(type)` ([market data type](https://interactivebrokers.github.io/tws-api/market_data_type.html))
- **1 = Live** (requires subscriptions). **2 = Frozen** (last close quote; same subscriptions as live). **3 = Delayed** (free, **15–20 min**, ticks 66–76). **4 = Delayed-Frozen.** **Snapshot** (`reqMktData(..., snapshot=True)`) is one-time, billed per request.

### US small-cap real-time subscriptions (approximate monthly, non-professional)
Small-caps trade on NASDAQ + NYSE American; you want Network A/B/C L1 plus TotalView depth. ([market data pricing](https://www.interactivebrokers.com/en/pricing/market-data-pricing.php), [campus subscriptions](https://www.interactivebrokers.com/campus/ibkr-api-page/market-data-subscriptions/))
- **US Securities Snapshot and Futures Value Bundle — ~$10/mo**, waived if you generate ≥ $30 commissions/month.
- **US Equity and Options Add-On Streaming Bundle — ~$4.50/mo** (real-time L1 for NYSE/AMEX/NASDAQ).
- **NASDAQ TotalView-OpenView — ~$1.50/mo non-pro** (full depth-of-book; essential for order-book scalping). Pro rates are far higher.
- **Snapshot quotes — ~$0.01 each** for US equities/ETFs, ~first 100/month effectively free via $1.00 auto-waiver.
- **Non-professional vs professional status materially changes pricing** (pro fees are multiples higher) — confirm non-pro status.

> Exact dollar figures should be reconfirmed on IBKR's live pricing pages (`*.php`), which block automated fetch; bundle names and behavior are corroborated from IBKR campus docs.

### Rate limits / pacing
- **Message rate ("50 msg/sec"):** max outbound msgs/sec = **Max Market Data Lines ÷ 2**. Default 100 lines → **50 msg/sec**. Each subscribe and cancel = 1 message; only client→TWS counts. More lines (commissions or Quote Booster Packs) scales this up.
- **Simultaneous market-data lines ("100 lines"):** every account starts at **100 concurrent lines** (always ≥100). After month 1, allocation = greater of (commissions ÷ 8) and (equity × 100 ÷ 1,000,000); buy **Quote Booster Packs** (+100 each) to scan more tickers.
- **Historical data pacing** (`reqHistoricalData`) — violation if any of: (1) identical requests within **15 s**; (2) **6+** requests for the same Contract+Exchange+TickType within **2 s**; (3) **>60 requests per 10-minute** window. **BID_ASK counts DOUBLE.** ([historical limitations](https://interactivebrokers.github.io/tws-api/historical_limitations.html))
- **Tick-by-tick:** no more than 1 request for the same instrument within 15 s.

### Relevant error codes ([message codes](https://interactivebrokers.github.io/tws-api/message_codes.html))
- **100** Max messages/sec exceeded. **101** Max tickers reached (line cap). **102** Duplicate ticker ID. **103** Duplicate order ID. **104** Can't modify a filled order.
- **162** Historical Market Data Service error (delivers the pacing-violation message). **165** Historical query message.
- **354** Requested market data is not subscribed (entitlement missing). **420** Invalid real-time query (real-time pacing). **10089/10090** market data requires additional subscription / part not subscribed.

---

## Blockers for unsupervised VPS operation

These require manual intervention or special handling; nothing fully eliminates the first two.

| # | Blocker | Why it breaks unattended ops | Workaround / mitigation |
|---|---------|------------------------------|--------------------------|
| 1 | **Weekly 2FA re-authentication** | Tokens reset **Sunday ~01:00 ET**; first login of the week needs a manual IBKR Mobile (IB Key) push approval. 2FA cannot be disabled and paper accounts don't escape it. | One scheduled manual phone tap per week. IBC + `ReloginAfterSecondFactorAuthenticationTimeout=yes` to retry the prompt; some teams use a phone-automation rig to auto-approve the push. No supported zero-touch path. |
| 2 | **Mandatory daily restart** | TWS/Gateway must terminate at least once daily; the client↔TWS socket drops at restart. | Use **Auto restart** (not Auto logoff) — reconnects without 2FA on weekdays. IBC `AutoRestartTime`. Client reconnects automatically via `ib_async` `Watchdog`. Add the Second Factor Settling Time buffer before reconnecting. |
| 3 | **No true headless GUI mode** | Gateway/TWS are Java GUI apps; can't run with no display. | Run under **Xvfb** (+ window manager) or a Docker image (gnzsnz/ib-gateway-docker) that bundles it. |
| 4 | **Cold-restart re-prompts 2FA** | Any crash, VPS reboot, OS update, or network/disk failure forces a cold login → 2FA. | Harden the VPS (disable auto OS reboots, monitor disk/network); supervise the process (Docker/systemd); alert on cold restart so a human can tap. |
| 5 | **Self-updating build incompatible with IBC** | IBC silently breaks with the auto-updating installer. | Install the **offline/standalone** TWS/Gateway build. |
| 6 | **Market-data & news entitlement gates** | Missing subscriptions → delayed data or error 354/10089; entitlements can lapse if billing/commission waivers change. | Pre-provision subscriptions (L1 bundle + streaming add-on + TotalView, Benzinga for news); monitor for 354/10090; confirm non-pro status; watch commission-waiver thresholds. |
| 7 | **Rate / pacing violations** | Exceeding 50 msg/sec, 100 lines, or historical pacing throttles or errors the client (100/101/162/420). | Throttle requests; cap concurrent lines or buy Quote Booster Packs; keep historical requests <60/10 min, avoid 6 same-contract/2 s, remember BID_ASK counts double. |

---

## Recommendations summary

- **Library:** `ib_async` v2.1.0 (the maintained successor to the now-unmaintained `ib_insync`; do not use the stale PyPI `ibapi`). Asyncio-native, automatic state sync, built-in `Watchdog`.
- **Runtime:** IB Gateway (offline build, ~4 GB RAM) on Linux, under Xvfb, wrapped by IBC 3.24.0, ideally in a supervised Docker container (gnzsnz/ib-gateway-docker).
- **Session:** Auto **restart** (not logoff) via IBC `AutoRestartTime`; `ClosedownAt` Friday evening; accept one manual 2FA tap per week after Sunday 01:00 ET; `ReloginAfterSecondFactorAuthenticationTimeout=yes`.
- **Reconnect:** rely on `ib_async` `Watchdog` + a backoff `connect` loop; on reconnect resync `nextValidId`, re-request open orders/positions/account, and re-subscribe market data (branch on 1101 vs 1102).
- **Orders:** use `ib.bracketOrder()` for atomic entry + TP + SL with correct transmit flags; OCA links the TP/SL children.
- **Data/news:** L1 bundle (~$10, waivable) + streaming add-on (~$4.50) + NASDAQ TotalView (~$1.50) non-pro for quotes/depth; Benzinga Pro (~$35) via `reqMktData` `mdoff,292:BZ` for real-time per-ticker breaking news (free feeds are commentary only). Architect around 50 msg/sec and 100 lines.

### Source list
- IBC: https://github.com/IbcAlpha/IBC , https://github.com/IbcAlpha/IBC/blob/master/userguide.md , https://github.com/IbcAlpha/IBC/blob/master/resources/config.ini
- Auto restart / Sunday reset: https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm
- Lock and Exit / API settings: https://ibkrguides.com/tws/usersguidebook/configuretws/configurelockandexit.htm , https://ibkrguides.com/tws/usersguidebook/configuretws/apisettings.htm
- 2FA / IB Key: https://www.ibkrguides.com/securelogin/sls/faq.htm , https://www.interactivebrokers.com/campus/trading-lessons/ib-key-two-factor-authentication-iphone/
- ib_async: https://github.com/ib-api-reloaded/ib_async , https://pypi.org/project/ib_async/ , https://ib-api-reloaded.github.io/ib_async/ , https://ib-api-reloaded.github.io/ib_async/api.html
- ib_insync status: https://github.com/mattsta/ib_insync , https://snyk.io/advisor/python/ib-insync
- Official API: https://interactivebrokers.github.io/tws-api/client_wrapper.html , https://pypi.org/project/ibapi/
- Message/error codes: https://interactivebrokers.github.io/tws-api/message_codes.html , https://interactivebrokers.github.io/tws-api/error_handling.html
- Orders / bracket / OCA: https://interactivebrokers.github.io/tws-api/order_submission.html , https://interactivebrokers.github.io/tws-api/bracket_order.html , https://interactivebrokers.github.io/tws-api/oca.html
- Open orders / state: https://interactivebrokers.github.io/tws-api/open_orders.html
- News: https://interactivebrokers.github.io/tws-api/news.html
- Market data: https://interactivebrokers.github.io/tws-api/market_data_type.html , https://interactivebrokers.github.io/tws-api/historical_limitations.html , https://interactivebrokers.github.io/tws-api/tick_data.html , https://www.interactivebrokers.com/campus/ibkr-api-page/market-data-subscriptions/ , https://www.interactivebrokers.com/en/pricing/market-data-pricing.php , https://www.interactivebrokers.com/en/pricing/research-news-services.php
- VPS footprint: https://tradingfxvps.com/best-vps-for-interactive-brokers-tws-gateway-hosting/
- Docker: https://github.com/gnzsnz/ib-gateway-docker , https://github.com/hartza-capital/docker-ib-gateway
