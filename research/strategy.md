# The strategy — canonical spec

**This file is the single source of truth for what the system does.** Where any other document
disagrees with it, this file wins; where this file disagrees with the code, this file is broken and
CI will say so.

Everything in the tables below is **generated from `src/small_cap_stack/config.py`** by
`make strategy`. Do not edit inside the generated markers — change `Settings` and regenerate.
`tests/test_strategy_doc.py` fails when the committed block drifts from the code, so a rule can
only be wrong here for as long as it takes CI to notice.

- **This file = the state.** What the rules are, right now.
- **[`decisions.md`](./decisions.md) = the log.** Why each rule is what it is, and when it changed.
- **[`bull-flag.md`](./bull-flag.md) = the *what*** of the setup · **[`engine-v2.md`](./engine-v2.md)
  = the *how*** of the detector. Both are narrative; both defer to this file on numbers.

---

## Three stages, and each owns one question

Most of the drift this file exists to end came from one habit: saying "the strategy" for three
different rule sets that happen to run in sequence. Each stage owns exactly one question, and a
rule belongs to whichever question it answers.

```
IBKR scan  ──►  every row becomes  ──►  ENGINE: is this a trade  ──►  BOOK: what do I do
   §1          an opportunity           we would take?                with the ones it picked?
                (no filter)                    §2                              §3

           "what do we SEE"          "what would we SELECT"          "how do we EXECUTE"
```

1. **The scan — what we see.** Deliberately wide: Phase 1 is a data-collection exercise, so the net
   is cast well beyond what is tradeable.
2. **The engine — what we'd select.** Whether a seen name formed a bull flag *we would take*: the
   shape gates, and the two **selection** rules (price band, trigger-time window). Runs
   **compute-on-read** over the whole day's bars after the close — nothing is decided live.
3. **The book — how we'd execute.** Given the setups the engine selected, what a $500 cash account
   does with them: capacity, sizing, exits, costs.

**The dividing line is selection vs execution.** Before #567 it was arbitrary — the price band and
the trigger window sat in the book under a `portfolio_` prefix, so the code implied they were
execution rules when they decide selection. They now live with the shape gates. Conversely the
2-trades-a-day cap stays in the book: it is a capacity constraint falling out of settled cash
(`position_fraction × max_trades_per_day = 1.0`), not a judgement about the setup.

**`passed` and `takeable` are different questions, on purpose.** `passed` means the bull flag is
well-formed — the shape grammar the review workbench and the 25 golden fixtures are written
against. `takeable` means *and it is one we'd select*. A $1.50 name or an 11:00 break can be a
textbook flag we simply don't trade; it stays visible and scoreable on the results page, which is
exactly what a data-collection phase needs. Folding selection into `passed` would report it as a
malformed setup and throw the observation away.

So a name can be captured, charted, scored and published and still never reach the book. That is
not an inconsistency — the three stages answer different questions.

`capture.on_scan_tick` opens an opportunity for **every** scanner candidate. There is no filter
between §1 and §2.

---

<!-- BEGIN GENERATED — edit config.py, then run `make strategy` -->

### 1. The scan universe — what IBKR returns

| Rule | Value | `Settings` field |
|---|---|---|
| Price | $1.00 – $50.00 | `scan_min_price / scan_max_price` |
| Today's change | > 10% | `scan_change_pct` |
| Trailing 5-min volume | > 100,000 (native `stVolume5minAbove`) | `scan_min_5m_volume` |
| Stock types excluded | ETF, ETN | `scan_exclude_stock_types` |
| Scan code | `TOP_PERC_GAIN` @ `STK.US.MAJOR` | `scan_code / scan_location` |
| Rows per tick | 50 (IBKR hard cap) | `scan_max_rows` |
| Scan window | 04:00 ET – 11:59 ET | `scan_start / scan_end` |
| Tick cadence | every 60s | `tick_interval_sec` |

### 2. The engine — what counts as a setup

| Rule | Value | `Settings` field |
|---|---|---|
| Pole | ≤ 4 higher highs | `bull_flag_max_pole` |
| Pole minimum move | ≥ 2% | `bull_flag_min_pole_pct` |
| Consolidation | ≤ 4 candles | `bull_flag_max_cons` |
| Retracement | ≤ 50% of the pole | `bull_flag_max_retracement` |
| Peak upper wick | ≤ 50% of the bar's range | `bull_flag_max_peak_wick` |
| Peak colour | must close green | — the `peak_green` gate |
| Peak volume | > the consolidation's highest bar | — the `vol_peak_gt_cons` gate |
| Consolidation low | > the pole base | — the `cons_holds_base` gate |
| Trigger (decides *when*) | last consolidation high + 1 tick ($0.01) | `bull_flag_trigger_offset_ticks` |
| Fill (R is measured here) | last consolidation high + 3 ticks ($0.03) | `bull_flag_fill_offset_ticks` |
| Stop | the consolidation low | — `R = fill − stop` |
| Staleness | the trigger bar must open ≤ 30 min after the first scanner hit | `entry_staleness_min` |
| Gap pole | disabled — a pole needs a higher high into its peak | `bull_flag_gap_pole` |
| Exhaustion | reject the 3rd+ contiguous cycle of the day | `bull_flag_exhaustion_cap` |
| Cycle volume floor | 50,000 (any bar in the cycle, pole or fade) | `scan_min_5m_volume` // 2 |
| Tick size | $0.01 | `tick_size` |
| ATR window | 14 bars (score only, gates nothing) | `bull_flag_atr_window` |
| **Selection** — price band | $2.00 ≤ `entry_fill` ≤ $20.00 | `select_price_min / select_price_max` |
| **Selection** — trigger window | 04:00 ET ≤ trigger open < 09:15 ET | `select_window_start / select_window_end` |

