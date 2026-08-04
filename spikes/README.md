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
| [`portfolio_slot_split.py`](#portfolio_slot_splitpy) | #416 | Replay the virtual book under different per-slot notional caps |
| [`open_drive_sweep.py`](#open_drive_sweeppy) | #418 | Quantify a second strategy: a 10-min ORB with a consolidation requirement |
| [`scanner_reconstruct.py`](#scanner_reconstructpy) | #428 | Rebuild a scanner appearance from bars alone, and calibrate it against what we actually saw |
| [`massive_replay.py`](#massive_replaypy) | #428 | Massive (ex-Polygon) adapter: vendor minute bars → 5-min grid → detector → R |

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

### `open_drive_sweep.py` — issue #418

**Q:** The engine only trades the pre-market. Is there a second strategy at the 09:30 open — the
09:30–09:35 bar as an opening range, the 09:35–09:40 bar as a consolidation, entry a tick above the
consolidation high — and what is it worth?

**A: the setup is real, the money is not — at $500.** Over 2026-07 (22 trading days with bars, 46
candidates on 13 days) the 1-trade/day book returns **+5.67R over 13 trades at 54% win, +0.436R per
trade**. But standing it up on its own $500 ends at **$497.67 — a −0.5% return on +5.67R.** The
stops are tight (1–7% of entry), so the 50% notional cap sizes **10 of 13** trades and each risks
**2.46%** of equity against a configured 5%. Cranking risk to 20% / cap to 100% still only reaches
+7.1% for a 21.5% drawdown. This is a capital constraint, not a strategy failure.

Two further results. **Not one of the ten pre-registered contrasts survives Holm** on the 215-setup
ungated population — all four gates point the right way (body-dominant +0.30R, cons-lower-volume
+0.22R) and none is separable from noise. **No fitted threshold earns its place** either: every
grid row's CI covers the permissive default. And the owner's 5/5 split is the best of four ORB
lengths tested (+0.44R vs +0.23R at 10/5 and **−0.79R** at 15/5).

⚠️ The **universe is symbols on the scanner strictly before the trigger fires** — applied at
extraction, and never relaxed by any variant. Each ORB length carries its own cutoff matched to its
own trigger, so the lengths sit on different (each legitimately tradable) populations.

**Selection arm (follow-up, 2026-08-02):** "first to trigger" is really an alphabetical lottery —
nearly every candidate fills on the same 09:40 bar, and replaying the same book with the same-bar
tie-break reversed swings the month by more than the strategy's whole edge. Because every OD-5/5
setup is final at 09:40 (clock-fixed candles + the cutoff), ranking the day's setups **is**
decidable at trigger time here, unlike the bull-flag. The refined rule — commit to the widest
planned stop inside the sizing band `[3%, risk/pos = 10%)`, one working order, roll to the next
setup on a pre-fill stop breach — turns the same month from **$497.67 (−0.5%, 10.4% dd)** into
**$529.80 (+6.0%, 4.2% dd)** with every trade deploying ≥ ~1.5% of equity instead of the ~1% the
cap squeezed out of the tight-stop picks. See
`docs/reports/2026-08-02-open-drive-picking-the-days-stock.md`.

Replays from the **Parquet store**, so it needs the box. `--validate` replays the current book
through the production `simulate_portfolio_adaptive` and refuses to report anything unless it
reproduces the published `portfolio.json` trade-for-trade.

```bash
git show origin/dashboard-data:portfolio.json > /tmp/portfolio.json
scp -i ~/.ssh/oracle_scs spikes/open_drive_sweep.py /tmp/portfolio.json root@<box>:/tmp/
ssh -i ~/.ssh/oracle_scs root@<box> \
  'docker cp /tmp/open_drive_sweep.py small-cap-stack-app-1:/tmp/ && \
   docker cp /tmp/portfolio.json small-cap-stack-app-1:/tmp/ && \
   docker exec small-cap-stack-app-1 python /tmp/open_drive_sweep.py \
       --store /data --payload /tmp/portfolio.json --validate --json /tmp/open-drive.json'
```

---

### `scanner_reconstruct.py` — issue #428

**Q:** Nobody sells historical scanner output. If all we have is bars, can we rebuild *when the
scanner would have surfaced a symbol* — closely enough that a multi-year backtest measures the same
thing the live tracker measures?

**A: yes, but only with the previous daily close.** The three hard scan gates are price-derived
(`scan_min_price`/`scan_max_price`, `scan_change_pct`, `scan_min_5m_volume`; float and news are
collected, never gated), so an appearance is reconstructible — except the change gate needs the
prior session's close, which a single day of bars does not carry. Measured over the 25 committed
review cases (real bars, real logged appearance times):

- **Bars alone:** the reconstruction fires a **median 18 min early**, and on 6 of 25 it fires on the
  very first bar of the day. Only **11/25** reproduce the same trade. The change gate is not a
  detail — it is what holds a symbol back until it has actually run.
- **With the change gate resolvable:** **20/25** appearances are explained — 10 already land within
  one bar-grid of the logged time, and 10 more are explained by a *feasible* previous close (the
  harness inverts the gate and solves for the interval of prior closes consistent with the observed
  appearance, so the missing input is falsifiable rather than assumed).
- **5/25 are unexplained** by any previous close (FATE, FWDI, CIFR, IREN, OPEN) — these bound how
  far a reconstructed universe transfers, and point at the two known biases: the IBKR 50-row cap on
  a busy morning, and a vendor volume basis that disagrees with `stVolume5minAbove`.
- Given the right appearance bar, the **engine reproduces the trade 24/25** and agrees on takeable
  **25/25** — so the reconstruction risk is concentrated entirely in *appearance time*, not in
  detection. That is the useful decomposition: buy the previous closes, and the rest follows.

Vendor-agnostic by construction (bars in, appearance out), so it serves both the calibration above
and the Massive harvest next door. Needs no API key and no store:

```bash
python spikes/scanner_reconstruct.py --fixtures
python spikes/scanner_reconstruct.py --fixtures --json data/spikes/recon-fixtures.json
python spikes/scanner_reconstruct.py --store /data --date 2026-07-02   # box/Mac only
```

### `massive_replay.py` — issue #428

The vendor half: Massive (ex-Polygon) REST → 1-min bars → the IBKR-aligned :00/:05 5-min grid →
`scanner_reconstruct` → `detect_day` → R-metrics. Stdlib-only HTTP (no new dependency), unadjusted
prices by default (a split-adjusted feed silently breaks the $1–50 gate for any pre-split year),
and `next_url` pagination.

The appearance is reconstructed on the **minute** series — a true trailing 5-min rolling sum, the
closest analogue to IBKR's continuously-updated `stVolume5minAbove` — while detection runs on the
**5-min** series. That split is the whole reason to pull minute data rather than 5-min aggregates.

⚠️ **`MASSIVE_API_KEY` lives in GitHub Actions secrets only.** A cloud session has no secret store,
so the key never goes there. Drive it with **`.github/workflows/spike-massive.yml`**, which runs on
`ubuntu-latest` — deliberately *not* the self-hosted `vps` runner, keeping vendor pulls off the 4 GB
box — and publishes curated JSON to the orphan `spike-massive-data` branch. Raw bars stay in the
runner's workspace and die with it.

```bash
python spikes/massive_replay.py selftest             # no key: aggregation + grid alignment
MASSIVE_API_KEY=… python spikes/massive_replay.py probe --symbol ARCT --date 2026-07-02
MASSIVE_API_KEY=… python spikes/massive_replay.py day --symbol ARCT --date 2026-07-02
MASSIVE_API_KEY=… python spikes/massive_replay.py universe --date 2026-07-02 --prev-date 2026-07-01
```

`probe` is the Stage-1 go/no-go on the vendor itself, before a penny is spent: extended-hours bars
from 04:00 ET, delisted tickers resolving, `adjusted=false` really returning as-traded prices, and
the 1-min → 5-min fold landing on the grid with volume preserved.

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

### `portfolio_slot_split.py` — issue #416

**Q:** Does the 2/day trade cap waste capital on days with only one setup, and would a **75/25**
first-trade / second-trade notional split deploy more of the book?

**A: no to both.** Over 2026-07 (25 sessions, 11 setups) the cap dropped **zero** trades — it has
never been the binding constraint under any configuration the book has run. A slot split leaves
**total R unchanged** by construction and moves end equity by ~±1.5% in *opposite directions* in the
adaptive and fixed-2R books. The real limiter is the 5% risk budget, which binds before the notional
cap on 8 of 11 trades. Full write-up:
`docs/reports/2026-08-01-the-2-trade-a-day-cap-is-it-wasting-capital.md`.

Unlike the other portfolio spikes this replays the **published payload** (`portfolio.json` +
`charts/` from the `dashboard-data` branch) rather than the Parquet store, so it runs from a cloud
session with no box access. `--validate` re-runs the published configuration through the harness and
refuses to report anything unless it reproduces all eight published books trade-for-trade.

```bash
git show origin/dashboard-data:portfolio.json > data/spikes/portfolio.json
python spikes/portfolio_slot_split.py --payload data/spikes/portfolio.json \
    --charts data/spikes/charts --validate --json data/spikes/slot-split.json
```
