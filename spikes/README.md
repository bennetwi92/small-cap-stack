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
| [`open_drive_sweep.py`](#open_drive_sweeppy) | #418 | Quantify a second strategy: a 10-min ORB with a consolidation requirement |
| [`scanner_reconstruct.py`](#scanner_reconstructpy) | #428 | Rebuild a scanner appearance from bars alone, and calibrate it against what we actually saw · **shipped as `harvest/reconstruct.py`** |
| [`massive_replay.py`](#massive_replaypy) | #428 | Massive (ex-Polygon) adapter: vendor minute bars → 5-min grid → detector → R · **shipped as `harvest/source.py`** |
| [`massive_calibration.py`](#massive_calibrationpy) | #428 | Does Massive data alone recreate the pre-market opportunities we actually saw? · **shipped as `harvest/prefilter.py`** |
| [`harvest_analysis.py`](#the-harvest-validation-harnesses) | #431 | The recon store's own funnel, per session |
| [`harvest_compare.py`](#the-harvest-validation-harnesses) | #431 | Recon vs live, stage by stage |
| [`harvest_premarket.py`](#the-harvest-validation-harnesses) | #431 | The same comparison restricted to the pre-market population — the only one that means anything |
| [`harvest_bookgap.py`](#the-harvest-validation-harnesses) | #431 | Which selection rule stops a well-formed setup becoming takeable |
| [`window_0400.py`](#window_0400py) | #569 | Replay the book under the 05:30 vs 04:00 selection floor |
| [`prefix_stability.py`](#prefix_stabilitypy) | #675 / #312 | Does the detector's answer change as the day's bars accumulate? (Gate 5's "sleeper") |
| [`regime_panel.py`](#regime_panelpy) | #690 | The **wide** setup-level panel: every opportunity-run over both stores, with the fitted selection rules carried as columns rather than applied |
| [`regime_scan.py`](#regime_scanpy) | #690 | Is there a regime? Trailing aggregates vs today, block structure, and whether the filter should differ per regime |
| [`adaptive_book_sweep.py`](#adaptive_book_sweeppy) | #690 | Switch the retired adaptive target (§D-38) and risk ladder (§D-23) back on, over 197 sessions |
| [`rule_sweep.py`](#rule_sweeppy) | #690 | Which single selection rules actually pick better setups, judged on old and recent data separately |

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

Answered #379 (2026-07-19): **keep the pre-market cutoff** — every relaxation was worse over the
12-session sample, and post-open candidates lost ~0.5R each against pre-market's ~breakeven.

> ⚠️ **Superseded — noted here 2026-08-07 (#536).** The cutoff this answer defended was 09:30;
> the live one is **09:15** (`config.py::select_window_end`), tightened by **#383** on 2026-07-21
> because the final pre-open ramp/auction trades like the open, which this strategy excludes.
> #379/#380 only ever swept *relaxations* (10:00–12:00), so nothing here argued against tightening.
>
> The window's lower end has its own history, and it is not the one you would guess: a **05:30
> floor shipped** (#405, 2026-07-31) and was **reversed** a week later (#569, 2026-08-07), leaving
> 04:00 with no floor. Note the replay *favoured* the floor — 14 trades / +9.62R with it against
> 18 / +4.93R without — and it was reversed anyway, on the standing "an unmeasured rule defaults
> open" principle plus a year of the owner's own trading saying the best fills come at 04:00.
> n=4 unlocked trades decides nothing either way. `research/decisions.md` carries the reasoning;
> `research/strategy.md` carries the live value.

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

> ⚠️ **The three `#428` harnesses below are superseded but NOT retired, and that is a decision
> (#543).** Their code shipped — `MassiveClient` → `harvest/source.py::MassiveSource`, `aggregate`
> and the rolling volume → `harvest/reconstruct.py`, the universe prefilter →
> `harvest/prefilter.py::candidates`, the appearance reconstruction →
> `harvest/reconstruct.py::reconstruct_hit`. But **#428 is still open**, and its question — does the
> reconstruction faithfully recreate what the live scanner saw — is the *trust* question Gate 3
> depends on (`research/phase-2-roadmap.md`). That has to be re-asked as the sample deepens, so
> **`.github/workflows/spike-massive.yml` stays** as the calibration harness and retires with #428,
> not before. It is `workflow_dispatch`-only on a hosted runner, so it costs nothing idle.
>
> They are listed under *Active* rather than *Answered* for that reason: the code is done, the
> question is not.

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
  far a reconstructed universe transfers. By construction *no* feasible previous close explains
  them (a feasible one lands the case in `change-gate` instead), so the residual mechanism is
  genuinely open; the known candidate bias is a vendor volume basis that disagrees with
  `stVolume5minAbove`. (This bucket was attributed to "the IBKR 50-row cap on a busy morning"
  until #536; #460 measured that cap as never binding — see the amendment below.)
- Given the right appearance bar, the **engine reproduces the trade 24/25** and agrees on takeable
  **25/25** — so the reconstruction risk is concentrated entirely in *appearance time*, not in
  detection. That is the useful decomposition: buy the previous closes, and the rest follows.

> **The reconstruction itself has moved (#431).** `rolling_window_volume` / `reconstruct_hit` and
> the 1-min → 5-min fold now live in `small_cap_stack.harvest.reconstruct`, and the REST client in
> `small_cap_stack.harvest.source` — they became a *producer* (the overnight harvest writes ~500
> sessions into the paper book's store through them), and spikes are exempt from mypy and the test
> suite. These harnesses **import them back** rather than keeping a copy, so the calibration below
> measures exactly the code the box runs. A second copy is how #428's numbers would quietly stop
> describing #431's output. What stays here is the calibration: `Case`, `implied_prev_close`,
> `calibrate_case`, and the CLI.

Vendor-agnostic by construction (bars in, appearance out), so it serves both the calibration above
and the harvest next door. Needs no API key and no store:

```bash
python spikes/scanner_reconstruct.py --fixtures
python spikes/scanner_reconstruct.py --fixtures --json data/spikes/recon-fixtures.json
python spikes/scanner_reconstruct.py --store /data --date 2026-07-02   # box/Mac only
```

Four ground-truth sources build the same `Case` (symbol, trading date, appearance, bars), so the
calibration is indifferent to where the truth came from: `load_fixture_cases` (the 25 committed
review cases — the regression baseline), `load_store_cases` (a live `Store`, box/Mac only),
`load_export_cases` (a `data-export` Parquet slice) and `load_dashboard_cases` (the published
dashboard payload). Only the last carries a `hit_quantum_sec`, because only it floors the
appearance to a bar; the loaders are covered by `tests/test_scanner_reconstruct_cases.py`.

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

### `massive_calibration.py` — issue #428

**Q:** Run the *whole* chain off vendor bars — Massive minute data in, appearance out, `detect_day`
over it, R out — and does it reproduce the pre-market opportunities the live tracker actually
recorded?

**A: yes, to within about a minute, on 7 of 8 — and the 8th is not a data problem.** Measured over
the pre-market session (04:00–09:30 ET, the window the paper book actually trades) against the 25
review cases, 8 of which carry a live pre-market appearance. 31 vendor calls on the free tier.

- **Appearance timing:** median **−0.34 min**, and 6 of 7 land within 5 minutes (excluding SNDQ,
  below). Reconstructing on the **minute** series beats the 5-min grid on every single case
  (median −0.34 vs +3.16 min) — which is the empirical case for buying minute data rather than
  5-min aggregates.
- **Trades:** **6/8 reproduce the same trade** (same decision, same entry bar time, same stop to the
  cent) and **8/8 agree on takeable**; 4 takeable live, 4 takeable from vendor data. ΣMax R 12.27
  live vs 10.24 reconstructed.
- **Prices agree, volume does not.** Closes match to a median **$0.005** (max $0.033), but Massive's
  consolidated volume runs a median **1.10×** IBKR's (mean 1.18×, max 2.29×). Massive also omits
  no-trade minutes where the IBKR series is dense, so the two sources disagree on *bar count* as
  well as volume — which is why entry bars must be compared by wall-clock time, never by index.
- **The prev-close inversion validated out of sample:** intervals predicted before the data was
  bought contained the true previous close in **6 of 8** (MSTZ predicted 10.99–11.19, actual 11.11).

**The two divergences.** They were long described as separate mechanisms with only one
fixable; #460 disproved the second, and see the amendment below for where that leaves them.

1. **OKLL — IBKR's change-percent reference is not the consolidated previous close.** It surfaced
   OKLL at 06:04, when the price implies only 9.6% against Massive's 4.91 close. For IBKR to have
   seen >10%, its reference must be **≥0.39% lower**. A systematic, correctable offset.
2. **SNDQ — a late appearance no fixed change reference explains.** SNDQ passed every gate from
   04:27 (10.3% change, 114k 5-min volume; IBKR's own volume was 1.88M at 04:00, so volume is not
   the constraint), yet the tracker first saw it at 08:35 — when the price was *lower* (2.14) than
   at 04:27 (2.15). No fixed change reference produces that ordering, so no gate explains it.

> ⚠️ **AMENDED 2026-08-06 (#460), corrected here 2026-08-07 (#536).** This section used to read
> "the 50-row rank cap, and this is provable rather than guessed … only capacity can [explain it]"
> — the most strongly-worded claim in the repo, and **wrong**. Measurement says the 50-row cap has
> **never bound**: across 20 live days the busiest tick carried **45** symbols, and pre-market
> peaked at **11**. The divergence is real; the mechanism isn't capacity. The live hypothesis is
> the same one that explains OKLL above — IBKR's change-percent *reference price* (#433) — which
> would also move an appearance time without any ranking involved.
>
> The retraction is recorded at `research/decisions.md` (2026-08-06) and in `config.py`; this file
> was missed, so the disproved version stood here for a day. It matters beyond tidiness: it feeds
> a wrong prior about recon-vs-live density, which is live work.
>
> **What replaces it is: not known.** #433's reference-price offset is the obvious candidate and
> explains OKLL, but as described everywhere in this repo that offset is *fixed* and systematic —
> and a fixed reference is exactly what item 2 above rules out. Either the reference moves
> intraday (nothing here has shown that) or the mechanism is something else. Say "unexplained"
> until it is measured; that is the whole lesson of the sentence being retracted.

The transferability picture is correspondingly open. A per-symbol reconstruction *can* model a
wrong reference price — that is what #433's inversion harness does — but it could never model a
ranking effect, so which of those SNDQ is decides how much the whole-market view is worth. The
Stage-3 shape (grouped-daily for everything, minute bars for candidates) buys that view either way.

⚠️ **The harness still prints the old label.** `spikes/massive_calibration.py` classifies this
divergence as `"rank-cap"` and `tests/test_massive_calibration_divergence.py` pins it, as does
`spikes/scanner_reconstruct.py`'s commentary. Read that string as *"late appearance, no fixed
reference explains it"* — the classifier's name, not a claim about capacity.

Fetch and analysis are split so the free tier's 5-calls/min budget is spent once:

```bash
MASSIVE_API_KEY=… python spikes/massive_calibration.py --fetch --cache data/spikes/massive
python spikes/massive_calibration.py --cache data/spikes/massive --json out.json
python spikes/massive_calibration.py --cache data/spikes/massive --regular-hours   # contrast
```

#### Out-of-sample validation (#428, 2026-08-04)

The result above rests on 8 symbol-days from a single week, so `--cases` adds ground-truth sources
beyond the 25 fixtures, and the fixture path stays the regression baseline.

**It held, and by a wider margin than the in-sample run.** 31 symbol-days over 2026-07-30 (the
busiest field collected — 100 opportunities) and 2026-08-03, 33 vendor calls:

| | in-sample (8 cases) | out-of-sample (31 cases) |
|---|---|---|
| median appearance Δ | −0.34 min | **−0.97 min** (range −4.5 … +1.5) |
| within 5 min | 6/7 | **30/31**, 0 outside, 1 undecidable |
| same trade | 6/8 | **31/31** |
| same takeable | 8/8 | **31/31** |
| Massive/IBKR volume | median 1.10×, max 2.29× | median **1.18×**, max **3.37×** |
| close agreement | median $0.005, max $0.033 | median **$0.002**, max **$0.11** |
| prev-close intervals | 6/8 | **24/27 (89%)** |
| divergences >5 min | 2 (both appearance-timing — see the amendment above) | **0** |

ΣMax R 32.478 live vs 32.472 reconstructed, 3 takeable either way.

**Read the zero narrowly.** `--live-window-only` scopes the harvest to symbol-days the live scanner
*did* surface pre-market, so the late-appearance population — a symbol whose gates passed at 05:00
that IBKR only surfaced at 10:30 — was never fetched. Zero unexplained is therefore conditional on *the
scanner surfaced it*, and says nothing about the false-positive side where #432 lives. Both dates
also sit within five weeks of the in-sample week, because collection only began 2026-07-01: this is
a different-week test, not a different-regime one.

**The minute series is doing the work.** Reconstructing off our own 5-min bars over the same 31
cases lands a median +2.5 min out and as much as +77.5 (19/25 within 5), against −0.97 off Massive's
minute bars — the empirical case for buying minute data rather than 5-min aggregates, now on a
sample 4× the original.

**#433 corroborated out of sample.** All three prev-close interval misses run the same direction —
the true close sits *above* the predicted bound, i.e. IBKR surfaced the symbol when a consolidated
reference says it had not yet cleared 10%. Required discounts: APLD 0.30% (comparable to #433's
measured ≥0.39%), PUSA 5.3%, NEXR 11.3%. The last two are far larger than #433 measured and are not
explained by that offset alone.

```bash
# the box's own Parquet, via the `box-data` skill's data-export slice
python spikes/massive_calibration.py --cache … --cases export --export-dir data/spikes/export
# ...or the published dashboard payload, when a session's proxy blocks the Actions API
python spikes/massive_calibration.py --cache … --cases dashboard \
  --charts-dir data/spikes/dashboard/charts --stats data/spikes/dashboard/stats.json \
  --dates 2026-07-30,2026-08-03 --live-window-only
```

Two traps the dashboard path handles explicitly, both of which quietly corrupt the result otherwise:

- **The published appearance marker is floored to its 5-min bar** (`charts.py::_bar_containing`),
  so treating it as exact reads ~3 min late. `Case.hit_quantum_sec` carries the floor, the delta
  becomes a bounded interval, and `within_5min` returns `None` rather than guess when the quantum
  alone would decide the verdict.
- **The R-metrics must be gated mid-bar, never on the marker itself.** `detect_day` gates entry on
  `bar.start >= first_hit` and bar starts sit on the same 5-min grid, so every instant strictly
  inside the marker bar yields the identical trade — while the bar start lets a bar the live engine
  could not have taken count as takeable. On 2026-08-03, where the raw microsecond appearance is
  also published, mid-bar reproduces the true trade on **61/61** and the bar start is wrong on
  **19/61**.

`--live-window-only` restricts the harvest to cases the summary actually scores, trading the
`vendor_hits_without_live_premarket_hit` count for a ~13× smaller call budget.

---

### The harvest validation harnesses — issue #431

Four thin recon-vs-live harnesses written 2026-08-07, committed in #543. They import production
code only (`portfolio.extract.extract_day_trades`, `rmetrics.compute_r_metrics`,
`bullflag.detect_day_with_settings`, `report.day_opportunities`) — no duplicated logic, so they
cannot drift from the engine the way a copied gate would.

⚠️ **All four are read-only against the stores.** They construct `Store(...)` and call `read`;
`Store.append` is the only writer and none of them calls it.

⚠️ **They expect the Mac's local analysis layout**, `data/live` alongside `data/recon` — *not* the
box's, where the live root is `data/` itself and `data/recon/` sits inside it.

| harness | asks |
|---|---|
| `harvest_analysis.py` | the recon store's own funnel, per session, dumped to `data/spikes/harvest_runs.parquet` |
| `harvest_compare.py` | recon vs live, stage by stage |
| `harvest_premarket.py` | the same, restricted to runs whose appearance is before 09:30 ET |
| `harvest_bookgap.py` | which selection rule stops a well-formed, fired setup becoming takeable |

**`harvest_premarket.py` carries the load-bearing methodological point.** The harvest reconstructs
the scanner for 04:00–09:30 ET only (`reconstruct.PREMARKET`), by design — the book's window sits
inside it — while the live tracker scans to 11:59. So a raw cross-store funnel compares 5.5 hours of
live scanning against 5.5 hours of pre-market reconstruction and **makes recon look thin when it
isn't**. Restricting both sides to pre-market appearances is the only comparison that means
anything.

`harvest_bookgap.py` was **ported in #543**: it originally attributed drops to a price band and a
time window the *book* applied on top of `takeable`, and #567 moved both into the engine. On the
post-#567 code those tests are True by construction for any takeable setup, so it would have
reported a meaningless 100% conversion — and it read four `Settings` fields the same PR renamed, so
it raised `AttributeError` first. It now attributes at the stage where the answer lives:
`passed + fired → takeable`, split by exhaustion / price band / window off `DaySetup`.

### `window_0400.py` — issue #569

Replays the virtual book under the 05:30 and 04:00 selection floors and prints both. This is the
harness behind `decisions.md` §D-36: 14 trades / +9.62R at 05:30 against 18 / +4.93R at 04:00, the
four unlocked trades all stopping out. Kept because the decision is explicitly revisitable once the
reconstructed history makes the window measurable rather than watchable.

### `prefix_stability.py`

**Q:** `research/phase-2-roadmap.md` calls this **"the sleeper"** and makes it the reason Gate 5
(#312) is log-only and precedes any order code — the v2 detector segments the *longest valid*
pole+consolidation over a day's bars, so run live against a **growing prefix**, might the
segmentation it picks at 08:35 differ from the one it picks at 16:00? If so, live and replay
disagreeing would silently invalidate the paper book as a predictor of the live one.

**A: no. 2,018 / 2,018 fired runs match the full-day answer exactly, with zero churn at any
intermediate prefix.** Measured 2026-08-08 over 81 sessions (51 recon 2026-04-17→06-30 + 30 live
2026-07-01→08-07) under post-#643/#584/#644 settings:

| store | runs | fired | first fire == full day | churned |
|---|---|---|---|---|
| recon | 1,220 | 909 | **909** | 0 |
| live | 1,454 | 1,109 | **1,109** | 0 |
| `--minute` (recon) | 1,220 | 909 (762 on a *partial* bar) | **909** | 0 |

Compared on `entry_trigger`, `entry_fill`, `stop`, the trigger bar's timestamp, the three segment
indices, `passed`, `takeable`, `exhausted` and `score` — exact match, no tolerance.

**Why it holds — structural, not luck.** `bullflag/day.py`'s candidate loop takes the *earliest*
cycle with a valid trigger and breaks; `entry_trigger`/`entry_fill` come from `bars[cons_end].high`
and `stop` from the consolidation lows, all closed bars strictly before the trigger; gates, score,
exhaustion and both selection rules read only bars ≤ trigger. **The chosen setup is causal.**

⚠️ **What it does NOT clear.** Both arms use the *same bars, truncated*. This clears the
**algorithm** and says nothing about the **inputs** — live bar formation and revision, missing or
late bars, feed restarts, or run/`first_hit` segmentation from live scanner hits. Gate 5's question
is therefore **"are the live bars the same bars"**, not "is the detector prefix-stable".

Kept live (not retired) so a future detector change has to re-prove this rather than inherit it.

```bash
python spikes/prefix_stability.py --store data/recon
python spikes/prefix_stability.py --store data/live
python spikes/prefix_stability.py --store data/recon --minute   # in-progress bars, needs bars_1m
```

### `regime_panel.py`

Builds the modelling set the regime work runs on: one row per (session, store, symbol, run) that
the flag grammar resolves to a setup, replayed over the raw bars of **both** stores — 5,024 setups
over 197 sessions (166 reconstructed, 31 live).

⚠️ **It is deliberately built with the fitted selection rules switched OFF.** The price band, the
minimum stop distance, the exhaustion cap, the entry-staleness cutoff and every book rule are
recorded as **columns**, never applied, because they were fitted on 61 sessions and are exactly what
this investigation may replace. What is kept is the flag grammar (R is *defined* against the entry
and stop it produces) and the scanner-appearance gate (the no-lookahead constraint, not a tuned
threshold).

The panel is a strict **superset** of the shipped population, not a different measurement:
`detect_day`'s greedy cycle walk picks a run's setup on the pole/trigger/appearance chain alone and
never consults a gate, a price, a window or a stop distance. `verify` proves it by re-deriving the
shipped `takeable` set from the wide panel's columns and checking it against a real run of the
shipped detector — **40 sampled sessions, 0 mismatches**.

```bash
python spikes/regime_panel.py build --store data/live --recon-store data/recon
python spikes/regime_panel.py verify --panel data/spikes/regime_panel.parquet \
    --store data/live --recon-store data/recon
python spikes/regime_panel.py summary
```

### `regime_scan.py`

The regime question itself, over the panel. Population is pre-market only (scanner appearance before
09:15 ET) — which is also what makes the two stores comparable: on the raw population recon shows
19.7 setups/session against live's 56.5, but that gap is almost entirely **in-market appearances**
(recon reconstructs pre-market only), and restricted to pre-market they sit at 18.5 and 21.4 with no
step across the 2026-06-30 → 07-01 boundary. So the record is one continuous 197-session series.

Four readings, each answering a different form of "is there a regime":

- `scan` — every trailing aggregate (windows 1/3/5/10/20) against every same-day target, with an
  **exact** circular-shift permutation test, BH FDR over the whole grid, the hypothesis count
  printed, and recon as discovery / live as holdout. `--detrend` residualises both series on
  calendar order, which is not optional: activity drifts upward across the record, so a trailing
  count and today's count correlate for reasons that have nothing to do with regime.
- `persist` — the block test. A hot/cold period is a *block* effect, so compare the between-block
  share of setup-level variance against blocks assembled from the same sessions **shuffled**. Run
  raw and detrended, because the shuffled null destroys ordering and a slow drift would otherwise
  read as a period. Plus the forward-horizon table (trailing-H vs forward-H) and lag-1..5.
- `terciles` — the pooled setup-level outcome per regime third, with day-block bootstrap CIs. The
  table a risk rule would actually read.
- `interact` — "should the filter differ per regime?", in the only form this sample can answer:
  does the **ordering** of a filter feature's buckets change across regime terciles? A per-regime
  threshold grid is the unpooled extreme and is the D-39/D-40 failure mode at a third of the data.

```bash
python spikes/regime_scan.py scan --detrend
python spikes/regime_scan.py persist --draws 3000
python spikes/regime_scan.py terciles --feature p2r_w10
python spikes/regime_scan.py interact --feature p2r_w10
```

### `adaptive_book_sweep.py`

Both adaptive layers **already exist and are switched off** — the target optimiser by
`portfolio_target_grid = (2.0,)` (a one-value grid makes `best_target` a no-op, §D-38), the risk
ladder by `portfolio_risk_rungs = 1` (§D-23). Each was retired on 61 sessions. This turns them back
on over **197** and replays, running the **real** `build_portfolio_payload` under `Settings`
overrides so sizing, costs, the notional cap and the exit model can never drift from what ships.

⚠️ Reads `books_all`, not `books`. `books` is deliberately live-only, because the book is
path-dependent twice over (the daily re-fit reads a trailing window; every position sizes off
running equity), so splicing the reconstructed days in front *replaces* the live record rather than
extending it. `books_all` is the combined simulation and the only view that sees all 197 sessions.

⚠️ **Do not run on the box.** Each variant is a full pass and `build_portfolio_payload` holds every
collected day's bars — the CX23 OOMs on one `--all` backfill, let alone eleven.

**Findings (2026-08-14, 197 sessions).** The adaptive target is neutral at best: given a
1R–5R menu it re-picked **2.0 on 100 of 100 trades** when fitted on all prior trades, and every
shorter window made it worse (trailing-20 moved the target 8 times → $188; trailing-40 moved it 24
times → $112, against $204 for the shipped fixed 2R). The fixed-target sweep says the same thing
from the other side — 1R ends at $86, **2R at $204**, 3R at $72, and 4R/5R end **negative**. Only
~15 in 100 setups reach 4R, which nowhere near pays for the misses. §D-38 holds at 3× the sample.

The risk ladder is a different story: three rungs stepping every decisive day takes 58 trades
instead of 100 and ends at **$303 with a 20% max drawdown**, against $204 and **41%** flat. It
roughly halves the worst stretch. ⚠️ But it helps *because the book currently loses money* — over
these sessions $500 → $204 — so trading less of it loses less. That is braking, not regime
detection, and the same ladder would likely cut winners once the selection rules are profitable.
Re-test it **after** the rules, not before.

```bash
python spikes/adaptive_book_sweep.py --store data/live --recon-store data/recon \
    --json data/spikes/adaptive_sweep.json
```

### `rule_sweep.py`

A scorecard for the rules that decide **which setups to take**, over the 3,740 pre-market setups in
the wide panel. §D-38 records >150 threshold variants swept over 79 sessions and §D-39/§D-40 were
fitted on 61 and collapsed out of sample, so the defence here is not a bigger sweep but a **cheaper
verdict**: a rule counts only if it beats its own half's base rate in the **old** data (166 recon
sessions) *and* the **recent** data (31 live) separately. That is a weak test on purpose — it cannot
confirm a rule, only refuse one.

**Findings (2026-08-14).** Base rate is 0.249 at a 2R target; break-even is 0.333 before costs and
~0.429 after what a $500 account pays. **14 of 35 candidate rules survive both halves, and not one
of them clears break-even on its own.** The strongest family is *freshness* — scanner attention
before the break: ≤1 hit gives 0.309 (+0.059), ≤4 gives 0.279, and a 15-minute staleness cutoff
gives 0.265. Then price ≥ $3 (+0.028) and ≥ $5 (+0.025), and stop ≥ 4% (+0.012).

Two shipped rules do **nothing** on this record and fail the both-halves test:

| shipped rule | keeps | hit | vs base |
|---|---|---|---|
| all shape gates pass | 271 | 0.247 | **−0.002** |
| break before 09:15 | 3481 | 0.248 | **−0.001** |

The bull-flag shape gates — the machinery the whole engine is built around — select no better than
taking everything. Worth a decision of its own; this spike only measures it.

⚠️ **The stacked result is not out-of-sample evidence and must not be read as any.** Running the
real book with the three rules that map to `Settings` knobs (price ≥ $5, stop ≥ 4%, staleness ≤ 15m)
takes $500 → **$576** over 197 sessions against $204 shipped, with max drawdown 41% → 16%. But split
it and the gain is **entirely in the fitted half**:

| | old (166 recon sessions) | recent (31 live sessions) |
|---|---|---|
| shipped | 77 trades, 36.4% win, +3.11R | 23 trades, 39.1% win, +3.36R |
| three rules | 25 trades, 56.0% win, **+16.72R** | 13 trades, 38.5% win, **+1.81R** |

Better in the old data, slightly *worse* in the recent — the exact signature §D-39/§D-40 showed
before they broke. 13 recent trades cannot refute it either; it is simply not settled.

Also confirms the ordering the adaptive sweep predicted: adding the risk ladder **on top of**
profitable rules costs money ($475 vs $576), because it starts cutting winners.

```bash
python spikes/rule_sweep.py single
python spikes/rule_sweep.py stack --min-keep 60
```

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
50-row API maximum (`decisions.md`) — which #460 then measured as **never binding** in practice
(busiest live tick: 45 symbols; pre-market peak: 11), so it is headroom rather than a constraint.

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
