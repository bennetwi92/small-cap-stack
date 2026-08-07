---
title: Does a smaller float buy a bigger Max R?
published: 2026-07-31
summary: Float is a tail effect, not a gradient — it barely moves the median trade but roughly doubles the odds of a big one, and the break is at ~5M shares, not at the 20M gate.
tags: strategy,data,float
correction: 2026-08-07 — this report calls `float < 20M` "the live rule" and charts it as a gate. There is no live float gate and never was (#551): `float_max_shares` feeds a count in the EOD report and filters nothing, so the closing recommendation argues against a rule that was not running. The measured float/Max-R relationship stands; its framing as a re-gating decision does not. Data window ends 2026-07-25.
---

Yes — and in the direction expected. But it is a **tail effect, not a gradient**: float barely
moves the typical trade, while roughly doubling the odds of a big one. And the break is at
**~5M shares, not at the 20M gate** we actually run.

> **452 triggered opportunity-runs · 276 symbols · 17 trading days, 2026-07-01 → 2026-07-24.**
> Max R = favourable excursion under the consolidation-low stop. Numbers are as of 2026-07-24
> and have **not** been refreshed since — see *What this is*, at the end.

| | |
| --- | --- |
| **Rank correlation** | **−0.124** — ρ(float, Max R), 95% CI [−0.199, −0.054] |
| **Reaches 3R, &lt;5M float** | **16.3%** vs 8.0% at ≥5M — a **2.0×** lift |
| **Reaches 5R, &lt;5M float** | **7.0%** vs 2.7% at ≥5M — a **2.6×** lift |
| **Median Max R** | **0.58 → 0.24** from &lt;1M to ≥50M float — the middle hardly moves |

## Probability of reaching a target, by float

The decline lives entirely in the tail, and it flattens above ~5M shares — **5–20M, 20–50M and
≥50M are indistinguishable from each other**. The bars are labelled because the difference that
matters is the difference between the small bars.

<style>
.fvr-legend{ display:flex; flex-wrap:wrap; gap:18px; margin:0 0 8px; font-size:11.5px; color:var(--dim); }
.fvr-legend span{ display:inline-flex; align-items:center; gap:7px; }
.fvr-sw{ width:10px; height:10px; display:inline-block; }
/* .md-body caps prose at 82ch (~615px); at that width the 860-unit viewBox scales
   to 0.72x and the bar labels stop being legible, so the plot breaks out of the
   measure up to its natural width — and below ~640px it scrolls rather than shrink. */
.fvr-plot{ background:var(--inset); border:1px solid var(--line); padding:14px 12px 8px; overflow-x:auto; margin:0 0 14px; width:min(884px, calc(100vw - 26px)); }
.fvr-svg{ display:block; width:100%; min-width:640px; height:auto; }
.fvr-grid{ stroke:var(--line); stroke-width:1; }
.fvr-base{ stroke:var(--line-2); stroke-width:1; }
.fvr-ax{ fill:#6f7590; font-family:var(--mono); font-size:10.5px; }
.fvr-bk{ fill:var(--ink); font-size:11.5px; }
.fvr-val{ fill:var(--ink); font-family:var(--mono); font-size:10.5px; font-variant-numeric:tabular-nums; }
.fvr-gate{ stroke:var(--gold); stroke-width:1; stroke-dasharray:3 3; }
.fvr-gatetext{ fill:var(--gold); font-family:var(--mono); font-size:10px; }
</style>

<div class="fvr-legend"><span><i class="fvr-sw" style="background:#256e78"></i>reaches 2R</span><span><i class="fvr-sw" style="background:#37a9b8"></i>reaches 3R</span><span><i class="fvr-sw" style="background:#4fe3ef"></i>reaches 5R</span></div>
<div class="fvr-plot">
<svg class="fvr-svg" viewBox="0 0 860 320" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Grouped bars: probability of reaching 2R, 3R and 5R across five float buckets, declining with float size">
<line class="fvr-grid" x1="42" y1="270.0" x2="846" y2="270.0"/>
<text class="fvr-ax" x="34" y="273.5" text-anchor="end">0%</text>
<line class="fvr-grid" x1="42" y1="207.5" x2="846" y2="207.5"/>
<text class="fvr-ax" x="34" y="211.0" text-anchor="end">10%</text>
<line class="fvr-grid" x1="42" y1="145.0" x2="846" y2="145.0"/>
<text class="fvr-ax" x="34" y="148.5" text-anchor="end">20%</text>
<line class="fvr-grid" x1="42" y1="82.5" x2="846" y2="82.5"/>
<text class="fvr-ax" x="34" y="86.0" text-anchor="end">30%</text>
<line class="fvr-grid" x1="42" y1="20.0" x2="846" y2="20.0"/>
<text class="fvr-ax" x="34" y="23.5" text-anchor="end">40%</text>
<line class="fvr-gate" x1="524.4" y1="14" x2="524.4" y2="270"/>
<text class="fvr-gatetext" x="529.4" y="11">live gate: float &lt; 20M</text>
<g>
<path fill="#256e78" d="M75.7,270.0 V83.0 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>&lt;1M float · reaches 2R: 30.4% of 112 runs</title></path>
<text class="fvr-val" x="90.2" y="75.0" text-anchor="middle">30</text>
<path fill="#37a9b8" d="M107.8,270.0 V155.5 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>&lt;1M float · reaches 3R: 18.8% of 112 runs</title></path>
<text class="fvr-val" x="122.4" y="147.5" text-anchor="middle">19</text>
<path fill="#4fe3ef" d="M140.0,270.0 V244.9 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>&lt;1M float · reaches 5R: 4.5% of 112 runs</title></path>
<text class="fvr-val" x="154.6" y="236.9" text-anchor="middle">4</text>
</g>
<text class="fvr-ax fvr-bk" x="122.4" y="290" text-anchor="middle">&lt;1M</text>
<text class="fvr-ax" x="122.4" y="305" text-anchor="middle">n=112</text>
<g>
<path fill="#256e78" d="M236.5,270.0 V115.5 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>1–5M float · reaches 2R: 25.2% of 115 runs</title></path>
<text class="fvr-val" x="251.0" y="107.5" text-anchor="middle">25</text>
<path fill="#37a9b8" d="M268.6,270.0 V186.1 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>1–5M float · reaches 3R: 13.9% of 115 runs</title></path>
<text class="fvr-val" x="283.2" y="178.1" text-anchor="middle">14</text>
<path fill="#4fe3ef" d="M300.8,270.0 V213.0 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>1–5M float · reaches 5R: 9.6% of 115 runs</title></path>
<text class="fvr-val" x="315.4" y="205.0" text-anchor="middle">10</text>
</g>
<text class="fvr-ax fvr-bk" x="283.2" y="290" text-anchor="middle">1–5M</text>
<text class="fvr-ax" x="283.2" y="305" text-anchor="middle">n=115</text>
<g>
<path fill="#256e78" d="M397.3,270.0 V172.4 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>5–20M float · reaches 2R: 16.1% of 56 runs</title></path>
<text class="fvr-val" x="411.8" y="164.4" text-anchor="middle">16</text>
<path fill="#37a9b8" d="M429.4,270.0 V228.6 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>5–20M float · reaches 3R: 7.1% of 56 runs</title></path>
<text class="fvr-val" x="444.0" y="220.6" text-anchor="middle">7</text>
<path fill="#4fe3ef" d="M461.6,270.0 V250.5 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>5–20M float · reaches 5R: 3.6% of 56 runs</title></path>
<text class="fvr-val" x="476.2" y="242.5" text-anchor="middle">4</text>
</g>
<text class="fvr-ax fvr-bk" x="444.0" y="290" text-anchor="middle">5–20M</text>
<text class="fvr-ax" x="444.0" y="305" text-anchor="middle">n=56</text>
<g>
<path fill="#256e78" d="M558.1,270.0 V166.8 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>20–50M float · reaches 2R: 17.0% of 53 runs</title></path>
<text class="fvr-val" x="572.6" y="158.8" text-anchor="middle">17</text>
<path fill="#37a9b8" d="M590.2,270.0 V237.4 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>20–50M float · reaches 3R: 5.7% of 53 runs</title></path>
<text class="fvr-val" x="604.8" y="229.4" text-anchor="middle">6</text>
<path fill="#4fe3ef" d="M622.4,270.0 V261.1 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>20–50M float · reaches 5R: 1.9% of 53 runs</title></path>
<text class="fvr-val" x="637.0" y="253.1" text-anchor="middle">2</text>
</g>
<text class="fvr-ax fvr-bk" x="604.8" y="290" text-anchor="middle">20–50M</text>
<text class="fvr-ax" x="604.8" y="305" text-anchor="middle">n=53</text>
<g>
<path fill="#256e78" d="M718.9,270.0 V170.5 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>≥50M float · reaches 2R: 16.4% of 116 runs</title></path>
<text class="fvr-val" x="733.4" y="162.5" text-anchor="middle">16</text>
<path fill="#37a9b8" d="M751.0,270.0 V213.6 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>≥50M float · reaches 3R: 9.5% of 116 runs</title></path>
<text class="fvr-val" x="765.6" y="205.6" text-anchor="middle">10</text>
<path fill="#4fe3ef" d="M783.2,270.0 V256.8 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>≥50M float · reaches 5R: 2.6% of 116 runs</title></path>
<text class="fvr-val" x="797.8" y="248.8" text-anchor="middle">3</text>
</g>
<text class="fvr-ax fvr-bk" x="765.6" y="290" text-anchor="middle">≥50M</text>
<text class="fvr-ax" x="765.6" y="305" text-anchor="middle">n=116</text>
<line class="fvr-base" x1="42" y1="270" x2="846" y2="270"/>
</svg>
</div>

The live rule is `float < 20M shares` ([decision #1](https://github.com/bennetwi92/small-cap-stack/blob/main/research/decisions.md)),
but the data's own break is at ~5M. Inside the passing region, **&lt;5M reaches 3R on 16.3% of runs
against 7.1% for 5–20M** — the gate's own passing set is not homogeneous, and above 5M float has
stopped discriminating at all.

## The same cut, on trustworthy float readings only

Float is the worst-measured variable in this study. `fmp` and yfinance agree within ±25% on only
**43%** of runs and differ by **more than 5×** on **16%** — in both directions (MTEN: fmp 11,610
vs yfinance 3,914,389). `fmp` also returns implausible sub-100k floats on **74 of 526** runs,
**25 of them exactly zero** — and `_FLOAT_PRIORITY` reads fmp *first*, so that is what the live
gate decides on.

Measurement error drags a correlation toward zero, so −0.124 is a **floor**. Restricted to the
183 runs where both sources agree, ρ sharpens to **−0.206** and the buckets go cleanly monotone:

<div class="fvr-legend"><span><i class="fvr-sw" style="background:#256e78"></i>reaches 2R</span><span><i class="fvr-sw" style="background:#37a9b8"></i>reaches 3R</span><span><i class="fvr-sw" style="background:#4fe3ef"></i>reaches 5R</span></div>
<div class="fvr-plot">
<svg class="fvr-svg" viewBox="0 0 860 320" preserveAspectRatio="xMidYMid meet" role="img" aria-label="The same grouped bars restricted to runs where both float sources agree within 25 percent, showing a cleaner monotone decline">
<line class="fvr-grid" x1="42" y1="270.0" x2="846" y2="270.0"/>
<text class="fvr-ax" x="34" y="273.5" text-anchor="end">0%</text>
<line class="fvr-grid" x1="42" y1="220.0" x2="846" y2="220.0"/>
<text class="fvr-ax" x="34" y="223.5" text-anchor="end">10%</text>
<line class="fvr-grid" x1="42" y1="170.0" x2="846" y2="170.0"/>
<text class="fvr-ax" x="34" y="173.5" text-anchor="end">20%</text>
<line class="fvr-grid" x1="42" y1="120.0" x2="846" y2="120.0"/>
<text class="fvr-ax" x="34" y="123.5" text-anchor="end">30%</text>
<line class="fvr-grid" x1="42" y1="70.0" x2="846" y2="70.0"/>
<text class="fvr-ax" x="34" y="73.5" text-anchor="end">40%</text>
<line class="fvr-grid" x1="42" y1="20.0" x2="846" y2="20.0"/>
<text class="fvr-ax" x="34" y="23.5" text-anchor="end">50%</text>
<line class="fvr-gate" x1="524.4" y1="14" x2="524.4" y2="270"/>
<text class="fvr-gatetext" x="529.4" y="11">live gate: float &lt; 20M</text>
<g>
<path fill="#256e78" d="M75.7,270.0 V42.0 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>&lt;1M float · reaches 2R: 46.2% of 26 runs</title></path>
<text class="fvr-val" x="90.2" y="34.0" text-anchor="middle">46</text>
<path fill="#37a9b8" d="M107.8,270.0 V100.0 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>&lt;1M float · reaches 3R: 34.6% of 26 runs</title></path>
<text class="fvr-val" x="122.4" y="92.0" text-anchor="middle">35</text>
<path fill="#4fe3ef" d="M140.0,270.0 V254.0 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>&lt;1M float · reaches 5R: 3.8% of 26 runs</title></path>
<text class="fvr-val" x="154.6" y="246.0" text-anchor="middle">4</text>
</g>
<text class="fvr-ax fvr-bk" x="122.4" y="290" text-anchor="middle">&lt;1M</text>
<text class="fvr-ax" x="122.4" y="305" text-anchor="middle">n=26</text>
<g>
<path fill="#256e78" d="M236.5,270.0 V106.5 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>1–5M float · reaches 2R: 33.3% of 39 runs</title></path>
<text class="fvr-val" x="251.0" y="98.5" text-anchor="middle">33</text>
<path fill="#37a9b8" d="M268.6,270.0 V196.0 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>1–5M float · reaches 3R: 15.4% of 39 runs</title></path>
<text class="fvr-val" x="283.2" y="188.0" text-anchor="middle">15</text>
<path fill="#4fe3ef" d="M300.8,270.0 V209.0 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>1–5M float · reaches 5R: 12.8% of 39 runs</title></path>
<text class="fvr-val" x="315.4" y="201.0" text-anchor="middle">13</text>
</g>
<text class="fvr-ax fvr-bk" x="283.2" y="290" text-anchor="middle">1–5M</text>
<text class="fvr-ax" x="283.2" y="305" text-anchor="middle">n=39</text>
<g>
<path fill="#256e78" d="M397.3,270.0 V169.0 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>5–20M float · reaches 2R: 20.8% of 24 runs</title></path>
<text class="fvr-val" x="411.8" y="161.0" text-anchor="middle">21</text>
<path fill="#37a9b8" d="M429.4,270.0 V231.5 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>5–20M float · reaches 3R: 8.3% of 24 runs</title></path>
<text class="fvr-val" x="444.0" y="223.5" text-anchor="middle">8</text>
<path fill="#4fe3ef" d="M461.6,270.0 V252.0 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>5–20M float · reaches 5R: 4.2% of 24 runs</title></path>
<text class="fvr-val" x="476.2" y="244.0" text-anchor="middle">4</text>
</g>
<text class="fvr-ax fvr-bk" x="444.0" y="290" text-anchor="middle">5–20M</text>
<text class="fvr-ax" x="444.0" y="305" text-anchor="middle">n=24</text>
<g>
<path fill="#256e78" d="M558.1,270.0 V187.0 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>20–50M float · reaches 2R: 17.2% of 29 runs</title></path>
<text class="fvr-val" x="572.6" y="179.0" text-anchor="middle">17</text>
<path fill="#37a9b8" d="M590.2,270.0 V256.0 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>20–50M float · reaches 3R: 3.4% of 29 runs</title></path>
<text class="fvr-val" x="604.8" y="248.0" text-anchor="middle">3</text>
<text class="fvr-val" x="637.0" y="265.0" text-anchor="middle">0</text>
</g>
<text class="fvr-ax fvr-bk" x="604.8" y="290" text-anchor="middle">20–50M</text>
<text class="fvr-ax" x="604.8" y="305" text-anchor="middle">n=29</text>
<g>
<path fill="#256e78" d="M718.9,270.0 V180.5 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>≥50M float · reaches 2R: 18.5% of 65 runs</title></path>
<text class="fvr-val" x="733.4" y="172.5" text-anchor="middle">18</text>
<path fill="#37a9b8" d="M751.0,270.0 V234.5 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>≥50M float · reaches 3R: 7.7% of 65 runs</title></path>
<text class="fvr-val" x="765.6" y="226.5" text-anchor="middle">8</text>
<path fill="#4fe3ef" d="M783.2,270.0 V265.5 q0,-3.0 3.0,-3.0 h23.2 q3.0,0 3.0,3.0 V270.0 Z"><title>≥50M float · reaches 5R: 1.5% of 65 runs</title></path>
<text class="fvr-val" x="797.8" y="257.5" text-anchor="middle">2</text>
</g>
<text class="fvr-ax fvr-bk" x="765.6" y="290" text-anchor="middle">≥50M</text>
<text class="fvr-ax" x="765.6" y="305" text-anchor="middle">n=65</text>
<line class="fvr-base" x1="42" y1="270" x2="846" y2="270"/>
</svg>
</div>

## The numbers

| recorded float | n | median R | P(≥2R) | P(≥3R) | P(≥5R) |
| --- | ---: | ---: | ---: | ---: | ---: |
| &lt;1M | 112 | 0.58 | 30.4% | 18.8% | 4.5% |
| 1–5M | 115 | 0.38 | 25.2% | 13.9% | 9.6% |
| 5–20M | 56 | 0.34 | 16.1% | 7.1% | 3.6% |
| 20–50M | 53 | 0.54 | 17.0% | 5.7% | 1.9% |
| ≥50M | 116 | 0.24 | 16.4% | 9.5% | 2.6% |

And the same table on the 183 runs where both float sources agree within ±25%:

| recorded float | n | median R | P(≥2R) | P(≥3R) | P(≥5R) |
| --- | ---: | ---: | ---: | ---: | ---: |
| &lt;1M | 26 | 1.12 | 46.2% | 34.6% | 3.8% |
| 1–5M | 39 | 0.83 | 33.3% | 15.4% | 12.8% |
| 5–20M | 24 | 0.34 | 20.8% | 8.3% | 4.2% |
| 20–50M | 29 | 0.12 | 17.2% | 3.4% | 0.0% |
| ≥50M | 65 | 0.36 | 18.5% | 7.7% | 1.5% |

## What holds up

**Robust.** Leave-one-date-out keeps the P(≥3R) gap in +0.075…+0.095 across all 17 refits. The 29
runs reaching 5R come from 26 distinct symbols, so this is not one lucky ticker. ρ = −0.105 with
one row per symbol; ρ = −0.245 on takeable trades only (n=44, too thin to lean on).

**Not price in disguise.** ρ(float, price) = +0.363 and ρ(price, Max R) = +0.183 push the
*opposite* way, and the effect survives within every price tercile.

## What follows

- **Don't trade float as a signal on its own.** ρ ≈ −0.12 to −0.21 sorts the tail, not the median
  trade. It is a tie-breaker or a size input, not an edge by itself.
- **Treat ~5M as a scoring input, not a hard re-gate.** The ≥20M bucket still produced 9.5% of
  runs reaching 3R, so tightening the gate to 5M would discard real winners along with the noise.
- **Fix the float feed first.** A gate whose input disagrees more than 5× with a second source on
  16% of symbols, and reads exactly zero on 25 runs, is mis-gating in *both* directions — passing
  large-float names and rejecting small-float ones. That is the highest-value change here, and it
  is worth more than any re-tune of the threshold. Refs
  [#109](https://github.com/bennetwi92/small-cap-stack/issues/109),
  [#41](https://github.com/bennetwi92/small-cap-stack/issues/41).

## What this is

Max R is a **ceiling on what the trade offered** under a fixed consolidation-low stop — not what
an exit policy would have captured. None of this is expectancy.

Seventeen trading days in a single market regime is a small sample, and the per-bucket tail
probabilities rest on tens of runs, not hundreds. The float-source disagreement above means the
bucket a run lands in is itself uncertain for a good fraction of rows.

This analysis was run on **2026-07-25** against the tracker store and published here on
2026-07-31 without re-running it, so the window ends 2026-07-24 and the last week of collected
data is not included. The harness was a one-off and was never committed, so these numbers are
reproducible only by re-deriving the query — worth doing when the sample is meaningfully larger,
and worth committing the script that time.
