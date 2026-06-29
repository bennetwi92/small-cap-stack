# Prior-Repo Mining Report: `entresys_light`

Source: `https://github.com/bennetwi92/entresys_light` (cloned `--depth 1`, read-only, into scratch).
Date: 2026-06-29. 264 tracked files, Python 3.11, conda env `e-lite`.

> **Bottom line for the small-cap stack:** `entresys_light` is a mature, well-tested
> **Opening-Range-Breakout (ORB)** system, *not* a Warrior-style float/news/bull-flag
> momentum scanner. The **infrastructure** (raw-`ibapi` connector, bracket/dual-bracket
> order manager with 3-tier DB fallback, event-driven session lifecycle, multi-currency
> account sizing, MongoDB caching, pullback detection, backtest exit simulator) is
> high-quality and directly reusable. The **entry signal logic** (OR breakout) does NOT
> map to the target strategy and should be replaced, not lifted. There is **no** float,
> short-interest, news, or true bull-flag detection anywhere in the repo — those are net-new.

---

## 1. High-Level Architecture

### Process / task model
- **Single process, multi-threaded.** No async/await, no `ib_insync`/`ib_async` — uses the
  **raw `ibapi`** package (`EClient`/`EWrapper`). The IBKR message loop (`EClient.run()`)
  runs in one daemon thread (`run_thread()`); the main thread runs the session loop.
- **Callback-driven**, not polling. Bar arrivals fire registered callbacks (`<10ms` latency
  claimed). The main `Session._event_loop` only polls every ~1s for completion and every 5s
  for connection health (`src/entresys/live/session.py:484`).
- Thread-safety via `threading.Lock`/`Event` on every shared structure in `IBConnector`
  (order status, account summary, executions, req-id allocation). Strategy state mutation
  uses a per-strategy `_state_lock` with a double-check pattern (`strategy.py:813`).

### Three product surfaces
1. **US live trading** — `src/entresys/` (`core/`, `data/`, `live/`) + `scripts/live/`.
2. **Multi-market backtest** — `backtest/` (LSE, B3, XETRA, + 6 more), runner pattern,
   per-symbol Mongo collections (`backtest/runners/`, `backtest/engine/orchestrator.py`).
3. **Analytics/research** — `data-science/`, `analytics/` (Panel dashboards), Jupyter.

### Entry points
- Live: `scripts/live/run.py` (current) and `live/run*.py` (legacy, being phased out — one
  per market: `run_us.py`, `run_lse.py`, `run_b3.py`). CLI flags: `--symbols --periods
  --risk-pct --max-positions --target-r --market-open --sounds --debug`.
- Connection smoke test (run daily before trading): `live/validate_setup.py`.
- Backtest: `python -m backtest.runners.backtest_lse`, `... .engine.orchestrator --market ...`.
- Data collection: `scripts/data/collect_bars.py`.

### Runtime layering (enforced separation of concerns, documented in `CLAUDE.md`)
```
ib_connector.py     raw ibapi callbacks/threading, NO business logic
data_manager.py     high-level IBKR requests + DataFrame construction + pacing
database_manager.py ALL MongoDB ops (no API logic)
data_sourcing.py    DataSource orchestration: mongo-first / mongo-only / ibkr-only
core/ (or_features) pure feature functions on DataFrames (no I/O)
live/ (session/symbol/strategy/order_manager) real-time orchestration + execution
```

### Key dependencies
`pandas`, `pymongo`, `ibapi` (raw), `numpy`, `scipy`, `python-dotenv`, `pytz`,
`streamlit`/`panel`/`plotly` (dashboards), `textual`/`rich` (TUI), `yfinance` (FX-rate
fallback only). Dev: `pytest`, `pytest-mock`, `pytest-cov`, `pytest-asyncio`.
Storage: **MongoDB** database `entresys_lite` (note: package is `entresys-light`, DB is
`entresys_lite` — naming mismatch). Config via `.env` (`MONGO_URI`).

---

## 2. IBKR Connection Handling  ⭐ (most reusable)

**File:** `src/entresys/data/ib_connector.py` — `class IBConnector(EWrapper, EClient)`.