### 3. The book — what actually gets traded

| Rule | Value | `Settings` field |
|---|---|---|
| Starting equity | $500.00 | `portfolio_start_equity_usd` |
| Trades per day | 2, taken first-by-trigger-time | `portfolio_max_trades_per_day` |
| Risk target | 5% of the day's opening equity | `portfolio_risk_fraction` |
| Notional cap | 50% of the day's opening equity | `portfolio_position_fraction` |
| Exit target | 2R fallback | `portfolio_target_r` |
| Adaptive target | grid 1.5R, 2R, 2.5R, 3R, fit over all history, ≥ 8 prior trades, 1σ paired margin to switch | `portfolio_target_grid / _adaptive_* / _target_switch_z` |
| Breakeven arm | disabled | `portfolio_breakeven_r` |
| Risk throttle | off (flat risk) | `portfolio_risk_rungs / _risk_step_days` |
| Exit slippage | 2 ticks ($0.02) on stop / close exits, 0 on the limit target | `portfolio_exit_slippage_ticks` |
| Excluded symbols | CCUP, CRCG, OKLL, SNDQ | `portfolio_exclude_symbols` |

### 4. Collected, never gated

| Collected | Where it goes | Does it filter? |
|---|---|---|
| Float (`float_max_shares` = 20,000,000) | `fundamentals` dataset; the EOD report's `float_ok` **count**; the results/portfolio pages as context | **No.** `gates.py::float_gate` has one caller, `report.py` |
| News (`has_recent_news`) | `news` dataset; the EOD report's `with_recent_news` **count** | **No.** `gates.py::news_gate` has the same single caller |
| Short interest | not collected in Phase 1 | **No.** No source is wired |
| Quality score (0–1) | published on the results page and the inspector | **No.** It ranks passing setups; it never rejects one |

<!-- END GENERATED -->

---

## Where each rule lives

| Section | Implemented in |
|---|---|
| §1 Scan | `scanner.py::build_subscription` — the only place a scan is defined |
| §2 Engine | `bullflag/day.py::detect_day` (segmentation + levels + the selection rules, via `DaySetup.takeable`), `bullflag/gates.py::evaluate` (the shape gates, via `passed`), `rmetrics.py::compute_r_metrics` (R, MAE, stop-first) |
| §3 Book | `portfolio/sim.py::_select_day` (the daily cap), `portfolio/costs.py` (sizing), `portfolio/exit.py` (exits), `portfolio/ledgers.py` (costs and tax). `extract.py::_qualify` no longer selects — it only checks the numbers are usable. |
| §4 Not gated | `gates.py::float_gate` / `news_gate` — report counts only |

`config.py` is the single source of truth for the values. A knob that isn't threaded through
`detect_day_with_settings` does nothing; `tests/test_settings_wiring.py` is the guard.

## Conventions the numbers assume

- **R is measured against the conservative fill, not the trigger.** The trigger decides *when* a
  setup fires; R is deliberately measured from a worse price so Phase 1 never overstates the edge.
- **Stop-first, intrabar.** If a bar breaches the stop, the trade is treated as closed at the stop
  on that bar — its high is not credited and no later bar is measured.
- **Gap-through.** A fill is never better than the trigger bar's open.
- **The fill is not checked against the bar's high.** The trigger fires on a 1-tick break, but R is
  measured from the 3-tick fill — so when the trigger bar's high lands between the two, the book
  records a fill *above that bar's entire range*, at a price it never printed. This is deliberate
  and it never flatters the strategy: a higher entry means wider risk, a smaller position and a
  worse R. It is the extreme of the conservative-fill rule, not an exception to it (#555).
- **The analysis window ends at the regular close**, so R is measured over the full session even
  though entries are pre-market only.
- **Store raw, compute derived on read.** Changing any rule above replays the entire history on the
  next publish; there is no stored state to migrate.

## What this file deliberately does not cover

- **Phase 2/3 execution** — order types, the app-side stop, the limit-through parameter. Nothing
  here places an order. See [`phase-2-roadmap.md`](./phase-2-roadmap.md).
- **The Open Drive strategy** ([`open-drive.md`](./open-drive.md)) — specified, **not traded**, and
  deliberately not merged into the book. Its 09:40 trigger falls outside §3's window by
  construction, so the two strategies' selection rules cannot both fire on one name.
- **The recon book.** Vendor-reconstructed sessions run the same §1–§3 rules over a separate store
  and are published as `books_all`, never merged into the live `books`.

---

_Established by the 2026-08-07 strategy-drift audit (#551). Before it, these rules were stated
across seven surfaces with four live price bands and a float gate that had never run._
