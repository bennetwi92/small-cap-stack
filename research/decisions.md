# Resolved Decisions — Research Phase Closeout

**Date:** 2026-06-29. Resolves the open questions in [`findings-index.md`](./findings-index.md) §3.

> **This file is the log — *why* each rule is what it is, and when it changed. It is not the spec.**
> For what the system does *right now*, read [`strategy.md`](./strategy.md): it is generated from
> `config.py` and CI fails when it drifts. Entries below are dated and some are superseded further
> down the file; where one disagrees with `strategy.md`, `strategy.md` wins (#551).

## Locked decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Float threshold | **< 20 million shares** (share count, NOT $ market value). ⚠️ **This is a threshold, not a gate — it filters nothing.** `float_max_shares` feeds the EOD report's `float_ok` **count** and nothing else; the paper book takes names far above it. See [`strategy.md`](./strategy.md) §4. |
| 2 | Scanner / broker | **IBKR only.** User trades via the **TWS Mosaic scanner** today and considers it sufficient. ⚠️ Headless system must use the **API scanner (`reqScannerSubscription`)**, a different/more limited surface than Mosaic — see Spike below. |
| 3 | Exit strategy (Phase 1) | **Not required for execution** in Phase 1 (tracking only). BUT "Max R" reporting needs a **notional entry trigger + notional stop** to compute R — see Phase-1 note below. |
| 4 | News source | **Try IBKR news feed first** (what user used before). Subscribe to a paid service only if insufficient. |
| 5 | VPS | ⚠️ **REVISED 2026-07-01: Hetzner Cloud CX23** (x86, 2 vCPU/4 GB, Ashburn US-East, **€6.59/mo** per the Hetzner **console price estimate**, 2026-07 — *not* an invoice; none exists yet, first clean one is August, see #284). Switched from ~~Oracle Ampere Always-Free~~ after repeated "Out of host capacity" on the free A1 tier. Images are multi-arch so the host is swappable; deploy tooling retargeted to x86/`vps`. Oracle A1 kept as a $0 alternative (RUNBOOK §12) if capacity is obtainable. |
| 6 | Market data | User **will subscribe to IBKR market data** (incl. pre-market). Pre-market feed is a solved problem via IBKR. |
| 7 | Weekly 2FA | **Accepted for now** (one manual phone tap/week). User aware of a second-username / relaxed-2FA workaround to apply later himself. |
| 8 | Branching | **Trunk-based: protected `main` + short-lived branches, all work via PRs**, required CI checks before merge. Chosen because much work happens in PRs / Claude Code on mobile. |
| 9 | Stack | **Python + `ib_async`** (the maintained fork). Prior repos' raw-`ibapi` code is adapted, not lifted verbatim. |
| 10 | Storage | ⚠️ **SUPERSEDED 2026-06-29 by [architecture-review.md](./archive/architecture-review.md): use DuckDB-over-Parquet** (not Postgres/TimescaleDB) for Phase 1. ~~Self-hosted PostgreSQL (+ TimescaleDB) on the Oracle VM's 200 GB block volume.~~ Parquet-on-disk + growth-friendly intent unchanged; the embedded analytical engine changed. |
| 11 | Phase-1 scope | **Tracker only — places no orders.** Records every scanner-flagged opportunity, which gates it passed, whether a notional entry would have triggered, and Max R achieved + other stats. **All stats computed on the fly from cached raw data** so methodology can change retroactively. |

## Core architectural principle (from Q11)
**Store raw, compute derived on read.** Capture everything raw at flag time (bars, scanner snapshot, fundamentals, news, short interest) and keep gate evaluation + stat computation as **replayable pure functions** over that raw data. Changing gate definitions or the entry/stop spec later must NOT require re-collecting data — only re-running the computation over the cached raw record.

**Capture split — discovery intraday, bars at EOD (DECISION 2026-07-01, #62).** The intraday 60s tick does **discovery only**: scanner hits + opening opportunities + news/fundamentals at flag time (all point-in-time — not reconstructable later). The day's **5-min bars are pulled once in an end-of-day batch** (~16:20 ET, before the 16:30 report): a single `reqHistoricalData(durationStr="1 D", "5 mins", useRTH=False)` returns the whole session (04:00 ET→close) per flagged symbol. Replaces the fragile keepUpToDate streaming, which lost data + duplicated bars on a mid-session restart (observed after a deploy) and implicitly assumed a real-time feed we don't have (data is ~15 min delayed). The EOD job reads opportunities from storage and discovery rehydrates its open-set from storage on startup, so **restarts/deploys during market hours no longer create gaps**. Phase-1 places no orders, so real-time bars have no operational value.

## Standing principle — collect before you filter (2026-08-07, #569)

**While the sample is this thin, prefer collecting to filtering. A selection rule that narrows the
book needs measured support; an unmeasured one defaults OPEN.**

The reasoning is an asymmetry in what each mistake costs:

- A rule **wrongly imposed is invisible.** It removes candidates before they ever become trades, so
  the record contains no trace of what it excluded, and no amount of staring at the book reveals
  the error. You cannot miss what you never logged.
- A rule **wrongly omitted is visible.** The bad trades land in the book where you can see them,
  and — everything being compute-on-read — removing the rule later replays the whole history under
  it. Reverting costs one line and no data.

The two errors are not symmetric, so the cheap direction is to leave the net wide.

**Prior discretionary experience counts as evidence**, of a different kind than replay: the owner
traded this strategy manually for a year, across far more sessions and regimes than the tracker has
collected, and reports that **the best fills came at 04:00**. Thirty replayed sessions containing
four early triggers do not overturn that — they are not powered to.

**Worked example — the 05:30 floor (#405, reversed by #569).** Added on an unmeasured judgement
about thin tape. Reversing it admitted four trades, all stop-outs, −4.69R. That looks like the
floor was right, and it may be; but four trades at a ~43% base win rate produce four losses about
10% of the time by chance, so the number decides nothing either way. Under this principle the rule
goes back to open and the tape gets collected until there is something to measure.

**What ends this principle.** The reconstructed history (#428/#431) is rebuilding ~500 pre-market
sessions from purchased vendor bars. At that sample these stop being coin flips and become
measurable, and selection rules can be argued on evidence rather than on which error is cheaper.
Until then, treat any proposal that *narrows* the book — a price floor, a time window, a minimum
stop distance — as needing to clear a bar this sample mostly cannot.

⚠️ **This is a Phase-1 data-collection stance, not a trading philosophy.** In Phase 2/3 a wide net
costs real money and the asymmetry reverses: an unmeasured rule left open becomes a live loss
rather than a free observation. Revisit at the Phase-2 gate.

**Known gap it creates.** The book logs what the 2-a-day *cap* costs — a dropped candidate becomes
a `SkippedTrade` carrying the R it would have made — but logs **nothing** for a setup a *selection
rule* rejected, because those never become candidates at all. So today the only way to see a
selection rule's cost is to open the rule, which is part of why #569 went the way it did. Worth
closing once the harvest lands; not worth building against 30 sessions.

## Entry / stop spec (for Max-R measurement)
- **Entry trigger (CONFIRMED 2026-07-01, ⚠️ SUPERSEDED for engine v2 2026-07-10 by #182/#190 — see
  below):** ~~5 ticks above the high of the last _complete_ consolidation candle (i.e.
  `breakout_high + 5 × tick_size`; for $2–10 names tick = $0.01, so +$0.05). Revised from the
  earlier "1 tick above" (`notes.md`) after the user confirmed the real entry.~~ **Superseded — the
  5-tick entry is gone.** v2 splits it: a **1-tick** trigger decides *when* the setup fires and R is
  measured at a conservative **3-tick** fill (#182/#190,
  `Settings.bull_flag_trigger_offset_ticks` / `bull_flag_fill_offset_ticks`). `entry_offset_ticks`
  was deleted in #302 once the legacy detector went (#296).
- **Stop (CONFIRMED 2026-06-29):** the **low of the consolidation candle(s)** (the flag low). This is the R denominator; `R = entry − stop`.
- **Analysis window (CONFIRMED 2026-07-01, #93):** R-metrics (trigger / Max R / MAE) are measured only through the **regular close, `capture_end` = 16:00 ET** — after-hours bars are **excluded** so illiquid after-hours prints can't set Max R. Store-raw is preserved (all bars are kept in storage; the analysis window is bounded on read in `report.py`).

## Strategy notes captured 2026-06-29 (from `notes.md`)
> `notes.md` was the trader's raw scratch capture. Every bullet in it became a locked decision —
> the entry rule (above), exhaustion/re-entry and pre-market orders (below) — so the file was
> **deleted in #297**. This section is the record; other docs citing "from `notes.md`" mean here.
- **Opportunity exhaustion / re-entry (issue #36) — RULE CONFIRMED 2026-07-01:** a symbol can form >1 opportunity/day (runs, exhausts, extends again). **Rule (from the user):** once spotted, a symbol can't be re-spotted for **60 min** — a gap of ≥60 min with no scanner hits begins a *new* opportunity (e.g. pre-market pop → fade → market-open pop = the 2nd is new). Segmented **at analysis time** in `report.py` from the raw `scanner_hits` (not in live capture): each run gets its own bar window (extended back `reentry_lookback_min`=30 so the pole is captured), independent bull-flag/R-metrics, id `<date>:<symbol>#<run>`. Configurable via `Settings.reentry_gap_min`/`reentry_lookback_min`. Recomputes retroactively over already-collected data.
- **Pre-market orders (issue #37):** pre-market is **limit-only**; stops/TP must be **app-monitored** pre-market (broker-native stops only in the regular session). Reuse tradepilot's app-side exit logic. Execution concern (P2/P3).

## Scope (from user, 2026-06-29)
- User only ever acts on the **top 2–3 scanner rows, mostly the top 1.** The system only needs the *top few* candidates correct — the 50-row API cap and broad-universe concerns are largely moot.
- **UPDATE 2026-07-12 — scanner breadth raised to the full cap (`scan_max_rows` 10 → 50).** For *acting*, the top few still suffice; but Phase-1 is a data-collection exercise and on busy mornings there are far more than 10 low-float runners in play. Store-raw/compute-on-read means we capture the whole ranked list now and decide actionability on read later. One scanner request per tick regardless of row count, and opportunities dedup per symbol/day (news/fundamentals fetched once per distinct symbol; EOD bar/news batches are paced), so the wider net is safe on IBKR pacing. 50 is the API hard cap (`numberOfRows` is `min(scan_max_rows, 50)`).

## Remaining technical risks → validation spikes (before building)
- **A. API scanner vs Mosaic** (issue #8): ⏳ **largely validated 2026-06-29** — the API scanner returned a ranked candidate list **pre-market**, addressing the main suspected weak spot. `reqScannerParameters` confirmed IBKR exposes **trailing 5-min volume natively** (`stVolume5minAbove`, `stVolumeVsAvg5minAbove`, scan code `HIGH_STVOLUME_5MIN`), so the strategy's "5-min volume > 100k" is a built-in filter — NOT day volume, NOT derived from bars. Recommended scan: `TOP_PERC_GAIN` + ~~price 2–10~~ + `changePercAbove 10` + `stVolume5minAbove 100000` @ `STK.US.MAJOR` (**the price leg was widened to $1–50 by #126, below; the shipped subscription is [`strategy.md`](./strategy.md) §1**). Remaining: user to confirm API top 1–3 == Mosaic top 1–3 at the same moment.

  > **Criterion #5 (5-min volume > 100k) resolved:** native `stVolume5minAbove` scanner filter. This was a previously-open data-feasibility item in [`archive/strategy-validation.md`](./archive/strategy-validation.md).
- **B. Pre-market bar completeness** (#9): ✅ **GREEN** — active names get contiguous gap-free 5-min bars from 04:00 ET; only a leading absence before first trade. No interpolation needed.
- **C. IBKR news sufficiency** (#10): ✅ **GREEN to start** — account entitled to 8 providers incl. Dow Jones DJ-N (per-symbol headlines + retrievable bodies + halt notices). Start with included feed; measure timeliness in Phase 1 before paying.
- **D. Tradability gate** (#25, new): ✅ **GREEN** — `whatIfOrder` + error 201 reliably flags symbols IBKR blocks for the account even while they trade. Confirmed CBRG BLOCKED (PRIIPs/KID). **Account is under EU/UK PRIIPs rules** → expect some US small-cap SPAC/warrant/ETP runners to be un-orderable. **Add a tradability gate to the gate engine (#15).** Re-validate on live in P3.

## Architecture decisions (2026-06-29) — see [architecture-review.md](./archive/architecture-review.md)
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
  and the reconstruction's own `price_ok` check — both read the settings. (The `price_gate`
  named here was one of six unused gates deleted in #517; the scanner subscription is where
  the band is actually applied.) `tick_size` stays $0.01 (all names ≥$1 use a penny
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
- **Pole = a run of higher highs**, from a **single higher-high bar** up to `bull_flag_max_pole`
  (**4** since #302 — was 8 under the legacy detector); `bull_flag_min_pole`=1. ~~**Not**
  colour-gated — a non-green bar is allowed as long as the high still makes a higher high (SNDQ
  counted a 7-bar pole; SOXS/OKLL/DJT "characterised by higher highs").~~ ⚠️ **SUPERSEDED for
  engine v2 2026-07-10 by #182/#190** (colour-gated: no red/doji bar in the pole; a red *peak* is
  allowed and rejected by the `peak_green` gate). The colour-agnostic legacy detector was **deleted
  in #296** — v2 is the only engine. `pole_len` counts the higher highs; the ascending run's launch bar sets the pole
  base for the retracement. The peak must be a higher high than its predecessor, so a *descending* flag isn't
  mistaken for the peak. *Preferable* (soft, not yet quantified — deferred like the wick filter):
  the pole contains ≥1 big green candle.
- **Flag = a genuine pullback** of `1..bull_flag_max_cons` (**4** since #302 — was `max_flag`=6)
  bars that stays below the pole peak and
  **makes lower highs** — the trader tracks *highs*, not lows (correction 2026-07-03). Multi-bar:
  non-increasing highs with a net lower high; single-bar: any candle below the peak. Rejects
  consolidations that tick back up (ETHT/NBIZ).
- **Retracement gate:** reject a flag retracing > `bull_flag_max_retracement`(0.50) of the pole
  height, measured on the flag low (the risk). Encodes "back through the pole" (AHMA/CLRO/CYH/DJT).
- **Volume:** the pole's peak bar volume **must exceed** the consolidation's peak bar volume (hard).
  Whether the consolidation volume is reducing is recorded (`cons_vol_reducing`) but **not** gated —
  it may be flat.
- Entry/stop spec **unchanged** (~~5 ticks above the last consolidation high~~; stop = flag low).
  ⚠️ The 5-tick entry was superseded by the **1-tick trigger / 3-tick fill** split (#182/#190, §"Entry"
  below). Current values: [`strategy.md`](./strategy.md) §2.

**Follow-ups (separate issues, not in #127):** ATR%/movement gate for "barely moving/ranging" names
(CLVT/CYH/CMMB); entry appearance-bar gate #122 (SOXS/JEM mid-bar appearance); later-intraday setups
& entry-staleness (CLRO/TSDD/AHMA "entry an hour after the scan"); half-pole-stop research (IREZ).

## Engine v2 volume gate = peak-bar (DECISION 2026-07-10, #176 — reaffirms #127)
The engine-v2 redefinition (`research/bull-flag.md`, umbrella #176) keeps the volume filter on the pole's
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

**`entry_offset_ticks` (the legacy 5-tick entry) was deleted in #302**, along with the legacy
detector itself (#296). The trigger/fill split is the only entry model: both are live, read from
`Settings`, and pinned by `tests/test_settings_wiring.py`.

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
  triggered**; (2) **pre-market fill inside `[05:30, 09:15)` ET** — the **trigger bar** opens at or
  after **05:30** and before **09:15** (cutoff AMENDED 2026-07-21, #383, from 09:30: the final
  pre-open ramp/auction 09:15–09:30 trades like the open and is excluded from this strategy — a
  VMAR entry at ~09:25 on 2026-07-20 was a loss; spike #379/#380 only swept *relaxations* of the
  old cutoff, never this tightening. Floor ADDED 2026-07-31: no trades on the 04:00–05:30 tape.
  Like the $2 price floor this is a **selection** call, not a measured edge — the 2026-07-31
  time-of-day report found *no* pre-market window statistically separable from another (04:00–06:00
  is −0.32R over 86 triggers, but permuting entry-time labels within a day reproduces that spread
  68% of the time). The **scan window stays 04:00–11:59**, so pre-05:30 names keep being captured,
  charted and scored on the results page — they simply stop being takeable. Both bounds are still
  deliberately stricter than the results-page `first_hit`-based "premarket" label, which can tag a
  setup that only *breaks* in-session); (3) **entry price (`entry_fill`) ∈ [$2, $20]** (narrower
  than the $1–50 scan universe, #126; floor AMENDED 2026-07-31, #386, from $1 — sub-$2 entries are
  excluded from the book. This is a **selection** decision, not a cost one: `research/broker-costs.md`
  §3 still stands and the **scanner floor stays $1**, so sub-$2 names keep being captured, charted
  and scored on the results page — they simply stop being takeable. Store-raw/compute-on-read means
  the whole historical book re-simulates under the new floor on the next publish);
  (4) take the **first 2 by trigger time** each day, later
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
  - **All history, and a margin before switching (AMENDED 2026-08-06, #476).** Two changes, both
    from the same report as #474. (1) **`portfolio_adaptive_window_days` → `None`.** A trailing
    window is itself a regime bet — with a stationary distribution you would use every trade. Its
    length trades estimation error (longer is better) against regime staleness (shorter is better),
    and at n=13 we are overwhelmingly in the estimation-error half, so discarding trades to stay
    current buys nothing. Shorten it again only when drift is something we can *measure*.
    (2) **`portfolio_target_switch_z` = 1.0.** The fit was a raw argmax over four noisy means; a
    pick other than the fallback must now clear one standard error of **paired** edge before the
    book acts on it. Paired is both the correct test and far more powerful, because the same trades
    are scored under both exit rules and the per-trade variance largely cancels: against the 2.0R
    fallback on 13 trades, 1.5R is decisive at z=−4.38 while 2.5R (−0.89) and 3.0R (−0.84) are
    merely undecided. On the current data the gate changes no historical decision — the fit's pick
    never differed from the fallback on any of the 10 days it ran — so it is a guard for the future
    bought at no cost. A zero-variance edge is scored as ±∞σ, not as an unmeasurable one: a
    deterministic improvement is the *strongest* evidence, and dividing by a zero SE would block
    exactly the switches most clearly justified. Three states are now published and rendered —
    `fitted`, `thin` (never ran), `margin` (ran, not proven) — because "no evidence yet" and
    "evidence too weak" point at different fixes.
  - **Window widened 20 → 40 calendar days (AMENDED 2026-08-06, #463; superseded by #476 above).** "Window length is a tunable
    parameter" turned out to be load-bearing: at 20 days the re-fit **never ran once**. The live
    book takes ~13 candidates per 36 calendar days, so a 20-day window held at most **7** against a
    `min_samples` of **8** — permanently one short. Every day of the book's published history, and
    the target advertised for the next session, was the **2.0R fallback** while the page called it
    a re-fitted target. 40 days (≈28 trading days) holds ~14 at the current arrival rate. Slower
    response to regime drift is the accepted cost. `min_samples` deliberately did **not** move —
    firing sooner by fitting a target on 5 trades buys a number, not evidence. The fix that keeps
    this from recurring is the instrumentation, not the constant: `TargetFit` now carries
    `fitted` + `trailing_n` through `daily_targets` and `next_session`, and both the portfolio and
    plan pages render **FITTED vs FALLBACK** explicitly. Note the arrival rate, not the window, is
    the real constraint — as the harvest deepens the sample this may want revisiting again.

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
- **VPS running cost** (`vps_gbp_per_month`, default **£5.70**) — charged monthly like the
  market-data fee but kept as its own line (different real-world expense; no waiver). Every month
  present is billed whether or not it traded. The figure is the Hetzner CX23 at **€6.59/mo** — the
  **console's price estimate** (2026-07), *not* an invoice: the box was created 2026-07-01 and
  Hetzner has not billed yet. The knob is GBP because the cost model is GBP-denominated end-to-end,
  so the EUR→GBP conversion (~0.865) is folded into the default rather than modelled as a third
  currency.
  **Unconfirmed (#284):** the estimate was taken while a 10 GB block volume was attached (empty from
  creation; deleted 2026-07-17). If it was a project-level figure it likely *included* that volume
  (~€0.48/mo), putting the box's true cost nearer **€6.11** and this default ~£0.41/mo high. July's
  invoice won't settle it either — the volume was deleted mid-month, so it carries a partial charge.
  **August is the first clean invoice**; reconcile then. Also revisit if the euro moves materially
  or the box is resized.

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
  full risk, not a slow many-step climb. `1` disables the throttle.
- **Signal = winning/losing *days*, with a streak requirement.** The ladder steps **one rung only
  after `portfolio_risk_step_days` consecutive same-direction days** (default **2**; `1` = eager,
  one rung per decisive day). A run of net-positive days steps risk **up** one rung, a run of
  net-negative days **down** one. The day's result is its **aggregate realised R over its qualifying
  setups** — deliberately **size-independent** (pure R, not sized P&L), so a book throttled to the
  **0 rung** (which takes no trades) can still be scored on its *would-be* setups and **re-arm** when
  the tape turns; otherwise 0% would be an absorbing state (no trades → no P&L → stuck). A
  **flat / no-setup day holds both the rung and the streak** — an information-less day carries no
  momentum, so "in a row" counts *decisive* days across flat gaps rather than resetting on them.
  (Amended same day, #239: the first cut was one-rung-per-day, which the user found too twitchy — a
  single green/red day shouldn't move risk — so the streak requirement was added.)
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

### ⚠️ REVERSED 2026-08-06 (#474) — the throttle ships OFF (`portfolio_risk_rungs=1`)

The decision above was never tested against data; it was adopted on the reasoning that exposure
*should* track how hot the market is. Measured on the first 29 sessions (13 trades, 12 active days)
in the report *"Does past behaviour predict future performance?"* (`docs/reports/`, 2026-08-06), the
premise does not hold and the machinery is not free:

- **The premise is a bet on serial correlation of daily results, and none is detectable.** Lag-1
  autocorrelation of daily R: **+0.31**, permutation p=**0.27**, CI −0.32…+0.75. Conditioning on
  *two* up days (+0.98R) is **worse** than on one (+1.23R) — the wrong shape for momentum. The
  down-trigger has fired once in the book's life; the next day was +2.00R.
- **Absent that correlation it is a structural drag, not a neutral guard.** Over 500
  calendar-preserving shuffles — day order permuted, trade population preserved, so serial
  correlation is zero *by construction* — the ladder cost a mean **$22.35**, losing on **291**
  shuffles and winning on **72**. The mechanism is arithmetic, not luck: fixed-fractional sizing
  already de-risks after a loss (5% of a smaller balance is fewer dollars), and the ladder cuts a
  *second* time on the same information. Kelly sizes on current equity, not on recent streaks.
- **On the live path** it cost **$32.84** (5.3% of the book) and bought **0.01pp** of drawdown,
  having de-risked into the two best days in the sample.
- Under the null it does buy **0.83pp** of drawdown reduction — real insurance, honestly measured.
  The premium is ~**3.5% of the book per month**, which is a bad price for a small compounding
  account. That trade is the whole decision; it is not a claim that the ladder does nothing.

**Nothing is deleted.** `risk_ladder` / `step_risk_rung` / `_day_signal_r` stay implemented and
exhaustively tested (the ladder tests now pin `portfolio_risk_rungs=3` explicitly), so re-enabling is
a one-line config change. The bar for that is a sample which can *detect* the effect: ~85 active days
for an autocorrelation of 0.3, against the 12 we have.

If capital preservation is still wanted — and it reasonably might be — the right shape is a
**drawdown circuit-breaker** (cut risk when equity falls X% from its high-water mark), which is a
statement about ruin and needs no autocorrelation premise to justify itself. Not adopted here.

## Ledger gap-months + the skipped log's two populations (DECISION 2026-07-16, #249/#251 — refines #230/#232/#239)

Three narrow amendments from the #249/#251/#256 audit fixes. All in `portfolio.py`; all
mutation-tested.

- **Recurring costs are billed per calendar month, not per *observed* month.** `_DataFeeLedger` and
  `_VpsLedger` settled a fee only when a day from a *new* month arrived, so a month with **zero
  collected dates** — a data outage — never triggered a rollover and was silently free (June data →
  September data charged June once and re-anchored, dropping July and August). Both now walk month
  by month between the first and last collected date. This is #232's own thesis: the subscription
  and the box bill whether or not you trade **and whether or not we collected**. Gap months carry no
  commission, so the market-data **waiver cannot apply** to them. A gapless run bills exactly as
  before, and each gap month gets its own dated `CashFlow` at the start of the month it rolls into.
- **`_WithdrawalLedger` is deliberately NOT part of this.** It is anchored the same way, so a data
  gap still yields one withdrawal rather than one per missed cadence. That asymmetry is intentional:
  a monthly *bill* accrues on the calendar regardless, but a *payout* is a fraction of profit above
  the high-water mark, and during an outage that profit didn't change. Paying yourself twice out of
  one unchanged profit pool is a modelling choice, not an obvious fix — tracked in **#274** rather
  than settled by a drive-by. (Raised by review of #249.)
- **The skipped log has two populations, and the headline counts only one.** `SkippedTrade` gains
  `skip_reason`:
  - `"cap"` — past the day's `max_trades_per_day` by trigger time. **This alone** feeds
    `skipped_total_r` / `skipped_count`, because the page asks exactly one question — *what did the
    N/day cap cost me?* — and mixing populations would make it misattribute.
  - `"unaffordable"` — selected, but `size_position` returned `qty < 1` **at full configured risk**.
    These previously vanished from every log (#251).

  **Throttled sizing is never "unaffordable".** Any kill-switch rung can size to zero on a wide stop
  (rung 1's 2.5% is a $12.50 risk budget at $500, so a $15/share-risk setup sizes to 0 while the
  book is healthy), so the test is `rf >= portfolio_risk_fraction`, not `rf > 0`. Blaming equity for
  what the ladder did would be a lie on the page. For the same reason a **rung-0 day logs no cap
  skips**: nothing was taken, so the cap was never the binding constraint — counting those would
  inflate the cap's cost with kill-switch days.

## Trading calendar of record: exchange_calendars XNYS (DECISION 2026-07-17, #137)

Root cause of the 2026-07-03 junk session: nothing in the code knew the market was closed
(Independence Day observed), so the app scanned all day, captured a perennial ETF (SOXS) as the
day's only "opportunity", and clobbered the dashboard's last completed session. Weekends scanned
too.

- **Source of truth: `exchange_calendars` (PyPI, XNYS calendar)** — pure-Python, offline,
  deterministic. Knows weekends, full holidays, historical ad-hoc closures, and **early closes**.
  Fits the offline test + compute-on-read replay model (no live connection needed), and holidays
  are published years ahead, so a dependency bot keeps it current with near-zero effort
  (`.github/dependabot.yml` added alongside).
- **Manual override:** `Settings.calendar_closed_dates` marks extra closed dates so an
  *unscheduled* closure (e.g. a national day of mourning) is patchable via env without a release.
- **Rejected:** a hardcoded holiday list (it *is* the keep-it-updated problem the incident
  exposed). **Deferred:** the IBKR `tradingHours` runtime cross-check (authoritative and
  self-updating, but needs a live connection; worth adding at connect time if an ad-hoc closure
  ever slips through).
- **Wiring:** `market_calendar.is_trading_day` / `early_close_et` (pure, cached). `_on_tick` skips
  the scan block + stats refresh on non-trading days (status export still runs); `eod_bars` /
  `eod_report` no-op; `eod_backfill` filters its *dates* rather than skipping the job — gating the
  whole job on a weekend would strand a failed Friday EOD (Monday's 3-day lookback no longer
  reaches Friday). Early closes never clip the 04:00–11:59 pre-market scan window and the 16:20+
  EOD crons stay valid on a 13:00 close, so job times are unchanged; `early_close_et` exists for
  any consumer that does care about the close.

## Repo stays PUBLIC; automation stays $0 (DECISION 2026-07-17, #344)

Deciding how to host the GitHub-native automation layer (`research/archive/github-automation.md`) under a
**hard $0 constraint** (no paid plan). $0 is decisive and rules the topology:
- **GitHub Pages on the Free plan works only from a *public* repo** — a private repo would take the
  `docs/` cockpit dashboard offline (private-repo Pages needs Pro+).
- **The self-hosted runner is reliably free only on a *public* repo** — the (postponed) $0.002/min
  self-hosted charge applies to *private* repos, and the Free plan's $0 default spending limit makes
  private-repo jobs *fail rather than bill*. So a "public-code / private-ops split" is **worse** at
  $0, not better: the box runner is free on public, metered/failing on private.

**Decision: stay public, harden in place.** Rejected: fully-private (kills the dashboard on Free),
GitHub Pro (costs money), and the public/private split (worse at $0 + doubles ops).

**Consequence accepted:** the risks a repo-flip would have erased are now **permanent design
elements**, not removable — (a) prompt-injection gating on any agent that reads public issue/PR text
(#343), (b) scrubbing public Pages telemetry to coarse liveness only, no ops-recon, no signals at
P2/P3 (#340/#341), and (c) supply-chain hardening — SHA-pinned actions, least-privilege
`GITHUB_TOKEN`, no fork-writable cache on deploy paths (#348).

**Self-hosted runner posture — A1 now, A2 later:**
- **A1 (do now, #333):** keep the runner but lock its jobs to `push`-to-`main` + `workflow_dispatch`
  only (owner-only triggers a fork PR cannot invoke) + "require approval for outside collaborators".
  Residual risk: never click "approve & run workflows" on an untrusted fork PR.
- **A2 (tracked follow-up, #353):** remove the self-hosted runner from GitHub entirely and make the
  box **pull-based** (systemd/cron `git pull` + `docker compose up`; box *pushes* the data-export
  branch on a schedule). Closes the §0 hole at its root — no runner to hijack — while staying public
  + $0 + keeping Pages. Cost: re-architecting deploy + data-export to box-initiated, losing the
  "click deploy in Actions" UX.

## The GitHub automation layer is ROLLED BACK (DECISION 2026-07-19, #377)

The agent/automation layer built on 2026-07-17 (PRs #358–#369) is **removed**. Nine workflows
deleted — `claude`, `spec`, `triage`, `self-heal`, `overnight-analyst`, `commands`, `watchdog`,
`workflow-keepalive`, `oom-victim-test` — plus `src/small_cap_stack/watchdog.py` and the
`spike-request` issue form. The design writeup is archived at `research/archive/github-automation.md`.

**Why.** It cost protocol and returned nothing measurable. Over its life the agent workflows
opened **zero** issues and **zero** PRs; `commands` and `spec` fired only as skips; `self-heal`
and `overnight-analyst` never ran at all; `watchdog` ran 20× green and silent. Against that, it
added a spec gate, five slash-commands, a `trivial` lane, an agent-PR close/reopen CI nudge, and
~27 lines of CLAUDE.md protocol — enough that the repo stopped feeling legible to its owner.
The **spec gate was actively harmful**: instructing the agent to refuse `strategy`-labelled work
without `spec-ready` put a gate between the owner and the most common category of work in the repo.

**What replaces it: nothing — the prior model was never displaced.** Work is the owner driving
Claude Code (desktop/mobile), one issue per unit of work, one PR per issue. That loop never routed
through any of the deleted workflows. Liveness stays on the app's **Healthchecks.io dead-man's
switch** (`monitoring.py`, live and armed on the box), which predates the automation layer by three
weeks (#29) and already covered "would I know if the tracker died".

**Kept:** `canary.py` (local data-quality JSON, no GitHub coupling) · the runner lockdown (#355) ·
swap + container memory limits (#329) · the seven human-triggered workflows (`ci`, `deploy`,
`build-image`, `publish-dashboard`, `backfill-dashboard`, `deploy-backfill-publish`, `data-export`).
**Kept deliberately inert:** the `CLAUDE_CODE_OAUTH_TOKEN` / `WATCHDOG_HEARTBEAT_URL` secrets and
the `alert`/`trivial`/`needs-spec`/`spec-ready` labels — harmless with no workflows to read them,
and they make a future rebuild one step instead of five.

**If rebuilt:** ship the *single* piece that removes a real, felt pain, run it for a week, and only
then consider a second. The failure here was building ten pieces in one day for pains not yet felt.

---

## 2026-07-31 — Reports are repo prose served by Pages, not box data (#392)

**Decision.** A *report* (a written analysis, produced on request) is a markdown file committed to
`docs/reports/` with front-matter metadata, listed on a new **Reports** page in the dashboard and
served straight out of GitHub Pages. `src/small_cap_stack/reports.py` builds
`docs/reports/index.json` (the list the page reads); `make reports` regenerates it and
`tests/test_reports.py` fails if the committed index is stale.

**Why not the box.** Every other page reads the `dashboard-data` branch, which `publish-dashboard`
**force-pushes as a fresh single commit every 15 minutes** — hand-written content there survives at
most one cycle. Reports are prose authored alongside the code, so they belong in git, reviewed
through the normal PR flow. `docs/` is already the Pages source, so merging *is* publishing: no box
round-trip, no new workflow, and no dependency on the runner being healthy to read an analysis.

**Why front matter over a JSON manifest.** Metadata lives next to the prose it describes, so a
report is one file to write and one file to review; the generated index is a build artifact of it.
The parser is a deliberately tiny YAML *subset* (`key: value` scalars) rather than a YAML
dependency — it rejects unknown keys loudly, which catches `sumary:`-class typos at CI time
instead of silently dropping the field.

**Rendering.** Markdown is rendered client-side by `marked` from the same jsDelivr CDN the charts
and grids already use, styled with cockpit tokens rather than the renderer's stylesheet. A CDN
failure degrades to the raw markdown source rather than a blank pane.

---

## 2026-08-01 — The portfolio page projects forward, and says when it can pay a salary (#411)

**Decision.** The virtual-portfolio page gains a second view — **VIEW → Projection** — carrying a
bootstrap Monte-Carlo of the next year plus the arithmetic for "when could this replace the day
job". The book view is unchanged. Both views are separate `.pf` regions swapped by the selector,
because the book view is already sized to fill exactly one screen on a desktop: anything appended
*inside* it would have to scroll, and one-screen-no-scroll is the cockpit's whole contract (#397).

**Method — moving-block bootstrap over trading days, not trades.** Each collected day becomes one
scale-free sample: its trades' P&L as a fraction of that day's *opening* equity (what both
concurrent positions size against), plus the commission it generated on the same base. Days the
book sat out are kept — they set the cadence at which the withdrawal floor and the tax year
arrive. Paths draw days in **5-day blocks** so losing runs stay runs; i.i.d. day sampling washes
streaks out and roughly halves the projected drawdown, which is the number the whole feature
exists to produce. The four period ledgers (VPS, market data, CGT, withdrawals) are the *same
objects* the historical day-walk uses, settling in the same order, so a projected pound and a
historical pound are computed once.

**Why day-level returns rather than replaying candidates.** Re-running selection/sizing/exit over
resampled *setups* would need a year of synthetic bars and would still assume the same thing this
does — that the edge persists. Returns-on-opening-equity carries costs, sizing and the day's risk
rung with it, in one number that is already tested.

**The income question is not an extrapolated equity curve.** The withdrawal policy takes money out,
so the curve that pays you is not the curve that compounds. Instead the projection carries a
reinvest-everything shadow balance, annualises *its* growth, and inverts the steady-state identity
`T = P − fixed − max(0, P − exempt)·cgt_rate` to get the capital that sustains a given take-home.
`capital_for_income` and `income_from_capital` are each other's exact inverse by test, so the ramp
chart and the ladder table can never quote different tax rules.

**The day rate is compared net-of-tax, both sides.** £800/day inside IR35 is employment income;
matching a gross assignment rate against a post-CGT withdrawal would flatter the day job by the
whole PAYE bill. `portfolio_day_rate_net_fraction` (0.52) is an explicit, adjustable estimate —
employer NI + apprenticeship levy off the top, then PAYE/NI with the personal allowance tapered —
not a tax engine, and the page says so.

**Two guards, because the honest answer is often "no".** A non-positive growth rate returns `None`
rather than a date. And above **10×/yr** the projection sets `growth_implausible`: fixed-fractional
compounding turns a short lucky run into hundreds of times the account per year, and the capital
arithmetic then divides by that rate and reports that a £91k salary needs $551 — right arithmetic,
meaningless input. The page leads with that instead, and dims the table.

**Determinism.** The RNG is seeded from settings. `publish-dashboard` rebuilds every 15 minutes and
an unseeded fan would drift between publishes, reading as news when it was noise.

**Cost.** ~0.5 s per book, ~5 s per payload build across all nine. Tests dial
`portfolio_projection_paths` down — at production settings the portfolio suite went 4.5 s → 57 s,
which is an order of magnitude of CI spent re-running a simulation those tests assert nothing about.

## 2026-08-04 — The 2-year harvest runs on the FREE tier, delivered nightly (#430)

**Decision: ingest path 1 — Massive free tier, REST, no purchase.** #430 laid out three paths and
expected this one to be a non-starter, recorded only so the arithmetic was on the record. It is
what we are building. The owner's call, made with the cost known: **~363 h of wall clock ≈ 45
nights** at the free tier's 5 calls/min, versus 4 nights for $29 (Starter, polite 60/min) or ~1 for
flat files.

**Why 45 nights is acceptable — the harvest is incremental, not a batch job.** The number that
made this look prohibitive was time-to-*completion*. That is the wrong metric for a job whose
output is a growing sample rather than a single artifact. The harvest runs **newest-first,
backwards in time**, one whole session at a time, and each completed day lands in the store as soon
as it is done. So the deliverable is not "a backtest in 45 nights", it is **a slightly deeper
sample every single morning** — ~11 trading days per 8-hour night (2,400 calls ÷ ~218 calls/day:
one grouped-daily call plus a mean 217 candidates). Nothing is gated on the harvest finishing, and
stopping it early leaves a complete, contiguous, usable history rather than a partial one.

Two ordering rules follow from that and are part of the decision:

- **Grouped-daily for the whole window first** (~500 calls, under two hours). #428 established the
  previous close is a *required* input, not a prefilter nicety — it is the one scan gate a single
  day of bars cannot decide, and without it reconstruction fires a median 18 min early. Pulling all
  of them up front means every subsequent night's minute-bar work is immediately correct.
- **Then minute bars, newest-first.** The most recent history is the most relevant to a strategy
  being validated now, and it is contiguous with the live collection window, so the combined book
  has no hole in the middle.

**The virtual portfolio publishes live and reconstructed side by side, never merged in place.**
This is the constraint that made the decision safe to take. `books` stays live-only and
byte-identical to what it was; the reconstructed days appear in a second set, `books_all`, spliced
in date order, selected by a `DATA: Live | + History` control on the portfolio page. Every trade,
skip and cached candidate carries a `source` of `"live"` or `"recon"`, and each book reports a
`by_source` split.

The reason for parallel books rather than one longer curve is that the book is **path-dependent
twice over**: the adaptive re-fit chooses each day's target and risk rung from a trailing window,
and every position sizes off running equity. Splicing ~500 reconstructed days in front of the live
ones does not *extend* the live record — it *replaces* it, with a live segment starting from
whatever equity the reconstruction ended at and trading targets chosen by vendor-derived trades.
Phase-1's deliverable is what the tracker actually saw, so it is preserved as-is.

Consequences of the same reasoning:

- **The forward projection stays live-only.** It answers "what will *my account* do", so it must
  resample observed returns. A reconstructed-heavy history describes a universe we never had.
  ⚠️ **AMENDED 2026-08-06 (#460):** the stated mechanism was wrong. This said "only capacity
  explains" SNDQ's late live appearance; measurement says the 50-row cap has **never bound** (max 45
  symbols in a tick across 20 live days, and **11** in pre-market). The divergence is real but comes
  from appearance *timing* — most likely #433's change-percent reference price — not capacity. The
  decision stands; the reason is corrected. It also keeps a second 500-path × 252-day Monte Carlo per target off the 2-vCPU box.
- **Live wins on any overlapping date.** The #428 calibration days sit in both stores; live is the
  ground truth the reconstruction is measured against, so the harvested copy is dropped (and the
  drop is counted in the payload's `coverage` block, not silently swallowed).
- **A separate store root** (`data/recon`, `Settings.recon_subdir`), not a `source` column in the
  live partitions. The two are date-disjoint, and separate trees mean no existing reader — the EOD
  report, charts, the canary, `collected_dates` — can start returning vendor rows by accident. Only
  code that explicitly opts in (`build_portfolio_payload(recon_store=…)`) ever sees them.

**One lever could cut 45 nights materially, and it is measurable before spending a night on it.**
The candidate count (mean 217/day) is what sets the budget, and it comes from a prefilter whose
volume floor is *day volume > 100k* — chosen because a name clearing a 100k trailing 5-min sum must
by definition trade at least 100k on the day. That is airtight but very loose. Measured against the
only ground truth available — the 25 committed review cases, every one a name the live scanner
actually surfaced — the **minimum captured-window volume is 1.25M** (p10 2.5M, median 17.5M);
**none** is under 1M. A floor of 500k would therefore retain 25/25 with 2.5× headroom while cutting
the candidate set by whatever share of the >10%/$1–50 population sits between 100k and 500k. That
share is unmeasured, so **no floor is being changed on this decision** — but re-running the
prefilter at several thresholds costs ~3 grouped-daily calls and belongs in #431 before the first
full night runs.

**Not chosen, and why:** Starter at $29 was rejected on the owner's stated preference for $0, not
on the merits — it remains the correct escape hatch if the sample is wanted sooner, and flat files
remain the right answer for any future harvest wider than two years. Nothing here forecloses
either: the store layout and the portfolio's provenance split are the same whichever path fills them.

## 2026-08-04 — The harvest runs as a memory-capped nightly job, not a batch (#431)

**Decision: the producer for #430's store is a systemd-timed, containerised, checkpointed nightly
run — `src/small_cap_stack/harvest/`, driven by `python -m small_cap_stack.harvest`.** #430 decided
*what* to buy (free tier, REST, no purchase, newest-first); this decides *how it is allowed to run
on a box that has already killed itself once*.

**Three guards, in layers, because none of them alone is sufficient.**
1. **Streaming** — one session, one symbol at a time; bars are derived to rows and dropped before
   the next symbol. Peak resident set is one symbol-day of minute bars plus one session's rows, and
   it does not grow with the number of sessions harvested. That is the #273 failure mode designed
   against rather than promised away.
2. **A window the job refuses to run outside** — ⚠️ **AMENDED 2026-08-05 (#455): 12:30→03:00 ET**,
   from 17:00→03:00. At the free tier the harvest's calendar is set purely by hours-per-day, and
   12:00–16:20 ET is the only block of the box's day nothing is scheduled in — worth ~4.5 hours,
   taking ~40 nights to ~27. Still hard-stopping at 03:00, clear of the 03:45 `eod_backfill` and
   the 04:00 scan window. Being *launched* at the right time and *refusing* the wrong one are
   different guarantees; a late timer, a manual re-run or an overrun only trips the second.
   Overriding takes two deliberate flags (#261's principle).
   **The widened window spans the two EOD jobs, so it needed a third bound: `harvest_eod_recess_et`
   (16:10).** The reviewed-away assumption was that `HostGuard` would cover this. It cannot — the
   guard is checked once per *session*, and a session is ~47 min, so a 12:30 start puts session
   boundaries at 15:38 and 16:25 and the harvest is *inside* a session, holding 1 GB with no swap,
   across both EOD jobs while `build_portfolio_payload` runs. A deadline is enforced between
   symbols and by the "don't start what you cannot finish" pre-check, so it bounds where the
   container can still be *running*; the guard only bounds where a new session may *begin*.
3. **A cgroup limit the kernel enforces** — a separate `docker run --memory=1g` with swap disabled
   and `--oom-score-adj=800`. Deliberately **not** `docker exec` into the app: sharing the
   tracker's 2 GB cgroup would spend the tracker's headroom and OOM the tracker instead of the
   harvest. The in-process host-headroom check stops *at* a checkpoint; the cgroup cap kills
   *before* the host is at risk. A promise is not a limit.
   ⚠️ **AMENDED 2026-08-05 (#452):** the limits live on **`scs-harvest.slice`** with
   `--cgroup-parent`, not on the service unit, and the deprioritisation is `CPUWeight`/`IOWeight`,
   not ~~`nice -n 19`/`ionice` idle~~. `docker run` hands container creation to the daemon, so the
   container landed in `/system.slice/docker-….scope` — the unit's `MemoryMax` bounded a ~15 MB
   docker client and the `nice` prefix deprioritised a process blocked on a socket. Measured, and
   verified by having the kernel kill a container given no `--memory` of its own inside a 64 M
   slice.

**A session is atomic.** Each dataset lands in one parquet file written at session end, and the
checkpoint is marked after. So a kill — hard stop, OOM, hard reboot — leaves the date with no files
and unclaimed, and the next run discards any leftovers before redoing it. The alternative fails
silently: a half-written day extracts perfectly well, just from half the symbols, and nothing
downstream could tell. One file per `dt=` partition is also what keeps read cost sane
(#318/#319/#321).

**Deviation from the issue, deliberate: the stored 5-min bars span the FULL session, not just
pre-market.** #431 asked for pre-market only to bound the payload, but the vendor returns a whole
session per request — so trimming saves storage, not API budget, and the budget is the scarce
thing. Truncating at 09:30 would have cost accuracy for nothing: `portfolio.exit.simulate_exit`
marks an unresolved trade to the *last bar it can see*, so every still-open 09:10 entry would close
at 09:25 and be reported as that trade's result — a silent downward bias on exactly the trades that
were working. The restriction is kept where it does buy something: the raw **1-min** series is
stored pre-market only (~330 rows/symbol-day), because that is all the appearance reconstruction
reads.

**The reconstruction primitives moved from `spikes/` into the package.** They are a producer now —
500 sessions written into the store the paper book reads — and spikes are exempt from mypy and the
test suite. The spikes import them back rather than keeping a copy, so #428's calibration measures
exactly the code the box runs; a second copy is how the evidence quietly stops describing the
output.

**Still not modelled, and it bounds what the harvest is evidence *for*:** the appearance *time*.
⚠️ **AMENDED 2026-08-06 (#460):** this originally named the IBKR 50-row rank cap and cited #428 as
having shown it load-bearing. Measured across 20 live days the cap has **never bound** — 45 symbols
in the busiest tick, **11** in pre-market, zero ticks at 50 — so it explains nothing here. What does
still differ is *when* a name is surfaced, and #433 is the open question on that (the vendor's daily
close includes post-market prints IBKR's reference price does not). A reconstructed day can
therefore still surface setups the live scanner would not have, which is why #430 keeps the two
stores apart and stamps every trade with `source` — the harvest widens the sample, it does not
extend the Phase-1 record.

**Unchanged pending measurement:** the day-volume floor stays at 100k. `harvest sweep` measures
what a tighter floor would cut, against stored rows and for no API calls; run it before the first
full night (RUNBOOK §13.1). If it halves the candidate set, 45 nights becomes ~23.

## 2026-08-06 — Delegation comes back, alone (#489, amends #377)

**Decision.** One piece of the rolled-back automation layer returns: `claude.yml`, the delegation
loop. Labelling an issue `agent` dispatches a Claude agent on a **GitHub-hosted** runner, which
builds the issue on its own branch and opens a PR; `@claude …` on that PR revises it in place; a
human reads the diff and squash-merges. Model is Sonnet 5.

**Explicitly not returning:** the `/spec` gate, auto-triage, the slash-command control plane, the
watchdogs, the overnight analyst, the auto-merge risk policy. Liveness remains the Healthchecks
dead-man's switch (#29). #377's verdict on all of that stands unchanged.

**Why this one and not the rest.** #377's finding was that the layer opened zero PRs — but the
`claude` workflow was the only part of it whose job was to open PRs at all, and it was never
actually used that way: it sat behind a `/spec` gate that had to be cleared first. The value here
isn't automation, it's **concurrency** — three small issues building on hosted runners while the
Mac session works on the piece that needs judgement. That is a real pain (serial single-session
throughput) and it is the pain the archived doc's own lesson says to spend on first: *build the one
piece that removes a real pain, use it for a week, and only then build the second.*

**The triage rule** (the actual deliverable — a workflow nobody knows when to use is #377 again).
Delegate only when **all four** hold:
1. `make check` is a sufficient verdict — the hosted runner has no `.env`, no IB Gateway, no box,
   no `/data`, no `data/recon/`.
2. The brief is closed-form — exact files, exact behaviour, a named test. **The agent cannot ask a
   question mid-flight**, so anything needing a mid-course decision is not delegable.
3. XS or S tier (≤ ~250 lines, ≤ ~5 files). M/L cost more to review than they save in typing.
4. It isn't the thing being actively iterated on right now.

Engine/strategy work **qualifies** when the brief names the exact rule and the exact test — that
logic is exhaustively unit-tested, so CI is a real gate there. Deliberately wider than #377's spec
gate, which failed by fencing off precisely this category. Spikes, reports, review-page
investigations, deploys/backfills/harvest and anything touching `data/` or IBKR stay in-house.

**Nothing auto-merges.** Branch protection requires `lint-typecheck-test` and zero approvals, so an
armed auto-merge would put unread agent code on `main`; the human read is the whole safeguard.

**Cost.** These runs draw on the **same Max subscription quota** as the local session — this buys
wall-clock parallelism, not extra capacity. Hosted-runner minutes are free on a public repo.

**Standard it is held to.** Same one that retired the last layer: if it isn't used within a week,
delete it. The procedure lives in the `delegate-issue` skill; the CLAUDE.md footprint is capped at
the one bullet under "How work gets done".

## 2026-08-06 — Reconstructed sessions publish to their OWN chart namespace (#488)

Results only ever listed live-captured dates, so every harvested pre-market session was invisible
there and a `recon` trade opened in the Portfolio inspector drew no candles. #488 offered two
shapes for the fix; this is the one taken and why.

**A separate namespace, not a `source` field on the live index.** Reconstructed dates get
`recon_index.json` + `charts/recon/<date>.json`; `index.json` and `charts/<date>.json` are
untouched, byte for byte. Tagging rows inside the one index would have made *every* existing
consumer — Results, the review workbench, anything added later — start returning vendor-derived days
the moment this landed, silently and with no tag, which is the failure #430 built two stores to
prevent. Here a reader has to *ask*, exactly as `build_portfolio_payload` has to be handed a
`recon_store`. The rows carry `source: "recon"` as well, so a future consumer that does merge the
two indexes still cannot lose the provenance.

**The producer is the harvest, one date at a time, per completed session.** `run`/`auto` publish
each session's payload as it lands (`harvest charts` is the catch-up path). A session is ~47 minutes
of rate-limited waiting and one date's charts are seconds of compute, so the work vanishes into a
budget already dominated by `time.sleep` — whereas batching it to the end of a night would put an
archive-shaped job exactly where the run is trying to stop clear of the 03:45 `eod_backfill` or the
16:20 EOD batch. It also inherits the checkpoint's contract: a night killed mid-run has published
everything it harvested.

**At most `recon_charts_max_dates` (30) sessions are published at once, and eviction is by PUBLISH
ORDER, not by date.** The cap is about the *publish pipe*, not memory: `publish-dashboard`
force-pushes the whole `data/dashboard` tree every 15 minutes, and a date's payload is 1.5–3 MB of
full-day bars — a finished ~500-session harvest would put ~1 GB through that push every quarter of
an hour and the same again down every browser that opened Results with `+ History`.

⚠️ The *anchor* was wrong in the first cut and it mattered. Capping to the newest 30 **dates** —
chosen to mirror #449's "the segment contiguous with the live record" — looks reasonable and is
useless: the harvest walks *backwards*, so once the window filled (~2 nights at ~18 sessions), every
later session was older than everything already published, fell outside the window, and never
published at all. ~94% of a finished harvest would have been permanently invisible in Results while
the per-session hook kept paying for two store reads and an index rewrite to do nothing. Ranking on
*when we published it* instead makes the window follow the harvest: every morning the page carries
what last night rebuilt. It also makes every harvested session **reachable** — `harvest charts
--dates <d>` republishes any date and moves it to the front of the window. The cap now decides how
much is resident, not which half of the archive exists. The stated cost: a session published two
nights ago is evicted, so a reader wanting a specific older one has to ask again — a command, where
the alternative was an impossibility.

Evicted dates are pruned from disk and from the index, and `capped_dates_dropped` says how many — a
silent truncation would read as "that is all the harvest has". ⚠️ The cap bounds the date *count*,
not bytes: `harvest_max_candidates` is uncapped, so a busy reconstructed session's payload can run
well past a live day's. The ~500× arithmetic above is not a measurement; check `du -sh
/data/dashboard` and the publish job's duration once the first window lands.

**Results fetches the reconstruction only when asked.** The small index is read every load (it is
what decides whether the DATA control exists at all); the multi-megabyte payloads are fetched on the
first switch to `+ History` and then filtered, never refetched. Default behaviour is unchanged.

**`harvest charts` takes the container-name lock even though it spends no vendor budget.** It and
the per-session hook both read-modify-write `recon_index.json`; interleaved, one writes an index
built from a stale snapshot and orphans the other's payload. That name is the only cross-process
mutex on the box, so the lock now guards the checkpoint *and* the dashboard artifacts. `charts` also
does real work (DuckDB + polars + the detector, per date), so it takes the harvest's cgroup slice
rather than the 512 MB no-slice envelope `status`/`sweep`/`prefilter` use. Relatedly,
`dashboard.write_json`'s temp file now carries the writer's pid: a fixed `<name>.tmp` is atomic
against *readers* only, and two writers could `os.replace` a mixed payload under the final name —
invalid JSON in an artifact the frontend parses on load.

**What a reconstructed day cannot show, stated rather than zeroed:** no float (the vendor sells no
share count), no news, and no saved review (the workbench annotates live opportunities only, so its
link is hidden rather than pointed at a page that would load empty). The appearance time is a
*reconstruction* — the live gates replayed over the minute tape — not an observed scanner hit.

---

## Open Drive — a second strategy for the 09:30 open (DECISION 2026-08-02, #418)

Spec: [`open-drive.md`](./open-drive.md). Measurement:
`docs/reports/2026-08-02-the-0930-open-a-second-strategy.md`. Harness: `spikes/open_drive_sweep.py`.

The engine trades the pre-market only. The time-of-day report (#387) measured the tape's forward
excursion peaking at 09:00–10:00 (+8.5% / +5.6% median 60-min upside, against +1.1% at 04:00–06:00)
with **170×** the pre-market's median $/bar, and the engine putting 32 of its 787 triggers there.
Its recommendation #4 was to look harder at that window. This is the result of doing so.

**The strategy — an opening-range breakout with a consolidation requirement.** The 09:30–09:35 bar
is the opening range (green, body larger than both wicks combined); the 09:35–09:40 bar is a
consolidation of it (less volume, shorter); entry is one tick above the consolidation high from
09:40, R is measured against a 3-tick fill, the stop is the consolidation low. **One trade a day,
first to trigger.** The 5/5 split is locked: expectancy falls monotonically as the trigger moves
later — +0.436R at 5/5, +0.227R at 10/5, −0.034R at 5/10, **−0.794R at 15/5**.

**The universe is symbols on the scanner strictly before the trigger fires.** This is not a gate or
a treatment but the definition of what could have been traded, applied before anything is counted
and relaxed by no variant. Where a length moves the trigger later its cutoff moves with it, so
lengths are compared on different — each legitimately tradable — populations. Companion to the
#379 no-lookahead rule: that one governs *selection* (first-to-trigger, never best-of-day), this one
governs *population*.

**Decision: specified, not traded.** Over 2026-07 (22 days with bars, 46 candidates on 13 days) the
book returns **+5.67R over 13 trades at 53.8% wins** — and on its own $500 ends the month at
**$497.67**. Three findings drive the decision:

1. **The R cannot be monetised at $500.** `size_position`'s notional cap binds whenever the stop is
   tighter than `risk_fraction / position_fraction` = 10% of entry. The bull-flag's stops run a
   median 13.9% and are usually risk-bound; Open Drive's run **1–7%**, so it is cap-bound on **10 of
   13** trades, each risking 2.46% of equity against a configured 5%. At 20% risk and a 100% cap
   every trade is cap-bound for +7.1% and a 21.5% drawdown. This is a capital constraint, not a
   failure of the setup — the mirror image of #416, where the cap was dormant because stops were wide.
2. **No individual rule is defensible at this sample.** None of ten pre-registered contrasts survives
   Holm on the 215-setup ungated population (best raw p = 0.080, `price ≥ $5`). All four gates point
   the right way; none separates from noise. No fitted threshold cleared its permissive default
   either, so **the strategy is exactly as the trader stated it, with nothing tuned**.
3. **It must not share the adaptive book.** Slotting it in costs **$218** of end equity and 3.4R,
   because the daily target re-fit and the risk ladder see the merged candidate stream — so the
   merged book's *bull-flag* leg is no longer the one that was measured. At a fixed 2R it adds
   +3.67R and still costs $20. If ever traded it needs its own book and its own target fitting.

**Two of the trader's stated rules were dropped**, not demoted to score terms: the consolidation
being "more wicky" than the opening candle (P(≥2R) 19% either way) and the opening bar's relative
volume (RVOL>1 → RVOL>10 moved the population 137 → 119 with flat statistics). The RVOL result
carries a caveat — the store has no average daily volume, so the only baseline available was the
same morning's pre-market, and for a news gapper the opening bar is nearly always the session's
largest. Revisit if ADV is ever captured.

**If it is traded**, the capital shape is: `portfolio_max_trades_per_day` stays **2** — slot 1
pre-market bull-flag, slot 2 Open Drive, fractions 0.50/0.50 — preserving the settled-cash
invariant (`0.50 × 2 = 1.0`) and satisfying the good-faith rule ($250 + $250 ≤ $500) by construction.

**Two structural notes.** Open Drive is **immune to prefix instability**, the "sleeper" risk in
`phase-2-roadmap.md`: its two candles are fixed by the clock and final at 09:40, so live and replay
cannot diverge — unlike the bull-flag's longest-valid segmentation over a growing window. It would
also be the first strategy able to use **broker-native brackets**, trading after the bell rather
than under the pre-market limit-only constraint (#37).

**Open.** The largest caveat is that the store holds **5-min bars only**, so the shortest expressible
opening range is five minutes; on 1-min bars this is a different setup with tighter stops, which
would sharpen the entries and worsen the sizing problem at once. Also open: a real RVOL baseline;
the float direction (**+0.226R for ≥20M here**, against the direction `float_max_shares < 20M`
implies — note that threshold **gates nothing**, so this is a contrast against an *unapplied* rule
rather than evidence about a live filter (#551), and
against `float-vs-max-r`'s tail finding); and a re-run at 60+ days when collection completes
~2026-10-01. Nothing here is established — 13 trades, expectancy interval −0.40R to +1.26R.

## DECISION 2026-08-07 (#567) — the engine selects, the book executes

The split between the engine and the paper book was arbitrary. The **entry price band** and the
**trigger-time window** lived in the book as `portfolio_entry_price_min/max` and
`portfolio_premarket_earliest/cutoff`, so the code said they were execution rules when they decide
**selection** — whether a setup is one we would take at all. Meanwhile the engine carried a
04:00–11:59 window that gated nothing, which meant the only effective time-of-day rule in the whole
system lived in the book while looking like it lived in the engine.

**The line is now selection vs execution.**

| Stage | Question | Owns |
|---|---|---|
| Scan | what do we **see** | price/change/volume/type filters, the scan window |
| Engine | what would we **select** | shape gates, price band, trigger-time window, staleness, exhaustion |
| Book | how would we **execute** | the 2-a-day cap, sizing, exits, costs, ledgers |

The 2-trades-a-day cap stays in the book deliberately: it is a capacity constraint falling out of
settled cash (`position_fraction × max_trades_per_day = 1.0`), not a judgement about the setup. The
symbol exclusion list also stays — it is data hygiene for ETFs captured before the scanner's
`stkTypes` filter existed, not strategy.

**The selection rules bite on `takeable`, NOT on `passed`.** `passed` keeps meaning "the bull flag
is well-formed" — the shape grammar the review workbench and the 25 golden fixtures are written
against. A $1.50 name or an 11:00 break can be a textbook flag we simply don't select; it stays
visible and scoreable on the results page, which is what a data-collection phase needs. Folding
selection into `passed` would report it as a malformed setup and throw the observation away. The
fixtures pin `passed` and `failing_gates` but not `takeable`, so all 25 were untouched by the move
— which is the evidence the refactor changed where the rules run, not what they decide.

**Verified book-neutral.** Replaying the whole local store (30 sessions, 2026-07-01 → 2026-08-07)
before and after gives the identical book: 14 candidates, 14 trades, +9.62R, $650.43 closing,
0.11% max drawdown, 57.1% win rate.

Settings renamed: `select_price_min` / `select_price_max` / `select_window_start` /
`select_window_end`. Nothing sets them via env, so the rename is internal; the published payload
keys (`entry_price_min`, `premarket_earliest_et`, …) are unchanged, so the dashboard contract holds.

## DECISION 2026-08-07 (#569) — the selection window opens to 04:00 (reverses #405)

`select_window_start` 05:30 → **04:00**, i.e. the whole pre-market is selectable again. The cutoff
stays 09:15 and the **scan** window is unchanged at 04:00–11:59.

**Measured first.** Replaying the local store (30 sessions, 2026-07-01 → 2026-08-07) under both
floors:

| floor | trades | R | closing | max dd | win rate |
|---|---|---|---|---|---|
| 05:30 (before) | 14 | +9.62 | $650.43 | 0.11% | 57.1% |
| 04:00 (now) | 18 | +4.93 | $575.84 | 0.15% | 44.4% |

The four unlocked trades — SHPH 04:20, SUNE 04:30, LGHL 04:20, UPC 04:25 — **all stopped out**, for
−4.69R and −$74.58 (−11.5% of the book). Nothing was displaced: the earlier triggers pushed no
later winner out of the 2-a-day cap, so the change is purely additive.

**Taken with that in hand — and the owner's reasoning is the point, not the number.** This is not
a claim that 04:00–05:30 is profitable. It is a claim that **it is too early to be deciding this at
all**, and that a rule which prunes the book also blinds it: those four trades are only visible
*because* the window was opened. See the standing principle above — an unmeasured rule defaults
open, because a rule wrongly imposed leaves no trace of what it removed.

Two further inputs, both of which outweigh 30 sessions of replay:

- **The owner traded this strategy manually for a year and reports the best fills came at 04:00.**
  That is a larger and more varied sample than anything the tracker holds. It is not decisive
  either, but it is evidence, and it pointed the opposite way to the floor.
- **n=4 is not evidence.** Four losses at a ~43% base win rate happens about 10% of the time by
  chance. The floor being reversed was not measured either (#405 said so in its own words): the
  time-of-day report found no pre-market window statistically separable from another, the
  04:00–06:00 block sitting at −0.32R over 86 triggers with a permutation p of 0.68.

Revisit when the reconstructed history (#431) makes this measurable rather than watchable.

⚠️ **The published book will drop from ~$650 to ~$576 and show 18 trades.** That is this decision
landing, not a regression. `spikes/window_0400.py` re-runs the comparison.

Compute-on-read means the whole history replays under the new floor on the next publish — no stored
state to migrate, and reverting is one line.