- **Auth / transport:** plain TCP socket to `127.0.0.1`. No username/password — relies on
  TWS/Gateway being logged in with API enabled. **TWS port 7497 (paper) / Gateway 4002.**
  Live ports (7496/4001) are referenced but the code is hard-wired to paper in `Session`.
- **Connect sequence** (`session.py:348 _connect_to_ibkr`): random `clientId` (1–9999) to
  dodge "client id in use"; `connect()` then `run_thread()`; wait on `connection_event`
  (10s timeout); then `sleep(2)` and verify `nextValidOrderId` arrived. Reusable pattern.
- **`nextValidId` callback** sets `nextValidOrderId` and fires `connection_event`.
- **No automatic reconnection.** Health is *monitored* (`_check_connection_health` →
  `isConnected()`); on loss the session raises `ConnectionError` and shuts down cleanly.
  There is retry-with-exponential-backoff only for the **account-balance fetch**
  (`session.py:_fetch_account_balance`, 3 tries). **Gap: no reconnect/resubscribe logic** —
  worth adding for the new system.
- **Request-id management:** thread-safe allocators `get_next_req_id()` (data, starts 1000)
  and `get_next_order_id(count=N)` (orders, atomic N-block allocation for brackets).
- **Order-id version compat:** `cancel_order()` wraps the ibapi 9.x vs 10.x `OrderCancel`
  signature difference (`ib_connector.py:599`). `_coerce_bar_types()` converts ibapi 10.x
  `Decimal` bar fields to float/int for pandas (`ib_connector.py:78`). Keep both.
- **Market-data subscriptions supported:** `reqHistoricalData` (with `keepUpToDate` →
  `historicalDataUpdate`), `reqRealTimeBars`, `reqTickByTickData` (last + bid/ask),
  `reqHistoricalTicks`. Callbacks dispatch by `reqId` into handler dicts
  (`data_handlers`, `tick_handlers`, `bidask_handlers`) plus a symbol→callback registry for
  bars. Historical pacing: `time.sleep(2.0)` before each request (`data_manager.py:46`).
- **Order/exec persistence:** `IBConnector` optionally takes a `db_manager` and persists
  `orderStatus` and `execDetails` callbacks to Mongo — but wrapped so DB failures never
  interrupt trading (logged, swallowed). Good pattern.

Reusable connect snippet (paraphrased, `session.py:350`):
```python
client_id = random.randint(1, 9999)        # avoid "client id already in use"
self.ib = IBConnector(db_manager=self.db_manager)
self.ib.connect("127.0.0.1", self.port, clientId=client_id)
self.ib.run_thread()
if not self.ib.connection_event.wait(timeout=10):
    raise RuntimeError("Failed to connect to IBKR")   # TWS/Gateway not running
time.sleep(2)                               # let nextValidOrderId arrive
```

---

## 3. Scanner / Float / News / Pattern Detection — mapped to the 8-step process

The target strategy's 8 steps map only **partially**. Honest gap analysis:

| Target step | In `entresys_light`? | Where / notes |
|---|---|---|
| 1. Scan volume spike (5-min vol >100k) | **Partial** | Ranks by **nominal volume** (Σ Close×Volume) over OR bars, picks top-N. No absolute 100k threshold, no "spike vs baseline" except `rvol_5d/45d` computed from prior `screens`. `scan_strategies/top_3_nominal_volume.py`, `screener.py`, `strategy.calculate_lookback_features`. |
| 2. Check float / short interest | **MISSING** | No float, shares-outstanding, or short-interest data anywhere. Net-new. |
| 3. Check news / breaking-news catalyst | **MISSING** | No news feed/NLP. Watchlists are *manually* scraped from Bear Bull Traders. Net-new. |
| 4. Daily chart check | **Partial** | `overnight_gap_pct`, `gap_direction`, `prev_day_volume` from prior-day 1-min bars; no daily-trend/MA structure check. `or_features.py`. |
| 5. Bull-flag on 5-min bars (≤2 green extension, ≤2 red consolidation) | **No bull-flag; has pullback** | `live/pullback.py` detects a single retracement inside the OR (lower-low after high for LONG), not the multi-candle flag-pole/consolidation rule. Reusable as a *building block*, not the pattern. |
| 6. Plan position (risk/sizing/entry) | **Yes (strong)** | Dual-constraint sizing (risk% of equity vs margin cap), `symbol._calculate_position_size`. |
| 7. Execute entry | **Yes (strong)** | Bracket / market-bracket / limit-bracket / dual-bracket via `order_manager.py`. |
| 8. Manage TP/SL real-time | **Yes** | Native IBKR bracket + OCA + trailing stops; managed broker-side, "hands-off". |

