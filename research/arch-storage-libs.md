# Architecture: Storage & Trading Libraries

Research for a small-cap momentum **opportunity tracker** on a single Oracle Cloud
Ampere ARM (aarch64) VPS. Free/OSS only, headless, unattended, solo dev.

- **Date:** 2026-06-29
- **Guiding principle:** *store raw, compute derived on read* — capture raw 5-min
  bars + per-opportunity snapshots (scanner row, fundamentals, news), recompute all
  stats/gates later as pure functions so methodology can change retroactively.
- **Profile:** single-writer, low-concurrency, low write rate, append-mostly,
  data **grows over years**. Phase 1 = log opportunities for 3 months; later
  paper/live.
- **Verdict up front:** The tentative *TimescaleDB + Parquet + object storage* is a
  reasonable choice but is **over-provisioned for Phase 1**. Recommend
  **DuckDB-over-Parquet** as the analytical layer, with a small relational store
  (SQLite, or plain Postgres) for mutable snapshot/state metadata. Defer
  TimescaleDB until a concurrent-writer / live-dashboard need actually appears.

---

## 1. Time-series / analytical storage (single node)

### What the workload actually is
- **Ingest:** one writer appending 5-min OHLCV bars (~78 bars/symbol/RTH day) plus a
  handful of JSON-ish snapshots per opportunity. This is *not* a tick firehose — it
  is kilobytes-to-megabytes per day.
- **Volume:** even thousands of small-cap symbols across multiple years stays in the
  **single-digit-to-low-tens of GB** (compressed columnar). It fits comfortably in
  200 GB, and most working sets fit in 12–24 GB RAM.
- **Analytics:** large scans / group-bys / window functions to recompute features and
  gates from raw bars. Classic embedded-OLAP, single-user pattern.
- **Concurrency:** effectively one writer, occasional reader. No multi-client
  transactional contention.

### Candidates

| Option | Fit for this workload | ARM/aarch64 | Footprint / ops | Verdict |
|---|---|---|---|---|
| **DuckDB over Parquet** | Excellent. Embedded OLAP, reads/writes Parquet natively, SQL window funcs = ideal for "compute-on-read". Single-writer / multi-reader model matches a solo tracker exactly. | First-class. Official Linux **arm64** binaries + wheels; **1.4.x LTS** line (1.4.3 LTS, Dec 2025). | Zero daemon — a library in your process. No server RAM tax on the shared VPS. Lowest ops burden. | **RECOMMENDED primary** |
| **TimescaleDB** (Postgres ext) | Good, but its strengths (continuous aggregates, multi-client SQL, relational integrity, ASOF-style ops via tooling) are wasted in Phase 1. Note: core Postgres has **no native ASOF JOIN**. | Solid: official **APT arm64** packages, actively built (2.21.x mid-2025, 2.25.x in Debian sid). | Always-on Postgres daemon competing for RAM; backups, `VACUUM`, version upgrades, extension pinning = real solo-dev ops. | Defer — adopt if/when live or multi-writer |
| **QuestDB** | Great *ingest* numbers and time-bucketed SQL (`SAMPLE BY`, `LATEST ON`), purpose-built for finance. But your ingest rate is trivial, so the headline advantage doesn't apply. | Runs on aarch64, but **QuestDB themselves recommend x86_64**; SIMD + JIT optimizations are limited on ARM. Direct strike against an Ampere VPS. | JVM server process; another always-on daemon. | Not recommended on ARM |
| **ClickHouse** | Excellent analytical engine + compression (~10:1), proven scale. Overkill for GB-scale single-node; heavier to run. | aarch64 supported. | Server + heavier operational surface than DuckDB. | Overkill |
| **Plain Parquet + pandas/polars** | Storage format is right, but hand-rolling partition pruning / predicate pushdown / joins re-invents what DuckDB gives free. | n/a (format) | Lowest infra, highest code. | Use Parquet as the *format*, DuckDB as the *engine* |

### Recommendation
Adopt **DuckDB querying partitioned Parquet** as the analytical core:

- Persist raw 5-min bars as **Parquet partitioned by `symbol`/date** (e.g.
  `bars/symbol=AAPL/year=2026/month=06/*.parquet`) for cheap pruning and trivial
  object-storage sync.
- DuckDB reads these files directly with full SQL (window functions, `qualify`,
  `asof`-style joins via `ASOF JOIN`, which **DuckDB does support** — a notable edge
  over plain Postgres) — perfect for pure-function "compute-on-read".
- For **mutable, relational, frequently-upserted metadata** (opportunity snapshots,
  scanner rows, run state, gate-version bookkeeping) where you want indexed lookups
  and referential integrity, use a small relational store. Two clean options:
  - **SQLite** — zero ops, embedded, fine for a single-writer tracker; or
  - **plain PostgreSQL** (no Timescale extension) if you want a network DB / future
    multi-client access.
- **Backups:** Parquet files sync incrementally to free object storage (Oracle Object
  Storage / Backblaze B2 / Cloudflare R2); the small metadata DB dumps alongside.

