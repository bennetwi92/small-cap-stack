# TradePilot — Prior Trading System Code Mining Report

**Repo:** https://github.com/bennetwi92/tradepilot (cloned read-only, depth 1, default branch)
**Date analyzed:** 2026-06-29
**Verdict:** This repo is a **manual-entry, semi-automated risk-management + execution console**, NOT an end-to-end scanner-driven momentum bot. It is strong on steps **6–8** of the target 8-step process (plan / execute / manage exits) and essentially **absent** on steps 1–5 (scan / float / news / daily-chart / pattern detection). The exit-automation engine (trailing stop + breakeven + R-multiple) is the highest-value reusable asset.

---

## 1. High-Level Architecture

**Language/runtime:** Python 3.10, conda env (`environment.yml`). 88-char Black, type hints mandatory, Pydantic v2.

**4-component system with strict boundaries** (`CLAUDE.md`):

| Component | Path | Role | Runtime |
|---|---|---|---|
| `risk_calculator/` | `src/risk_calculator/engine.py` | Pure position-sizing / R:R math, zero deps, no I/O | imported library |
| `broker_service/` | `src/broker_service/main.py` | FastAPI + raw IBKR API wrapper; order exec, market data, historical bars | uvicorn process |
| `risk_one/` | `src/risk_one/` | Tkinter MVVM UI; the actual trading brain (entry/exit automation lives here) | `python -m src.risk_one` process |
| `analytics/` | `src/analytics/` | Read-only historical analysis, Jupyter + scripts | ad hoc |

**Process/task model — NO daemon/cron/supervisor.** Two processes started manually (`make start`):
1. `uvicorn src.broker_service.main:app` (port 8000) — connects to TWS/Gateway.
2. `python -m src.risk_one` — Tkinter desktop UI.

They communicate via **HTTP (orders, subscriptions, fundamentals, calc) + WebSocket (live market data / OHLCV / executions)**. The IBKR client itself runs in a **daemon thread** inside the FastAPI process (`threading.Thread(target=tw_app.run, daemon=True)`), connected in the FastAPI `lifespan` context manager (`main.py:96-133`).

**Key dependencies:** `fastapi`, `uvicorn`, `websockets` (server) / `websocket-client` (UI), `ibapi` (raw IBKR, pip-installed), `pydantic==2.10.6`, `requests`, `tk`, `yfinance==0.2.66`, `scikit-learn`/`pandas`/`numpy` (VIX experiment).

**Important:** automation logic (arm entry, arm exit, trailing stop, breakeven) lives in the **UI ViewModel** (`src/risk_one/view_model.py`), driven by WebSocket ticks — NOT in the broker service. The broker service is a thin, stateless IBKR proxy. This means automation only runs while the desktop UI is open.

---

## 2. IBKR Connection Handling

**API:** Raw `ibapi` (`EClient` + `EWrapper` subclass), **NOT ib_insync / ib_async**. Core class `TWApp` in `src/broker_service/tw_app.py` (872 lines).

- **Auth/gateway:** Assumes a logged-in TWS or Gateway already running locally (`127.0.0.1`). Paper = port **7497**, Live = port **7496** (`config.py` `BrokerConfig`). `clientId = 100 + random.randint(0,99)` (`main.py:43,104`).
- **Connection lifecycle:** `tw_app.connect(...)` then run loop in daemon thread; disconnect on FastAPI shutdown. `set_loop()` stores the asyncio loop so the IBKR thread can push data to WebSocket clients via `asyncio.run_coroutine_threadsafe(...)`.
- **Safety guards (worth copying):** `TWApp.connect()` (`tw_app.py:58-113`) refuses live port in test env, validates port matches `TRADING_MODE`, logs full audit context. `_validate_order_environment()` (`tw_app.py:299-333`) refuses to place orders if a real `TWApp` (not `MockTWApp`) is detected under `APP_ENV=test`.
- **Reconnection logic:** ⚠️ **NONE.** `connectionClosed()` (`tw_app.py:142-181`) only cancels historical subscriptions and broadcasts a `connection_status: disconnected` message. There is no auto-reconnect / retry loop. This is a gap to fix in any reuse.
- **Order placement pattern:** `place_trade_order()` → `_create_contract()` (STK / SMART / USD) + `_create_order()`. Orders set `outsideRth=True` (critical for the 4am–noon pre-market window), `eTradeOnly=False`, `firmQuoteOnly=False`. Supports MKT / LMT / STP / STP LMT. Order/exec state surfaced via `openOrder`, `orderStatus`, `execDetails` callbacks → broadcast over WebSocket.
- **Market data:** `subscribe_to_symbol()` → `reqMktData`; `tickPrice` updates a `{bid,ask,last}` dict and broadcasts `type: market_data`.
- **Historical / streaming bars:** `request_historical_data()` (`tw_app.py:719`) uses `reqHistoricalData` with `whatToShow="TRADES"`, `useRTH=0`, `keepUpToDate=True` for live 5-min bar streaming; threading.Event + 10s/30s timeout to return seed bars synchronously, then `historicalDataUpdate` streams `type: ohlcv_update`. Thread-safe subscription tracking via `_subscriptions_lock`. Duration helper `_convert_duration_to_ibkr_format()` normalizes "30 mins" → "1800 S".