**Price/float/time-window criteria** ($2–10, float <$20M, change >10%, window 04:00–11:59 ET):
none of these gates exist. Pre-market window constant exists (`PRE_MARKET_START = 04:00`,
`constants.py`) but the strategy keys off **market open (09:30)**, not a 4am–noon momentum
window. All of these are net-new for the small-cap system.

### Entry signal that *does* exist (replace, don't reuse)
`live/strategy.py` — ORB: when the OR period (1/3/5/10/15 min) completes it records
`or_high/or_low/or_range`, then triggers on the **first 1-min bar that *closes* beyond the
OR boundary** within an entry window of `max(15min, …)`. Includes bar-freshness
(`MAX_BAR_AGE_MINUTES=2`) and bar-completeness (`MIN_BAR_AGE_SECONDS=65`) guards — those
two guards are reusable for any bar-close-triggered system.

### Filter engine (reusable, strategy-agnostic) ⭐
`Strategy.evaluate_filter_combos()` / `evaluate_rules()` (`strategy.py:320,487`) is a clean,
generic feature-gate evaluator: OR-across-combos, AND-within-combo, operators
`> < >= <= between outside ==`, NaN/Inf → conservative exclude. Combos are loaded from JSON
(`config/filter_combos/*.json`) produced by the offline discovery pipeline. This is a good
home for the small-cap criteria (price band, change%, volume threshold, float band) once
those features are fed in.

---

## 4. Risk, Sizing, Entry, Exit / TP / SL

### Position sizing — `symbol._calculate_position_size` (`symbol.py:1273`) ⭐
Dual-constraint, takes the **minimum** of:
1. **Risk-based:** `qty = (cash_balance * risk_pct/100) / per_share_risk`.
2. **Margin-based:** `qty = (usable_cash * max_cash_per_position / margin_req) / entry_price`,
   where `usable_cash = cash * 0.95` (buffer) and per-symbol, per-direction margin reqs come
   from `config/position_sizing.json`. PDT note: `max_cash_per_position` defaults to `1/max_positions`
   (0.25 → 4 concurrent positions).

### Stops / targets
- Stop = `STOP_PERCENTAGE` into the OR from the breakout boundary.
  ⚠️ **Doc/code mismatch:** `CLAUDE.md` and live docstrings say 25%; `constants.py:STOP_PERCENTAGE = 0.5`
  (midpoint). Verify intended value before reusing.
- Risk `R = |entry − stop|`; target = `entry ± optimal_target·R` (default `optimal_target` 1.0R;
  pullback strategy uses 2.0R).
- **Pullback-based stop:** `order_strategies/pullback_bracket.py` places the stop at the
  pullback's extreme wick when a pullback exists, else falls back to the OR stop — tighter
  risk. Good idea to keep.

### Order execution — `live/order_manager.py` ⭐ (very reusable)
All orders are IBKR **bracket orders** (parent + children, `transmit=False` until the last
child, `parentId` linkage). Variants:
- `submit_bracket_order` — limit parent at `ask+0.05`/`bid−0.05` for marketability.
- `submit_market_bracket` — market entry + stop + target.
- `submit_limit_bracket` — limit entry with `timeout_seconds` + `timeout_action`
  (CANCEL or MARKET) and configurable TIF.
- `submit_dual_bracket_orders` — splits qty 50/50 into two OCA brackets: B1 = 2R target +
  0.75R trailing stop; B2 = −0.95R hard stop + 1.5R trailing stop. Demonstrates `TRAIL`
  orders + `ocaGroup`/`ocaType=1`.
