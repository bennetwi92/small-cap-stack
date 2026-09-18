# The data inventory — what we have, at what grain, and what it will not support

**Status:** LIVE (2026-09-18). Audit of the Phase-1 record as it stands at the close of the
2-year harvest, written so a data-science pass can plan against the data rather than discover it.

This is a *shape* document. It says what exists, at what grain, how it was produced, where the
holes are, and which questions the shape can and cannot answer. It does **not** restate the rules
— [`strategy.md`](./strategy.md) is the canonical spec — and it draws no strategy conclusions;
those belong in a dated report.

Companion: [`analysis-brief.md`](./analysis-brief.md) is the brief handed to the analysis agent
that works from this inventory.

---

## 1. The record at a glance

Two Parquet stores, same dataset names, different provenance, deliberately kept apart
(`Settings.recon_subdir`; every book trade carries `source: "live" | "recon"`).

| | **live** (`/data`) | **recon** (`/data/recon`) |
|---|---|---|
| what it is | the tracker's own observations | pre-market days rebuilt from purchased vendor minute bars |
| coverage | 2026-07-01 → 2026-09-17 | 2024-09-09 → 2026-06-30 |
| sessions | 58 | 453 (of 501 in window; 469 daily-universe days) |
| observation window | 04:00 – 11:59 ET scan, bars to 16:00 | **04:00 – 09:30 ET only** (`reconstruct.PREMARKET`) |
| scanner | IBKR `TOP_PERC_GAIN`, live, 60 s ticks | *reconstructed* from minute bars + previous daily close |
| news | yes | **no** |
| float / short interest | yfinance, per opportunity | **no float** — EDGAR `shares_outstanding` only (§D-41) |
| producer | `capture.py`, continuous | `harvest/`, nightly (`scs-harvest.timer`), now complete |

**~511 trading sessions, ~2 years, contiguous** — the harvest was deliberately run newest-first so
it abuts the live window with no hole in the middle (§D-30/§D-31). That contiguity is the single
most valuable structural property of the record: it makes a genuine time-ordered train/validate/
holdout split possible without a gap.

Harvest is **done**: 453 sessions, 82,705 vendor calls spent, last progress 2026-08-30.

---

## 2. Raw datasets — the store-raw layer

All are append-only, `dt=YYYY-MM-DD`-partitioned Parquet, read via DuckDB
(`storage.py::Store`). ⚠️ **Read cost tracks FILE count, not rows** — keep every hot-path read
`dt=`-scoped.

### 2.1 `opportunities` — the spine
One row per symbol-day, written the first time the scanner surfaces that symbol. Every other
dataset joins to it on `opportunity_id` (`"YYYY-MM-DD:SYMBOL"`).

| column | type | note |
|---|---|---|
| `opportunity_id` | str | `date:symbol`; the join key everywhere |
| `symbol` | str | |
| `con_id` | int | IBKR contract id — the stable identity across ticker changes |
| `exchange`, `currency` | str | |
| `trading_date` | date | |
| `first_seen_utc` | datetime | **first scanner appearance** — gates entry, anchors staleness |
| `first_rank` | int | scanner rank at first sight. ⚠️ live-only; the recon value is derived from the whole day's move and is therefore **lookahead** |

Live: 2,512 rows / 2,526 files. There is **no filter between the scan and this table** — every
scanner row becomes an opportunity, by design (§D-03, "collect before you filter").

