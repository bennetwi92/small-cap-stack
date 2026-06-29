# Research Findings — Index & Open Questions

**Phase:** Research / context-gathering (pre-design). **Date:** 2026-06-29.
**Status:** All 5 investigations complete. No architecture or app code produced (per phase scope).

This is the table of contents for the `research/` folder, a consolidated reusable-assets list, and the open questions to resolve before the design phase.

---

## 1. Per-file summaries

### [`tradepilot.md`](./tradepilot.md) — prior repo (actually traded this strategy)
A **manual-entry, semi-automated risk-management + execution console** (Python 3.10; FastAPI broker proxy + Tkinter MVVM UI over HTTP/WebSocket; raw `ibapi` in a daemon thread). Covers process **steps 6–8** (plan / execute / manage exits) well and has **nothing for steps 1–5** (scan, float, news, daily chart, bull-flag). The **crown jewel is the exit engine** — R-multiple sizing, post-fill stop/target re-derivation, breakeven auto-arm at 1.5R, high-water-mark trailing stop clamped above the original stop, bad-tick rejection, and a "most-protective-stop-wins" arbitrator — which fills the exit gap the README explicitly left open. No DB, no backtester, no reconnection; automation lives in the desktop UI (not headless); several endpoints return hardcoded mock data. **11 reusable assets.**

### [`entresys_light.md`](./entresys_light.md) — prior repo
A mature, well-tested **Opening-Range-Breakout (ORB)** system (Python 3.11, raw `ibapi`, single-process multi-threaded, MongoDB-backed). **Strategy mismatch** — it trades the 09:30 open, not the Warrior small-cap momentum strategy; no float/short/news/bull-flag/price-band/change%/4am-window logic exists (all net-new). But the **IBKR infrastructure is the crown jewel and directly reusable**: `ib_connector.py` (threaded EWrapper/EClient, thread-safe reqId/orderId allocation, ibapi 9.x/10.x compat shims) and `order_manager.py` (bracket / dual-trail-OCA orders, multi-market tick rounding, 3-tier DB-fallback persistence). Dual-constraint position sizing maps cleanly to step 6; a JSON-driven filter-combo engine and an offline bracket exit-simulator are reusable building blocks. **14 reusable assets.**

### [`ibkr-integration.md`](./ibkr-integration.md) — headless/unattended IBKR ops (web research)
Use **IB Gateway** (offline build, ~4 GB, lighter than TWS) wrapped by **IBC 3.24** under **Xvfb** on Linux, ideally in a supervised Docker container (gnzsnz/ib-gateway-docker). Use **`ib_async` v2.1.0** (the actively maintained fork) — `ib_insync` is dead (author passed away March 2024) and PyPI `ibapi` is stale. `ib_async` is asyncio-native, auto-syncs order/position state, and ships a `Watchdog`. Configure **Auto restart** so weekday restarts skip 2FA, but **security tokens reset every Sunday ~01:00 ET → one manual 2FA login per week**. Reconnection is NOT automatic (backoff loop + Watchdog; branch on error 1101 re-subscribe vs 1102 preserved). `ib.bracketOrder()` gives atomic entry+TP+SL via OCA. Real-time per-symbol breaking news needs **paid Benzinga Pro (~$35/mo)**; free feeds are commentary only. Architect around **50 msg/sec, 100 data lines**, and historical pacing rules.

### [`free-tier-services.md`](./free-tier-services.md) — free infrastructure (web research)
Anchor constraint: a **persistent ~1.5–2 GB RAM process** (rules out serverless / sleep-on-idle). **IB Gateway now has an official ARM/aarch64 build**, which unlocks **Oracle Cloud Always Free (Ampere A1 ARM)** — the only no-expiry free VPS with enough RAM (GCP e2-micro x86 1 GB is the RAM-tight fallback; AWS / Fly.io / Render are unsuitable). CI/CD = **GitHub Actions** (unlimited public, 2,000 min/mo private; or self-hosted runner on the VPS). Monitoring = **Healthchecks.io** dead-man's-switch + **Grafana Cloud free** + **Better Stack**, alerting via Telegram/Discord/email. Storage = **SQLite on the VPS** as primary (3 months of 5-min bars ≈ 50–150 MB), Neon free Postgres if managed Postgres wanted. **Whole stack = $0/month.** Watch the Oracle June-2026 A1 reduction (4→2 OCPU / 24→12 GB) and 30-day idle reclamation.