**Reusable snippet — outside-RTH equity order (the load-bearing config for pre-market small-caps):**
```python
order.outsideRth = True      # required for 4am–noon ET trading
order.eTradeOnly = False
order.firmQuoteOnly = False
# contract: secType="STK", exchange="SMART", currency="USD"
```

**Client-side tick-size rounding** (`broker_client.py:99-118`): <$1 → 4 decimals, ≥$1 → 2 decimals, to avoid IBKR limit-order rejection. Directly relevant to $2–10 universe.

---

## 3. Mapping to the 8-Step Strategy Process

| Step | Strategy criterion | TradePilot coverage |
|---|---|---|
| 1. Scan volume spike | 5-min vol >100k, change >10% | ❌ **No scanner.** No universe scan, no volume-spike detector. Symbols are typed in manually. |
| 2. Float / short interest | float <$20M | ⚠️ Partial. `fundamentals_service.py` pulls `floatShares` + `shortPercentOfFloat` from **yfinance**; broker `/fundamentals/{symbol}` returns **hardcoded mock data**. No automated float gate / filtering. |
| 3. News check | breaking news | ❌ **None.** No news feed/integration anywhere. |
| 4. Daily chart check | trend context | ❌ Not implemented (only intraday 5-min bars fetched). |
| 5. Bull-flag detection (≤2 green ext / ≤2 red consolidation candles) | pattern | ❌ **No pattern detection.** 5-min OHLCV bars are fetched and rendered as candlesticks in the UI for the human to eyeball; no algorithmic bull-flag logic. |
| 6. Plan position (risk/sizing/entry) | — | ✅ **Strong.** `risk_calculator/engine.py` — see §4. |
| 7. Execute entry | — | ✅ Entry automation: `ENTRY_ARMED` → auto-buy when `last_price >= plan_entry_price` (`view_model.py:1239-1246`, `_place_entry_order`). |
| 8. Manage TP/SL real-time | — | ✅ **Strong, the gem.** Trailing stop + breakeven + target — see §4. |

**Net:** TradePilot implements the *back half* of the funnel. A small-cap-stack rebuild can lift steps 6–8 wholesale and must build steps 1–5 new.

---

## 4. Risk / Sizing / Entry / Exit Logic (the valuable part)

### Sizing & planning — `src/risk_calculator/engine.py` (pure, 356 lines)
- `risk_per_trade = (percent_risk/100) * balance`.
- `simulated_entry = plan_entry + slippage`; `risk_per_share = simulated_entry - plan_stop`.
- `target_quantity = int(risk_per_trade / risk_per_share)` (floors to respect risk cap).
- Commission `$2.02 / 100 shares` (`IBKR_COMMISSION_PER_100_SHARES`); default slippage `0.03` tuned for "$2–$20 low-float" names.
- `target_price = simulated_entry + R:R * risk_per_share`.
- **Post-fill re-derivation:** after real fill, recomputes `adjusted_target_price` and `adjusted_stop_price` to preserve the intended dollar-risk-per-share and R:R against the *actual* fill (handles partial fills via `fill_ratio`).
- **R-multiple:** `(position_high - entry_fill) / (entry_fill - adjusted_stop)`.
- Engine is **stateless/pure** and *preserves* broker-fed fields (fills, bid/ask, position_high) from `existing_outputs` — never overwrites them (engine §1).

