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

**The null does not depend on the block length.** Block 20 was pre-registered, and it is also the
scale of R4's own 20-trade memory, so a replicate that keeps a block intact keeps part of R4's
alignment with it. Re-run across lengths (free; reported, never selected among): **p = 0.4726
(block 1) · 0.4925 (5) · 0.5025 (20) · 0.4677 (40)**, against an unchanged real best of −0.1742.

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

### The null, checked against its own weakest assumption

R1, R2 and R3 are functions of the panel alone, so holding their masks fixed while the outcome
series is permuted is the right construction. **R4 is not** — it is the trailing mean of the outcome
series itself, so a search run against a permuted record would have re-derived R4, and its cuts,
from the permuted outcomes. Re-running the T1 null that way (R4 rebuilt inside every replicate):
real best **−0.1742**, null median **−0.1750**, q95 −0.1269, **p = 0.4776**. The headline null was
not measuring the wrong thing; it is dominated by the arithmetic of removing *k* sessions from a
losing stream, not by which sessions a particular feature picks.

### §7.4 sensitivity on the best T1 gate — reported, never used to select

Run two ways, because the construction turns out to matter and the first one is the less faithful.

R2 attention at cut 14.7662, × 0.8 … 1.2:

| multiplier | cut | J | trades/session | sessions aside |
|---|---|---|---|---|
| 0.8 | 11.8130 | **−0.3064** | 0.977 | 6 |
| 0.9 | 13.2896 | −0.2741 | 0.910 | 24 |
| 1.0 | 14.7662 | −0.1742 | 0.707 | 78 |
| 1.1 | 16.2428 | −0.0766 | **0.440** | 149 |
| 1.2 | 17.7194 | −0.0360 | **0.218** | 208 |

⚠️ **That band is in the wrong units, and saying so cuts against W3's own finding.** Protocol §5.3
requires a grid to be a quantile of the fit distribution; multiplying a *cut* by 0.8–1.2 in feature
units moves the stand-aside fraction from 2 % to 78 %, which is not a ±20 % perturbation of the
parameter that was pre-registered. The faithful band perturbs the **fraction** (30 % → 24 … 36 %):

| multiplier | stand aside | cut | J | J − baseline | trades/session |
|---|---|---|---|---|---|
| 0.8 | 24 % | 14.4224 | −0.2172 | +0.0822 | 0.763 |
| 0.9 | 27 % | 14.6106 | −0.1951 | +0.1043 | 0.733 |
| 1.0 | 30 % | 14.7662 | −0.1742 | +0.1252 | 0.707 |
| 1.1 | 33 % | 14.8894 | **−0.1956** | +0.1038 | 0.677 |
| 1.2 | 36 % | 14.9813 | −0.1584 | +0.1410 | 0.647 |

**Both constructions are reported and neither selects anything.** In feature units J is a monotone
function of how many sessions the rule removes and of nothing else, it falls **below** the baseline
at 0.8×, and throughput leaves the band at 1.1× and 1.2×. In quantile space J never falls below
baseline — so §9.1 condition 6 fails as first measured and would pass as re-measured — and it is
**not monotone**: 33 % scores worse than both 30 % and 36 %. A parameter whose objective wobbles
non-monotonically across its own ±20 % band is not carrying a signal; that reading is the same
either way, and neither construction rescues a candidate that already fails conditions 1 and 2.

---

## 4. T4 — the refit cadence (2 trials)

Filter A's condition-3 constant, refit on a trailing window. The **trajectory is free** (a quantile
of a feature column reads no outcome); the two **J scores on FIT are the trials**. Frozen constant
4,857,910.10; ±20 % band [3,886,328 — 5,829,492].

| schedule | refit constants (millions) | spread | inside ±20 % band | **J on FIT** | vs frozen | picks differing |
|---|---|---|---|---|---|---|
| 125 sessions, quarterly | 4.782 · 4.614 · 4.559 · 4.893 · 5.801 · 5.480 · 5.335 · **6.115** | 32 % | **7 of 8** | −0.3005 | −0.0011 | **5 of 266** |
| 250 sessions, semiannual | 4.633 · 4.758 · 5.199 · 5.748 | 23 % | **4 of 4** | −0.3003 | −0.0009 | **4 of 266** |

