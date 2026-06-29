# Architecture Review — synthesis & decisions

**Date:** 2026-06-29. Synthesises the four architecture research reports and records the decisions taken.
Detailed evidence: [`arch-trading-frameworks.md`](./arch-trading-frameworks.md) · [`arch-orchestration.md`](./arch-orchestration.md) · [`arch-ibkr-runtime.md`](./arch-ibkr-runtime.md) · [`arch-storage-libs.md`](./arch-storage-libs.md).

**Guiding principle (user):** use established, mature, maintained technology — don't reinvent the wheel, but don't over-engineer a single-VPS, single-writer, Phase-1 tracker either.

---

## Decisions

| Area | Decision | Why |
|---|---|---|
| **Trading core** | **Assemble on `ib_async`** (no framework) for P1–P2. Revisit **NautilusTrader** at P3 only if live OMS/risk justifies it. | NautilusTrader would replace `ib_async`, has **no ARM wheels** (Rust/clang source build), and P1 "track only" uses none of a framework's OMS/risk/execution value. backtrader is archived; LEAN is heavy/cloud-oriented; others are backtest-only. `ib_async` (v2.x, maintained) already gives an asyncio event layer. |
| **Runtime/orchestration (#12)** | **One long-lived `asyncio` process**: `asyncio.TaskGroup` (+`anyio` if needed) for in-process task dependencies; **APScheduler 3.x** `AsyncIOScheduler` with `CronTrigger(tz="America/New_York")` for 04:00/11:59/EOD triggers. **No** Airflow/Prefect/Dagster/Celery/Temporal. | Those are batch-DAG / distributed-workflow engines — wrong shape for a 24/5 tick-driven loop, and each needs an always-on scheduler+DB that taxes one VPS for no benefit. "Tasks with dependencies" belongs in-process. Avoid APScheduler 4.0 (pre-release). |
| **Process supervision** | **systemd** unit (`Restart=always`) runs the app; **Docker Compose** (`restart: unless-stopped`) runs IB Gateway. Don't double-supervise. | OS-standard, journald logs, reboot recovery. |
| **IBKR runtime (#11)** | **gnzsnz/ib-gateway-docker + IBC** for login/daily-restart/2FA-policy/headless display. Custom code is a **thin (~200-line) reconnect-and-resync supervisor** — NOT `ib_async.Watchdog`. | Watchdog only works when it launches Gateway via a local IBC process; it can't manage a separate container, and never re-subscribes data or resyncs orders. The Docker image removes nearly all ops code. |
| **Storage (#7) — supersedes decision #10** | **DuckDB over partitioned Parquet** as the analytical core; **SQLite (or plain Postgres later)** only for small mutable state/metadata. Defer TimescaleDB until a real live/multi-writer need appears. | Workload is single-writer, low-rate, append-mostly, GB-scale over years = embedded-OLAP, not a tick firehose. DuckDB has zero daemon footprint, ASOF JOIN, and Parquet keeps migration open. TimescaleDB's strengths are unused in P1 while it costs RAM + solo-dev ops. |
| **DataFrames** | **polars** for bar/feature pipelines (Arrow-native with DuckDB); `polars-lts-cpu` as ARM fallback; pandas for glue. | 3–10× over pandas; Arrow interop with DuckDB/Parquet. |
| **Indicators** | **TA-Lib** (v0.6.8 ships prebuilt **aarch64 wheels** now) for primitives (VWAP, etc.); implement bull-flag + ≤2-green/≤2-red candle logic ourselves as pure functions. | ARM build pain is gone. Original `pandas-ta` is inactive (use `pandas-ta-classic` if avoiding the C dep). |
| **Validation** | **Pydantic v2** at record boundaries; **pandera** at DataFrame boundaries (use native pandera types for polars frames). | Already chosen; pandera adds frame-schema safety. |
| **Observability (#5)** | **structlog** (JSON in prod) bridged to stdlib + **prometheus-client**, shipped to **Grafana Cloud** (push/Alloy for a headless job); **Healthchecks.io** dead-man's-switch. | Pure-Python, ARM-clean; matches the free-tier monitoring already chosen. |
| **Calendar/TZ** | **pandas-market-calendars** (pre-market/extended-hours sessions) + stdlib `zoneinfo` storing UTC; install `tzdata`. | Correct US session + pre-market handling. |
| **Deployment** | **Docker Compose** (gateway + app) launched/kept-alive by a **systemd** unit; optional **Ansible** later for host provisioning. No Terraform/K8s. | Pragmatic single-box standard. |

> **Avoid on ARM:** QuestDB (its docs recommend x86_64 — no SIMD/JIT on ARM).

---

## Impact on the issue backlog

- **#7 Storage** → re-scope to **DuckDB + Parquet** (+ SQLite for mutable state); drop the TimescaleDB/Postgres requirement for Phase 1.
- **#11 IBKR connection** → re-scope from "Watchdog" to a **thin reconnect-and-resync supervisor** atop the Docker/IBC stack. Thin layer: backoff reconnect, daily-restart-vs-cold-failure detection, on-connect resync (orders/positions/account), market-data re-subscription registry, error-code routing (1100/1101/1102), cold-restart alert, idempotent startup.
- **#12 Orchestration** → re-scope to **application runtime skeleton**: one asyncio process + `TaskGroup` pipeline + APScheduler triggers; supervised by systemd. No external orchestrator.
- **#6 VPS** → already covers Gateway-in-Docker; add Compose + systemd as the deployment shape.
- **#5 Monitoring** → confirm structlog + prometheus-client + Grafana Cloud + Healthchecks.io.
- **#16 Bull-flag** → reuse TA-Lib primitives; pattern/candle logic stays custom.

## Dependency plan (add as each build issue needs them, to keep CI lean)
Runtime: `ib-async`, `pydantic`, `pydantic-settings`, `python-dotenv` → add `apscheduler<4`, `duckdb`, `pyarrow`, `polars`, `structlog`, `prometheus-client`, `pandas-market-calendars`, `tzdata`. Indicators (with #16): `TA-Lib`. Validation (when frames appear): `pandera`. Keep `pandas` for glue.

## Open follow-ups
- Confirm Grafana Cloud push path (Alloy vs remote-write) when building #5.
- TA-Lib vs `pandas-ta-classic` final call at #16 (C dependency on the VPS image).
- Backtesting the 3-month opportunity log later: borrow **vectorbt** / **zipline-reloaded** offline (research-only; not in the live path).
