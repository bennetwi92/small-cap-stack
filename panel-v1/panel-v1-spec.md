# panel-v1 — the frozen analysis panel (Stage 0, #736)

**Status:** FROZEN (2026-09-18). Produced by the Stage-0 `builder` pass, per
[`analysis-protocol.md`](./analysis-protocol.md) §4. The **sha256 below is the identity of the
panel**, not its row count (§4.4). An agent whose `sha256sum` does not match character for
character **stops and reports** — it never rebuilds.

## 1. Artefacts and checksums

Published on the **`data-export`** branch under `panel-v1/`. The standalone checksums are in
[`panel-v1.sha256`](./panel-v1.sha256).

| artefact | sha256 | contents |
|---|---|---|
| `panel-v1.parquet` | `ff77c4516e78db9cd05081ce0c9f5bf7db3b6052eaccbbc9c557950c1cde37e7` | 9,195 rows, every split; HOLDOUT outcomes nulled (§3) |
| `paths-v1/paths-v1.parquet` | `2b5bd485ca0010f925ec343c4174ab0ef28a78029424489f75e4af79b3f594a0` | post-trigger 5-min paths, **FIT + CHECK only**: 6,740 keys, 718,263 bars (`key, bar, etmin, open, high, low, close`; `bar == 0` is the trigger bar) |
| `sessions-v1.parquet` | `d797f739329d0c8dff7b7b4abe6baa14507815a2c7d4e0504989fe3539026b60` | the session universe (`source, dt, split`), which is J's denominator: flat sessions count as 0 |
| `custodian/panel-v1-custodian.parquet` | `db8c12d7efb6e87f5e371b286551b64126e0431f73e12206dea03f77bbf23207` | **custodian only, never published**: the unredacted panel |
| `custodian/paths-v1-full.parquet` | `eba52871d9a189e3572e23050fa666a522467e2719f4489ab664315bfbf0c21d` | **custodian only, never published**: every split's paths, with volume |

Verify: `cd panel-v1 && sha256sum -c panel-v1.sha256`.

Load: `spikes/analysis_v1/stage0.py` → `load_panel_v1()`, `load_paths_v1()` (the
`engine_lab.load_paths` dict format), `load_sessions_v1()`. That module is also the **one**
implementation of the splits, Filter A, the four exit families, the per-leg cost model, the §6.2
report and the §5.1 column-subsetting runner (`run_rule`). Import it; do not fork it.

## 2. Build

| | |
|---|---|
| command | `.venv/bin/python spikes/regime_panel.py build --store data/live --recon-store data/recon --out data/spikes/panel-v1/panel-v1-full.parquet`, then `spikes/analysis_v1/stage0.py paths · free · costs · baseline · power · geometry · publish` |
| package commit | `7f5f7ff779c46dbdac73c8877605812d3c088fba`, plus this PR's one-line `regime_panel.py` fix (`report._funds_for` now returns a 3-tuple; `shares_outstanding` is taken from it) |
| settings profile | `regime_panel.WIDE_OVERRIDES`: every selection threshold switched off; the grammar left as shipped |
| data | the box's store rsynced to the Mac on 2026-09-18. Recon has 453 sessions (2024-09-09 → 2026-06-30); live has 55 bar sessions (2026-07-01 → 2026-09-17) |
| cuts (§4.1) | `trigger_et_min < 570` on both halves; `cons_has_range` (56 pre-market rows dropped); `dt ≤ 2026-09-17` |
| toolchain | Python 3.11, polars 1.42.0 |
| path integrity | 9,195 / 9,195 rows have a path; `replay_bracket(target=None)` reproduces every row's `max_r` (**0 mismatches**) |

## 3. Splits — realised (S0-A)

| split | dates | half | sessions | panel rows |
|---|---|---|---|---|
| FIT | 2024-09-09 → 2025-09-30 | recon | 266 | 4,516 |
| CHECK | 2025-10-01 → 2026-03-31 | recon | 125 | 2,224 |
| HOLDOUT | 2026-04-01 → 2026-09-17 | recon | 62 | 1,300 |
| HOLDOUT | 2026-04-01 → 2026-09-17 | live | 55 | 1,155 |
| **total** | | | **508** | **9,195** |

The row count is inside S0-A's 7,500–11,000 band. No split is thinner than §7.1 states: FIT 266 vs
~245–265, CHECK 125 vs ~110–125, HOLDOUT 117 vs ~115–125. ⚠️ **The live half is 55 sessions,
not 58.** Three `opportunities` partitions (2026-07-03, a market holiday, and 07-04/07-05, a
weekend) have no bars.