Both schedules hold throughput at 1.000 trades/session. The one refit outside the band is the last
(2026-09-14, +25.9 %), computed on a window that is mostly holdout-window **feature** data; no
holdout outcome was read.

⚠️ **The two claims in that table are measured over different spans and must not be quoted as
commensurable:** the trajectories run across the whole record (feature columns only), the **J scores
are FIT-only**.

⚠️ **Amendment W3-A2 confounded the two axes** — the retained pair differs on *both* window and
cadence. The confound was **dissolved for free** afterwards: `refit_schedule` reads a feature
quantile and no outcome, so the two released cells' **trajectories** cost nothing (their J scores,
which would have been the trials, were never computed).

| cell | refits | spread vs frozen | inside the ±20 % band |
|---|---|---|---|
| 125 sessions, quarterly | 8 | 32.0 % | 7 of 8 |
| 125 sessions, semiannual | 4 | 30.9 % | 3 of 4 |
| 250 sessions, quarterly | 8 | 23.4 % | **8 of 8** |
| 250 sessions, semiannual | 4 | 23.0 % | **4 of 4** |

**The window drives band membership; the cadence does not.** Both 125-session windows breach the
band (always on the same late reading, 6.115 M); neither 250-session window breaches it at either
cadence. And a smaller spread from a semiannual schedule is **not** evidence that it is steadier —
it draws four samples against quarterly's eight, which is arithmetic, not stability.

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

**The capital-adequacy view** (§6.2 item 9, reported and never optimised). On the FIT Filter-A
stream at F2, fixed $500 sizing: end equity **−$2,174.85**, max drawdown **−493.3 %**, **0**
unaffordable trades, **1,992** qualifying setups turned away by the capacity cap. Equity below zero
and a drawdown beyond −100 % are artefacts of sizing that does not shrink as equity falls; the
honest reading is that **the account is ruined inside the FIT window on every exit family**. That is
a statement about $500 against this stream, not about the strategy's R-quality, and the two are kept
apart deliberately (operator fact 3).

**Serial dependence — the direct test.**

| test | value | verdict |
|---|---|---|
| autocorrelation of session net R, lags 1…8 | −0.012, −0.063, +0.036, +0.003, −0.086, +0.070, +0.042, +0.042 | indistinguishable from zero |
| Ljung–Box Q(20) | **12.92** vs χ²₀.₉₅(20) = 31.41 | independence **not rejected** |
| Wald–Wolfowitz runs | 111 runs vs 109.57 expected, z = **0.215** | no clustering |

For contrast, the *features* are strongly serially dependent on FIT (R1 lag-1 0.972, R3 0.904,
R2 0.854). **The state persists; the outcomes do not.** That gap is the finding.

---

### What this record could have detected, and did not

Recomputed from S0-H's measured sd (1.4076 net R/trade) at 80 % power, two-sided, Bonferroni at
W3's 24-trial allocation:

| block | trades | α = 0.05 | **Bonferroni /24** |
|---|---|---|---|
| ungated FIT stream | 266 | 0.2418 | 0.3383 |
| the best T1 gate's retained stream | 188 | 0.2876 | **0.4024** |
| the best T2 gate's retained stream | 179 | 0.2948 | 0.4124 |

Set that against what a gate would have had to do:

- **to be useful** — to lift the retained stream from −0.2994 to `J > 0` — it needs **+0.2994
  R/trade**;
- **to be believable** at this allocation it needs **+0.4024 R/trade**;
- **the largest lift actually observed** across all sixteen gates was **+0.056 R/trade** — the best
  conjunction moved net R/trade from −0.2994 to −0.2433, and the best marginal gate to −0.2464
  (+0.053).

⚠️ Two things follow. The observed effects are **a seventh of the detection floor**. And there is a
**dead band between +0.30 and +0.40 R/trade** in which a gate would clear `J > 0` and still fail the
null — so on this record, at this allocation, a *marginally* profitable timing rule was never
certifiable in the first place. That is a property of the design, and it was knowable in advance;
it is stated here so the null is read as "nothing large enough to certify", not as "nothing".

## 6. The verdict — a documented null

*(§6–§9 are the interpretation step, run in a separate session per protocol §13.4, on the numbers
above and nothing else.)*