### [`strategy-validation.md`](./strategy-validation.md) — data feasibility per criterion
Most **per-ticker** criteria are Easy/Medium on free sources (Finnhub quote/news + FMP shares-float + FINRA short interest); price/change%/quote are Easy. The **two hardest gaps** both point to IBKR as the practical backbone: (a) the **pre-market 4am–noon window** — only IBKR (`useRTH=0`) supplies real-time pre-market 5-min bars cheaply; yfinance is too delayed/rate-limited/ToS-exposed for live use; and (b) the **universe scanner** — there is no free real-time pre-market low-float gainer scanner; IBKR's scanner (≤50 rows, ≤10 scans, weak pre-market coverage) is the only cheap option, with float/short applied as post-filters. Bull-flag and candle-count detection are local compute, feasible given clean gap-free bars. Short interest is inherently stale (~1–2 wk lag).

---

## 2. Consolidated reusable assets (repo → path → purpose)

**Priority tier (lift first):**

| Repo | Path | Purpose |
|---|---|---|
| tradepilot | `src/risk_calculator/engine.py` | **Step 6** — pure sizing / R:R / post-fill adjustment / R-multiple. Battle-tested, pure, testable. Highest value. |
| tradepilot | `src/risk_one/view_model.py` (exit block ~792–1100, 1248–1458) | **Step 8** — trailing stop + breakeven (1.5R) + most-protective-stop arbitrator + bad-tick filter + marketable exit orders. **Fills the README's open exit gap.** |
| entresys_light | `src/entresys/data/ib_connector.py` | Raw `ibapi` plumbing: threaded loop, reqId/orderId allocation, bar/tick/order/exec callbacks, version-compat shims. Adopt, then add reconnection. |
| entresys_light | `src/entresys/live/order_manager.py` | **Steps 7–8** — bracket / market / limit / dual-trail-OCA order builders, tick rounding, 3-tier DB-fallback persistence. Most valuable live-trading asset. |
| entresys_light | `src/entresys/live/symbol.py` (`_calculate_position_size`) | **Step 6** — dual-constraint (risk% vs margin) sizing with PDT cap. |

**Secondary tier (reuse / adapt):**

| Repo | Path | Purpose |
|---|---|---|
| tradepilot | `src/broker_service/tw_app.py` | IBKR wrapper incl. `keepUpToDate` live 5-min bar streaming (step-5 input); `outsideRth=True` pre-market order config. |
| tradepilot | `src/broker_service/config.py` | Paper/live port + env safety guards (prevents live-money accidents). |
| tradepilot | `src/risk_one/broker_client.py` (`_round_price_to_tick_size`) | Tick-size rounding essential for $2–10 names; clean WS-client pattern. |
| tradepilot | `src/broker_service/diagnostics.py` | WebSocket stream latency/frequency metrics — observability, reusable as-is. |
| tradepilot | `src/risk_one/STATE_MACHINE.md` | Documented arm/fill/exit position lifecycle. |
| tradepilot | `tests/mock_tw_app.py` + `tests/conftest.py` | Simulated IBKR for safe testing without live fills. |
| tradepilot | `.claude/patterns.md` | 30 codified financial-safety/architecture rules — guardrail seed. |
| entresys_light | `src/entresys/live/strategy.py` (`evaluate_filter_combos` + bar freshness/completeness guards) | Generic JSON-driven feature-gate engine — ideal home for small-cap entry criteria. (Discard the ORB signal itself.) |
| entresys_light | `src/entresys/data/data_manager.py` / `data_sourcing.py` / `database_manager.py` | High-level bar requests with pacing; mongo-first cache orchestration; UTC-store/NY-read tz handling. |
| entresys_light | `src/entresys/live/session.py` | Connection lifecycle, signal handlers, graceful shutdown, market-open wait, account-balance fetch w/ retry. |
| entresys_light | `src/entresys/live/pullback.py` | Single-retracement detection — building block toward bull-flag (not the full pattern). |
| entresys_light | `src/entresys/live/database_fallback.py` | JSON-file fallback for orders/positions/executions on DB outage. |
| entresys_light | `src/entresys/core/or_features.py` (+ `core/features/*`) | Pure single-pass OHLCV feature pipeline (volume, gap, VWAP) — reuse framework + relevant features. |
| entresys_light | `backtest/analysis/pipeline/bracket_simulator.py`, `exit_simulator.py` | Offline 1R/2R/split/time exit simulation — evaluate TP/SL schemes before live. |
| entresys_light | `live/validate_setup.py` | Daily TWS connection/data/order smoke test — pre-trade safety check. |