**Walk-forward folds (§7.2)** over the 266 FIT sessions, `stage0.folds_v1()`:
- train 2024-09-09..2025-01-24 → test 2025-01-27..2025-04-07
- train 2024-09-09..2025-04-07 → test 2025-04-08..2025-06-18
- train 2024-09-09..2025-06-18 → test 2025-06-20..2025-08-29

**HOLDOUT redaction (S0-B).** On all 2,455 HOLDOUT rows (2026-04-01 →
2026-09-17), these columns are null: `bars_to_max_r`, `day_dollar_volume`, `day_high`, `day_low`, `day_volume`, `entry_price`, `fill_above_entry_bar_high`, `first_rank`, `mae_r`, `max_gain_pct`, `max_r`, `n_day_bars`, `n_scanner_hits_day`, `realised_risk`, `run_count`, `same_bar_stop`, `stop_index`, `stopped_out`, `total_significant_cycles`, `triggered`.
That is the union of §4.3's nine, the harness `OUTCOME_COLS` and the seven day aggregates. A holdout
`day_high` is the outcome at a lower resolution. HOLDOUT features ship intact. HOLDOUT paths are
absent. `stage0.cmd_publish` asserts each of these row for row, and nothing ships otherwise.

## 4. Columns (S0-C, S0-D)

**`RULE_COLUMNS` — frozen, 35 columns.** A candidate predicate is handed
`df.select(RULE_COLUMNS)` and nothing else (§5.1, `stage0.run_rule`):

`bars_before_pole`, `breakout_level`, `cons_has_range`, `cons_len`, `cons_vol_reducing`, `cum_dollar_vol_pre_trigger`, `cum_volume_pre_trigger`, `cycle_num`, `day_open`, `entry_fill`, `entry_trigger`, `ext_at_peak`, `ext_at_trigger`, `failing_gates`, `first_hit_et_min`, `halted_consolidation`, `hits_before_trigger`, `passed`, `planned_risk`, `pole_has_big_green`, `pole_len`, `pole_pct`, `pole_volume`, `range_before_pole_pct`, `retracement`, `run`, `runup_pre_appearance`, `rvol_pole`, `staleness_delay_min`, `stop`, `stop_pct`, `trigger_et_min`, `trigger_idx`, `untraded_cons_bars`, `vol_share_pole_pre_trigger`

How it was derived:
- **Start:** the harness `TRIGGER_TIME_SAFE`, which has **45** columns, not the 46 the inventory states.
- **S0-C:** none of the seven lookahead columns is inside the whitelist.
- **Removed:** identity and bookkeeping columns (`dt, source, symbol, seg_id, key, split, opportunity_id`).
- **Removed at S0-D** for > 20 % null: `float_shares`, `shares_outstanding`, `short_percent`.
- **Missing from panel-v1:** `shares_source` and `shares_as_of`. The stale panel got them from a post-processing spike this pass is blind to.
- **⚠️ S0-C amendment A1:** `cum_volume_to_trigger`, `cum_dollar_vol_to_trigger` and `vol_share_pole` sum the trigger bar's **full** volume and close, which are not known when the order fires mid-bar.
  - They are removed from `RULE_COLUMNS`. They stay in the panel as descriptions.
  - They are replaced by strict variants over bars strictly before the trigger: `cum_volume_pre_trigger`, `cum_dollar_vol_pre_trigger` and `vol_share_pole_pre_trigger`.
  - The trigger bar's share of `cum_dollar_vol_to_trigger` on FIT: q10 / q50 / q90 = 1.0% / 5.9% / 32.6%.

**Nullity** (fraction null; only columns with any nulls are shown — every other candidate is 0 %
everywhere):

| column | FIT | CHECK | HOLDOUT recon | HOLDOUT live | |
|---|---|---|---|---|---|
| `float_shares` | 100.0% | 100.0% | 100.0% | 5.0% | dropped |
| `range_before_pole_pct` | 13.6% | 11.3% | 12.6% | 10.5% | kept |
| `rvol_pole` | 13.6% | 11.3% | 12.6% | 10.5% | kept |
| `shares_outstanding` | 33.0% | 27.0% | 18.5% | 3.6% | dropped |
| `short_percent` | 100.0% | 100.0% | 100.0% | 11.0% | dropped |

Per-split q10/q50/q90 of every candidate column (stationarity) is in `stage0-free.json`, which is
regenerable with `stage0.py free`.