**W3 freezes no timing overlay.** Its Stage-3 entry under protocol §12.1 is a documented null.

| # | plan §9.1 condition | result |
|---|---|---|
| 1 | `p ≤ 0.05` on the FIT search null | **FAILS**, and not marginally — 0.5025 (T1), 0.4129 (T1+T2), 0.47–0.50 at every block length |
| 2 | `J > 0` on FIT **and** above baseline | **FAILS the sign clause.** Best J = −0.1637. All 16 points are above baseline — that *is* the trap, not a pass |
| 3 | throughput ∈ [0.6, 1.0] | passes everywhere (0.647–0.92) |
| 4 | `J > 0` and above baseline on CHECK | unmet — T5 unspent, correctly |
| 5 | quantile stable across three walk-forward folds | unmet — never run |
| 6 | `J` above baseline throughout ±20 % | **fails in feature units** (−0.3064 at 0.8×); passes in quantile space. Reported both ways; neither decides anything |
| 7 | net, not gross | passes |

Conditions 1 and 2 fail on evidence in hand, so the verdict does not rest on the unspent trials.

### Why sixteen improvements are not one finding

J is a per-**session** average over every session, and a session stood aside contributes **0**. Zero
is better than −0.2994. **So on a losing stream, not trading is mechanically an improvement, and the
more you sit out the better J looks.** The limit of the entire hypothesis class is "stand aside every
session", which scores **J = 0** and beats all sixteen tested points. Every gate in §3 is a partial
step toward that trivial optimum, and the only thing between the search and it is the throughput
constraint — which is protocol §6 doing exactly the job it was built for.

Four independent demonstrations, each sufficient on its own:

**(a) The zero-information benchmark.** A gate carrying no information at all scores baseline × the
fraction of sessions it keeps. Measured against that:

| gate | kept | zero-information J | actual J | **excess** |
|---|---|---|---|---|
| R1 breadth @ 10/20/30 % | .92/.82/.71 | −.2757/−.2443/−.2117 | −.2976/−.2686/−.2284 | **−.0219 / −.0243 / −.0167** |
| R2 attention @ 10/20/30 % | .90/.81/.71 | −.2701/−.2410/−.2117 | −.2659/−.2320/−.1742 | +.0042 / +.0090 / **+.0375** |
| R3 VIX @ 10/20/30 % | .90/.80/.70 | −.2689/−.2398/−.2093 | −.2824/−.2700/−.2078 | **−.0135 / −.0302** / +.0015 |
| R4 stream @ 10/20/30 % | .91/.82/.72 | −.2713/−.2443/−.2162 | −.2508/−.2017/−.1869 | +.0205 / **+.0426** / +.0293 |
| union @ 20/20 % | .673 | −.2015 | −.1637 | +.0378 |

**Five of the twelve marginal gates are worse than standing aside at random.** The largest excess
anywhere in the search is **+0.0426 R/session**. The headline "+0.1357 from the best conjunction" is
+0.098 of throughput reduction and +0.038 of everything else.

**(b) The per-trade column says it outright.** Baseline net R/trade is −0.2994. R1 gated: −0.323 /
−0.329 / −0.323. R3 gated: −0.314 / −0.337 / −0.297. **Those gates raised J while making every trade
actually taken worse.** A rule cannot be finding bad days while the days it keeps get worse.

**(c) The best real effect is a seventh of what the record can see.** The largest genuine per-trade
improvement in the whole search is **+0.056 R/trade**, against a minimum detectable effect of
**0.378** (S0-J) — about half a standard error even ignoring multiplicity entirely.

**(d) The null settles it without any of the above.** Real best-of-search J = −0.1742; null median
best-of-search = **−0.1740**. The search landed on the fiftieth percentile of what luck produces at
this intensity, to four decimal places.

### The six unspent trials are returned, not saved for later

- **T5 would be worse than wasted.** Protocol §8.2: every candidate carried to CHECK is a
  multiplicity on a ~95-trade block that must adjudicate up to three. Spending it on a known null
  degrades the block for the workstreams that have a candidate.
- **T3 is the most plausible of the three and still fails.** Re-scoring under F3/F4 matters only if
  the gate's usefulness could flip across exit families — but F3's MDE is 0.477 R/trade and F4's is
  0.242 (S0-J), and the effect being chased is 0.05. It is invisible under every family in the
  design.