### State machine — `src/risk_one/STATE_MACHINE.md`
`DISARMED → ENTRY_ARMED → EXIT_ARMED → DISARMED`. Entry trigger = ask/last crosses plan entry; exit triggers = stop/target. Driven entirely by WebSocket ticks in the ViewModel.

### EXIT logic (README leaves this open — here is what they actually ran)
All in `src/risk_one/view_model.py`. On every `market_data` tick while `EXIT_ARMED` (`view_model.py:1248-1262`):
```
if last_price <= effective_stop_price:  -> _place_exit_order("STOP_LOSS")
elif last_price >= adjusted_target_price: -> _place_exit_order("TARGET_PRICE")
```

Three layered exit mechanisms feed `effective_stop_price`:

1. **Fixed target (TP):** `adjusted_target_price` from R:R. Hard exit.
2. **Breakeven stop:** auto-arms when `r_multiple >= breakeven_r_threshold` (default **1.5R**), moving the stop to entry price (`engine.py` §5, `arm_breakeven`, `view_model.py:1226-1236`). Can be manually armed/disarmed; `_breakeven_manually_disarmed` flag prevents nagging re-arm.
3. **Trailing stop:** activates at `trailing_stop_r_threshold` (1.0–10.0R), trails the high-water-mark by `trailing_stop_distance_r * initial_risk`, **clamped never to fall below the original stop** (`_calculate_trailing_stop_price_long`, `view_model.py:863-903`). Includes **bad-tick rejection** (`is_bad_tick`: rejects price moves > `MAX_PRICE_CHANGE_THRESHOLD = 5.0` R, `view_model.py:792-830`).

**Stop precedence:** `_handle_trailing_breakeven_interaction()` (`view_model.py:951-1000`) picks the **most protective (max) stop** among trailing / breakeven / adjusted_stop for a long. Clean, reusable arbitration logic.

**Exit order marketability (notable quirk):** ALL exits — including target hits and manual closes — place a **SELL LMT at `bid_price - (bid_price * exit_limit_price_reduction_percent)`** (aggressive ~10% haircut) for fill probability in fast small-caps (`_place_exit_order` `view_model.py:1406-1458`, `manual_close_position`). Fallback to stop/target price only if no live bid. Reasonable for thin names but worth re-examining (a 10% bid haircut can give up real money on a target exit).

**Entry order:** `_place_entry_order` buys `target_quantity` as LMT at `max(ask,last) + slippage`, fallback `plan_entry_price`.

**Execution accounting:** `_handle_execution_data` (`view_model.py:1269+`) maintains weighted-average fill price and cumulative filled qty for BOT/SLD, flips `position_status` OPEN→CLOSED, auto-arms exit when fully filled.

---

## 5. Backtesting, Data Storage, Monitoring

- **Backtesting:** ❌ None. Closest artifact is an **experimental VIX Random-Forest** model (`src/risk_one/services/vix_service.py`, `vix_model_rf_v1.pkl`) — explicitly flagged "DO NOT use for real trading, trained on 33 days." Not a backtester; skip.
- **Data storage:** No DB. Only `src/risk_one/persistence.py` — JSON "stash" of `ManualInputs` at `~/.tradepilot/risk_one_stash.json`. Plus `cache/vix_data_v1.pkl`. Account/positions are **mock/hardcoded** in `main.py` (`DU123456`, $12,000) — never wired to real TWS account/position APIs (TODO Phase 2.3). No trade journal / fills persistence.
- **Monitoring/observability:** `src/broker_service/diagnostics.py` (`MessageDiagnostics`) — WebSocket message frequency per symbol, broadcast latency (avg/p95/max), timeout/failure counters, exposed via `GET /diagnostics` and `POST /diagnostics/print`. Decent reusable pattern for stream health. Otherwise logging is inconsistent (lots of `print()` mixed with `logging`).

---

## 6. Reusable Assets Table