## 5. Filter A — frozen constants (protocol §9, as amended)

| condition | frozen value |
|---|---|
| 1 | `trigger_et_min < 570` |
| 2 | `hits_before_trigger >= 1` |
| 3 | `cum_dollar_vol_pre_trigger >= 4857910.10055` — **FIT q50, frozen** (amendment A1: the strict pre-trigger column replaces §9's `cum_dollar_vol_to_trigger`) |
| 4 | earliest 1 trigger per session by `trigger_et_min` (ties: `symbol`, `run`) — never ranked |
| sensitivity band (§7.4, never used to select) | q40 = `3260067.17`, q60 = `6991445.405000001` |

**Realised throughput (S0-D):** 1.000 trades/session on FIT, CHECK and HOLDOUT at q50 (q40: 1.000;
q60: FIT 0.996). Every session has a qualifying setup. That is inside [0.6, 1.0], at its edge, and
above the protocol's expected 0.85–0.95. In practice condition 3 decides *which* setup is earliest,
never *whether* a session trades.

## 6. The cost audit (S0-F) — the exit gate

There are **no real broker fills** (Phase 1 places no orders). By operator decision, `c` is the
pinned IBKR tiered schedule (`engine_lab.Costs`, which reproduces `broker-costs.md`) priced per leg
on panel-v1's own geometry.
- **Sizing:** full buying power of the $500 cash account, `qty = floor(500 / entry)`.
- **Slippage** applies on every non-limit exit. It is an **unaudited assumption**, so it is shown at 0 / 2 / 4 ticks.
- **Break-even** is `p* = (1 + c_loss) / (1 + T + c_loss − c_win)`, because limit winners pay no slippage.

**Gate:** F1 net break-even on the FIT panel (full buying power, 2-tick slippage) =
**77.0%**, which does not exceed 80 %. **F1 is NOT cancelled.**

| population · sizing · slippage | c_loss mean | c_loss q10/q50/q90 | c_win | F1 break-even | F2 break-even |
|---|---|---|---|---|---|
| FIT panel · full_bp · slip0 | 0.118 R | 0.027 / 0.097 / 0.227 | 0.118 R | 74.6% | 37.3% |
| FIT panel · full_bp · slip2 | 0.274 R | 0.059 / 0.224 / 0.560 | 0.118 R | 77.0% | 40.4% |
| FIT panel · full_bp · slip4 | 0.430 R | 0.092 / 0.357 / 0.893 | 0.118 R | 78.9% | 43.2% |
| FIT panel · harness_5pct_50pct · slip2 | 0.302 R | 0.077 / 0.249 / 0.575 | 0.146 R | 78.6% | 41.3% |
| Filter-A FIT stream · full_bp · slip2 | 0.183 R | 0.043 / 0.160 / 0.336 | 0.082 R | 73.9% | 38.1% |

**By `stop_pct` decile** (FIT panel, full buying power, 2-tick slippage; decile edges are fixed on
FIT). `c_trail` is a non-limit exit at entry, the cost floor F3 pays. `c_F4` is half at a +1 R limit
and half at entry.

| decile | median stop_pct | c_loss | c_win | c_trail | c_F4 | F1 break-even | F2 break-even |
|---|---|---|---|---|---|---|---|
| D1 | 1.21% | 0.465 | 0.242 | 0.465 | 0.430 | 85.0% | 45.5% |
| D2 | 2.39% | 0.393 | 0.167 | 0.393 | 0.300 | 80.7% | 43.2% |
| D3 | 3.35% | 0.373 | 0.154 | 0.373 | 0.274 | 79.9% | 42.7% |
| D4 | 4.26% | 0.338 | 0.139 | 0.338 | 0.245 | 78.7% | 41.8% |
| D5 | 5.21% | 0.294 | 0.120 | 0.294 | 0.212 | 77.3% | 40.8% |
| D6 | 6.21% | 0.264 | 0.108 | 0.264 | 0.190 | 76.3% | 40.0% |
| D7 | 7.45% | 0.225 | 0.092 | 0.225 | 0.161 | 75.0% | 39.1% |
| D8 | 9.12% | 0.183 | 0.075 | 0.184 | 0.132 | 73.6% | 38.1% |
| D9 | 11.90% | 0.138 | 0.057 | 0.138 | 0.100 | 72.0% | 36.9% |
| D10 | 18.47% | 0.070 | 0.029 | 0.070 | 0.051 | 69.5% | 35.2% |

Decile edges (`stop_pct`): 0.0181, 0.0288, 0.0382, 0.0475, 0.0568, 0.0678, 0.0824, 0.1033, 0.1426.

⚠️ **F1 survives by a thin margin and not everywhere.** Its break-even exceeds 80 % in decile D1 at
2 ticks, is 80.7 % in D2, and reaches 78.9 % over the whole FIT panel at 4 ticks. A selection that
concentrates on tight stops takes F1 over the wall. **Percentage stop distance is a cost lever**
(§11.1): the cost of a loss runs from 0.47 R in the tightest decile to 0.07 R in the widest.

**The inventory's 3× disagreement, resolved.** The two figures measured different things:
- **§6's ~10 %** matches the commission-only **median** (0.097 R at 0 ticks). It omits slippage and the tight-stop tail.
- **§8's 42.9 %** back-solves to `c ≈ 0.286 R` charged on every trade. That is close to the measured **mean loss cost (0.274 R)**, but overstates winners, which fill at a limit (0.118 R).
- **Measured 2 R net break-even: 40.4 %.** Both inventory sections are corrected in this PR.

## 7. Dispersion and the Filter-A baseline (S0-H) — FIT only, 266 sessions

Scored on the Filter-A stream with full buying power and 2-tick slippage. Families are as in
protocol §10, with the conventions in ledger amendment A3. **4 trials.**

| family | J (net R/session) | gross R/session | net R/trade mean | **sd** | longest losing 20-session run | deepest DD (R) | perm-null p (B=200) |
|---|---|---|---|---|---|---|---|
| F1 | -0.2981 | -0.1711 | -0.2981 | **0.805** | 13 of 13 | -81.3 | 0.005 |
| F2 | -0.2994 | -0.1450 | -0.2994 | **1.408** | 4 of 13 | -82.4 | 0.0199 |
| F3 | -0.3133 | -0.1316 | -0.3133 | **1.776** | 9 of 13 | -87.7 | 0.0149 |
| F4 | -0.4532 | -0.2861 | -0.4532 | **0.900** | 13 of 13 | -121.2 | 0.01 |

Common to all four:
- 266 trades (1.000/session); 1,992 qualifying setups turned away by the capacity cap; 0 unaffordable.
- `stop_pct` of taken trades: q10 / q50 / q90 = 2.29% / 6.62% / 14.65%.
- FIT is recon-only, so there is no live row to report separately.

The permutation null: within each session, the outcome of Filter A's pick is drawn from that
session's own pool of panel rows. `p` is the share of null J ≥ real J.

Session net-R deciles (0 → 100 %) and capital adequacy, per family:

- **F1**: deciles [-1.773, -1.257, -1.187, -1.108, -1.044, 0.346, 0.395, 0.428, 0.455, 0.473, 0.49]; best-decile share of total -0.158; end equity $-2,220.79; max drawdown -557.6 %
- **F2**: deciles [-1.773, -1.306, -1.24, -1.187, -1.13, -1.085, -1.052, -1.025, 1.899, 1.962, 1.99]; best-decile share of total -0.645; end equity $-2,174.85; max drawdown -493.3 %
- **F3**: deciles [-1.773, -1.306, -1.224, -1.168, -1.112, -1.068, -1.026, -0.03, 0.468, 1.163, 16.386]; best-decile share of total -1.117; end equity $-2,310.41; max drawdown -462.5 %
- **F4**: deciles [-1.773, -1.305, -1.21, -1.164, -1.096, -1.051, 0.205, 0.339, 0.446, 0.65, 2.743]; best-decile share of total -0.238; end equity $-3,811.69; max drawdown -773.1 %

⚠️ Two report fields read strangely here, and both are stated rather than hidden:
- **Best-decile share** is negative because the total is negative. It is not interpretable below zero.
- **End equity below $0 and drawdowns beyond −100 %** happen because sizing is fixed at $500 and does not shrink as equity falls. On every family the account is ruined within the FIT window.

## 8. The power table, recomputed (S0-J)

Minimum detectable effect in net R/trade at 80 % power, from S0-H's measured sd. Trades assumed:
0.8 × realised sessions, the throughput target §7.2 used (FIT 213, CHECK 100, HOLDOUT 94).
FIT columns are two-sided α = 0.05, Bonferroni-corrected at each workstream's allocation.
CHECK is ≤ 3 candidates. HOLDOUT is 3 candidates, one-sided α = 0.0167 (§12.2).

| family | sd | FIT, 1 | FIT, W3 (24) | FIT, W2 (36) | FIT, W1 (54) | FIT, 120 | CHECK, 3 | HOLDOUT, 3 |
|---|---|---|---|---|---|---|---|---|
| F1 | 0.805 | 0.155 | 0.216 | 0.223 | 0.229 | 0.241 | 0.261 | 0.247 |
| F2 | 1.408 | 0.270 | 0.378 | 0.390 | 0.401 | 0.422 | 0.455 | 0.431 |
| F3 | 1.776 | 0.341 | 0.477 | 0.491 | 0.505 | 0.532 | 0.574 | 0.544 |
| F4 | 0.900 | 0.173 | 0.242 | 0.249 | 0.256 | 0.270 | 0.291 | 0.276 |

**No family is under-powered** at the 0.6 R/trade bar in any column, so no crossed design is cut.
The measured sd differs from §7.2's assumed 1.4 R: F1 and F4 are about half of it, F2 matches, and
F3 is 27 % above it.

## 9. Recon session window and `bars_1m` (S0-E)

- **F3 and F4 may hold past the bell on recon.** 448 of 453 recon sessions (98.9%) reach 15:55 ET, clearing the ≥ 95 % bar. Per panel row, 98.5 % (FIT) and 98.1 % (CHECK) of paths reach 15:55.
- **W2 proceeds as planned.** `bars_1m` covers 453 / 453 recon sessions (100%).
- ⚠️ `bars_1m` holds **1,468,265 rows**, not the ~32 M the inventory estimated.

## 10. Review geometry audit (S0-G) — through the redacting loader

`stage0.load_reviews_redacted()` drops `note` and `max_r` in the JSON `object_hook`, as each object
is parsed. Neither ever exists in a Python object.
- **Reviews:** 167 in total. 110 are `no_trigger`. **16** match a pre-market panel row by (date, symbol, run); the rest are in-market triggers or run mismatches. 116 legacy files have no run suffix and are matched as run 1.
- **Agreement:** the detector's consolidation low is within 2 % of the human's **and** its trigger falls in the human consolidation window + 30 min on **14 / 16 (87.5%)**.
- ⚠️ **Caveat:** the trader chose which days to review, and that choice is outcome-informed, so this is conditional on being reviewed. It is **not** a population statistic.
- ⚠️ **Caveat:** the residual leak is not zero, because a human's taste in geometry correlates with what happened next. **No workstream may condition a rule on a review.**
- On 16 matches the rate is a diagnostic, not an estimate.

## 11. Harness inventory (S0-I)

| primitive | needed by | status |
|---|---|---|
| panel + splits + session universe | all | **`stage0`** — `load_panel_v1`, `split_v1`, `load_sessions_v1`, `folds_v1` |
| post-trigger paths | all | **`stage0.load_paths_v1`** (the `engine_lab.load_paths` format) |
| F1 / F2 bracket | all | `engine_lab.replay_bracket`, wrapped as `stage0.exit_fixed` |
| **F3 runner / F4 hybrid** | all | **built here** — `stage0.exit_runner` (arming threshold is a parameter, for W2), `stage0.exit_hybrid` |
| per-leg costs at real sizing | all | **built here** — `stage0.leg_costs`, `price_trade`, `FULL_BP`. `engine_lab.score()` prices one round trip and cannot price F4's third order |
| capacity, earliest-N by time | all | `engine_lab.build_book`; `stage0.filter_a` for N = 1 |
| column subsetting (§5.1 layer 1) | W1 | **built here** — `stage0.run_rule` over the frozen `RULE_COLUMNS` |
| `assert_no_lookahead` (§5.1 layer 2) | W1 | `engine_lab` — ⚠️ its whitelist still lists the inclusive columns; `RULE_COLUMNS` is the authority |
| §6.2 report | all | **built here** — `stage0.session_report` |
| block-by-session permutation null | all | pattern in `stage0.cmd_baseline`; a **search-level** null (re-run the whole search per replicate) needs the workstream's own search callable, so each plan wraps its search. `engine_lab.permutation_pvalue` is **not** §7.3's null: it resamples a single selection, not the search |
| W2: 1-minute replay, grid reconciliation | W2 | not built. These are W2's own Stage-0/2 items (W2-0b, E1/E2), not shared primitives |
| W3: session-state series | W3 | not built. This is W3-0a, W3's own Stage-0 item |

No primitive that any plan requires at Stage 1 is missing from the shared layer, so there is **no
stop-and-report**.

