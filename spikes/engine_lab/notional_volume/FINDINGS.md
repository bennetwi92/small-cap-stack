# Notional (dollar) volume on the pole candle — findings (#719, Refs #690)

**Verdict: `ARTEFACT`.** Confidence **high**.

Neither candidate absolute-magnitude feature improves on `SHIPPED` or `SHIPPED +
shares_outstanding<=50e6` (D-45) at any threshold tested. On the stronger base
(`SHIPPED+shares_outstanding<=50e6`), both features are **monotonically harmful** — every
threshold makes net R/trade worse than no threshold at all. On `SHIPPED` alone, the single best
cell nudges net R/trade from -0.070 to -0.057 — still net-negative, not a plateau under
sensitivity, and still net-negative in aggregate under walk-forward.

Re-run: `.venv/bin/python spikes/engine_lab/notional_volume/step1_characterise.py`, then
`step2_additive.py`, then `step3_battery.py`. Outputs land in
`data/spikes/engine-lab/notional_volume/`.

⚠️ **The panel has grown since #690/#719 were scoped.** `common.load_panel()` currently returns
7,672 rows over 414 sessions (dev 2024-12-30→2026-04-30, 334 sessions; val 977 rows/41 sessions;
holdout 2026-07-01→2026-08-25, 858 rows/39 sessions) — not the 3,639 rows/197 sessions the
`engine_lab/README.md` describes. This spike ran against the panel as it exists today, per the
brief ("reuse `common.py`, do not reimplement"); the split boundaries (`DEV_END`/`VAL_END`) and
the "do not touch HOLDOUT" rule are unchanged, so the discipline held, but any comparison to
`SHIPPED`'s +11.0R/+0.9R numbers quoted in the README no longer applies to this larger population
— `common.baseline()` on the current panel is net **-20.0R** over 234 trades. This is a fact about
the shared harness's data, not something specific to this feature.

⚠️ **The "must work on both halves" (recon/live) check could not be run.** In the current panel,
`source=live` exists **only** inside HOLDOUT (2026-07-01 onward) — every DEV and VAL row is
`recon`. There is no way to check this feature (or any feature) against real live data without
touching HOLDOUT, which the brief forbids. All numbers below are recon-only; "works on live" is
untested by design, not overlooked.

---

## Feature construction

