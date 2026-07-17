# Architecture Research: Event-Driven Trading Framework vs. Assemble-on-`ib_async`

**Date:** 2026-06-29
**Question:** Should the small-cap momentum system adopt a full open-source event-driven framework (and write only strategy code), or assemble a lightweight custom system on the already-chosen `ib_async` IBKR client?
**Constraints:** Oracle Cloud Ampere **ARM/aarch64** VPS, headless/unattended, Python; free/open-source only; phased rollout **Phase 1 = track only (log opportunities for 3 months)** → Phase 2 paper → Phase 3 live; principle "store raw, compute derived on read"; strong "don't reinvent the wheel" preference.

---

## TL;DR Recommendation

**Assemble a lightweight custom system on `ib_async`** for Phases 1–2, and **re-evaluate adopting NautilusTrader only at Phase 3** if live-execution complexity (OMS, risk, position reconciliation) actually justifies it. For Phase 1 "track only," a full framework is pure overhead: you would not use its execution engine, order/portfolio/risk subsystems, or backtest-parity machinery at all — you would only use its event loop, which `ib_async` already gives you natively. The frameworks also create friction against the constraints you have already locked in: NautilusTrader's IB adapter uses the official `ibapi` (not `ib_async`), so adopting it **replaces** your chosen client; and it ships **no stable ARM64 wheels** (source build with Rust+clang required on aarch64). LEAN has documented IB-on-ARM live-trading limitations and a heavy Docker footprint.

---

## Comparison Table

| Framework | Language / Runtime | Maturity & Maintenance (as of 2026-06) | License | Event-driven? | Backtest+Live parity? | IBKR support quality | ARM/aarch64 | Footprint | Learning curve | Fit for track→paper→live |
|---|---|---|---|---|---|---|---|---|---|---|
| **NautilusTrader** | Rust core + Python API | **Very active.** v1.228.0 beta, 25 Jun 2026; frequent releases | LGPL-3.0 (commercial OK) | **Yes** (Rust-native deterministic engine) | **Yes** (same engine backtest & live) | Strong, mature adapter — but uses official **`ibapi`** (repackaged as `nautilus-ibapi`), **not `ib_async`**; needs TWS/Gateway | **No stable PyPI wheels for ARM64** — build from source (Rust+clang+uv) or use nightly `--pre` wheels (not recommended for live). Py 3.12–3.14 | Moderate–heavy (Rust toolchain to build) | **High** | Excellent for live; overkill for track-only; ARM build is a real ops burden |
| **QuantConnect LEAN** | C#/.NET (Python algos), Docker | Active, large org-backed | Apache-2.0 | Yes (scheduled/event) | Yes | Good, but **local live IB on ARM is problematic** (M1/ARM local live-deploy limitations); Docker images "bulky, not size-optimized" | ARM64 Dockerfile exists, but IB **live** on ARM is the weak spot | **Heavy** (Docker, .NET runtime) | High (.NET + LEAN conventions) | Cloud-oriented; self-host on ARM VPS is awkward; replaces `ib_async` |
| **backtrader** | Pure Python | **Effectively archived** (~last real update 2020); Py 3.10+ / modern matplotlib friction | GPL-3.0 | Yes (event-driven) | Partial (live via `IBStore`, dated) | Live via `IBStore` but old/manual; not actively fixed | Pure Python (runs anywhere) but stale deps | Light | Low–moderate | Fine to learn from; not for production |
| **vectorbt** (OSS) | Python (NumPy/Numba) | Maintained; OSS is research edition | Apache-2.0 (PRO is commercial) | **No** (vectorized) | Backtest/research only | None for live | Pure Python; OK on ARM | Light–moderate | Moderate | **Research/backtest only** — useful as an offline analysis tool on logged data |
| **vectorbt PRO** | Python | Active, commercial | Commercial (paid) | No | Research-heavy | None native | OK | Moderate | Moderate | Out of scope (not free) |
| **Zipline-reloaded** | Python | Maintained for compatibility (stefan-jansen) | Apache-2.0 | Event-loop (research) | **Backtest/research only** | No official live (third-party bridges, unmaintained) | Pure Python | Light–moderate | Moderate | Equity-factor research only |
| **QSTrader** | Python | Maintained by QuantStart, modest activity | MIT | Schedule-driven | Backtest + some live | Limited | Pure Python | Light | Low–moderate | Schedule-driven, not tick/event for intraday scalping |
| **LiuAlgoTrader** | Python | Niche; multi-process, ML-oriented | MIT | Yes | Partial | Alpaca-first; IB weaker | Pure Python | Moderate | Moderate | Niche; small community |
| **PyAlgoTrade** | Python | **Stale** (Python-2 era origins) | Apache-2.0 | Yes | Backtest + paper/live | Dated | Pure Python | Light | Low | Not recommended |
| **bt** | Python | Maintained, portfolio-allocation focus | MIT | No (periodic rebalance) | Backtest only | None | Pure Python | Light | Low | Wrong paradigm (allocation, not intraday) |

