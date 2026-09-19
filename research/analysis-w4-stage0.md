# W4 — Stage 0 measurement record (2026-09-19)

**Status:** MEASURED, not interpreted. Workstream **W4** ([#748](https://github.com/bennetwi92/small-cap-stack/issues/748)),
plan [`analysis-plan-4-joint.md`](./analysis-plan-4-joint.md) §5, protocol
[`analysis-protocol.md`](./analysis-protocol.md) as amended by §16. Ledger entries on
[#735](https://github.com/bennetwi92/small-cap-stack/issues/735): the free items, the
pre-registration of the eight charged trials, and their results — posted in that order.

This document transcribes the measurements so the **interpretation session** (protocol §13.4) can
read them without the gitignored JSON. It draws no conclusion. Harness:
`spikes/analysis_v1/w4.py` (`vix` · `free` · `charged`), which imports `stage0` and forks nothing.

⚠️ `C` is a **ceiling** — the oracle-exit R net of costs on a trigger-time-safe stream (amendment
P-1). It appears here only beside `J` and `κ`. A candidate freezes on `J`, never on `C` or `κ`.

**Trials:** 8 of W4's 120 spent (global 106 / 240). No stop-and-report is open. No null was run
(none is due at Stage 0). CHECK and HOLDOUT untouched.

---

## 1. Free items (W4-0a … W4-0f) — 0 trials

### W4-0a — the panel, by bytes
`sha256sum -c panel-v1.sha256`: **8 / 8 OK** against `research/panel-v1.sha256`. 508 sessions
(FIT 266 · CHECK 125 · HOLDOUT 62 recon + 55 live), 9,195 rows.

### W4-0b — `max_r` is stop-truncated (plan §3.2)
- `replay_bracket(target_r=None)` reproduces every row's `max_r` on all **6,740 / 6,740** FIT+CHECK
  rows that have a path: **0 mismatches** at 0.02 R.
- The walk is stop-armed by construction: `if lo[k] <= stop:` returns before the bar's high is
  folded into the excursion, on every bar including bar 0. Proven on a synthetic path (entry 10,
  stop 9; bar 0 high 10.5, bar 1 touches the stop, bar 2 rallies to 14): the walk returns
  **0.5 R, stopped, 1 bar held**. A raw path maximum would have reported 4 R.
- No raw path maximum is computed anywhere in `w4.py`, on any real row. W2's `R_max` is not read.

### W4-0c — the six state series (constraint 12: populated on both halves)

| series | construction | FIT | CHECK | HOLDOUT recon | HOLDOUT **live** |
|---|---|---|---|---|---|
| R1 breadth | prior-session distinct triggered symbols in panel-v1, 5-session mean | 261/266 | 125/125 | 62/62 | **55/55** |
| R2 attention | prior-session mean `hits_before_trigger`, 5-session mean (W3-A1's form; ρ = 0.385 to the all-day series, carried with that stated) | 261/266 | 125/125 | 62/62 | **55/55** |
| R3 VIX | prior VIX close | 266/266 | 125/125 | 62/62 | **55/55** |
| R3b VIX Δ5 | prior close minus the close 5 VIX days earlier (reported, not an axis) | 266/266 | 125/125 | 62/62 | **55/55** |

The 5 unpopulated FIT sessions are the warm-up; an undefined state **trades** (W3's convention).
A-R5 / A-R6 are A-R1 / A-R3 with the stand-aside side reversed. A-R4 (trailing 20-trade net R of
the stream) reads outcomes: it is built in the charged stage on FIT+CHECK, and on HOLDOUT it is
computable by the custodian only (W3's precedent). **Nothing dropped; no trials returned.**

**VIX source.** Rebuilt from CBOE's public daily history
(`cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv`, sha256
`d385cf23df800eb39bba2f68a0f5ee7a1d6cbcb87234b7b3aab48a1b0b8455ad`, last row 2026-09-18), cached
at `data/spikes/vix_daily_w4.parquet` (gitignored; `w4.py vix` regenerates it). It reproduces W3's
cuts exactly (24.13 / 20.87 / 19.27; FIT mean 18.79 vs W3's 18.78).

### W4-0d — throughput calibration on FIT (band [0.45, 1.0], amendment P-2)

Selection thresholds are FIT quantiles keeping 20 / 10 / 5 % of rows (protocol §5.3):

| axis | column | op | q80 threshold → trades/session | q90 | q95 |
|---|---|---|---|---|---|
| A-S1 | `hits_before_trigger` | ≥ | 25 → 0.947 | 35 → 0.816 | 46 → 0.515 |
| A-S2 | `cum_dollar_vol_pre_trigger` | ≥ | 15,802,035 → 0.932 | 27,166,815 → 0.748 | 43,653,285 → 0.519 |
| A-S3 | `retracement` | ≤ | 0.5714 → 0.970 | 0.44755 → 0.793 | 0.3636 → 0.560 |
| A-S4 | `stop_pct` | ≥ | 0.10329 → 0.910 | 0.14265 → 0.707 | 0.18445 → 0.455 |
| A-S5 | `ext_at_trigger` | ≤ | −0.00008 → 0.914 | −0.06216 → 0.722 | −0.11988 → 0.466 |

State-gate cuts (FIT quantiles from the declared tail) and the realised stand-aside share of FIT
sessions:

| gate | side | 10 % | 20 % | 30 % |
|---|---|---|---|---|
| A-R1 breadth | stand aside below | 8.0 (7.9 %) | 9.2 (18.4 %) | 11.0 (29.3 %) |
| A-R2 attention | below | 13.371 (9.8 %) | 14.185 (19.5 %) | 14.766 (29.3 %) |
| A-R3 VIX | above | 24.13 (10.2 %) | 20.87 (19.9 %) | 19.27 (30.1 %) |
| A-R5 breadth reversed | above | 20.8 (9.8 %) | 17.6 (19.2 %) | 16.0 (29.3 %) |
| A-R6 VIX reversed | below | 14.775 (10.2 %) | 15.37 (19.5 %) | 16.03 (30.1 %) |

**Joint grid: 270 points (15 selection levels × 18 state gates incl. A-R4 nominal), 201 in band,
69 dropped before Stage 1.** Every dropped point is a **q95** level crossed with a stand-aside of
20 % or deeper, or 10 % for A-S4/A-S5, which sit at the floor alone. Nothing at q80 or q90 is
dropped. A-R4's pair throughput is nominal (selection throughput × (1 − fraction)) until A-R4 is
built; it is re-measured then. The full matrix and the dropped list are in `w4-stage0-free.json`.

### W4-0e — feature–feature correlation on FIT (Pearson / Spearman)

Selection columns, over rows: `stop_pct|ext_at_trigger` 0.43 / 0.52; `hits|dollar_vol` 0.30 /
0.60; `retracement|ext_at_trigger` −0.09 / −0.51; `dollar_vol|ext_at_trigger` 0.21 / 0.30; every
other pair |ρ| ≤ 0.23. State series, over sessions: `R1|R3` −0.42 / −0.45; `R3|R3b` 0.44 / 0.28;
`R1|R2` −0.16; the rest |ρ| < 0.15. **No pair exceeds |ρ| = 0.9. Nothing dropped.**

### W4-0f — drift (flagged for the freeze report; never used to drop an axis)

Share of rows a **FIT** threshold keeps on each block:

| column | level | FIT | CHECK | HOLDOUT recon | HOLDOUT live |
|---|---|---|---|---|---|
| `hits_before_trigger` | q90 | 10.8 % | 10.3 % | 12.8 % | 12.8 % |
| `cum_dollar_vol_pre_trigger` | q90 | 10.0 % | 11.4 % | **17.7 %** | 8.7 % |
| `retracement` | q90 | 10.0 % | 8.5 % | 10.9 % | 7.7 % |
| `stop_pct` | q90 | 10.0 % | 8.8 % | **14.5 %** | 11.4 % |
| `ext_at_trigger` | q90 | 10.0 % | 13.3 % | 11.1 % | **18.0 %** |

State means: R1 breadth 14.1 / 14.6 / 17.4 / 17.6 (FIT / CHECK / HOLDOUT recon / live); R2
attention 16.0 / 15.3 / 17.2 / 17.0; R3 VIX 18.8 / 19.0 / 18.5 / 16.2.

---

## 2. The charged trials (S4-0g … S4-0k) — 8 trials

**Definitions, as pre-registered.** `pool_i = max(max_r_i, −1) − c_i`; `max_r` is the panel's
stop-truncated value; `c_i` is the oracle exit priced by `stage0.price_trade` at $500 full buying
power as **one non-limit leg at `entry + r·risk`** (both commissions plus 2-tick slippage on the
exit — the `c_trail` floor). An unaffordable row contributes 0 and is counted.
`C = Σ pool_i / |Σ|` over every session in the block; a session with no trade contributes 0.

### S4-0g — the stop-truncated `max_r` over all 4,516 FIT rows (1 trial)

| mean | median | share ≤ 0 | share ≥ 1 R | share ≥ 2 R | top-decile rows' share of Σ max(max_r, 0) |
|---|---|---|---|---|---|
| 1.885 | 0.500 | 29.5 % | 38.0 % | 23.8 % | **59.4 %** (uniform: 10 %) |

Deciles (0 → 100 %): −0.571 · 0.000 · 0.000 · 0.026 · 0.250 · 0.500 · 0.865 · 1.453 · 2.500 ·
4.878 · 136.485. Per-row pool: mean 1.613 R, sd 4.719 R. 0 rows unaffordable.

### S4-0h and S4-0i — the ceilings (1 trial each)

| stream | `C` net ceiling / session | gross ceiling / session | mean cost / trade | trades | pool sd / trade | pool se | trades with pool ≤ 0 | top-decile sessions' share of positive pool | `stop_pct` q10 / q50 / q90 |
|---|---|---|---|---|---|---|---|---|---|
| **unfiltered N = 1** (`C_raw`) | **+2.1152 R** | 2.3330 | 0.2177 R | 266 (1.000/s) | 7.032 | 0.431 | 31.6 % | 66.4 % | 2.27 / 6.69 / 15.49 % |
| **Filter A** (`C_A`, reference only) | **+2.1699 R** | 2.3517 | 0.1818 R | 266 (1.000/s) | 5.380 | 0.330 | 29.3 % | 60.2 % | 2.29 / 6.62 / 14.65 % |

Per-trade pool deciles — unfiltered: −0.872 · −0.306 · −0.147 · −0.016 · 0.182 · 0.313 · 0.752 ·
1.211 · 2.635 · 4.771 · **83.162**. Filter A: −0.973 · −0.230 · −0.080 · 0.022 · 0.245 · 0.442 ·
0.959 · 1.730 · 3.269 · 5.658 · 51.924. On the unfiltered stream the single largest trade
contributes 83.16 R = 0.313 R/session of the 2.115; without it `C_raw` = 1.803 (arithmetic on the
charged numbers, not a new trial).

### S4-0j — `κ` of F1–F4 on the unfiltered N = 1 stream (4 trials)

| family | `J` net R / session | gross R / session | **`κ = J / C_raw`** | net R / trade sd | losing 20-session blocks (of 13) | deepest DD (R) | end equity from $500 |
|---|---|---|---|---|---|---|---|
| F1 scalp 0.5 R | −0.3880 | −0.2331 | **−0.183** | 0.821 | 13 | −103.6 | −$3,216 |
| F2 base 2 R | −0.4379 | −0.2470 | **−0.207** | 1.363 | 11 | −117.4 | −$3,524 |
| F3 runner | −0.4540 | −0.2363 | **−0.215** | 1.467 | 9 | −125.4 | −$3,129 |
| F4 hybrid | −0.5491 | −0.3506 | **−0.260** | 0.873 | 13 | −146.6 | −$4,845 |

All four: 266 trades, 1.000/session, 0 unaffordable. Session net-R deciles (0 → 100 %):
- F1: −1.842 · −1.336 · −1.210 · −1.146 · −1.067 · 0.128 · 0.364 · 0.412 · 0.445 · 0.467 · 0.490
- F2: −1.842 · −1.395 · −1.296 · −1.228 · −1.177 · −1.128 · −1.077 · −1.036 · 1.864 · 1.948 · 1.990
- F3: −1.842 · −1.377 · −1.259 · −1.198 · −1.135 · −1.077 · −1.031 · −0.303 · 0.252 · 1.093 · 9.642
- F4: −1.842 · −1.371 · −1.241 · −1.187 · −1.126 · −1.066 · −1.018 · 0.285 · 0.401 · 0.478 · 2.588

Best-decile share of total is negative for every family because the totals are; end equity is
below $0 because sizing is fixed at $500 — both S0-H caveats apply unchanged.

### S4-0k — `sd(pool_i)` and the MDE on `C` (1 trial)

α = 0.01 two-sided, 80 % power, sd = 7.032 per trade on the N = 1 stream:

| FIT trades | 266 (1.00/session) | 221 (0.83) | 154 (0.58) | 120 (0.45) |
|---|---|---|---|---|
| MDE on `C`, per trade | **1.474 R** | 1.617 R | 1.937 R | 2.194 R |

Per-session MDE at the realised 1.000 trades/session: **1.474 R**. `|C_raw|` = 2.115 R, 4.9 se from
zero. For reference, sd over all 4,516 FIT rows is 4.719.

---

## 3. Gates and the fork — mechanical readings only

| gate / rule | inputs | reading |
|---|---|---|
| **G1** (plan §5.3): `C_raw ≤ 0` **and** top-decile share < 0.25 | `C_raw` = +2.115; share = 0.594 | **does not fire** |
| **Power cancellation** (plan §7.3): per-session MDE on `C` > `|C_raw|` | 1.474 vs 2.115 | **does not fire** |
| **Stage-0 exit gate** (plan §5.3) | W4-0a–0f ledgered; 8 trials posted; power table recomputed; 5 selection + 6 state axes ≥ 4 + 4; 201 joint points ≥ 24 | **met on the measurement side** |
| **The fork** (plan §5.2) | `C_raw` > 0; κ = −0.183 / −0.207 / −0.215 / −0.260 (F1 / F2 / F3 / F4) | **row not read here** — the plan gives no numeric "low / high" κ threshold; the interpretation session reads it |

---

## 4. Starting prompt for the interpretation session

> Copy from here down.

You are the **W4 Stage-0 interpretation session** (protocol §13.4 — you interpret; you do not
measure). Read, in order: `research/analysis-protocol.md` §16, `research/analysis-plan-4-joint.md`
§3, §5, §6, §7, and this document in full. Then the three W4 comments on ledger #735 dated
2026-09-19. Work blind exactly as plan §13 says.

Your deliverables, all posted to #735 before any Stage-1 trial is scored:

1. **Read the §5.2 fork.** `C_raw` > 0 and every `κ` < 0. Decide which row that is, and say why,
   with the heavy-tail facts in §2 in view (sd 7.0 per trade; the top decile of sessions holds
   66 % of the positive pool; one trade is 83 R; the median per-trade pool is 0.31 R). If the row
   is *capture*, ledger the budget amendment toward Stage B **before** Stage 1 and state what
   Stage A still owes. If you find that the "> 0 / high κ" row's stop-and-report applies, say so
   and stop.
2. **Confirm or amend the Stage-0 exit gate** and the recomputed power table (§2, S4-0k). State
   plainly what size of cell effect on `C` this record can resolve.
3. **Pre-register Stage A** (plan §7.1 A1–A5, §6.3 axes and directions, §6.4 leak block, §8
   nulls N-A / N-B / N-AB with A-R4 rebuilt in every replicate, B = 200, seed 20260919), with the
   throughput-calibrated grid from §1 W4-0d (201 in-band points; the 69 dropped are named in
   `w4-stage0-free.json`) and the drift flags carried into the freeze report.

You do not run any trial. You do not touch CHECK or HOLDOUT. Measurement resumes in a fresh
session from your ledger entries.

> Copy to here.