- Multi-market tick rounding `_round_price` (USD $0.01, BRL R$0.01, LSE price-banded pence).
- `outsideRth=True`, `eTradeOnly=False`, `firmQuoteOnly=False` on every order (premarket
  execution + avoids IBKR error 10268) — useful gotchas to copy.
- **3-tier persistence** (`_create_and_persist_position`): Mongo → fallback manager → local
  JSON file (`db_fallback/`). Trading never blocks on DB errors.

### Backtest exit modeling
`backtest/analysis/pipeline/bracket_simulator.py` + `backtest/analysis/exit_simulator.py`
simulate 1R / 2R / split(50%@1R,50%@2R) / time-based exits over 1-min bars and report
`exit_efficiency = exit_r / max_r`. Reusable for evaluating TP/SL schemes offline.

---

## 5. Backtesting, Data Storage, Monitoring

- **Backtest engine:** `backtest/engine/orchestrator.py`, market-agnostic config dict
  (`backtest/core/config.py` `MARKETS`), per-market runners, per-symbol Mongo collections
  (`lse_1_min_bar_LLOY`) claimed 60× faster than shared collections. Golden-reference
  timezone tests exist (`tests/backtest/e2e/test_timezone_golden_reference.py`).
- **Filter / strategy discovery:** `backtest/analysis/pipeline/` (filter_discovery,
  oos_validation, consistency_metrics) + `data-science/scripts/` produce the JSON filter
  combos consumed live. Good offline→online loop to imitate.
- **Data storage (MongoDB `entresys_lite`):** time-series collections `1_min_bar_with_vwap`,
  `30_min_bar` (UTC stored, converted to `America/New_York` on read); `samples`
  (features+targets, upsert by symbol+date); `screens` (per-day OR snapshots used for
  `rvol_5d/45d` lookback); positions/orders/executions collections; local `db_fallback/`.
- **Monitoring / observability:** mostly `print()` to a colorized console
  (`live/console.py`), optional sound alerts (`live/sound.py`), a Textual live dashboard
  (`live/live_dashboard.py`), and Panel analytics dashboards (`analytics/`). Uses Python
  `logging` in data/order layers. **No metrics/Prometheus/structured logs/alerting** — a
  gap if you want unattended operation.
- **Tests:** large pytest suite (`tests/` US + `tests/backtest/`), with unit/integration/
  e2e/live splits, shared fixtures in `conftest.py`. Strong example coverage for the
  order manager, strategy, session, and timezone handling.

---

## 6. Reusable Assets Table

Lift small pieces; reference paths over copy-paste. Paths are repo-relative to the clone.