---

## Narrative

### What Phase 1 actually requires
Phase 1 is **track-only**: scanner → gate checks (price/float/news/5-min volume/%change/bull-flag) → plan a (hypothetical) position → log the opportunity. There are **no orders, no portfolio state, no fills, no risk engine, no backtest-vs-live reconciliation**. The single hard requirement is: subscribe to market data, react to ticks/bars in an event loop, evaluate gates, and persist raw events ("store raw, compute derived on read").

`ib_async` already delivers the event loop and the data subscriptions: it is an asyncio framework with event callbacks (ticker updates, bar updates, news, scanner subscriptions) on top of IB's TWS/Gateway. Layering a full trading framework on top to get "event-driven" buys nothing in Phase 1 — and every framework's *core value* (the execution/OMS/portfolio engine) sits idle.

### The `ib_async` conflict
You have already chosen `ib_async` (the maintained `ib_insync` successor under the `ib-api-reloaded` org; latest **v2.1.0, Dec 2025**, actively maintained by Matt Stancliff). This matters because **NautilusTrader's IB adapter does not use `ib_async`** — it uses the official `ibapi` (repackaged as `nautilus-ibapi`). Adopting NautilusTrader therefore **discards your client decision** rather than building on it. LEAN likewise has its own .NET IB brokerage plugin. So "adopt a framework and keep `ib_async`" is not really on the table for the two production-grade options; it's framework *instead of* `ib_async`.

### The ARM/aarch64 reality
- **NautilusTrader:** no stable PyPI wheels for Linux ARM64. On your Ampere VPS you would either build from source (rustup + clang + uv, compiling the Rust core) or run nightly pre-release wheels that the project itself flags as not for live/production. That is ongoing maintenance friction on a headless unattended box, and it cuts directly against "mature, low-maintenance technology."
- **LEAN:** an ARM64 Dockerfile exists, but documented limitations specifically hit **local live trading with the IB brokerage on ARM**, and the Docker images are large/un-optimized — heavy for a 12–24 GB shared VPS.
- A pure-Python stack (`ib_async` + pandas/pyarrow/parquet + stdlib asyncio) is **architecture-independent** and installs cleanly on aarch64 with zero compilation drama.

### "Don't reinvent the wheel" — correctly scoped
The instinct to use a mature framework is right, but the relevant "wheel" is the **IB client** (mature = `ib_async`) and **per-concern libraries** (data, storage, indicators), not a monolithic execution engine you will not run for months. Assembling here is *not* reinventing: it is composing mature single-purpose libraries. The thing a framework would save you from writing — a robust OMS / fill model / risk manager — is exactly the part Phase 1 and Phase 2 (paper) barely exercise, and the part you should design deliberately rather than inherit blindly.

### When a framework *does* pay off
At **Phase 3 (live)** the calculus shifts: real order routing, partial fills, position/PNL reconciliation against the broker, idempotent reconnect-and-recover, and risk limits are genuinely hard and genuinely worth not reinventing. If by then your custom OMS feels fragile, **NautilusTrader is the strongest migration target** (active, event-driven, true backtest/live parity, LGPL). The cost is accepting its `ibapi`-based adapter (replacing `ib_async`) and solving the ARM build once (e.g., build a wheel in CI / a Docker layer for aarch64). That is a deliberate, well-scoped decision to make later with real requirements in hand — not a Phase-1 commitment.

---

## Recommendation & Trade-offs

**Adopt (b): assemble a lightweight custom system on `ib_async`.**