- **The walk-forward is a stability test on a parameter that will not be frozen**, and its §9.2 role
  is already served by T4 and the sensitivity band.

**18 of 24 spent. 6 returned to the global budget.**

---

## 7. The tuning protocol (§9.2) — W3's primary deliverable

This is what W3 was funded for, and it ships whatever the gate did.

### What T4 measured: the constant moves and the stream does not

Over the record the constant drifts 4.56 M → 6.12 M. That is not a wander — it rises monotonically
through the late record, alongside the Stage-0 stationarity flag (breadth mean 14.11 on FIT → 17.4
on the holdout). It is a **level shift in the underlying**, not parameter noise.

And it changes nothing. **J moves by −0.0011 and −0.0009 R/session — about 1.3 % of one standard
error of J** (se = 1.4076/√266 = 0.086), both in the *wrong* direction, and 4–5 picks of 266 differ.
Filter A's condition 3 sits on a flat part of the selection surface: you can move it by a third and
it is the same stream.

### The protocol

> **What may be re-tuned.** Threshold constants only — never a column set, a predicate form, an exit
> family or the capacity rule. Those need a new pre-registration. *(Plan §9.2, unchanged.)*
>
> **Filter A's condition-3 constant is not on a refit schedule at all.** Not "refit slowly" —
> **monitored, not controlled**:
> - recompute it on a **250-session trailing window, semiannually**, and use the reading **only** to
>   test band membership. It never updates the live constant.
> - **The band is the frozen one:** ±20 % of 4,857,910.10 = **[3,886,328 — 5,829,492]**.
> - **Inside the band → no action**, and ledger the reading. §9.2's re-tune branch says *apply it*;
>   T4 measured applying it at 0.001 R/session, so applying and not applying are indistinguishable
>   and not applying costs **no trial**. This is a deliberate narrowing of the plan, earned by
>   measurement rather than asserted.
> - **Outside the band → a rescue.** Do not apply it. Stop and open a new pre-registration. A rescue
>   is a statement that the rule has stopped describing the market, not a parameter update, and the
>   answer to one is never a wider band.
> - **Every *applied* refit is a trial, ledgered forward, forever.** Under this protocol the
>   expected number of applied refits is **zero**, which is precisely the property that stops a
>   production ledger from leaking.
>
> **The general admission test, which is what T4 actually earns:**
>
> > A threshold earns a refit schedule only if its ±20 % sensitivity band moves J by **more than one
> > standard error of J** on the fitting block, and **less than about two**.
>
> Below the floor, refitting measures noise and burns trials. Above the ceiling the parameter is a
> knife edge and the rule needs re-specifying, not tuning. Applied to what W3 measured: Filter A's
> condition 3 moves J by **0.013 se** — far below the floor. The R2 gate cut moves J across its
> feature-unit band by 0.27 R/session = **3.1 se** while throughput swings 0.98 → 0.22 — far above
> the ceiling. **No parameter this record produced falls inside the admissible band.**
>
> **The honest branch, arriving by an unexpected route.** Plan §9.2 anticipated "the constant wanders
> and the record supports no cadence". What was measured is the opposite and worse: **the constant is
> inert, and the one thing that is not inert is a cliff.** The response is the same — a fixed rule
> set plus forward paper collection, not a refit schedule invented to fill the gap.

⚠️ **The rescue rule has already fired once, in the dark.** The 2026-09-14 reading of 6.115 M is
+25.9 %, outside the band, on both 125-session windows. It sits in the holdout-feature region (no
outcome read). **The custodian should meet this in the freeze report rather than discover it.**

**What the W3-A2 confound still forbids.** The two *charged* cells differ on both axes, so no
statement of the form "use a 250-session window **because J was better**" is available — the J
scores cannot be attributed to an axis. The recommendation above rests on the **trajectories**,
which were completed for all four cells at zero trial cost, and on the fact that ΔJ ≈ 0.001 in both
charged cells, which neutralises the confound by effect size rather than by argument.

---

## 8. Prior (1) — what the period distribution settles

**Burst shape: confirmed, emphatically.** 76 winning sessions of 266 (28.6 %); the 80th-percentile
session is −1.025 R and the 90th is +1.899 R. Returns arrive in a small minority of sessions at full
size. That is exactly what the operator described, and nothing here dents it.