**Why not TimescaleDB now:** it solves concurrent-writer, continuous-aggregate, and
relational-at-scale problems you don't have in Phase 1, at the cost of an always-on
server on a RAM-constrained shared box. DuckDB's single-writer model is not a
limitation here — it is a match. Migration path is open: Parquet is engine-neutral,
so you can load it into Timescale/ClickHouse later if live/multi-writer needs arise.
DuckDB single-writer caveat: only one process may write the `.duckdb` file at a time;
keep all writes in your one ingest process (multiple readers are fine).

Sources:
- https://duckdb.org/2025/12/09/announcing-duckdb-143
- https://duckdb.org/install/
- https://duckdb.org/docs/current/connect/concurrency
- https://github.com/timescale/timescaledb/actions/workflows/apt-arm-packages.yaml
- https://packages.debian.org/timescaledb
- https://questdb.com/blog/timescaledb-vs-questdb-comparison/
- https://community.questdb.com/t/arm64-support-and-optimization/915
- https://www.tigerdata.com/learn/the-best-time-series-databases-compared
- https://www.index.dev/skill-vs-skill/database-timescaledb-vs-clickhouse-vs-questdb

---

## 2. DataFrame engine: pandas vs polars

- **polars**: 3–10x faster than pandas on large ETL, lower memory, lazy/streaming
  engine, Arrow-native (pairs naturally with DuckDB/Parquet). conda-forge publishes
  **linux-aarch64** builds; pip wheels for aarch64 are standard.
- **ARM gotcha:** on older/feature-limited ARM CPUs use **`polars-lts-cpu`** if the
  default wheel hits illegal-instruction issues (Ampere Altra is modern and generally
  fine, but keep `polars-lts-cpu` as the fallback). pandas has no such concern.
- **pandas**: still the broadest ecosystem; competitive on small (<1M-row) frames; best
  for ad-hoc interop with TA libs and plotting.

**Recommendation:** Use **polars** for the bar/feature pipelines (speed + memory +
Arrow/DuckDB synergy). Keep pandas available for glue and for any TA library that only
speaks pandas. DuckDB ↔ polars ↔ Arrow zero-copy interchange means you don't have to
pick exclusively.

Sources:
- https://www.databricks.com/blog/polars-vs-pandas
- https://github.com/astral-sh/uv/issues/15693 (polars-lts-cpu on ARM)
- https://www.shuttle.dev/blog/2025/09/24/pandas-vs-polars

---

## 3. Technical analysis / indicators

You implement the bull-flag + ≤2-green / ≤2-red candle logic yourself; you only want
trustworthy **primitives** (VWAP, volume features, MAs, ATR).

| Library | State (2026) | ARM | Notes |
|---|---|---|---|
| **TA-Lib (python)** | Active. **v0.6.8 (Oct 2025)** ships **prebuilt aarch64 wheels** (manylinux_2_28 + musllinux) bundling the C lib — **the historic ARM build pain is gone** since v0.6.5. | Yes, wheels | Fastest, battle-tested; pin `manylinux_2_28`. |
| **pandas-ta** (original) | **Maintenance: inactive** — ~yearly release, latest `0.4.71b0` beta. | pure-python | Risky as a sole dependency. |
| **pandas-ta-classic** | Active community fork, 250+ indicators, tests. | pure-python | Best pandas-ecosystem choice if avoiding C deps. |
| **ta** | Maintained but narrower scope; has VWAP. | pure-python | Lightweight fallback. |
| **mintalib** | numpy-core with **pandas *and* polars** interfaces. | numpy | Worth a look for polars-native pipelines. |

**Recommendation:** Use **TA-Lib** for indicator primitives — its ARM story is now
clean (prebuilt aarch64 wheels), and it's the most reliable/fastest. If you want to
avoid any C dependency, use **pandas-ta-classic** (not the inactive original
`pandas-ta`). For a polars-native path, evaluate **mintalib**. Implement VWAP and the
candle/bull-flag classification yourself as pure functions over polars frames
(trivial, and keeps them inside the "compute-on-read" model regardless of library).

Sources:
- https://pypi.org/project/TA-Lib/
- https://github.com/TA-Lib/ta-lib-python
- https://snyk.io/advisor/python/pandas-ta
- https://github.com/xgboosted/pandas-ta-classic
- https://pypi.org/project/mintalib/

---

## 4. Data models / validation

- **Pydantic v2** (already in use): confirmed good fit for API ingest models, config,
  and per-opportunity snapshot validation at the *record* boundary. Rust-core, fast,
  well-maintained — keep it.
- **pandera** for **DataFrame-level** schema validation (column types, ranges,
  nullability, uniqueness) of bar frames and feature outputs. As of **0.29 (Jan 2026)**
  it validates pandas, **polars**, Dask, PySpark, and Ibis from one schema; Pydantic-v2
  integration runs ~1.5–1.75x faster than v1.
  - **Gotcha:** the `PydanticModel` dtype exists in the *pandas* engine, **not** the
    polars engine — so for polars frames, define pandera schemas with native pandera
    column types rather than embedding a Pydantic model.

**Recommendation:** **Pydantic v2 at the record/IO boundary**, **pandera at the
DataFrame boundary** (validate raw bars on ingest and feature frames on read). Pure
Python — no ARM concerns.

