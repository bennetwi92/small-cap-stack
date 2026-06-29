# Resolved Decisions — Research Phase Closeout

**Date:** 2026-06-29. Resolves the open questions in [`findings-index.md`](./findings-index.md) §3.

## Locked decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Float threshold | **< 20 million shares** (share count, NOT $ market value). |
| 2 | Scanner / broker | **IBKR only.** User trades via the **TWS Mosaic scanner** today and considers it sufficient. ⚠️ Headless system must use the **API scanner (`reqScannerSubscription`)**, a different/more limited surface than Mosaic — see Spike below. |
| 3 | Exit strategy (Phase 1) | **Not required for execution** in Phase 1 (tracking only). BUT "Max R" reporting needs a **notional entry trigger + notional stop** to compute R — see Phase-1 note below. |
| 4 | News source | **Try IBKR news feed first** (what user used before). Subscribe to a paid service only if insufficient. |
| 5 | VPS | **Oracle Cloud Always Free — Ampere A1 (ARM)** primary; GCP e2-micro fallback. Approved. |
| 6 | Market data | User **will subscribe to IBKR market data** (incl. pre-market). Pre-market feed is a solved problem via IBKR. |
| 7 | Weekly 2FA | **Accepted for now** (one manual phone tap/week). User aware of a second-username / relaxed-2FA workaround to apply later himself. |
| 8 | Branching | **Trunk-based: protected `main` + short-lived branches, all work via PRs**, required CI checks before merge. Chosen because much work happens in PRs / Claude Code on mobile. |
| 9 | Stack | **Python + `ib_async`** (the maintained fork). Prior repos' raw-`ibapi` code is adapted, not lifted verbatim. |
| 10 | Storage | **Self-hosted PostgreSQL (+ TimescaleDB) on the Oracle VM's free 200 GB block volume** as primary store; raw per-opportunity bars/snapshots archived as **Parquet on disk**; periodic backup to free object storage. Chosen over SQLite / Neon-Supabase free tiers because **data requirements will only grow** and managed free tiers cap at ~0.5 GB. |
| 11 | Phase-1 scope | **Tracker only — places no orders.** Records every scanner-flagged opportunity, which gates it passed, whether a notional entry would have triggered, and Max R achieved + other stats. **All stats computed on the fly from cached raw data** so methodology can change retroactively. |

## Core architectural principle (from Q11)
**Store raw, compute derived on read.** Capture everything raw at flag time (bars, scanner snapshot, fundamentals, news, short interest) and keep gate evaluation + stat computation as **replayable pure functions** over that raw data. Changing gate definitions or the entry/stop spec later must NOT require re-collecting data — only re-running the computation over the cached raw record.

## Entry / stop spec (for Max-R measurement)
- **Entry trigger (from `notes.md`):** the **tick above the high of the last consolidation candle**.
- **Stop (CONFIRMED 2026-06-29):** the **low of the consolidation candle(s)** (the flag low). This is the R denominator.

## Scope (from user, 2026-06-29)
- User only ever acts on the **top 2–3 scanner rows, mostly the top 1.** The system only needs the *top few* candidates correct — the 50-row API cap and broad-universe concerns are largely moot.

## Remaining technical risks → validation spikes (before building)
- **A. API scanner vs Mosaic** (issue #8): ⏳ **largely validated 2026-06-29** — the API scanner returned a ranked candidate list **pre-market**, addressing the main suspected weak spot. `reqScannerParameters` confirmed IBKR exposes **trailing 5-min volume natively** (`stVolume5minAbove`, `stVolumeVsAvg5minAbove`, scan code `HIGH_STVOLUME_5MIN`), so the strategy's "5-min volume > 100k" is a built-in filter — NOT day volume, NOT derived from bars. Recommended scan: `TOP_PERC_GAIN` + price 2–10 + `changePercAbove 10` + `stVolume5minAbove 100000` @ `STK.US.MAJOR`. Remaining: user to confirm API top 1–3 == Mosaic top 1–3 at the same moment.

  > **Criterion #5 (5-min volume > 100k) resolved:** native `stVolume5minAbove` scanner filter. This was a previously-open data-feasibility item in `strategy-validation.md`.
- **B. Pre-market bar completeness:** are IBKR 4am 5-min bars clean/gap-free enough for bull-flag + ≤2-green/≤2-red candle counting on thin names?
- **C. IBKR news sufficiency:** does the IBKR news feed actually deliver per-symbol breaking-news signal, or is a paid feed needed?

## Minor note
- Repo is currently **public**. Public = unlimited GitHub Actions (good), but exposes strategy. Decide whether to keep public or make private (private = 2,000 Actions min/mo, or self-host a runner on the VPS).