**Serial dependence: refuted, in the only sense that matters operationally.** Three independent tests
agree. The eight reported lags run from −0.086 to +0.070 against a standard error of 1/√266 =
**0.061** — every one inside one se. Ljung–Box Q(20) = 12.92 against a 31.41 critical value, less
than half the threshold. Runs: 111 observed against 109.57 expected, z = 0.215.

**So the two halves of prior (1) come apart, and that is the finding.** *"The strategy is bursty"* is
true. *"The strategy runs hot and cold in stretches you could ride or sit out"* is not supported by
this record. The cold stretches are real and brutal — 81.8 % of 20-session windows negative, 98.7 %
of 40-session, 99.0 % of 60-session, underwater on 100 % of sessions, deepest −82.4 R — **and they
are fully accounted for by independent draws at a 28.6 % hit rate on a negative-expectancy stream.**
Long losing runs are what independence looks like at that hit rate; they are not evidence of a
regime.

**What this record could and could not have detected — precisely.** Ljung–Box at n = 266 rejects when
the autocorrelations sum to roughly Σr² ≈ 0.118: one lag at |r| ≈ 0.34, or a persistent |r| ≈ 0.08
across twenty. Below that the record is blind. **But the blind region is also the worthless region,
and that closes the loop.** A stand-aside rule cutting the worst 30 % of sessions on a lag-1
relationship of strength ρ lifts the retained mean by roughly ρ × 1.4 × 0.5 ≈ 0.70 ρ R/trade. So:

| to reach | needs a lag-1 correlation of about |
|---|---|
| the 0.378 R/trade this record can certify | **ρ ≈ 0.54** |
| the +0.05 R/trade the search actually found | **ρ ≈ 0.07** |
| what the record measured | **≈ 0** (all lags inside one se) |

An economically useful amount of hot-and-cold implies a session-to-session correlation near 0.5; the
record could have seen anything above roughly 0.3; it measured essentially zero. **The gap between
"undetectable" and "worth trading" does not exist here** — which is a much stronger statement than an
ordinary null, and it is the reason this workstream should not be re-run with more features.

One asymmetry recorded for W2's benefit: the longest run of consecutive losing 20-session blocks is
F1 13/13, F4 13/13, F3 9/13, **F2 4/13**. F2 is the only family whose losing runs break, and it
breaks them with dispersion (sd 1.408), not with skill — all four families are negative.

---

## 9. What retires, what is unsearched, and what to fund next

**Retired on this evidence:**
- **The session-level stand-aside hypothesis.** Four features, twelve marginal points, four
  conjunctions, nulls at four block lengths, p = 0.47–0.50. Do not fund more session-level state
  features on the same ~200 trades. This is the belief W3 existed to settle, and it is settled.
- **The operational reading of prior (1)** — "sit out the cold stretches". The distributional reading
  stands.
- **Scheduled refitting of Filter A's condition-3 constant**, replaced by the monitor in §7.

**Unsearched, not refuted — and not to be acted on from these tables:**
- **The reverse direction of R1 and R3.** Directions were pre-declared, which was correct. R1 and R3
  gated *worse* per-trade at every cut, so the information, if any, sits in the opposite sign — and
  that sign was never tested. ⚠️ **Flipping a direction after reading the per-trade column is exactly
  the selection-of-maximum bias the null exists to block.** If anyone wants it, it is a new
  pre-registration, with its own null.
- **Plan §5.1's R2 as written** — see §2 and the caveat in §6 of the ledger entry. The all-day
  attention hypothesis remains open.

### Should the next pass fund the W1 × W3 interaction (protocol §14)? On this evidence, no — conditionally

Plan §9.3 nominates it. The argument against it as the *first* thing funded is arithmetic:

**The Filter-A stream is gross-negative** — −0.1450 R/session before costs, −0.2994 after. Zeroing
every commission and every tick of slippage still leaves a stream that loses 0.145 R/trade. **Neither
timing nor execution can make a gross-negative stream viable; conditioning redistributes an edge, it
cannot manufacture one.** In hit-rate terms the stream needs ~38–40 % winners at a 2 R target and
delivers 28.6 %: ten points. The best of sixteen tested points closed 18 % of that gap while giving
up 29 % of throughput.