Sources:
- https://pandera.readthedocs.io/en/latest/polars.html
- https://www.union.ai/blog-post/pandera-0-17-adds-support-for-pydantic-v2
- https://github.com/unionai-oss/pandera/issues/1874

---

## 5. Structured logging / observability

- **structlog** over **stdlib logging**: structlog's bound-logger + processor pipeline
  is ideal for an unattended job (attach `run_id`, `symbol`, `gate_version` to every
  line). Configure it as a **front-end to stdlib logging** so 3rd-party libs stay
  compatible. Console-renderer (pretty) in dev, **JSON renderer in prod** — JSON feeds
  cleanly into Grafana Cloud Loki / Grafana Alloy log shipping.
- **prometheus-client** (official Prometheus Python client, actively maintained, pure
  Python → no ARM issue): expose counters/gauges/histograms (bars ingested, scanner
  hits, gate pass/fail, ingest latency, errors). For a headless unattended job prefer
  the **Pushgateway** or **textfile collector** pattern (or remote-write via Alloy)
  rather than a scraped HTTP endpoint, then visualize in **Grafana Cloud**.
- **Healthchecks.io**: ping at the start/end of each ingest cycle; a missed ping =
  dead-man's-switch alert. Orthogonal to the above and complementary.

**Recommendation:** **structlog (JSON in prod) bridged to stdlib** + **prometheus-client**
metrics shipped to **Grafana Cloud**, with **Healthchecks.io** as the liveness
watchdog. All pure-Python; no ARM gotchas.

Sources:
- https://www.structlog.org/en/stable/standard-library.html
- https://www.dash0.com/guides/python-logging-libraries
- https://tutorials.technology/tutorials/python-logging-best-practices-structlog-loguru-2026.html

---

## 6. Market calendar / timezone

- **exchange-calendars**: actively maintained successor to `trading_calendars`; broad
  exchange coverage including XNYS/XNAS.
- **pandas-market-calendars**: since v2.0 **mirrors all exchange_calendars calendars**
  and adds a pandas-friendly API plus, importantly, **explicit session/extended-hours
  handling** (`schedule(..., market_times=...)` / pre- and post-market columns) — the
  better fit for your **pre-market** small-cap momentum use case. Actively maintained
  (docs current into 2026).
- **zoneinfo** (stdlib): use for all tz math; store everything **UTC**, convert to
  `America/New_York` only for session logic/display. Requires `tzdata` package on
  minimal Linux images.

**Recommendation:** **pandas-market-calendars** (it wraps exchange-calendars and adds
pre/post-market session support) + stdlib **zoneinfo** with **UTC storage**. Both pure
Python — no ARM concerns; ensure `tzdata` is installed on the VPS.

Sources:
- https://pypi.org/project/pandas_market_calendars/
- https://pandas-market-calendars.readthedocs.io/en/latest/usage.html
- https://github.com/rsheftel/pandas_market_calendars/blob/master/docs/change_log.rst

---

## Consolidated recommended stack

| Area | Choice | ARM/aarch64 flag |
|---|---|---|
| Raw bar storage (format) | **Parquet**, partitioned by symbol/date | none |
| Analytical engine | **DuckDB** (1.4.x LTS) over Parquet | first-class arm64 wheels |
| Mutable metadata/state | **SQLite** (zero-ops) or **plain PostgreSQL** | both fine on arm64 |
| TimescaleDB | **deferred** until live/multi-writer need | arm64 APT pkgs exist |
| DataFrame engine | **polars** (pandas for glue) | fallback `polars-lts-cpu` |
| TA primitives | **TA-Lib 0.6.8** (or pandas-ta-**classic** if avoiding C) | prebuilt aarch64 wheels since 0.6.5 |
| Record validation | **Pydantic v2** | pure-python |
| DataFrame validation | **pandera 0.29+** (native types for polars) | pure-python |
| Logging | **structlog → stdlib**, JSON in prod | pure-python |
| Metrics | **prometheus-client** → Grafana Cloud (push/Alloy) | pure-python |
| Liveness | **Healthchecks.io** ping per cycle | n/a |
| Calendar | **pandas-market-calendars** (+ exchange-calendars) | pure-python |
| Timezone | stdlib **zoneinfo**, store UTC; install **tzdata** | n/a |
| Backups | Parquet + metadata dump → free object storage (R2/B2/OCI) | n/a |

### ARM gotchas summary
1. **QuestDB** explicitly recommends x86_64 — SIMD/JIT limited on ARM. **Avoid on Ampere.**
2. **polars** — keep **`polars-lts-cpu`** as a fallback if the default wheel faults.
3. **TA-Lib** — historic ARM build pain solved by prebuilt aarch64 wheels (v0.6.5+);
   pin `manylinux_2_28`.
4. **DuckDB single-writer** — keep all writes in the one ingest process; readers concurrent.
5. **zoneinfo** — install the **`tzdata`** package on minimal Linux images.
6. **pandera** — `PydanticModel` dtype is pandas-engine only; use native pandera types
   for polars frames.
