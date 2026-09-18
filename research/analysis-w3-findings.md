# W3 findings — time structure and the re-tuning protocol

**Status:** LIVE (2026-09-18). Stage-3 output of workstream **W3** of the three-agent analysis,
executed against [`analysis-plan-3-regime.md`](./analysis-plan-3-regime.md) and
[`analysis-protocol.md`](./analysis-protocol.md). Ledger: [#735](https://github.com/bennetwi92/small-cap-stack/issues/735).
Findings issue: [#739](https://github.com/bennetwi92/small-cap-stack/issues/739).

> **The result in one line: a documented null on the timing overlay, and a measured tuning
> protocol that ships regardless.** Nothing about *when to be trading* is frozen. The
> re-tuning question got an answer, and it is not the answer the project expected.

**Trials: 18 of 24 spent. 6 returned to the global budget of 120.**

---

## 1. What was asked, and against what

> Does the post-selection trade stream have exploitable time structure — can a session-level,
> trigger-time-decidable state variable tell you to stand aside — and what re-fitting cadence does
> this record actually support?

**Population:** the Filter-A stream (protocol §9) at its frozen constant
`cum_dollar_vol_pre_trigger ≥ 4,857,910.10`, capacity 1 earliest-by-time per session, scored under
**F2** (2 R bracket), **net** at $500 full buying power with 2-tick slippage. Filter A was never
tuned; W1's filter was never used; W1's and W2's intermediates were never read.

**The reference, and the only thing anything is compared against — the ungated Filter-A baseline on
FIT** (S0-H, `panel-v1-spec.md` §7, reproduced by W3 to four decimals as a check that the two
sessions hold the same ruler):

| | |
|---|---|
| J | **−0.2994** net R/session |
| gross | −0.1450 R/session |
| trades | 266 over 266 sessions (1.000/session) |
| net R/trade sd | 1.4076 |
| deepest drawdown | −82.4 R |
| permutation null | p = 0.0199 |
| minimum detectable effect at W3's allocation (S0-J) | **0.378 R/trade** |

⚠️ **The baseline is negative, and that is the whole difficulty of this workstream.** Read §3 before
reading any J in this document.

---

## 2. Stage 0 — what the record allowed before anything was scored

**Panel verified by bytes** against `panel-v1-spec.md` §1 (`panel-v1.parquet`, `sessions-v1.parquet`,
`paths-v1.parquet`), all three matching character for character. Never rebuilt.

**Four session-state features survived** W3-0a's both-halves coverage check and W3-0c's correlation
screen, against a floor of three:

| id | feature | populated: FIT / CHECK / HOLDOUT recon / HOLDOUT **live** |
|---|---|---|
| R1 | prior-session breadth, 5-session mean | 261/266 · 125/125 · 62/62 · **55/55** |
| R2 | prior-session pre-trigger attention per setup, 5-session mean | 261/266 · 125/125 · 62/62 · **55/55** |
| R3 | prior VIX close | 266/266 · 125/125 · 62/62 · **55/55** |
| R4 | trailing 20-trade net R of the stream | FIT+CHECK; custodian-only on HOLDOUT (it reads outcomes) |

The 5 unpopulated FIT sessions are the 5-session warm-up. **A session whose feature is undefined
trades** — the ungated default. Standing aside on an unevaluated session is the cheapest way for a
gate to flatter itself.

**Amendment W3-A1 — R1 and R2 are built from panel-v1's own rows, not the raw spine.** The published
raw tapes are recon-only and FIT+CHECK-bounded (amendment A4), and protocol §13.3 forbids exporting
any date ≥ 2026-04-01 — so a spine-derived feature can be computed but its live-half population can
only be *asserted*, never *verified*, which is the one thing W3-0a exists to prevent. Against the raw
tapes on the 391 sessions where both exist:

| panel-derived | vs raw spine | Pearson | Spearman |
|---|---|---|---|
| distinct triggered symbols | distinct opportunities | **0.988** | 0.984 |
| mean `hits_before_trigger` | hits per opportunity, all day | **0.385** | 0.377 |

**R1 is the plan's feature in all but name. R2 is not** — ⚠️ **W3 did not test plan §5.1's R2 as
written.** It tested pre-trigger attention intensity, a related but distinct hypothesis, and no
result here is evidence about the all-day attention series.

**W3-0b flagged, not dropped:** R1 breadth shifts up between FIT (mean 14.11) and HOLDOUT (17.36
recon / 17.60 live), so a FIT quantile of R1 stands aside on fewer holdout sessions than on FIT. VIX
drifts down (18.78 → 16.23 live). R2 is stable, and its live and recon means agree within 1.3 % —
the first evidence that the reconstructed and observed attention series measure the same thing.

**W3-0d census — 100 %.** Every session on FIT, CHECK and HOLDOUT produces a Filter-A trade
(1.000 trades/session), against a 60 % floor and a 40 % cancellation threshold. This had a
consequence that constrained the grid **before any outcome was read**: at 1.000 trades/session the
[0.6, 1.0] throughput band permits standing aside on **at most 40 % of sessions**, so a q50 gate
(0.50 trades/session) and a q70 gate (0.30) are disqualified by protocol §6 before their J exists.
The T1 grid is therefore stand-aside fractions of **10 / 20 / 30 %**. Plan §5.2 predicted exactly
this; the census is what made it decidable in advance.

**No cancellation condition fired:** four features ≥ three, census 100 % ≥ 40 %, and the recomputed
minimum detectable effect for F2 at W3's allocation is 0.378 R/trade, below the 0.6 R/trade bar.

---

## 3. T1 and T2 — the null, and why twelve improvements are not twelve findings

### T1 — twelve marginal stand-aside gates (12 trials)

Direction pre-declared per feature before the split was opened, and not searched.

| feature | stand aside | cut (FIT quantile) | J | **J − baseline** | trades/session | net R/trade |
|---|---|---|---|---|---|---|
| R1 breadth | 10 % | ≤ 8.0000 | −0.2976 | +0.0018 | 0.92 | −0.3231 |
| R1 breadth | 20 % | ≤ 9.2000 | −0.2686 | +0.0308 | 0.82 | −0.3293 |
| R1 breadth | 30 % | ≤ 11.0000 | −0.2284 | +0.0710 | 0.71 | −0.3231 |
| R2 attention | 10 % | ≤ 13.3707 | −0.2659 | +0.0335 | 0.90 | −0.2947 |
| R2 attention | 20 % | ≤ 14.1847 | −0.2320 | +0.0674 | 0.81 | −0.2884 |
| **R2 attention** | **30 %** | **≤ 14.7662** | **−0.1742** | **+0.1252** | 0.71 | −0.2464 |
| R3 VIX | 10 % | ≥ 24.1300 | −0.2824 | +0.0170 | 0.90 | −0.3143 |
| R3 VIX | 20 % | ≥ 20.8700 | −0.2700 | +0.0294 | 0.80 | −0.3372 |
| R3 VIX | 30 % | ≥ 19.2700 | −0.2078 | +0.0916 | 0.70 | −0.2972 |
| R4 stream | 10 % | ≤ −0.7663 | −0.2508 | +0.0486 | 0.91 | −0.2768 |
| R4 stream | 20 % | ≤ −0.5767 | −0.2017 | +0.0977 | 0.82 | −0.2472 |
| R4 stream | 30 % | ≤ −0.4246 | −0.1869 | +0.1125 | 0.72 | −0.2589 |

**Search null** — circular block permutation of the per-session net-R series against a fixed
session-state series, block 20 sessions, B = 200, the whole 12-point grid re-run against every
replicate:

| | |
|---|---|
| real best-of-search J | **−0.1742** |
| null best-of-search median | **−0.1740** |
| null best-of-search q95 | −0.1268 |
| **p** | **0.5025** |

### T2 — the conjunction of the two best features (4 trials)

Union gate on R2 attention × R4 stream — stand aside if **either** fires:

| fractions | cuts | J | J − baseline | trades/session | sessions aside |
|---|---|---|---|---|---|
| 10 % / 10 % | 13.3707 / −0.7663 | −0.2215 | +0.0779 | 0.812 | 50 |
| 10 % / 20 % | 13.3707 / −0.5767 | −0.1921 | +0.1073 | 0.748 | 67 |
| 20 % / 10 % | 14.1847 / −0.7663 | −0.1872 | +0.1122 | 0.722 | 74 |
| **20 % / 20 %** | 14.1847 / −0.5767 | **−0.1637** | +0.1357 | 0.673 | 87 |

**T1+T2 sequence null**, 16 points at matched intensity: real best **−0.1637**, null median
**−0.1694**, q95 −0.1180, **p = 0.4129**.

### §7.4 sensitivity on the best T1 gate — reported, never used to select

R2 attention at cut 14.7662, × 0.8 … 1.2:

| multiplier | cut | J | trades/session | sessions aside |
|---|---|---|---|---|
| 0.8 | 11.8130 | **−0.3064** | 0.977 | 6 |
| 0.9 | 13.2896 | −0.2741 | 0.910 | 24 |
| 1.0 | 14.7662 | −0.1742 | 0.707 | 78 |
| 1.1 | 16.2428 | −0.0766 | **0.440** | 149 |
| 1.2 | 17.7194 | −0.0360 | **0.218** | 208 |

J is a monotone function of how many sessions the rule removes, and of nothing else. At −20 % the
gate is **worse than not gating at all**; at +10 % and +20 % it is disqualified on throughput.

---

## 4. T4 — the refit cadence (2 trials)

Filter A's condition-3 constant, refit on a trailing window. The **trajectory is free** (a quantile
of a feature column reads no outcome); the two **J scores on FIT are the trials**. Frozen constant
4,857,910.10; ±20 % band [3,886,328 — 5,829,492].

| schedule | refit constants (millions) | spread | inside ±20 % band | **J on FIT** | vs frozen | picks differing |
|---|---|---|---|---|---|---|
| 125 sessions, quarterly | 4.858 · 4.782 · 4.614 · 4.559 · 4.893 · 5.801 · 5.480 · 5.335 · **6.115** | 32 % | **8 of 9** | −0.3005 | −0.0011 | **5 of 266** |
| 250 sessions, semiannual | 4.858 · 4.633 · 4.758 · 5.199 · 5.748 | 23 % | **5 of 5** | −0.3003 | −0.0009 | **4 of 266** |

Both schedules hold throughput at 1.000 trades/session. The one refit outside the band is the last
(2026-09-14, +25.9 %), computed on a window that is mostly holdout-window **feature** data; no
holdout outcome was read, and both J scores are FIT-only.

⚠️ **Amendment W3-A2 confounded the two axes.** The retained points differ on *both* window and
cadence, so T4 measures **whether the constant is stable at all**, not what drives its movement.

---

## 5. The period-outcome distribution (§6) — prior (1), measured

Free, from the S0-H baseline. FIT Filter-A stream, F2, 266 sessions.

**The shape.** Session net-R deciles: `[−1.773, −1.306, −1.240, −1.187, −1.130, −1.085, −1.052,
−1.025, 1.899, 1.962, 1.990]`. **76 of 266 sessions win.** The distribution is almost binary — a
session either loses about 1 R or makes about 2 R — which is what a 2 R bracket on a 29 % hit rate
looks like, and is exactly the burst shape prior (1) describes.

**Cold stretches.**

| window | windows | fraction summing negative | worst | median |
|---|---|---|---|---|
| 20 sessions | 247 | **81.8 %** | −19.34 R | −4.81 R |
| 40 sessions | 227 | **98.7 %** | −36.52 R | −10.23 R |
| 60 sessions | 207 | **99.0 %** | −45.43 R | −16.45 R |

Cumulative R is **underwater on 100 % of sessions** — it never revisits its starting peak — and the
deepest drawdown is −82.4 R. Longest run of consecutive losing 20-session blocks, of 13 blocks:
F1 13, **F2 4**, F3 9, F4 13.

**Top-decile share** is reported as −0.645 for F2 and is **not interpretable**, because the total it
divides is negative (`panel-v1-spec.md` §7 says the same). On a losing stream the statistic prior (1)
asked for cannot be computed; that is a fact about the stream, not a gap in the measurement.

**Serial dependence — the direct test.**

| test | value | verdict |
|---|---|---|
| autocorrelation of session net R, lags 1…8 | −0.012, −0.063, +0.036, +0.003, −0.086, +0.070, +0.042, +0.042 | indistinguishable from zero |
| Ljung–Box Q(20) | **12.92** vs χ²₀.₉₅(20) = 31.41 | independence **not rejected** |
| Wald–Wolfowitz runs | 111 runs vs 109.57 expected, z = **0.215** | no clustering |

For contrast, the *features* are strongly serially dependent on FIT (R1 lag-1 0.972, R3 0.904,
R2 0.854). **The state persists; the outcomes do not.** That gap is the finding.

---

## 6. The verdict

*(§6.1–§6.4 below are the interpretation step, run in a separate session per protocol §13.4.)*

---

## 7. The tuning protocol (§9.2) — W3's primary deliverable

---

## 8. What is retired, and what the next pass should fund

---

## 9. Limitations of this workstream, stated by its own author

---

## 10. Reproducing it

```bash
# the panel and the session universe, from the data-export branch, verified by sha256
git fetch origin data-export
git show FETCH_HEAD:panel-v1/panel-v1.parquet > data/spikes/panel-v1/publish/panel-v1.parquet
# … sessions-v1.parquet, paths-v1/paths-v1.parquet likewise

.venv/bin/python spikes/analysis_v1/w3.py stage0   # W3-0a…W3-0d   free
.venv/bin/python spikes/analysis_v1/w3.py spine    # W3-A1 evidence free
.venv/bin/python spikes/analysis_v1/w3.py period   # §6            free (from S0-H)
.venv/bin/python spikes/analysis_v1/w3.py t1       # 12 trials
.venv/bin/python spikes/analysis_v1/w3.py t2       # 4 trials
.venv/bin/python spikes/analysis_v1/w3.py t4       # 2 trials
.venv/bin/python spikes/analysis_v1/w3.py sens     # §7.4          free
```

VIX is pulled once to `data/spikes/vix_daily_w3.parquet` (`^VIX` daily closes, 2024-08-01 →
2026-09-17). Seed 20260918 everywhere. Outputs land in gitignored `data/spikes/w3/`.
