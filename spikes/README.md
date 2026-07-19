# Spikes

Time-boxed, throwaway experiments that de-risk decisions before we build. Spike code is not
production code and is exempt from the package's mypy strictness (it lives outside
`src/small_cap_stack`), but it is still ruff-linted.

**The agreement** (CLAUDE.md): every spike maps to a **GitHub issue**, and its findings are recorded
as a comment on that issue — not just in chat. Outputs (CSV/JSON/XML) go to `data/spikes/`, which is
gitignored. **Never commit data.**

A spike whose question is answered is dead weight: retire it to *Answered* below, or delete it. Two
were deleted for exactly this reason (#296) — the engine-v2 golden-parity test and
`divergence_v1_v2.py`, whose v1-vs-v2 comparison silently became v2-vs-v2 once #180 repointed
`compute_r_metrics`.

---

## Active

| Spike | Issue | What it's for |
|---|---|---|
| [`viz_engine.py`](#viz_enginepy) | #140 / #176 / #182 | Per-opportunity visual review of the engine |
| [`review_regression.py`](#review_regressionpy) | #194 | Re-pin the reviewed cases after a rule change |
| [`review_metaanalysis.py`](#review_metaanalysispy) | #173 | One flat row per run: engine vs the trader's ground truth |
| [`review_meta_sweep.py`](#review_meta_sweeppy) | #173 | Replay candidate gate/param changes over the reviewed day-set |
| [`warrior_library.py`](#warrior_librarypy) | #304 | Warrior Trading transcript corpus for rule provenance |
| [`portfolio_cutoff_sweep.py`](#portfolio_cutoff_sweeppy) | #379 | Replay the virtual book under different selection filters |

### `viz_engine.py`

Renders one opportunity's full trading day (04:00–16:00 ET) as an HTML candle chart, marking how the
engine tokenises the day and picks the pole / consolidation / entry, the prior pump–fade **cycles**
(the exhaustion rule), the scanner-appearance ("seen") line, entry/stop levels, and the gate table.
This is the harness the trader drives one opportunity at a time to refine the rules.

Its rules have largely **graduated into the core package** (`bullflag/day.py::detect_day` is the port
of `pick_setup` + the exhaustion wiring, validated against 25 reviewed opportunities, #194).

### `review_regression.py`

Extracts the reviewed opportunities — the day's bars **plus** the expected engine outcome
(pole/cons/entry/stop, passed, failing gates, cycle number, exhausted) — as committed fixtures.

```bash
python spikes/review_regression.py              # CHECK: assert every fixture still matches
python spikes/review_regression.py --extract    # re-pin fixtures from a live /data snapshot (Mac/VPS)
```

⚠️ **The fixtures graduated to `tests/fixtures/review_cases/`** and are now asserted by
`tests/test_review_fixtures.py` **in CI** — so `--extract` is the live half of this spike (the
deliberate "re-pin the golden value" step after the trader signs off a new outcome), while check
mode merely duplicates what CI already runs on every PR.

The fixtures are ~160K of curated OHLCV **test inputs** (not runtime data, and outside the gitignored
`data/`) — a documented, trader-approved exception to "never commit data".

### `review_metaanalysis.py`

Cross-day meta-analysis of review-page feedback. Builds one flat row per run over a set of reviewed
days, joining engine R-metrics, the trader's review annotation (and the **corrected** annotation Max
R, anchored at `consolidation.t1` — *not* the buggy saved `annotations.max_r`), flag-time metadata
(rank persistence, float/short%, news recency) and candidate features. Emits JSON on STDOUT; the
human summary + verification assertions go to STDERR.

**Runs on the VPS** (needs `/data`). Reviews are shipped in via `REVIEWS_B64`.

### `review_meta_sweep.py`

Stage E of the same spike: replay-backtest candidate gate/param changes over the reviewed day-set.
Re-runs `compute_r_metrics` per run under each candidate `Settings` override and scores it against
the trader's ground-truth labels (tradeable vs no_trigger). Pure replay — the engine is already
backcastable over the cached bars.

**Runs on the VPS.** Driven by the `review-analysis` skill; see also the `box-data` skill for
pulling `/data` into a web session.

### `warrior_library.py`

Collects English auto-captions for Warrior Trading / Ross Cameron videos into a **gitignored**
library under `data/warrior-library/` (captions only — no video/audio), so a rule's provenance can be
checked against what is actually said rather than recollection. One code path serves both the
backfill and the daily incremental job; videos already in `index.json` are skipped, so re-runs are
cheap and idempotent.

```bash
python spikes/warrior_library.py --months 6       # backfill a rolling window
python spikes/warrior_library.py --since 20260101 # backfill from a date
python spikes/warrior_library.py --limit 5        # smoke test
```

YouTube requires a JS runtime to hand over caption URLs, so yt-dlp is pointed at the local `node`
(`--js-runtimes node`).

### `portfolio_cutoff_sweep.py`

Replays the virtual book (#230) under `Settings` overrides by calling the **real**
`build_portfolio_payload`, so a "what if I'd selected differently" question can never drift from the
live book. Two views: the full adaptive book per variant (what you'd have experienced, kill-switch
ladder and costs included) and a signal-isolation view that buckets every candidate pre-market vs
post-open, unsized and cost-free, so the ladder doesn't confound the comparison.

Answered #379 (2026-07-19): **keep the 09:30 pre-market cutoff** — every relaxation was worse over
the 12-session sample, and post-open candidates lost ~0.5R each against pre-market's ~breakeven.

Any variant must be **decidable at trigger time** — ranking a day's candidates against each other is
look-ahead bias. Running it surfaced #381 (the book selected different trades on identical inputs);
the numbers were re-pinned against that fix and are now stable run-to-run.

```bash
.venv/bin/python spikes/portfolio_cutoff_sweep.py --store /path/to/store-copy
.venv/bin/python spikes/portfolio_cutoff_sweep.py --store /data --json data/spikes/sweep.json
```

---

## Answered

These settled their question and are kept only as the record of *how* it was settled. The findings
live on the issues; the decisions live in `research/decisions.md`. Don't run them casually — they all
need a live IB Gateway.

### `api_scanner_vs_mosaic.py` — issue #8

**Q:** Can the IBKR **API** scanner (`reqScannerData`) reproduce the small-cap gainer scan the trader
runs in the TWS **Mosaic** GUI? (The headless system can only use the API, so a "no" would have sunk
the approach.)

**A: yes** — and the volume finding is now a locked rule: the strategy wants **trailing 5-min
volume**, not cumulative day volume. `volumeAbove` and snapshot `dayVol` are both day-cumulative;
IBKR exposes the short-term window natively, so we filter on it directly
(`stVolume5minAbove`) rather than deriving it from bars. Scanner breadth was later raised to the
50-row API cap (#—, `decisions.md`).

```bash
python spikes/api_scanner_vs_mosaic.py --dump-params            # → data/spikes/scanner_parameters.xml
python spikes/api_scanner_vs_mosaic.py --port 4002 --vol-window 5min --min-volume 100000
```

### `premarket_bar_completeness.py` — issue #9

**Q:** Are pre-market 5-min bars complete enough to detect a bull-flag on thin names? Reports, per
symbol, how many 5-min slots from 04:00 ET are filled and the largest contiguous gap.

A leading absence (first bar after 04:00) just means the stock hadn't traded yet — fine. Internal
gaps are what would distort candle counting.

### `ibkr_news_check.py` — issue #10

**Q:** Does IBKR deliver per-symbol breaking news before we pay for a feed? Lists entitled providers
(`reqNewsProviders`), pulls recent headlines (`reqHistoricalNews`), optionally the body
(`reqNewsArticle`).

### `ibkr_tradability_check.py` — issue #25

**Q:** Is a symbol actually **orderable on IBKR** (not merely un-halted)? Probes non-intrusively:
contract qualification → live snapshot (proves it trades) → `whatIfOrder` margin preview (**no**
execution).

**A: this gate is load-bearing.** Confirmed live — a scanner hit (CBRG) came back **BLOCKED**
(PRIIPs/KID restriction) while the rest were TRADABLE. Re-validate verdicts on a **live** account in
Phase 3; paper may not perfectly mirror restrictions.
