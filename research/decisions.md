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
- **Entry trigger (CONFIRMED 2026-07-01, ⚠️ SUPERSEDED for engine v2 2026-07-10 by #182/#190 — see
  below):** ~~5 ticks above the high of the last _complete_ consolidation candle (i.e.
  `breakout_high + 5 × tick_size`; for $2–10 names tick = $0.01, so +$0.05). Revised from the
  earlier "1 tick above" (`notes.md`) after the user confirmed the real entry.~~ Configurable via
  `Settings.entry_offset_ticks` / `tick_size` — **legacy engine only**, still live and unchanged
  until #180's cut-over.
- **Stop (CONFIRMED 2026-06-29):** the **low of the consolidation candle(s)** (the flag low). This is the R denominator; `R = entry − stop`.
- **Analysis window (CONFIRMED 2026-07-01, #93):** R-metrics (trigger / Max R / MAE) are measured only through the **regular close, `capture_end` = 16:00 ET** — after-hours bars are **excluded** so illiquid after-hours prints can't set Max R. Store-raw is preserved (all bars are kept in storage; the analysis window is bounded on read in `report.py`).

## Strategy notes captured 2026-06-29 (from `notes.md`)
- **Opportunity exhaustion / re-entry (issue #36) — RULE CONFIRMED 2026-07-01:** a symbol can form >1 opportunity/day (runs, exhausts, extends again). **Rule (from the user):** once spotted, a symbol can't be re-spotted for **60 min** — a gap of ≥60 min with no scanner hits begins a *new* opportunity (e.g. pre-market pop → fade → market-open pop = the 2nd is new). Segmented **at analysis time** in `report.py` from the raw `scanner_hits` (not in live capture): each run gets its own bar window (extended back `reentry_lookback_min`=30 so the pole is captured), independent bull-flag/R-metrics, id `<date>:<symbol>#<run>`. Configurable via `Settings.reentry_gap_min`/`reentry_lookback_min`. Recomputes retroactively over already-collected data.
- **Pre-market orders (issue #37):** pre-market is **limit-only**; stops/TP must be **app-monitored** pre-market (broker-native stops only in the regular session). Reuse tradepilot's app-side exit logic. Execution concern (P2/P3).

## Scope (from user, 2026-06-29)
- User only ever acts on the **top 2–3 scanner rows, mostly the top 1.** The system only needs the *top few* candidates correct — the 50-row API cap and broad-universe concerns are largely moot.
- **UPDATE 2026-07-12 — scanner breadth raised to the full cap (`scan_max_rows` 10 → 50).** For *acting*, the top few still suffice; but Phase-1 is a data-collection exercise and on busy mornings there are far more than 10 low-float runners in play. Store-raw/compute-on-read means we capture the whole ranked list now and decide actionability on read later. One scanner request per tick regardless of row count, and opportunities dedup per symbol/day (news/fundamentals fetched once per distinct symbol; EOD bar/news batches are paced), so the wider net is safe on IBKR pacing. 50 is the API hard cap (`numberOfRows` is `min(scan_max_rows, 50)`).

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

## Entry staleness bound (DECISION 2026-07-03, #130 — from notes.md)
A break more than **`entry_staleness_min` (default 30 min)** after the scanner appearance reads as
*faded* and is not counted as a takeable entry — the run reports setup-found-but-not-triggered
(AHMA's notional entry fired ~1hr+ after the scan, which the trader would never take). Applied in
`compute_r_metrics` alongside the #122 bar-close lower bound, so the valid trigger window is roughly
`[first_hit, first_hit + entry_staleness_min)`. Only applies when `first_hit` is known; backcastable
and tunable. **Deferred (folded into #102):** surfacing *later* distinct intraday setups (CLRO
11:00/11:50, TSDD 12:20) as their own opportunities — that needs the move/pump segmentation #102 is
chartered to decide, rather than a half-baked distinct-setup heuristic now.

## Pole wick filter + big-green signal (DECISION 2026-07-03, #132 — from notes.md)
"Too wicky → no trade" (AHMA/VRXA) is a hard reject on **pole quality**: the pole's **peak
(highest-high) bar must close strong** — its upper wick (`high − max(open, close)`) must be
≤ `bull_flag_max_peak_wick` (default **0.50**) of the bar's range. A pole is an up-thrust, so only
the *upper* wick matters (a lower wick is a bought dip); the peak bar is the top of the thrust and
shouldn't be a rejection candle. Colour-agnostic (uses `max(open, close)`), backcastable, tunable.
The "≥1 big green candle in the pole" preference (from the #127 refinement) is elevated to a
**recorded soft signal** `pole_has_big_green` (a green bar with body ≥ 50% of its range) — written
to the analysis dataset, **not** gated.

## Bull-flag redefined (DECISION 2026-07-03, #127 — from notes.md)
Reviewing the annotated charts against the engine, the trader's model of a setup differs materially
from the earlier "≤2 green candles" pole. Redefined `bullflag.detect` (backcastable — recomputes
over already-collected raw bars):
- **Pole = a run of higher highs**, from a **single higher-high bar** up to `bull_flag_max_pole`(8);
  `bull_flag_min_pole`=1. ~~**Not** colour-gated — a non-green bar is allowed as long as the high
  still makes a higher high (SNDQ counted a 7-bar pole; SOXS/OKLL/DJT "characterised by higher
  highs").~~ ⚠️ **SUPERSEDED for engine v2 2026-07-10 by #182/#190** (colour-gated: no red/doji bar
  in the pole) — **legacy `bullflag/detect.py` is unchanged and stays colour-agnostic**, live until
  #180's cut-over. `pole_len` counts the higher highs; the ascending run's launch bar sets the pole
  base for the retracement. The peak must be a higher high than its predecessor, so a *descending* flag isn't
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

## Engine v2 volume gate = peak-bar (DECISION 2026-07-10, #176 — reaffirms #127)
The engine-v2 redefinition (`bull-flag.md`, umbrella #176) keeps the volume filter on the pole's
**peak (thrust) bar** volume > consolidation volume — **not** the "max bar volume in the pole"
wording from the v2 sketch. They diverge only for a multi-bar pole where a *non-peak* higher-high
bar spikes in volume; peak-bar refuses to let an earlier bar's volume rescue a weak breakout bar.
Chosen to honour the locked #127 rule and keep v2 byte-identical to the legacy detector (parity).
Surfaced by the #179 code review; user confirmed peak-bar (Rule A).

## Engine v2 pole is colour-gated (DECISION 2026-07-10, #182/#190 — supersedes #127 for v2 only)
Walking through 8+ real opportunities one at a time in a chart viz (VRAX/MSTZ/MUZ/TVRD/CRCG/ARCT/
IRE/CONL/FCEL/OKLL), the trader confirmed: **"I don't like any red candles in the pole."** This
**reverses #127's "not colour-gated"** rule (which allowed SNDQ/SOXS/OKLL/DJT-style poles containing
a non-green bar) — **for engine v2 only**. Two rules, both validated bar-by-bar:
- **No red candle can be part of the pole, including the peak.** A red "peak" (a new high that
  reverses and closes weak within the bar — a shooting-star top, e.g. IRE) is disqualified entirely;
  the search continues for a later green peak.
- **A technically-higher-high bar that's doji-like (small body relative to range) doesn't extend the
  pole** even though its high still ticks up (MUZ/CRCG/CONL — a quiet pause between two real
  thrusts). It becomes the base (a height reference only), not an intermediate pole bar.
- Threshold: green (`close > open`) with body ≥ 50% of range (reuses `_is_big_green`, #132); the
  peak only needs to be green (any body size), matching the existing single-bar-pole tolerance.
- Effect: often **shrinks** the pole to the true immediate thrust, which then makes the retracement
  gate stricter (a shallow-looking pullback against a big multi-bar run becomes rejection-deep
  against the true, smaller pole) — seen repeatedly, and it's the gates working correctly.

**The legacy detector (`bullflag/detect.py`) is UNCHANGED and stays colour-agnostic** — this is a
v2-only redefinition, live only once #180 flips the settings/repoint. Implemented in `segment.py`.

## Engine v2 entry: 1-tick trigger, 3-tick conservative fill (DECISION 2026-07-10, #182/#190 —
supersedes the 2026-07-01 "5 ticks" entry-trigger decision above, for v2 only)
The 2026-07-01 decision revised entry from "1 tick above" to "5 ticks above" after the user
confirmed the real entry — but that confirmation predates any chart-by-chart review. Walking the
same 8+ real opportunities today, the trader clarified the two ideas were being conflated:
**"the 3 ticks does become a slippage modelled fill price for R. The trigger is always the tick
above the last high in the consolidation. Often I actually fill at that price anyway. 3 ticks is
being conservative."** So the two concepts are split, not just re-numbered:
- **`entry_trigger` = last consolidation candle's high + 1 tick** (`Settings.
  bull_flag_trigger_offset_ticks = 1`) — decides **when** a setup fires. Validated as "entry" on
  every one of the 8+ reviewed charts.
- **`entry_fill` = last consolidation candle's high + 3 ticks** (`Settings.
  bull_flag_fill_offset_ticks = 3`) — the price R is **measured against**, deliberately worse than
  the trigger to avoid overstating the edge, even though the real fill is often the trigger price
  itself. Captured on `Setup.entry_fill`; no legacy `BullFlag` slot yet — #180 must wire `rmetrics`
  to read it for R-measurement instead of reusing `entry_trigger`.

**The legacy detector's `entry_offset_ticks` (5) is UNCHANGED and unused by v2** — this is a v2-only
concept with no legacy equivalent, live only once #180 flips the settings/repoint.

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
- **Cloud reads live `/data` via an on-demand export workflow (DECISION 2026-07-07).** A web/mobile
  session **cannot** SSH into the box: Claude Code on the web allows only HTTP/HTTPS through a
  domain-allowlist proxy (no port-22 / raw-TCP egress, even on "Full") and has **no secret store**
  (env vars are stored in plaintext in the environment config), and the box keeps no inbound ports.
  So reads use the **write path in reverse**: `data-export.yml` (`workflow_dispatch`, runner label
  `vps`) `docker exec`s `scripts/analysis/export_query.py` against `/data` and commits the result to
  the orphan **`data-export`** branch, which the session reads back over GitHub. Chosen over
  (a) putting an SSH key in the cloud (can't connect *and* would sit in plaintext) and (b) a live
  HTTPS query endpoint on the box (breaks no-inbound-ports; needs a domain + TLS + token). Costs
  nothing in the cloud — no secret, **Trusted** network access suffices. Driven by the `box-data`
  skill; blocked on the runner (#6) like deploy.
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

## Virtual-portfolio tracker + execution model (DECISION 2026-07-15, #230)
A **pre-shadow** virtual portfolio, computed on-read over the Phase-1 dataset, that "takes" the
trades the user would take and reports an equity curve + trade log + stats in the web app. It places
no orders; it is the down-payment on Phase-2 (the *select → size → simulate-exit* logic is the real
shadow-mode brain — only "simulate exit from bars" gets swapped for "place bracket + capture fill"
in P2). Locks the following execution parameters (chosen by the user 2026-07-15):

- **Account:** UK **cash** account (no PDT — that's a margin-account rule). Starting equity **$500
  USD**. **Settlement needs no model — the 50% × 2/day cap already discharges it** (AMENDED
  2026-07-15, #234, superseding "settlement is IGNORED for v1"): per `broker-costs.md` §6 the
  binding rule is *total daily buy notional ≤ settled cash at the **start** of the day*, and since
  both trades size off `opening_equity` at 50% with a 2/day cap, max daily buy notional
  `= 2 × floor(0.50 × opening_equity / entry) × entry ≤ opening_equity`. Every trade closes
  same-day, so no unsettled position is carried and T+1 opens each day fully settled. The cap **is**
  the constraint, pinned by `test_settled_cash_invariant`. (The live danger is *sequential reuse* —
  recycling the same $250 twice intraday is a good-faith violation — which this book never does.)
- **Position sizing = risk-based, capped by notional** (AMENDED 2026-07-16, #237, superseding the
  original capital-based rule): each position **targets 5% of the day's opening virtual equity at
  risk** — `risk_qty = floor(0.05 × equity / (entry − stop))` — but is **capped at 50% of opening
  equity in notional** — `cap_qty = floor(0.50 × equity / entry)` — taking `qty = min(risk_qty,
  cap_qty)`. The 5% risk target binds on tight stops (where capital-based sizing would have taken
  wildly variable risk); the 50% cap binds on wide stops and remains the concentration /
  settled-cash bound. → still **max 2 concurrent positions, 2 entries per day**. Because the cap is
  always the upper bound, the settled-cash invariant is unchanged (`position_fraction ×
  max_trades_per_day = 0.50 × 2 = 1.0`, still pinned by `test_settled_cash_invariant`). R-multiples
  are size-independent so expectancy is still tracked in R.
  - *Superseded original:* capital-based, 50% of opening equity per trade
    (`qty = floor(0.50 × equity / entry_fill)`), risk-per-trade floating freely with stop distance.
- **Qualifying trade (all must hold):** (1) engine **v2 `pass`** (setup + every gate) **and
  triggered**; (2) **strictly pre-market fill** — the **trigger bar** opens before **09:30 ET**
  (deliberately stricter than the results-page `first_hit`-based "premarket" label, which can tag a
  setup that only *breaks* in-session); (3) **entry price (`entry_fill`) ∈ [$1, $20]** (narrower
  than the $1–50 scan universe, #126); (4) take the **first 2 by trigger time** each day, later
  qualifiers logged as *missed — at capacity*.
- **Stop:** consolidation low (engine v2, unchanged — the R denominator, #182/#190).
- **Exit = fixed R target `T` + optional breakeven arm at `b`·R.** Realized R is simulated by walking
  each trade's captured bars (reusing the `rmetrics._measure` stop-first / gap-through convention)
  inside the 16:00 ET analysis window (#93). **Costs + exit slippage are netted out** — at ~$250
  notional they are first-order, not a footnote.
- **Cost model = full IBKR tiered** (AMENDED 2026-07-15, #234, from commission-only; see
  `research/broker-costs.md`, #232). **Tiered** is the right plan for this account — IBKR Lite is
  US-residents-only, and tiered beats fixed across ~$1.70–20. Tiered **unbundles** the pass-throughs,
  and at these share counts they roughly **equal the commission itself**, so the original
  commission-only model understated a round trip by **20–50%**. Charged per trade: commission
  `max($0.35, qty × $0.0035)` + exchange liquidity-removal `$0.0030/sh` + clearing `$0.0002/sh` on
  **both** sides, plus FINRA TAF `$0.000166/sh` (cap $8.30) and SEC Section 31 `0.0000278 × proceeds`
  on the **sell**. The book is always liquidity-**removing** (stop-triggered entries, stop/market
  exits) so it never earns an add-liquidity rebate. Plus the **$10/mo market-data subscription**,
  charged at month rollover, waived above $30/mo commission, and applied **inline** so it compounds
  into sizing — it is ~2%/mo of a $500 book, and #232's central finding is that **fixed costs do not
  scale down with capital** (drag is ~9–13%/mo at $500 vs ~2.9%/mo at $2,000).
  ⚠️ The `$0.35` minimum and the `$0.0030/sh` removal rate are from corroborating secondary sources
  (IBKR 403s automated fetches) — verify in Client Portal before P2 funds anything real.
- **Adaptive target:** `T` (and `b`) are re-fit from recent results — over a trailing window pick the
  `(T, b)` maximising expectancy `E[R] = p(T)·T − (1 − p(T))·1` (with breakeven converting some −1R
  losers to 0R), where `p(T)` = fraction of recent qualifying setups that reached +`T`·R before the
  stop. Directly computable from the Max-R / bar data already captured. **Small-sample overfit is the
  main risk** (~2 trades/day): prefer a positive-expectancy *plateau* over the razor's-edge argmax;
  window length is a tunable parameter. ~~No loss-based **kill-switch** for now (2 trades/day makes
  it moot)~~ **(reversed 2026-07-16, #239 — an adaptive risk throttle / kill-switch was added; see
  below.)** A hard **≤2 open / ≤2 entries-per-day guard** is kept as idempotency against a
  reconnect/detection bug over-firing.

Deliverable: a typed, exhaustively-tested simulator in `src/small_cap_stack/` (per CLAUDE.md, this is
trading logic — the product), a `portfolio.json` export to the `dashboard-data` branch, and a thin
`docs/portfolio.html`/`.js` page. Open exit questions from `findings-index.md` §3 Q3 are **resolved
for this account** by the fixed-R-target-from-trailing-expectancy model above.

## Getting paid — withdrawals + UK tax + running cost (DECISION 2026-07-16)
The virtual book previously only ever **compounded** — it netted broker costs but never took money
out, so it answered "how big does $500 grow" and not "how much actually reaches my bank." This adds
a **getting-paid layer** on top: periodic withdrawals, a UK tax reserve, and the VPS running cost,
surfaced as a take-home figure in GBP. Built as a modelling layer on the existing paper book (three
new boundary ledgers alongside `_DataFeeLedger` in `portfolio.py`); **exhaustively unit-tested**, and
flowing through `portfolio.json` → the "Getting paid" panel on `docs/portfolio.html`. Decisions
(chosen by the user 2026-07-16):

- **Withdrawal rule = % of profit above a high-water mark, quarterly.** Each `withdraw_cadence_months`
  (default **3**) pay out `withdraw_fraction` (default **50%**) of the profit above the prior HWM,
  never below a **viability floor** (`withdraw_floor_usd`, default **$2,000**) and never distributing
  cash reserved for tax. The HWM then **ratchets to the post-withdrawal balance**, so each period only
  pays on genuinely new profit. Chosen over a fixed £/month salary (which drains the account to ruin
  in a drawdown) and over %-of-equity (which dips into base capital). **A no-op at the $500 start** —
  it stays dormant until the account clears the floor, which is the honest state (`broker-costs.md`
  §9: $500 is plumbing validation, not strategy validation). Withdrawals reduce equity, so the
  settled-cash invariant is preserved by construction (`test_settled_cash_invariant` still holds).
- **Tax = UK CGT base case, rate configurable.** Reserve `cgt_rate` (default **24%**, higher-rate
  share CGT post-30-Oct-2024) on net realised gains above the **£3,000** annual exempt amount
  (`cgt_annual_exempt_gbp`), accrued **per UK tax year (6 Apr–5 Apr)** and settled at the boundary.
  The reserve is held back from withdrawals so the book keeps enough to pay HMRC. Losses reduce the
  year's gain (floored at £0 within the year; cross-year loss carry-forward not modelled — a
  documented, conservative simplification). Real CGT is due the following **31 Jan**; the book settles
  at year-end, which reserves *earlier* (the safe direction for take-home).
  - ⚠️ **CGT-vs-trading-income is the biggest risk.** HMRC *could* treat systematic automated
    day-trading as a **trade** → Income Tax + Class 2/4 NIC (~42–47%) rather than CGT. For an
    individual, share speculation is *usually* still CGT (the badges of trade rarely bite securities
    dealing), but it is a genuine tail risk. The **rate is a config knob** precisely so the
    income-tax scenario can be modelled without code changes — set `cgt_rate` to ~0.42–0.47.
- **FX = single assumed GBP/USD rate** (`gbpusd_rate`, default **1.27**). The book is kept in USD
  (funded once from GBP, then permanently USD — `broker-costs.md` §5), so tax, VPS and take-home are
  derived through one rate rather than a daily FX series. The rate is quoted the market way (1 GBP =
  `gbpusd_rate` USD): USD→GBP divides, GBP→USD multiplies. **FX moves the taxable gain** (gains are
  legally computed in GBP per disposal); a single rate is an approximation, with a per-disposal daily
  series the accurate-but-heavier alternative (deferred).
- **VPS running cost** (`vps_gbp_per_month`, default **£10**) — charged monthly like the market-data
  fee but kept as its own line (different real-world expense; no waiver). Every month present is
  billed whether or not it traded.

**Other factors, recorded as non-blocking context** (so they aren't re-litigated): IBKR withdrawal
mechanics (1 free/month then a fee, plus a USD→GBP conversion spread — quarterly cadence keeps these
small; can be added as a per-withdrawal cost later); **PTP / Section 1446(f)** 10%-of-gross-proceeds
withholding on sales of US Publicly Traded Partnerships by non-US persons (rare for these names, ETFs
already excluded); and the *non-factors* — **no US CGT** for a non-resident alien (W-8BEN on file),
**no UK stamp duty** on US shares, and **no dividends** (intraday only).

Metrics stay honest under the new cash-flows: `return_pct` is a **total-value return** that adds
withdrawn cash back (so paying yourself doesn't read as a loss, while tax + VPS legitimately reduce
it), and `max_drawdown_pct` is measured on the **pure trading-P&L path** so scheduled cash-outs never
masquerade as a drawdown. Config knobs live in `config.py` as `portfolio_*` defaults (env-overridable,
consistent with the other portfolio knobs — not surfaced in `.env.example`).

## Adaptive risk throttle / kill-switch (DECISION 2026-07-16, #239 — reverses the #230 "no kill-switch" note)

The per-trade **risk fraction** (previously a fixed 5%) is now itself adaptive in the **adaptive
book**, throttled by recent results so exposure tracks how hot the market is — a kill-switch that
cuts to 0% in a losing streak and winds back to full in a good one. #230 had punted on this ("no
loss-based kill-switch for now — 2 trades/day makes it moot"); this decision adopts one.

- **Ladder (coarse on purpose).** Risk walks a small ladder of evenly-spaced rungs from **0 up to
  `portfolio_risk_fraction`** (the 5% cap), `portfolio_risk_rungs` rungs *including* the 0 floor —
  default **3 → (0%, 2.5%, 5%)**. Few rungs is deliberate: the user wants a **fast wind-up** back to
  full risk (2 good days from cold), not a slow many-step climb. `1` disables the throttle.
- **Signal = winning/losing *days*.** The ladder steps **one rung per day**: a net-positive day
  steps risk **up** one rung, a net-negative day **down** one, a flat/no-setup day **holds**. The
  day's result is its **aggregate realised R over its qualifying setups** — deliberately
  **size-independent** (pure R, not sized P&L), so a book throttled to the **0 rung** (which takes
  no trades) can still be scored on its *would-be* setups and **re-arm** when the tape turns.
  Otherwise 0% would be an absorbing state (no trades → no P&L → stuck).
- **Starts at full risk.** Kill-switch framing: the book begins live at the top rung and cuts *down*
  from there on a bad run, rather than earning in from 0. Stepping *today's* rung, then computing
  the step from *today's* resolved result for *tomorrow*, keeps it causal (no look-ahead) — the same
  discipline as the adaptive target.
- **Scope.** Only the **adaptive** book throttles risk (it already re-fits the R target); the
  fixed-target books stay at the full 5% as a clean baseline. Implemented as pure, replayable
  functions (`risk_ladder` / `step_risk_rung` / `_day_signal_r`) in `portfolio.py`, exposed on the
  page as a `daily_risk` series + a note, and exhaustively unit-tested (per CLAUDE.md). The
  settled-cash invariant is untouched: the throttle only ever sizes ≤ the existing 5% target, and
  the 50% notional cap remains the binding upper bound.
