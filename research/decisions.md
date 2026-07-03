# Resolved Decisions — Research Phase Closeout

**Date:** 2026-06-29. Resolves the open questions in [`findings-index.md`](./findings-index.md) §3.

## Locked decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Float threshold | **< 20 million shares** (share count, NOT $ market value). |
| 2 | Scanner / broker | **IBKR only.** User trades via the **TWS Mosaic scanner** today and considers it sufficient. ⚠️ Headless system must use the **API scanner (`reqScannerSubscription`)**, a different/more limited surface than Mosaic — see Spike below. |
| 3 | Exit strategy (Phase 1) | **Not required for execution** in Phase 1 (tracking only). BUT "Max R" reporting needs a **notional entry trigger + notional stop** to compute R — see Phase-1 note below. |
| 4 | News source | **Try IBKR news feed first** (what user used before). Subscribe to a paid service only if insufficient. |
| 5 | VPS | ⚠️ **REVISED 2026-07-01: Hetzner Cloud CX22** (x86, 2 vCPU/4 GB, Ashburn US-East, ~€4/mo). Switched from ~~Oracle Ampere Always-Free~~ after repeated "Out of host capacity" on the free A1 tier. Images are multi-arch so the host is swappable; deploy tooling retargeted to x86/`vps`. Oracle A1 kept as a $0 alternative (RUNBOOK §12) if capacity is obtainable. |
| 6 | Market data | User **will subscribe to IBKR market data** (incl. pre-market). Pre-market feed is a solved problem via IBKR. |
| 7 | Weekly 2FA | **Accepted for now** (one manual phone tap/week). User aware of a second-username / relaxed-2FA workaround to apply later himself. |
| 8 | Branching | **Trunk-based: protected `main` + short-lived branches, all work via PRs**, required CI checks before merge. Chosen because much work happens in PRs / Claude Code on mobile. |
| 9 | Stack | **Python + `ib_async`** (the maintained fork). Prior repos' raw-`ibapi` code is adapted, not lifted verbatim. |
| 10 | Storage | ⚠️ **SUPERSEDED 2026-06-29 by [architecture-review.md](./architecture-review.md): use DuckDB-over-Parquet** (not Postgres/TimescaleDB) for Phase 1. ~~Self-hosted PostgreSQL (+ TimescaleDB) on the Oracle VM's 200 GB block volume.~~ Parquet-on-disk + growth-friendly intent unchanged; the embedded analytical engine changed. |
| 11 | Phase-1 scope | **Tracker only — places no orders.** Records every scanner-flagged opportunity, which gates it passed, whether a notional entry would have triggered, and Max R achieved + other stats. **All stats computed on the fly from cached raw data** so methodology can change retroactively. |

## Core architectural principle (from Q11)
**Store raw, compute derived on read.** Capture everything raw at flag time (bars, scanner snapshot, fundamentals, news, short interest) and keep gate evaluation + stat computation as **replayable pure functions** over that raw data. Changing gate definitions or the entry/stop spec later must NOT require re-collecting data — only re-running the computation over the cached raw record.