**Build (Phase 1–2):**
- `ib_async` for connectivity, market-data subscriptions, IB scanner, and (Phase 2) paper orders — its asyncio event callbacks *are* your event-driven layer.
- A thin in-process event/state layer: scanner event → gate evaluators → opportunity record. Keep gates as small pure functions for testability.
- Storage: append-only **raw** event log (Parquet via pyarrow, or SQLite/TimescaleDB) — "store raw, compute derived on read." Derived metrics (5-min volume, %change, bull-flag) computed from raw on read.
- Indicators: `pandas` / `pandas-ta` / `ta-lib` (all fine on aarch64) rather than hand-rolled math.

**Borrow / reuse rather than reinvent:**
- **vectorbt (OSS)** or **zipline-reloaded** as an *offline* tool to backtest/evaluate the 3-month logged opportunity set — they are excellent at research even though they are not live engines.
- Steal NautilusTrader's *concepts* (clear separation of Data / Strategy / Execution; deterministic event ordering; message-based state) as design guidance for your own thin layer, so a future migration is low-friction.

**Defer / optional (Phase 3):**
- Re-evaluate **NautilusTrader** for live execution if/when OMS+risk complexity warrants it. Migrating means replacing `ib_async` with its `ibapi` adapter and building an ARM64 wheel once.

**Trade-offs of this choice:**
- *Pro:* Minimal moving parts for track-only; zero ARM compilation risk; keeps your already-chosen `ib_async`; fastest path to logging opportunities; no framework lock-in; you fully understand every line for a money-touching system.
- *Con:* You own the execution/OMS/risk code you eventually write for live (mitigated by deferring to a framework at Phase 3 if needed); you forgo turnkey backtest/live parity (mitigated by separate offline backtesting on the raw log).
- *Why not just take NautilusTrader now:* it discards `ib_async`, imposes a Rust source-build on aarch64, and 90% of its value (the execution engine) is unused until Phase 3 — paying its full learning-curve and ops cost months before any payoff.

---

## Sources

- NautilusTrader IB integration — https://nautilustrader.io/docs/latest/integrations/ib/
- NautilusTrader releases (v1.228.0, Jun 2026) — https://github.com/nautechsystems/nautilus_trader/releases
- NautilusTrader installation / ARM64 & Python support — https://nautilustrader.io/docs/latest/getting_started/installation/
- NautilusTrader PyPI — https://pypi.org/project/nautilus_trader/
- QuantConnect LEAN — IB brokerage docs — https://www.quantconnect.com/docs/v2/lean-cli/live-trading/brokerages/interactive-brokers
- LEAN engine getting started — https://www.quantconnect.com/docs/v2/lean-engine/getting-started
- LEAN ARM64 Docker discussion — https://www.quantconnect.com/forum/discussion/11319/lean-docker-images-for-arm64
- LEAN IB brokerage plugin (repo) — https://github.com/QuantConnect/Lean.Brokerages.InteractiveBrokers
- backtrader features / IB live docs — https://www.backtrader.com/docu/live/ib/ib/
- Python backtesting landscape 2026 (backtrader archive status) — https://python.financial/
- vectorbt (OSS) — https://github.com/polakowo/vectorbt ; PRO — https://vectorbt.pro/
- zipline-reloaded — https://github.com/stefan-jansen/zipline-reloaded
- Zipline-reloaded going-live notes — https://medium.com/@samuel.tinnerholm/from-backtest-to-live-going-live-with-zipline-reloaded-in-2025-step-by-step-guide-40e55ca264f1
- QSTrader / PyAlgoTrade / bt overviews — https://www.quantstart.com/articles/backtesting-systematic-trading-strategies-in-python-considerations-and-open-source-frameworks/ ; http://gbeced.github.io/pyalgotrade/
- LiuAlgoTrader — https://github.com/amor71/LiuAlgoTrader
- ib_async (maintained ib_insync successor, v2.1.0 Dec 2025) — https://github.com/ib-api-reloaded/ib_async ; https://pypi.org/project/ib_async/
- Framework comparison (Backtrader vs NautilusTrader vs VectorBT vs Zipline) — https://autotradelab.com/blog/backtrader-vs-nautilusttrader-vs-vectorbt-vs-zipline-reloaded