**Build new (no reusable prior art):** universe scanner (step 1), float/short-interest hard gate (step 2), news feed + relevance scoring (step 3), daily-chart context (step 4), algorithmic bull-flag detector with ≤2 green / ≤2 red candle counting (step 5), 4am–11:59am ET trading-window guard, IBKR auto-reconnect/resubscribe, headless multi-symbol orchestration, real account/position wiring, trade journaling/DB.

---

## 3. Open questions / decisions needed (before design phase)

> **✅ RESOLVED 2026-06-29** — all 11 questions answered; see [`decisions.md`](./decisions.md) for the locked decisions and the 3 remaining technical-validation spikes. Questions retained below for traceability.


### Strategy / data
1. **Float definition:** Is "float should be less than $20M" **20 million shares** or **$20M market value**? Changes the filter entirely. *(README §Strategy says "$20million"; needs your confirmation.)*
2. **Scanner approach:** No free real-time pre-market low-float gainer scanner exists. Accept IBKR's scanner (≤50 rows, ≤10 scans, weak pre-market coverage) with float/short as post-filters — or is broader universe coverage a hard requirement (may force a paid scanner)?
3. **Exit strategy (README leaves this open):** Adopt tradepilot's proven scheme — fixed R:R target + breakeven auto-arm at 1.5R + high-water-mark trailing stop, most-protective-stop-wins? Or define a different TP/SL model? (Note tradepilot's ~10% bid-haircut on exits should be re-examined.)
4. **News source:** Free IBKR/Finnhub feeds are commentary/lagging only; true per-symbol breaking news ≈ Benzinga Pro ~$35/mo. Stay free-only and accept weaker news signal, or is news a hard gate worth paying for? (Also: use Claude to assess news quality, per README §Process step 3?)

### Infrastructure / ops
5. **VPS choice:** Confirm **Oracle Cloud Always Free Ampere A1 (ARM)** as primary (GCP e2-micro fallback). Verify your account's current A1 limit in the OCI console given the June-2026 reduction. Comfortable with Oracle signup friction / idle-reclamation risk?
6. **Market-data entitlement:** The pre-market real-time requirement effectively forces an **IBKR Level-1 subscription** (≈ $10 L1 bundle, often waivable, + ~$4.50 streaming, + ~$1.50 NASDAQ TotalView non-pro). Commit to this paid-but-cheap data backbone, or attempt a free-only stack and accept delayed data (likely unworkable for 4am momentum)?
7. **Weekly 2FA:** Unattended operation has a **hard weekly blocker** — Sunday ~01:00 ET token reset requires one manual IBKR Mobile 2FA approval. Acceptable as a once-a-week phone tap, or do you need a fuller automation rig? (No supported zero-touch path exists.)

### Engineering / process
8. **Branching strategy** (README requires deciding up front): trunk-based with short-lived branches, or GitFlow with `develop`/`release`? Recommendation: trunk-based + protected `main` for a solo project — confirm.
9. **Library / language stack:** Standardize on **`ib_async`** (async) for the new system — even though both prior repos use raw `ibapi`? This means adapting (not lifting verbatim) their connector code. Confirm Python as the language.
10. **Storage:** SQLite-on-VPS as primary store for Phase 1 (3-month data collection), with optional Neon Postgres later — agree?
11. **Phase-1 scope (tracker-only):** Confirm Phase 1 records scanner hits + would-be entries/exits + outcomes WITHOUT placing any orders, for 3 months, to validate the strategy and data pipeline.

---

## 4. What we still don't know

- **Live data quality of free per-ticker sources** (Finnhub/FMP/FINRA) for genuinely thin, low-float small-caps — coverage gaps and staleness are likely but unquantified until tested.
- **Pre-market 5-min bar completeness from IBKR** for low-volume names at 4am — sparse/missing bars would distort the bull-flag and ≤2-green/≤2-red candle-count logic. Needs empirical validation.
- **IBKR scanner adequacy** — whether its pre-market scan actually surfaces the strategy's candidates, or misses them. Unverified.
- **Bull-flag detection spec** — the precise algorithmic definition (flagpole criteria, consolidation tolerance, how ≤2 green / ≤2 red maps to bar classification) is not yet pinned down; no prior repo implements it.
- **Real-world unattended uptime** — how often cold restarts / VPS reboots / OS updates will force mid-week 2FA re-auth in practice.
- **Daily-chart check (step 4)** — the README itself flags this step as "need to research what this step would do"; its purpose/criteria are undefined.
- **Whether free news is good enough** — can't know without your guidance on "what constitutes good news."
- **Actual IBKR market-data monthly cost** for this exact use case until subscriptions are selected in account management.
