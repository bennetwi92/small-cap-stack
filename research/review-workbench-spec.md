# Spec — Interactive chart-review workbench

**Status:** proposed · **Epic:** interactive chart-review workbench (`Refs #1`) · **Date:** 2026-07-05

## Why

Each day the trader wants to review the day's opportunities and record **what should have
happened** — their read of the pole, the consolidation, where entry and stop belonged, and the
resulting Max R — so Claude can compare that judgement against the engine's output and refine the
gates / bull-flag rules over time. This is the human-in-the-loop labelling that turns three months
of tracking into a corrected, teachable methodology.

The current dashboard (`docs/`) cannot support this. It is:

- **read-only** — a static GitHub Pages SPA (vanilla JS + TradingView Lightweight Charts v4.2.3)
  that polls pre-computed JSON from the force-pushed `dashboard-data` branch;
- **single-day** — the producer overwrites one `charts.json` each EOD, so only the last completed
  session is ever visible (no way to go back in time);
- **clipped** — each chart shows only the per-run window `[first_hit − 30 min, 16:00)`
  (`report.symbol_runs` / `_run_windows`), not the whole day;
- **un-annotatable** — no notes, no user-drawn levels.

## Decision: no backend — GitHub is the write-back store

The one genuinely new capability the feature set needs is **write-back** (saving notes and
annotations from the phone). A real backend (FastAPI + DB + inbound port) was considered and
rejected: it breaks the deliberate serverless / no-inbound-ports / "GitHub is the control plane"
design (decisions #53, #68–#70) and adds infra to run, secure, and pay for — unjustified for a
single-user tool.

Instead the **browser commits review JSON to a git branch via the GitHub REST API**. This keeps the
serverless design intact and — critically — means Claude reads the *exact same files* the trader
writes, closing the teach → compare → refine loop inside git where the rest of the control plane
already lives.

**Locked choices:** GitHub write-back (no backend) · chart range **04:00–16:00 ET** · phased
delivery (navigation + notes first; annotations + refine-loop second).

## Architecture

Three pieces: extend the read path (producer), add a write path (GitHub API), and build a
mobile-first review page.

### 1. Read path — multi-day publishing + full-day bars (producer)

Extend the existing pure, store-backed producer (`dashboard.py` / `charts.py`) to publish **many
days** instead of overwriting one, and to emit **full-day** (un-clipped) bar series.

- **`index.json`** — the navigation index powering the date + symbol dropdowns:
  ```json
  {
    "generated_utc": "...",
    "dates": [
      { "date": "2026-07-01",
        "opportunities": [
          { "opportunity_id": "2026-07-01:AHMA", "symbol": "AHMA",
            "run": 1, "run_count": 1, "triggered": true, "max_r": 2.3 }
        ] }
    ]
  }
  ```
  `dates` sorted descending (newest first). One entry per opportunity/run, mirroring the existing
  chart selection list.

- **`charts/<date>.json`** — same shape as today's `charts.json`, one file per trading date, never
  overwritten. Each chart's `bars` is the **whole day** for that symbol, `04:00 ≤ t < 16:00 ET`,
  not the run window. The source is already captured: `report._all_bars(bars, base_oid)` returns
  the full day (EOD batch fetches a `1 D` duration incl. pre-market); only `symbol_runs` windowing
  clips it today. The producer takes the full-day slice for the chart while still segmenting runs
  for the per-run annotations.

- **Markers become timestamps, not indices.** `compute_r_metrics` returns marker indices into the
  *run window*; those do not align to a full-day array. In `charts.build_opportunity_chart`, map
  each marker index → its bar's epoch `t` before returning, so the frontend places markers and
  entry/stop levels by timestamp on the full-day series. This stays backcastable and keeps one
  source of truth (the R-metrics engine still owns entry/stop/stop-first).

- **Publishing is automatic.** `publish-dashboard.yml` already `docker cp`s `dashboard/.` and
  force-pushes it to `dashboard-data`, so new `index.json` and `charts/<date>.json` files publish
  with no workflow change. The live loop (`app._refresh_stats_charts`) writes today's dated file +
  refreshes `index.json`; `dashboard_backfill.py` (re)generates any past date's dated file.

- **One-time full-archive backfill.** So the date picker is populated from day one, enumerate every
  past date with captured bars
  (`store.query("SELECT DISTINCT trading_date FROM opportunities")` or the `dt=` partition dirs)
  and generate each date's `charts/<date>.json` + a complete `index.json`. Runs once against the
  box's store (a `backfill-dashboard.yml` dispatch, or a loop over the distinct dates).

### 2. Write path — GitHub write-back

Reviews live on a **dedicated `review-data` branch**, kept separate from `dashboard-data` — the
latter is force-pushed as an orphan commit every 15 min and would clobber any writes. One file per
opportunity:

- **Path:** `reviews/<sanitized_opportunity_id>.json` where `:` and `#` → `_`
  (e.g. `2026-07-01:AHMA#2` → `reviews/2026-07-01_AHMA_2.json`).
- **Schema:**
  ```json
  {
    "schema_version": 1,
    "opportunity_id": "2026-07-01:AHMA#2",
    "symbol": "AHMA",
    "trading_date": "2026-07-01",
    "note": "free-text expectations…",
    "annotations": {
      "pole":          { "t0": 0, "t1": 0, "low": 0.0, "high": 0.0 },
      "consolidation": { "t0": 0, "t1": 0, "high": 0.0, "low": 0.0 },
      "entry": 0.0,
      "stop": 0.0,
      "max_r": 0.0
    },
    "updated_utc": "..."
  }
  ```
  (`t0`/`t1` are epoch seconds; `annotations` is absent/empty in Phase 1, filled in Phase 2.)

- **Auth:** a GitHub fine-grained PAT scoped to *contents: read + write* on this repo only, pasted
  once into the page and kept in the browser's `localStorage`. Save = `GET` the file's current SHA
  on `review-data` (if any) → `PUT /repos/{o}/{r}/contents/{path}` with base64 body + `sha` +
  `branch: review-data`. Load = `GET` the file (raw or contents API) for the opportunity's id;
  a 404 means "no review yet" → empty form. Saving overwrites the single file (the trader's stated
  "overwrite the previous one").

- **Security note.** The repo is public (github.io Pages + `raw.githubusercontent.com` fetch), but
  the PAT lives only in the trader's own phone `localStorage` — never embedded in the served HTML —
  and the `review-data` branch is low-sensitivity. Acceptable for a single-user tool. If the PAT is
  ever a concern, the fallback is the "serverless write proxy" variant (a GitHub Action via
  `repository_dispatch`, or a Cloudflare Worker) that commits on the browser's behalf so no token
  lives client-side.

### 3. Frontend — mobile-first review page

A new page **`docs/review.html` + `docs/review.js`** (shared additions to `docs/style.css`),
decoupled from the scrolling dashboard. Layout is a `100dvh` flex column that fits **one smartphone
screen in Chrome with no scrolling**:

- **Top control bar (compact):** date `<select>` (from `index.json`, newest first), symbol
  `<select>` + `‹` / `›` arrows that cycle opportunities with wrap-around. Changing either the date
  or the symbol loads the corresponding chart from `charts/<date>.json`.
- **Chart (flex-grows to fill):** reuses the existing `buildChart` idiom from `docs/app.js` —
  candlesticks + overlaid volume histogram (bottom ~20%) + entry/stop price lines + event markers +
  `fitContent()` — with markers placed by **timestamp** (not index) so the full-day series renders
  correctly. TradingView's chart already gives the easy pan/zoom the trader likes.
- **Bottom strip (compact):** entry / stop / **Max R** readout, plus a notes toggle + save button. A
  save-status line reflects the write-back result.
- **Notes:** a slide-up sheet (not an always-open textarea) so the chart keeps its height. Opening a
  new opportunity loads its saved note; editing + save overwrites the single `review-data` file.

## Phase 2 — annotations + refine loop

- **Tap-to-place levels.** `chart.subscribeClick` → `series.coordinateToPrice(y)` sets the entry and
  stop horizontal lines by tapping; two taps set the pole and consolidation **time ranges**, drawn
  as translucent bands via a Lightweight-Charts **series primitive** (`series.attachPrimitive`, v4).
  A small mode toolbar `[Pole] [Cons] [Entry] [Stop]` arms which element the next tap sets — chosen
  to stay within the single-screen budget.
- **Auto Max R.** Computed live client-side from the drawn levels + the full-day bars:
  `max_r = (max(high for bars with t ≥ entry_time) − entry) / (entry − stop)`. Recomputes on every
  entry/stop change and is persisted inside the review's `annotations`.
- **Compare-and-refine.** A pure Python helper `src/small_cap_stack/review_compare.py` reads the
  `review-data` reviews + the engine's `analysis` dataset / `stats.json` for a date and emits a
  per-opportunity diff (trader vs engine: pole length, consolidation, entry, stop, Max R). Claude
  uses that diff to propose concrete gate / `bullflag` rule changes, feeding the existing
  `research/decisions.md` loop — the same store-raw / compute-on-read replay that already lets
  methodology change retroactively.

## Delivery — issues

- **Epic:** interactive chart-review workbench.
- **Phase 1:** (1) full-day chart payloads + per-date publishing + review index + full-archive
  backfill; (2) review page — date/symbol navigation, full-day chart, mobile single-screen;
  (3) GitHub write-back + per-opportunity notes.
- **Phase 2:** (4) chart annotations (pole/consolidation/entry/stop) + auto Max R;
  (5) review-vs-engine comparison + refine loop.

## Files touched (for implementers)

- Producer: `src/small_cap_stack/dashboard.py` (`build_charts`, `write_json*`, new `build_index`),
  `src/small_cap_stack/charts.py` (`build_opportunity_chart` → timestamp markers, full-day bars),
  `src/small_cap_stack/report.py` (`symbol_runs`, `_all_bars`, `day_opportunities` — reused),
  `src/small_cap_stack/dashboard_backfill.py` (dated output + index refresh + archive backfill),
  `src/small_cap_stack/app.py` (`_refresh_stats_charts` writes today's dated file).
- Config: `src/small_cap_stack/config.py` (`capture_end` = 16:00; new 04:00 chart-start bound).
- Frontend: new `docs/review.html`, `docs/review.js`; additions to `docs/style.css`; reuse the
  `buildChart` idiom from `docs/app.js`.
- Publish: `.github/workflows/publish-dashboard.yml` (unchanged — already ships `dashboard/.`),
  `.github/workflows/backfill-dashboard.yml` (archive backfill dispatch).
- Phase 2: new `src/small_cap_stack/review_compare.py` + tests.
- Reused verbatim: raw 5-min bars + the pure `gates` / `bullflag` / `rmetrics` functions — no new
  capture; the full day is already stored.