**Capture split — discovery intraday, bars at EOD (DECISION 2026-07-01, #62).** The intraday 60s tick does **discovery only**: scanner hits + opening opportunities + news/fundamentals at flag time (all point-in-time — not reconstructable later). The day's **5-min bars are pulled once in an end-of-day batch** (~16:20 ET, before the 16:30 report): a single `reqHistoricalData(durationStr="1 D", "5 mins", useRTH=False)` returns the whole session (04:00 ET→close) per flagged symbol. Replaces the fragile keepUpToDate streaming, which lost data + duplicated bars on a mid-session restart (observed after a deploy) and implicitly assumed a real-time feed we don't have (data is ~15 min delayed). The EOD job reads opportunities from storage and discovery rehydrates its open-set from storage on startup, so **restarts/deploys during market hours no longer create gaps**. Phase-1 places no orders, so real-time bars have no operational value.

## Entry / stop spec (for Max-R measurement)
- **Entry trigger (CONFIRMED 2026-07-01):** **5 ticks above the high of the last _complete_ consolidation candle** (i.e. `breakout_high + 5 × tick_size`; for $2–10 names tick = $0.01, so +$0.05). Revised from the earlier "1 tick above" (`notes.md`) after the user confirmed the real entry. Configurable via `Settings.entry_offset_ticks` / `tick_size`.
- **Stop (CONFIRMED 2026-06-29):** the **low of the consolidation candle(s)** (the flag low). This is the R denominator; `R = entry − stop`.
- **Analysis window (CONFIRMED 2026-07-01, #93):** R-metrics (trigger / Max R / MAE) are measured only through the **regular close, `capture_end` = 16:00 ET** — after-hours bars are **excluded** so illiquid after-hours prints can't set Max R. Store-raw is preserved (all bars are kept in storage; the analysis window is bounded on read in `report.py`).

## Strategy notes captured 2026-06-29 (from `notes.md`)
- **Opportunity exhaustion / re-entry (issue #36) — RULE CONFIRMED 2026-07-01:** a symbol can form >1 opportunity/day (runs, exhausts, extends again). **Rule (from the user):** once spotted, a symbol can't be re-spotted for **60 min** — a gap of ≥60 min with no scanner hits begins a *new* opportunity (e.g. pre-market pop → fade → market-open pop = the 2nd is new). Segmented **at analysis time** in `report.py` from the raw `scanner_hits` (not in live capture): each run gets its own bar window (extended back `reentry_lookback_min`=30 so the pole is captured), independent bull-flag/R-metrics, id `<date>:<symbol>#<run>`. Configurable via `Settings.reentry_gap_min`/`reentry_lookback_min`. Recomputes retroactively over already-collected data.
- **Pre-market orders (issue #37):** pre-market is **limit-only**; stops/TP must be **app-monitored** pre-market (broker-native stops only in the regular session). Reuse tradepilot's app-side exit logic. Execution concern (P2/P3).

## Scope (from user, 2026-06-29)
- User only ever acts on the **top 2–3 scanner rows, mostly the top 1.** The system only needs the *top few* candidates correct — the 50-row API cap and broad-universe concerns are largely moot.

## Remaining technical risks → validation spikes (before building)
- **A. API scanner vs Mosaic** (issue #8): ⏳ **largely validated 2026-06-29** — the API scanner returned a ranked candidate list **pre-market**, addressing the main suspected weak spot. `reqScannerParameters` confirmed IBKR exposes **trailing 5-min volume natively** (`stVolume5minAbove`, `stVolumeVsAvg5minAbove`, scan code `HIGH_STVOLUME_5MIN`), so the strategy's "5-min volume > 100k" is a built-in filter — NOT day volume, NOT derived from bars. Recommended scan: `TOP_PERC_GAIN` + price 2–10 + `changePercAbove 10` + `stVolume5minAbove 100000` @ `STK.US.MAJOR`. Remaining: user to confirm API top 1–3 == Mosaic top 1–3 at the same moment.

  > **Criterion #5 (5-min volume > 100k) resolved:** native `stVolume5minAbove` scanner filter. This was a previously-open data-feasibility item in `strategy-validation.md`.
- **B. Pre-market bar completeness** (#9): ✅ **GREEN** — active names get contiguous gap-free 5-min bars from 04:00 ET; only a leading absence before first trade. No interpolation needed.
- **C. IBKR news sufficiency** (#10): ✅ **GREEN to start** — account entitled to 8 providers incl. Dow Jones DJ-N (per-symbol headlines + retrievable bodies + halt notices). Start with included feed; measure timeliness in Phase 1 before paying.
- **D. Tradability gate** (#25, new): ✅ **GREEN** — `whatIfOrder` + error 201 reliably flags symbols IBKR blocks for the account even while they trade. Confirmed CBRG BLOCKED (PRIIPs/KID). **Account is under EU/UK PRIIPs rules** → expect some US small-cap SPAC/warrant/ETP runners to be un-orderable. **Add a tradability gate to the gate engine (#15).** Re-validate on live in P3.

## Architecture decisions (2026-06-29) — see [architecture-review.md](./architecture-review.md)
- **Trading core:** assemble on **`ib_async`** (no framework) for P1–P2; revisit NautilusTrader at P3 only if justified.
- **Runtime (#12):** one long-lived **asyncio** process — `TaskGroup`/`anyio` for in-process task dependencies + **APScheduler 3.x** for time triggers. No external orchestrator (Airflow/Prefect/Dagster).
- **Supervision/deploy:** **systemd** (`Restart=always`) runs the app; **Docker Compose** runs IB Gateway (gnzsnz image + IBC). No K8s/Terraform.
- **IBKR connection (#11):** thin (~200-line) **reconnect-and-resync supervisor**; rely on IBC+Docker for login/daily-restart/2FA; do NOT use `ib_async.Watchdog` (wrong tool for the container split).
- **Storage (#7):** **DuckDB + partitioned Parquet** (+ SQLite for mutable state).
- **DataFrames:** polars (pandas for glue). **Indicators:** TA-Lib (ARM wheels now) + custom pattern logic. **Validation:** Pydantic v2 + pandera. **Observability:** structlog + prometheus-client → Grafana Cloud + Healthchecks.io. **Calendar:** pandas-market-calendars + zoneinfo (UTC).

## `setup_count` retired (DECISION 2026-07-02, #112)
- **Retire `setup_count`** (Option 1 of #112); derive `bull_flag` directly from the R-metrics pass
  (`RMetrics.setup_found`). Rationale:
  - **The integer was noise.** `_count_setups` counted flags across the *whole* segment window —
    including pre-appearance flags we could never have taken (unlike R-metrics, which are gated to
    the first trigger at/after the scanner hit, #99) — and wasn't deduped by move or tied to
    outcome. Its only consumer was `bull_flag = setup_count > 0`.
  - **`bull_flag` is derivable for free.** Every valid bull flag has strictly positive risk
    (`entry = breakout + entry_offset` and `stop = flag_low`, with `breakout = last_flag_high >
    flag_low`), so `RMetrics.setup_found` (already computed by `compute_r_metrics`, which iterates
    the same prefixes) is **exactly equivalent** to `setup_count > 0`. Deleting `_count_setups`
    removes a redundant prefix scan and leaves one source of truth.
  - **Option 3 (fold into #102) is blocked** — #102's move-start rule isn't chosen yet, so there's
    no `pump_index`/`pump_count` to fold into. Deciding #112 now keeps the report schema stable
    before the 3-month collection; #102 adds the *meaningful* per-move pump metrics later.
  - **Schema impact (intended):** the persisted `analysis` dataset drops the `setup_count` column,
    and the EOD markdown + Pages dashboard drop the `setups` column. `GateInputs.bull_flag` (the
    gate-engine input) is unrelated and unchanged.

## Scanner price range widened (DECISION 2026-07-02, #126)
- **$2–10 → $1–$50** (`scan_min_price`/`scan_max_price`). The original $2–10 band was the locked
  strategy range; widening captures lower-priced runners (≥$1) and higher-priced momentum names
  (≤$50) the tighter band excluded. Flows to the scanner subscription (`priceAbove`/`priceBelow`)
  and the `price_gate` — both read the settings. `tick_size` stays $0.01 (all names ≥$1 use a penny
  tick). Store-raw is unaffected; this only changes what the scanner surfaces going forward.

## Entry appearance-gate is bar-close granular (DECISION 2026-07-03, #122 — revises #99)
The #99 appearance gate ("a setup may only *trigger* at/after the scanner hit") was implemented at
**bar-start** granularity: reject a trigger bar whose `start < first_hit`. But the scanner ticks
every 60s while bars are 5-min, so appearance almost always lands *inside* a bar — and when a symbol
first appears **during the very breakout bar**, that bar's `start < first_hit`, so the entry was
deferred to a later, worse setup (observed on SOXS/JEM). Revised to **bar-close**: reject a trigger
bar only if it **closed at/before** `first_hit` (`bar.start + bar_interval <= first_hit`) — i.e. only
a break provably over before we saw it. This credits "appeared during the breakout bar" as takeable
(how it's actually traded) without ever crediting a move already finished. `bar_interval` is the
series' modal bar spacing, so a pre-market gap doesn't over-credit across a missing bar. The chart
appearance marker (`charts._bar_containing`) matches — it sits on the bar that *contains* `first_hit`,
not the next one (fixes the JEM 08:45-vs-08:40 dot). Backcastable over collected bars.

## Bull-flag redefined (DECISION 2026-07-03, #127 — from notes.md)
Reviewing the annotated charts against the engine, the trader's model of a setup differs materially
from the earlier "≤2 green candles" pole. Redefined `bullflag.detect` (backcastable — recomputes
over already-collected raw bars):
- **Pole = a run of higher highs**, from a **single higher-high bar** up to `bull_flag_max_pole`(8);
  `bull_flag_min_pole`=1. **Not** colour-gated — a non-green bar is allowed as long as the high still
  makes a higher high (SNDQ counted a 7-bar pole; SOXS/OKLL/DJT "characterised by higher highs").
  `pole_len` counts the higher highs; the ascending run's launch bar sets the pole base for the
  retracement. The peak must be a higher high than its predecessor, so a *descending* flag isn't
  mistaken for the peak. *Preferable* (soft, not yet quantified — deferred like the wick filter):
  the pole contains ≥1 big green candle.
- **Flag = a genuine pullback** of `1..bull_flag_max_flag`(6) bars that stays below the pole peak and
  **makes lower highs** — the trader tracks *highs*, not lows (correction 2026-07-03). Multi-bar:
  non-increasing highs with a net lower high; single-bar: any candle below the peak. Rejects
  consolidations that tick back up (ETHT/NBIZ).
- **Retracement gate:** reject a flag retracing > `bull_flag_max_retracement`(0.50) of the pole
  height, measured on the flag low (the risk). Encodes "back through the pole" (AHMA/CLRO/CYH/DJT).
- **Volume:** the pole's peak bar volume **must exceed** the consolidation's peak bar volume (hard).
  Whether the consolidation volume is reducing is recorded (`cons_vol_reducing`) but **not** gated —
  it may be flat.
- Entry/stop spec **unchanged** (5 ticks above the last consolidation high; stop = flag low).

**Follow-ups (separate issues, not in #127):** ATR%/movement gate for "barely moving/ranging" names
(CLVT/CYH/CMMB); entry appearance-bar gate #122 (SOXS/JEM mid-bar appearance); later-intraday setups
& entry-staleness (CLRO/TSDD/AHMA "entry an hour after the scan"); half-pole-stop research (IREZ).

## Fundamentals source (2026-06-29, issue #17)
- IBKR (Reuters) fundamentals are **unentitled** on the account (error 10358: "Fundamentals data is not allowed"). Phase-1 sources **float / shares outstanding / short% via yfinance** (free, no key; tradepilot precedent). Captured raw at flag time with a `source` column, so a hardened source (FMP float / FINRA short interest, **issue #41**) can be swapped in later and recomputed.

## Repo visibility (CONFIRMED 2026-06-29)
- **Public, by choice** — the user is happy for anyone to use what's built. Bonus: unlimited GitHub Actions. Never commit secrets/credentials (enforced via `.gitignore` + `.env`).

## Phone-driven control plane (2026-06-30, issues #51–#55)
Goal: build, test, fetch data, and deploy entirely from the Claude Code web/mobile container.

- **GitHub is the control plane.** The cloud container has full GitHub access (PRs, Actions, board)
  but cannot hold long-lived secrets, reach `127.0.0.1` on the Mac/VPS, or run IB Gateway. So every
  action taken from the phone is a GitHub action; data and deploys flow *through* GitHub / object
  storage, never via secrets baked into the ephemeral container.
- **Build/test in the container.** A `SessionStart` hook (`.claude/hooks/session-setup.sh`) runs
  `make setup` idempotently so `make check` works on turn one. The suite is fully offline — the
  IBKR-touching tests mock the connection; no Gateway needed (#51).
- **Data access without a broker.** VPS captures raw → pushes a *sanitized sample* to object
  storage (e.g. Cloudflare R2 / Backblaze B2); the dev session pulls it with `make fetch-fixtures`
  (`FIXTURES_URI`). Live IBKR entitlement + weekly 2FA stay on the VPS (#52, pairs with backup #48).
- **Deploy = GitHub → self-hosted runner on the VPS (DECISION, #53).** Chosen over
  SSH-from-hosted-runner because the box keeps **no inbound ports** (RUNBOOK) — a self-hosted runner
  polls GitHub *outbound*, so no inbound exposure and **no SSH key in the container**. Deploy is a
  manual `workflow_dispatch` (`deploy.yml`, runner label `vps`) triggerable from the phone; secrets
  live in GitHub Actions secrets + the VPS environment only.
- **Pull-based images (#54).** CI builds `linux/amd64` (Hetzner x86) and pushes to GHCR so the VM
  deploys by pulling a versioned tag rather than building on-box. (Compose `build:` → `image:` switch
  is deferred to the deploy wiring so local dev / the un-provisioned VM keep working. On Oracle/ARM,
  build `linux/arm64` instead.)
- **Network policy.** Pulling fixtures (and any future VPS read endpoint) requires the web
  environment's network policy to allow that egress — a deliberate config choice, documented in the
  RUNBOOK.
- **Off-box backups = restic → Backblaze B2 (DECISION 2026-07-01, #48).** The 3-month dataset (the
  product) is backed up nightly by a host `systemd` timer running `scripts/backup.sh`: **restic**
  (incremental + encrypted + deduplicated, retention keep-daily 7/weekly 5/monthly 4) to a **B2**
  bucket (10 GB free). Chosen over a nightly `tar` because append-only Parquet dedups perfectly and
  restic gives integrity checks + one-command restore. Config in root-only `/etc/scs-backup.env`;
  the backup pings a dedicated Healthchecks check (alerts on silent failure). The `RESTIC_PASSWORD`
  is stored off-box (password manager) so a box loss is recoverable.
- **Blocked on the VM (#6):** the deploy *execution* and the VPS-side fixture *producer*. The
  VM-independent halves (SessionStart hook, fixtures consumer scaffolding, the GHCR build job, the
  deploy workflow definition, and these docs) land now.