- `pole_dollar_volume = pole_volume * breakout_level`. There is no pole-close price column in the
  panel; `breakout_level` (the last consolidation candle's high) is used as a proxy. It is
  trigger-time-safe and structurally adjacent to the pole, but it is **not** the pole candle's own
  price — this is an approximation, and probably biases the dollar figure down for names that ran
  further between the pole and the breakout level.
- `cum_dollar_vol_to_trigger` — already first-class and trigger-time-safe in the panel (cumulative
  pre-market dollar volume to trigger). Used as-is, no proxy needed.
- `assert_no_lookahead(["pole_volume", "breakout_level", "cum_dollar_vol_to_trigger"])` passes —
  both candidates are built only from trigger-time-safe inputs.
- The two candidates correlate **0.82** with each other (largely redundant) and weakly with
  `rvol_pole` (0.14 and 0.10) — they are testing a different axis from the retired ratio feature,
  as intended, they just don't carry independent signal either.

## Step 1 — characterisation (DEV+VAL, row-level, not booked; n=6,814 / 375 sessions, all recon)

Both features are **weakly monotone** on the 50%-move rate, much weaker on the 2R rate, and
monotone-but-still-deeply-negative on mean net R/trade:

| feature | Q1 (n≈1363) | Q3 | Q5 |
|---|---|---|---|
| `pole_dollar_volume` — 50%-move rate | 4.3% | 5.1% | 9.0% |
| `pole_dollar_volume` — 2R rate | 20.8% | 25.7% | 23.0% |
| `pole_dollar_volume` — net R/trade | -0.778 | -0.491 | -0.478 |
| `cum_dollar_vol_to_trigger` — 50%-move rate | 5.0% | 4.9% | 8.7% |
| `cum_dollar_vol_to_trigger` — 2R rate | 22.2% | 24.9% | 22.9% |
| `cum_dollar_vol_to_trigger` — net R/trade | -0.718 | -0.523 | -0.504 |

Every quintile of the raw pool is net-negative — the 50%-move trend echoes `runup_pre_appearance`'s
shape from #690/#708, but here it never reaches the sign the shipped book needs, on its own or
across the whole distribution.

## Step 2 — additive test (`build_book`/`score`, 2/day, DEV+VAL, all recon)

| base | + feature @ threshold | trades | net R/trade |
|---|---|---|---|
| SHIPPED alone | — | 201 | **-0.070** |
| SHIPPED | + `cum_dollar_vol_to_trigger>=12e6` (best cell) | 144 | -0.057 |
| SHIPPED | + `cum_dollar_vol_to_trigger>=30e6` | 87 | -0.173 (worst) |
| SHIPPED | + `pole_dollar_volume>=3e6..8e6` | 60–166 | -0.085 to -0.091 |
| **SHIPPED+shares_outstanding<=50e6** | — | **97** | **+0.150** |
| SHIPPED+SO | + `cum_dollar_vol_to_trigger>=2e6` | 94 | +0.128 |
| SHIPPED+SO | + `cum_dollar_vol_to_trigger>=30e6` | 46 | +0.044 (worst) |
| SHIPPED+SO | + `pole_dollar_volume>=500k` | 96 | +0.132 |
| SHIPPED+SO | + `pole_dollar_volume>=5e6` | 72 | +0.008 (worst) |

Full 7-point grids for both features on both bases in
`data/spikes/engine-lab/notional_volume/step2_additive.json`. Every single added threshold on the
stronger base makes it worse — there is no cell anywhere in either grid that beats
`SHIPPED+shares_outstanding<=50e6` on its own.

## Step 3 — anti-overfitting battery (on the one improving cell: SHIPPED + `cum_dollar_vol_to_trigger>=12e6`)

- **Complexity budget:** 1 new threshold on top of the 6 already in `SHIPPED`. Well inside the
  ≤5-threshold budget, but it isn't a positive result to spend the budget on (see below).
- **Walk-forward** (6 expanding blocks, min 60 training sessions): baseline (`SHIPPED`) totals
  **-4.92R** over the 6 test blocks, 3/6 positive. Adding the filter narrows the loss to
  **-1.26R**, 4/6 positive — a real reduction in how bad the book is, but still net-negative in
  aggregate, not the "consistent positive return" bar this lab is testing against.
- **Sensitivity** (±20% on the one threshold, full DEV+VAL population): 9.6M → net R/trade
  **-0.1025**; 14.4M → **-0.0783**. Both worse than the unfiltered `SHIPPED` baseline (-0.070) —
  neither neighbour beats doing nothing, let alone the nominal 12M value (-0.057). Not a plateau.
- **Permutation** (500 draws, same day-count, random rows from the day's full pool): **p = 0.008**
  on the 144-trade book. This says the selection is *not* indistinguishable from a random subset of
  the day's rows — but the book it validates is still net-negative, so significance here says "this
  isn't noise", not "this is profitable". A low p-value on a losing book is not evidence for the
  feature.

## What would change my mind

- A pole-close price column (rather than the `breakout_level` proxy) might sharpen
  `pole_dollar_volume` enough to separate it from `cum_dollar_vol_to_trigger` (currently 0.82
  correlated) — worth one more look if that column ever lands in the panel, but it is unlikely to
  flip the sign given `cum_dollar_vol_to_trigger` (no proxy, first-class) does no better.
- A live-sourced DEV/VAL sample, so the recon-only ceiling on this analysis could be tested
  against the real one. Not available without spending HOLDOUT, which this spike did not do.
- A threshold that improved **both** bases, not just the weaker one at the cost of the stronger
  one. Nothing in either 7-point grid did that.

## Strongest evidence for (there isn't much)

- The characterisation quintiles are directionally consistent with the earlier `runup_pre_appearance`
  finding (#690/#708): dollar volume, like relative volume, rises with the 50%-move rate. The
  signal exists in the same shape as `rvol_pole`'s did — it's just as absent once the shipped
  gates are already selecting for it.

## Strongest evidence against

- The additive test on the stronger, already-shipped base (`SHIPPED+shares_outstanding<=50e6`) is
  monotonically harmful at **every** threshold of **both** features — the cleanest possible
  negative result the harness can produce.
- The one cell that improved anything (`SHIPPED` alone + `cum_dollar_vol_to_trigger>=12e6`) stays
  net-negative in-sample, net-negative under walk-forward, and does not sit on a sensitivity
  plateau.