And the interaction's power is *worse* than what has already failed: the joint space needs more
trials (MDE rising toward 0.42 R/trade at a 120-trial correction) while the effect it hunts lives by
construction in a *subset* of sessions, so the effective n falls — on half the record (~133 trades)
the single-test MDE is already 0.342 and a 30-point search pushes it near 0.48. It would be hunting a
~0.5 R/trade pocket inside a stream whose whole mean is −0.30 and whose best marginal signal was
0.05.

⚠️ **The condition, which W3 cannot resolve because constraint 11 forbids it from seeing W1's
result:** if W1 froze a selection candidate materially above the Filter-A baseline — at or near
break-even — then the interaction *is* the right next spend, because there would then be an edge for
regime conditioning to redistribute. **The custodian should resolve this at the holdout pass.**

**Fund instead, and start now:** protocol §14's own "cheap fix" — **300–500 hand annotations spread
across the recon window** (2024-09 → 2026-03). It costs the trader's chart time and no vendor spend;
it moves the only human-supervision asset the project has out of the holdout, where all 167 currently
sit; and it attacks **selection**, which is where this record says the deficit lives. The lead time
is chart hours, not compute, which is why it should be started rather than costed. Second priority,
far more expensive: §14's universe question, the only other route to a gross-positive stream, at
3–6 months of harvest nights.

---

## 10. Limitations, stated by the workstream itself

1. **One direction per feature was tested.** "Standing aside when VIX is high does not help" is
   supported; "VIX does not matter" is **not**. Same for breadth.
2. **Amendment W3-A1 is the largest weakness here, and it lands on the headline.** The best-of-search
   point is R2 at 30 % — a feature that tracks plan §5.1's R2 at ρ = 0.385, i.e. **15 % of shared
   variance**. The pre-registered attention hypothesis was not tested, and the substituted one may
   not be a timing feature at all: mean `hits_before_trigger` across a session's rows is high when a
   day's triggers came late on well-watched names, which is close to a statement about
   `trigger_et_min` — **a selection feature, and W1's** (plan §10). If a future pass wants this
   variable, settle the scope question first.
3. **The best-performing feature is the one most consistent with noise.** R2's autocorrelation is
   0.854 / 0.046 / −0.074 at lags 1/5/20. A 5-session rolling mean of an i.i.d. series has lag-1 ≈
   0.8 and lag-5 ≈ 0 *exactly*. R2 carries **no persistence beyond its own smoothing window**, while
   R1 (0.972 / 0.751 / 0.242) and R3 (0.904 / 0.609 / 0.302) do. That is a stronger reason to
   disbelieve the R2 result than the p-value is.
4. **The sensitivity band was first built in the wrong units** (§3), and both constructions are now
   reported. Condition 6's verdict differs between them; the verdict of the workstream does not.
5. **B = 200 floors p at 1/201 ≈ 0.005.** Irrelevant at p = 0.50; it would matter if anything were
   near the bar.
6. **R4 is the one feature an agent cannot coverage-verify on the live half**, because it reads
   outcomes and holdout outcomes are physically absent. The plan funded it knowing this.

**Hard-constraint audit:** no outcome-ranked selection, no lookahead column read, no statistic
reported on opportunities that could not have been traded, no holdout outcome touched in any form.
The T4 trajectory reads holdout **feature** columns, which §4.3 and §5.2 permit and which is flagged
wherever it appears.

---

## 11. Reproducing it

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
.venv/bin/python spikes/analysis_v1/w3.py nullr4   # null with R4 rebuilt per replicate   free
.venv/bin/python spikes/analysis_v1/w3.py extras   # block lengths, all 4 T4 trajectories,
                                                   # the quantile-space band, the
                                                   # zero-information benchmark           free
```

**Why the free runs are free** (protocol §5.2): a permutation null is not a trial, a ±20 % band
around an already-charged parameter is not a trial, and a quantile of a feature column reads no
outcome at all. None of them selected anything — and there was no candidate to select.

VIX is pulled once to `data/spikes/vix_daily_w3.parquet` (`^VIX` daily closes, 2024-08-01 →
2026-09-17). Seed 20260918 everywhere. Outputs land in gitignored `data/spikes/w3/`.