| File path | What it does | Why worth keeping |
|---|---|---|
| `src/entresys/data/ib_connector.py` | Raw `ibapi` EWrapper/EClient: threaded loop, reqId/orderId allocation, bar/tick/order/exec callbacks, version-compat cancel & Decimal coercion | Battle-tested IBKR plumbing; the hardest part to get right. Adopt wholesale, then add reconnection. |
| `src/entresys/data/data_manager.py` | High-level historical-bar / executions / completed-orders requests with pacing | Clean request/response-with-Event pattern; pacing built in. |
| `src/entresys/data/data_sourcing.py` (+ `database_manager.py`) | mongo-first/only/ibkr-only cache orchestration; all Mongo ops | Reusable caching strategy + tz handling (UTC store / NY read). |
| `src/entresys/live/order_manager.py` | Bracket / market / limit / dual-trail bracket builders, OCA, trailing stops, multi-market tick rounding, 3-tier persistence | The single most valuable live-trading asset. TP/SL/entry execution = target steps 7–8. |
| `src/entresys/live/session.py` | Connection lifecycle, signal handlers, graceful shutdown, market-open wait, multi-currency account-balance fetch (IBKR + yfinance FX fallback) w/ retry | Robust session scaffolding + FX/balance logic; reuse minus ORB-specific screening. |
| `src/entresys/live/symbol.py` (`_calculate_position_size`) | Dual-constraint (risk vs margin) position sizing w/ PDT cap | Directly implements target step 6 sizing. |
| `src/entresys/live/strategy.py` (`evaluate_filter_combos`, `_evaluate_filter`, freshness/completeness guards) | Generic JSON-driven feature-gate engine + stale/incomplete bar guards | Strategy-agnostic; ideal place to encode small-cap entry criteria. (OR breakout logic itself: discard.) |
| `src/entresys/live/pullback.py` | Single-retracement detection inside a bar window | Building block toward (not equal to) bull-flag detection. |
| `src/entresys/live/database_fallback.py` | JSON-file fallback for orders/positions/executions | Prevents data loss on DB outage; drop-in. |
| `src/entresys/core/or_features.py` (+ `core/features/*`) | Pure single-pass OHLCV feature pipeline (volume, gap, VWAP distance/crossovers, volume concentration/trend, etc.) | Clean NamedTuple/generator functional style; reuse the framework + relevant features (gap%, volume). |
| `config/position_sizing.json`, `config/order_execution.json` | Per-symbol margin reqs, cash buffers, limit-offset/timeout/TIF config | Schemas to copy; refresh margin values from TWS. |
| `backtest/analysis/pipeline/bracket_simulator.py`, `backtest/analysis/exit_simulator.py` | Offline 1R/2R/split/time exit simulation with exit-efficiency | Evaluate TP/SL schemes before going live. |
| `live/validate_setup.py` | Daily TWS connection / data / order smoke test (orders auto-cancelled) | Pre-trade safety check; adapt directly. |
| `live/console.py`, `live/sound.py`, `live/live_dashboard.py` | Colorized console, audio alerts, Textual dashboard | Lightweight observability for an attended session. |

**Count of reusable assets identified: 14** (table rows above; ~25 files including the
`core/features/*` and `order_strategies/*` siblings).

---

## 7. Tech Debt / Pitfalls / Things NOT to Repeat

- **Strategy mismatch.** This is ORB, not Warrior small-cap momentum. Do **not** reuse the
  OR entry signal, the OR-period scaffolding in `Strategy`, or the "nominal volume top-N"
  screen as your scanner. Keep the *plumbing*, replace the *signal*.
- **No reconnection / resubscription.** Connection loss = full shutdown. Acceptable for an
  attended session; insufficient for unattended intraday running. Add reconnect + idempotent
  resubscribe if you want hands-off operation.
- **Paper-trading hard-wiring.** `Session` advertises "PAPER TRADING ONLY" and defaults port
  7497; going live needs deliberate changes. Good safety default, but be aware.
- **Stop-percentage ambiguity.** `constants.STOP_PERCENTAGE = 0.5` contradicts the 25% in
  docs/docstrings. Pin this down before reusing the stop math.
- **`time.sleep()`-based order sequencing.** `_submit_bracket_to_ibkr` uses fixed 0.1–0.5s
  sleeps between parent/children and a 2s historical-pacing sleep. Works, but fragile/slow;
  consider event-driven confirmation instead.
- **Observability is print-based.** No structured logging, metrics, or alerting. Re-architect
  if you need monitoring/dashboards beyond an attended terminal.
- **`print()` mixed with `logging`.** Inconsistent; live layer is almost all `print`.
- **Legacy/duplication.** Two parallel live trees (`live/` legacy vs `scripts/live/` +
  `src/entresys/live/`), and two `market_data_manager`/`database_manager` copies (US vs
  `backtest/`). Pick one lineage; don't carry both forward.
- **Manual data dependencies.** Watchlists are hand-scraped from Bear Bull Traders; margin
  reqs are hand-edited daily. Both are toil the new system should automate.
- **DB name vs package name mismatch** (`entresys_lite` vs `entresys-light`) — minor but a
  known footgun in queries.
- **`samples`/feature pipeline assumes Mongo** for prev-day data; live feature calc silently
  skips gap features when Mongo lacks the prior day (`live_features.py:66` broad
  `except (ValueError, Exception)`), which can mask real errors. Tighten exception handling.
