---
name: review-analysis
description: Investigate a specific opportunity/run in the live Phase-1 tracker — pull the trader's saved review annotations (entry/stop/pole/consolidation/note) plus the raw bars from the VPS, then replay the R-metrics (engine Max R and the naive annotation Max R) to explain what the review page shows and why. Use when the user questions a number on the review page (Max R, triggered, stop-out) for a named symbol + date + run.
---

# review-analysis

Diagnose "why does the review page show X for `<SYMBOL> #<run>` on `<date>`?" This is DRAFT — refine as the data model changes.

## Where the data lives (three sources — hold them apart)
1. **Raw bars + engine analysis** — on the **VPS only** (`/data` volume). See [[reference-vps-data-access]] in memory / `deploy/host.local.md`.
   - `ssh -i ~/.ssh/oracle_scs root@138.199.151.179`, then `docker exec -i small-cap-stack-app-1 python -` piped a local script.
   - `from small_cap_stack.storage import Store; s = Store("/data")`. Datasets: `analysis, bars, opportunities, scanner_hits, news, fundamentals`.
   - ⚠️ The stored `analysis` parquet can be **stale** (written before the #130 first_hit gate; `first_hit` all-null). The **review page recomputes live** — reproduce with `dashboard.build_charts(...)` + `report.symbol_runs(...)`, NOT the `analysis` table.
2. **Trader's review annotations** — on the **`review-data`** GitHub branch, one file per run: `reviews/<oid>.json` where oid `:`/`#` → `_` (e.g. `2026-07-02:SNDQ#2` → `reviews/2026-07-02_SNDQ_2.json`).
   - `gh api "repos/bennetwi92/small-cap-stack/contents/reviews/<file>?ref=review-data" --jq .content | base64 -d`
   - Fields: `note`, `no_trigger` (verdict), `annotations{ pole, consolidation, entry, stop, entry_t, max_r }`. `entry_t` is epoch-secs of the entry tap; `max_r` is the value the page computed (`computeMaxR()` in `docs/review.js`).
3. **The front-end logic** — `docs/review.js`. `computeMaxR()` (~L599) derives the annotation Max R; `serializeAnnotations()` (~L875) stamps it at save time. Engine R-metrics: `src/small_cap_stack/rmetrics.py`.

## Procedure
1. Get the run's identity: `<date>:<SYMBOL>#<run>` (drop `#<run>` if the symbol ran once that day — check `run_count`).
2. Pull the annotation JSON (source 2). Note `entry`, `stop`, `entry_t`, saved `max_r`, `no_trigger`.
3. Pull the exact bars the page draws (source 1): `build_charts(store, Settings(), date, now)` → the chart whose `opportunity_id` matches; use its `bars` (full day 04:00–16:00 ET).
4. Reproduce the number the user is questioning:
   - **Engine Max R** (`—`/None when not triggered): replay `compute_r_metrics(run.bars, settings, first_hit=run.first_hit)`; trace the appearance (#99/#122) and staleness (#130, 30 min) gates.
   - **Annotation Max R** (the tap-drawn one): port `computeMaxR()` and run it on the day bars with the saved `entry/stop/entry_t`.
   Scripts: `scripts/analysis/probe_run.py` (bars + gate trace) and `scripts/analysis/probe_annotation_maxr.py` (annotation replay + fixed version). Pipe them in over SSH — set the env vars **on the remote side** (SSH does not forward local env):
   `ssh -i ~/.ssh/oracle_scs root@<host> "SYMBOL=SNDQ DATE=2026-07-02 docker exec -i -e SYMBOL -e DATE small-cap-stack-app-1 python -" < scripts/analysis/probe_run.py`
5. Explain the gap in plain terms (which gate fired / which bar stopped it / where the day high was), then state the corrected value.

## Known gotcha (found 2026-07-06, SNDQ #2)
`entry_t` is **not tappable** — `onChartClick` (review.js:631) sets `ann.entry` AND `ann.entry_t` from the *same* entry tap, so `entry_t` is just the **x-coordinate** of where you clicked to place the horizontal entry price line. `computeMaxR()` then treats the bar *at* `entry_t` as the fill bar; if you placed the entry level over the last consolidation candle (whose low often == the stop), the stop-first guard fires same-bar → Max R = 0 (SNDQ #2: saved 0, should be ~6.14R). Max R must **not** depend on the tap's x-position. Fix: derive the fill as the first bar **strictly after the drawn consolidation's end (`consolidation.t1`, fallback `entry_t`)** whose high reaches `entry`, then measure stop-first from there (matches the strategy: entry fills on the breakout bar *after* the consolidation). Tracked in the strategy backlog.