### 2.2 `scanner_hits` — the attention series
One row per candidate per 60 s tick in live; in recon, one row per minute bar whose reconstructed
gates pass (not just the first — run segmentation reads the gaps, #36).

`opportunity_id · symbol · ts_utc · rank`

Live: 156,428 rows / 21,001 files. This is the **only** record of *how much attention* a name was
getting and *when it started*, and it is the feature family that has consistently done best in
every rule search so far (freshness: hits before the break, staleness).

### 2.3 `bars` — the price/volume tape
5-minute OHLCV, `[04:00, 16:00) ET`, one row per bar per opportunity.

`opportunity_id · symbol · bar_start_utc · open · high · low · close · volume`

Live: 343,714 rows / 2,547 files. Byte-identical in shape across both stores (same
`capture.bar_record`), including IBKR's flat zero-volume filler candles — **the engine counts
bars, not minutes**, so `reconstruct.aggregate` synthesises them on the recon side (#442).

The recon side stores the **full session, not pre-market only**, on purpose: the exit walk marks an
unresolved trade to the last bar it can see, so truncating at 09:30 would close every still-open
09:10 entry at 09:25 and bias exactly the trades that were working.

Estimated recon volume: ~453 sessions × ~217 candidates × ~144 bars ≈ **14 M rows**. Measure, do
not trust, this figure.

### 2.4 `bars_1m` — recon only, the raw minute series
1-minute OHLCV, `[04:00, 09:30) ET`, ~330 rows per symbol-day. **Nothing in the package reads it.**
It exists because *store raw, compute derived on read* has a price tag here: a methodology change
that has to re-fetch costs another 45 nights of harvest. Estimated ~32 M rows.

This is the largest untouched asset in the record. Any question that needs sub-5-minute resolution
— microstructure of the break, fill realism, a finer trigger — can only be asked of this dataset,
and only on the recon half.

### 2.5 `news` — live only
`opportunity_id · symbol · time` (raw provider string) `· ts_utc · provider · headline · article_id`

Live: 21,827 rows / 4,241 files, 7-day lookback, ≤10 per opportunity. ⚠️ **Collected, never
gated.** Headlines are text — nothing has been extracted from them. Absent from recon, so news can
be a *live-window* variable only, on 58 sessions.

### 2.6 `fundamentals` — live only, per source
`opportunity_id · symbol · ts_utc · float_shares · shares_outstanding · short_percent · source`

Live: 2,512 rows / 2,641 files (yfinance; IBKR is unentitled, §D-18). ⚠️ **Collected, never
gated.** On the recon side there is no float at all — only EDGAR `shares_outstanding` (§D-41), so
**`float_shares` is null on ~83 % of any combined panel by construction.** Float cannot be a
regime input across the full record. Shares-outstanding can, and already ships as a selection rule
(≤ 50 M, §D-45).

### 2.7 `daily_universe` — recon only, harvest bookkeeping
Phase-1 of the harvest: grouped-daily bars for the whole US market per session, pre-filtered to the
locked price + change gates (`day_change_pct` measured on the day's **high**, not close — the
strategy trades a runner intraday). ~217 candidates/session survive the 100 k day-volume floor.

This is the closest thing we have to a **negative-sample universe**: names that gapped and ran but
never produced a setup. Nothing has used it for that yet.

### 2.8 `analysis` — live only, the EOD derived cache
`report.py::OpportunityAnalysis` flattened, one row per opportunity-run per session: scanner-hit
count, bar count, news count, float, the gate verdict (`passed`, `failing_gates`), the trade
levels (`entry`, `entry_fill`, `stop`), the outcome (`max_r`, `mae_r`, `stopped_out`), and the
segmentation (`run`, `run_count`, `cycle_num`, `exhausted`, `takeable`).

⚠️ This is a **cache of a computation**, not an observation. It is regenerated by replaying the
detector; when methodology changes it is stale until rebuilt. Never treat it as raw.

---

## 3. The derived layer — how a bar becomes a trade

Everything below is *compute-on-read* from §2. This is the pipeline any analysis replays, and the
place where methodology can be changed retroactively.

```
bars + scanner_hits
   └─ bullflag/day.py::detect_day_with_settings      segmentation: runs → cycles → pole/cons
        ├─ bullflag/segment.py, cycles.py, tokens.py   the flag grammar (H/L/E token walk)
        ├─ bullflag/features.py::extract               the 24-field FeatureVector (§3.1)
        ├─ bullflag/gates.py::evaluate                 → `passed`  (shape only)
        └─ DaySetup.takeable                           → `takeable` (shape AND selection)
   └─ rmetrics.py::compute_r_metrics                   → entry/stop/R, max_r, mae_r, stopped_out
        └─ portfolio/                                  → the book: capacity, sizing, exits, costs
```

Two properties that matter for modelling:

- **`passed` ≠ `takeable`, deliberately.** `passed` is the shape grammar alone. `takeable` adds
  selection (price band, stop distance, appearance window, entry cutoff, shares outstanding).
  A row can be a textbook flag we would never trade, and it stays in the record scoreable.
- **The detector is causal.** `prefix_stability.py` (§D-42) measured it: 2018/2018 exact — a
  prefix of a day yields the same verdict as the full day. The detector does not peek forward.
  This is why replaying it over history is legitimate at all.

### 3.1 The `FeatureVector` — 24 fields across six areas
`bullflag/features.py`. These are computed but **only a handful are gated**; the rest are carried
as candidate signal. SHAPE (`pole_len`, `cons_len`, `cons_strictness`, `token_string`) · VOL
(`peak_gt_cons`, `vol_ratio`, `cons_vol_reducing`, `pole_vol_concentration`) · WICK
(`peak_upper_wick`, `peak_is_green`, `pole_has_big_green`, `pole_avg_body`, `cons_indecision`) ·
POLE (`pole_height_pct`, `pole_height_abs`, `pole_velocity`, `pole_extension_atr`) · CONS
(`retracement`, `holds_base`, `cons_tightness`, `cons_drift_slope`) · LOC (`trigger_in_window`,
`bars_before_scan`).

### 3.2 Outcome variables
From `rmetrics.RMetrics`: `max_r` (peak favourable excursion in R), `mae_r` (worst adverse
excursion after entry), `stopped_out`, `stop_index`, `bars_to_max_r`, `max_gain_pct`,
`entry_price` (realised fill, ≥ `entry_fill` on a gap-through), `same_bar_stop`,
`fill_above_entry_bar_high`.

R is measured against the **conservative 3-tick fill**, not the 1-tick mechanical trigger
(§D-17). The distinction is load-bearing: the trigger decides *when*, the fill decides *what R is*.

---

## 4. The analysis-ready panel — `spikes/engine_lab/`

The existing modelling table, and the right starting point. `regime_panel.py` replays the shipped
detector under a `WIDE` settings profile that **switches every fitted threshold off** and records
the raw quantity each one reads, so the shipped book is re-derivable from the panel by a
`filter()` and nothing else.

- **Grain:** one row per triggered setup — `(date, source, symbol, run)`.
- **Size as last built:** 3,639 rows / 197 sessions (166 recon + 31 live), pre-market only,
  `cons_has_range` required. At ~18.5 setups/session, the **full 511-session record should yield
  ~9,000–9,500 rows.** ⚠️ **The panel is stale — it predates 287 recon sessions and 27 live ones.
  Rebuilding it is step one of any analysis.**
- **Feature dictionary:** `TRIGGER_TIME_SAFE` (46 columns a rule MAY read) and `OUTCOME_COLS`
  (the lookahead set), with `assert_no_lookahead()` enforcing the boundary mechanically.
- **Harness:** `load_panel` · `load_paths` (post-trigger bar paths as numpy, for exit replay) ·
  `replay_bracket` · `build_book` (time-ordered capacity cap) · `score` (gross **and net** R, per
  split, per source) · `SHIPPED` / `baseline()` · `walk_forward` · `permutation_pvalue` ·
  `sensitivity`.

⚠️ Four columns feel like trigger-time context and are **not**: `day_volume`, `day_dollar_volume`,
`day_high`, `day_low`, plus `n_scanner_hits_day`, `first_rank`, `run_count`. Each one topped every
feature ranking a previous pass ran — which is exactly what lookahead looks like from the inside.
Use `cum_volume_to_trigger`, `cum_dollar_vol_to_trigger`, `ext_at_trigger`, `hits_before_trigger`.

---

## 5. Labels and ground truth

| asset | what it is | size |
|---|---|---|
| `review-data` branch, `reviews/*.json` | **the trader's hand annotations** — pole/consolidation boxes, entry, stop, entry time, a free-text note, `no_trigger` flag | 167 files |
| `tests/fixtures/review_cases/` | the golden regression fixtures distilled from the above | 25 cases |
| `docs/reports/` | dated published analyses | ⚠️ **cleared in #729** ahead of this analysis |

The reviews are the only **human** label in the system — a judgement of what a good setup looks
like, independent of the detector. They are also irreplaceable (§5 of `how-we-work.md`): never
prune that branch. They cover the live window only, and 167 annotations against ~9,000 setups is
a supervision signal, not a training set.

A review record: `opportunity_id · symbol · trading_date · note · no_trigger ·
annotations{pole{t0,t1,low,high}, consolidation{t0,t1,high,low}, entry, stop, entry_t, max_r}`.

---

## 6. Book-level and auxiliary data

- **`portfolio.json`** (`dashboard-data` branch, republished every 15 min): the simulated book at
  8 exit targets × 2 populations (`books` = live only, `books_all` = live + recon), each with
  `stats`, `equity_curve`, `trades`, `skipped`, `cash_flows`, `projection`. Trade rows carry
  `sized_by`, `reason`, `realized_r`, `max_r`, costs and `source`. **`skipped` is as informative as
  `trades`** — it is the record of setups the capacity cap turned away, with their outcome.
- **VIX** — `^VIX` via yfinance, cached by `spikes/vix_regime.py`. The only external regime input
  wired so far. Coverage complete over the 197-session population.
- **Broker cost model** — `research/broker-costs.md`, pinned to the cent by
  `tests/test_portfolio_sim.py`. At $500, the $0.35/side commission minimum against ~$16.58 mean
  risk eats ~7 % of every R before slippage, ~10 % after.

---

## 7. Known biases, holes and traps

These are the things that will silently ruin an analysis. Every one has already cost this project
something.

1. **The two halves do not observe the same thing.** Recon reconstructs 04:00–09:30; live scans to
   11:59. A raw cross-store funnel compares 5.5 h of live scanning to 5.5 h of pre-market
   reconstruction and makes recon look thin when it isn't. **Restrict both sides to pre-market
   appearances** — this is what `load_panel`'s `trigger_et_min < 570` cut is for, and it is not
   optional.
2. **The scanner is reconstructed, not observed, on 89 % of sessions.** Appearance time is derived
   from minute bars plus the previous daily close (#428 established the close as *required* —
   without it appearances fire a median 18 min early). `first_rank` is not recoverable.
3. **Float is 83 % null** in any combined panel, and short interest is not collected at all.
4. **News exists on 11 % of sessions** and is unextracted text.
5. **Survivorship in the universe.** Both halves start from names that already gapped and ran. The
   record contains no sample of quiet names, so nothing here can answer "should we have been
   looking somewhere else".
6. **One setup per run.** The detector's greedy cycle walk yields one setup per run, so the row
   count is bounded by run segmentation — not by every flag that formed.
7. **The record is small where it counts.** ~9,000 setups sounds like a lot; at ~0.8 trades/day
   capacity it is ~400 *taken* trades over two years. Sharpe scales with √(independent bets).
   Most questions worth asking are under-powered, and the honest answer to several of them will be
   "this record cannot tell you".
8. **Multiplicity is the dominant risk, empirically.** `rule_sweep.py combos` searched 15,434
   rule combinations against a 25 % base rate and then re-ran the search on **shuffled outcomes**:
   the best combination found by luck alone scored 36.7/100 median, 43.7 best-of-200. Real data
   scored 51.0 on the fitting half — and **−0.194 R/session on the sessions it had not seen**.
   §D-39 and §D-40 were both fitted on 61 sessions and both collapsed out of sample.
9. **`passed` is not a given.** The shape gates — the machinery the whole engine is built around —
   selected *no better than taking everything* on the 197-session population (271 rows, 0.247 hit
   rate vs 0.249 base; −0.278 R/trade vs −0.247 over all rows). Three gates were removed on that
   evidence (§D-44). Treat the remaining grammar as one candidate filter, never as an axiom.
10. **Net, not gross.** A rule that improves gross R and worsens net R is common here and is the
    failure that matters at $500. `score()` returns both side by side.
11. **`analysis` and `portfolio.json` are caches.** They are downstream of whatever methodology was
    live when they were written. Rebuild from raw.

---

## 8. Where the numbers stand today

Stated as fact, not interpretation, because the analysis should start from it.

| book (2R target) | trades | win rate | total R | end equity from $500 | max DD |
|---|---|---|---|---|---|
| `books` — live only, 58 sessions | 18 | 27.8 % | **−3.84** | $398 | 29.5 % |
| `books_all` — live + recon, 511 sessions | 78 | 29.5 % | **−7.22** | **−$154** | 48.4 % |

Break-even at a 2R target is a 33.3 % hit rate before costs and ~42.9 % after what a $500 account
pays. **The shipped strategy is below break-even on the full record.** Nothing in the harvest has
yet been analysed; this is the state the analysis inherits, not a verdict on it.

Also of note: `books_all` shows **49 cap-bound and 51 unaffordable** trades against 78 taken —
at $500 the account, not the strategy, is frequently the binding constraint (§D-47 moved sizing to
full buying power for exactly this reason).

---

## 9. Getting at the data

| from | how |
|---|---|
| the Mac | direct `docker exec` / local `data/live` + `data/recon` layout — the `review-analysis` skill |
| the box | `scripts/box-job.sh`, **per date, one at a time**. ⚠️ never `docker exec` into the app; never `--all` |
| a cloud session | the **`box-data`** skill → `data-export.yml` on the self-hosted runner → result committed to the `data-export` branch. Takes `store` (live/recon), `dataset`, a date range, symbols, or raw DuckDB SQL; parquet/csv/ndjson |

⚠️ The box is a 2 vCPU / 4 GB CX23 and a heavy job takes it down hard. **The full recon `bars` and
`bars_1m` datasets cannot be moved into a cloud session** — tens of millions of rows against a
GitHub branch. The workable shape is: build the panel where the data is, export the *panel*
(~9,000 rows, trivially portable), and do the modelling on that. Push bar-level work down to the
box or the Mac as an aggregation, not a transfer.

---

## 10. What this shape supports — and what it does not

**Supports well**
- Selection modelling at the setup level: ~9,000 rows, 46 trigger-safe features, a clean causal
  boundary, a genuine time-ordered split with a contiguous 2-year spine.
- Exit and target search: `load_paths` gives the post-trigger bar path per setup, so any bracket,
  trailing rule or structural target is replayable without re-reading the store.
- Capacity and sizing questions: `build_book` + the cost model answer them in net dollars.
- Regime work at the session level: 511 sessions with a per-session outcome distribution, plus VIX.

**Supports weakly**
- Anything needing float, short interest or news: live-window only, 58 sessions.
- Anything needing intraday resolution finer than 5 minutes: `bars_1m`, recon only, pre-market only.
- Anything needing the live scanner's ranking: live only.

**Does not support**
- Post-09:30 behaviour on the recon half (not reconstructed).
- A universe question — the record only contains names that already gapped.
- A per-rule answer with the confidence a 15,000-combination search implies. The data has already
  demonstrated, on its own record, that it cannot distinguish that many hypotheses.