| File | What it does | Why worth keeping |
|---|---|---|
| `src/risk_calculator/engine.py` | Pure sizing / R:R / post-fill adjustment / R-multiple | Battle-tested, pure & testable; near drop-in for step 6. Highest value. |
| `src/risk_one/view_model.py` (exit block `~792–1100`, `1248–1458`) | Trailing stop, breakeven, stop-precedence, bad-tick filter, marketable exit orders | The strategy's actual exit brain (step 8). README left exits open — this fills it. |
| `src/broker_service/tw_app.py` | Raw `ibapi` EClient/EWrapper wrapper: orders, L1 data, streaming 5-min bars, threaded run loop | Reusable IBKR plumbing incl. `keepUpToDate` 5-min bar streaming (directly needed for step 5 input). |
| `src/broker_service/config.py` | Paper/live port + env safety (`BrokerConfig`) | Prevents live-money accidents; clean fail-safe defaults. |
| `src/broker_service/main.py` | FastAPI broker proxy: lifespan TWS connect, WebSocket broadcast, REST endpoints | Reference for HTTP+WS broker-service shape; copy structure, replace mock endpoints. |
| `src/risk_one/broker_client.py` | HTTP+WS client incl. `_round_price_to_tick_size` | Tick-size rounding is essential for $2–10 names; clean WS client pattern. |
| `src/broker_service/diagnostics.py` | WebSocket stream latency/frequency metrics | Stream-health observability; reusable as-is. |
| `src/risk_one/STATE_MACHINE.md` | Documented automation/position state machine | Saves re-deriving the arm/fill/exit lifecycle. |
| `src/risk_one/services/fundamentals_service.py` | yfinance float / short-interest fetch → Pydantic | Partial step 2 (float gate) — but yfinance latency/reliability caveats. |
| `tests/mock_tw_app.py` + `tests/conftest.py` | Simulated IBKR for safe testing during live trading | Lets you test execution logic without TWS / risking fills. |
| `.claude/patterns.md` | 30 codified financial-safety/architecture rules | Good guardrail seed (Decimal-for-money, unit comments, no bare except). |

**Count of reusable assets identified: 11.**

---

## 7. Tech Debt / Pitfalls — Do NOT Repeat

1. **No scanner / news / pattern detection at all** — steps 1–5 are entirely manual. The biggest build-from-scratch gap for a Warrior-style system.
2. **No IBKR reconnection logic** — `connectionClosed` just cleans up; a dropped TWS connection silently kills automation. Add reconnect + heartbeat.
3. **Automation lives in the desktop UI ViewModel, not a service** — strategy only runs while Tkinter is open and only for the single symbol on screen. Not headless, not multi-symbol. For a real bot, move automation into a service/daemon.
4. **Mock data masquerading as real** — `/account`, `/positions`, and broker `/fundamentals/{symbol}` return hardcoded values (e.g. account `DU123456`, $12,000). Easy to mistake for live data. Never ship mocks on data-bearing endpoints without a loud flag.
5. **`float` used for money despite a Decimal rule** — patterns.md mandates `Decimal`, but engine/ViewModel use `float` throughout. Rounding/precision risk.
6. **Aggressive 10% bid haircut on ALL exits including targets** — can surrender meaningful P&L on target exits; revisit per order type.
7. **Inconsistent logging** — `print()` scattered alongside `logging`; no structured audit/trade log persisted.
8. **`position_status` is a free-form string** ("OPEN"/"DRAFT"/"CLOSED") while `automation_status` is an enum — inconsistent and error-prone (string compared to "OPEN" in some spots, "POSITION_OPEN" documented elsewhere).
9. **VIX RF model is experimental noise** (33 days training) — ignore for trading; don't let it imply a backtesting capability exists.
10. **No persistence of trades/fills** — only a JSON stash of planned inputs; no journal, no DB, no P&L history.

---

## 8. Recommended Lift-and-Reuse Plan (for small-cap-stack)
- **Lift directly:** `risk_calculator/engine.py`, the exit-automation block from `view_model.py`, `tw_app.py` IBKR wrapper + 5-min `keepUpToDate` streaming, `config.py` safety, `broker_client._round_price_to_tick_size`, `diagnostics.py`, `mock_tw_app.py`.
- **Rebuild new:** scanner (vol-spike, %change, price band), float/short gate as a hard filter, news feed, daily-chart context, algorithmic bull-flag detector (≤2 green / ≤2 red), trading-window guard (4am–11:59am ET), reconnection, headless multi-symbol automation, real account/position wiring, trade journaling/DB.
