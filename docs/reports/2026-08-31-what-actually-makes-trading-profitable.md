---
title: Back to basics — what the evidence says actually makes trading profitable
published: 2026-08-31
summary: A survey of the systematic and retail-trading literature on where profit comes from. The single most transferable finding — from the one large-sample day-trading paper that found a real edge — is that selection is the alpha and the pattern is not. Our own data already says the same thing, twice.
tags: strategy, research, process
author: Claude
---

## The answer, up front

The request was to go back to basics and ask what, across systematic and retail trading, actually
raises the probability of making money. Nine hours of reading later, the literature converges on a
smaller set of answers than the volume of writing about trading would suggest — and most of them
are uncomfortable, because they say the thing traders spend their time on is not the thing that
pays.

**Five findings, ranked by how much they should change what we do:**

1. **The edge is in selection, not in the pattern.** The one large-sample study to find a durable
   intraday day-trading edge in US equities ran the *same* breakout rule over 7,000+ stocks and
   over a filtered subset. Unfiltered, it returned 29% across eight years. Filtered to "stocks in
   play" — abnormal opening volume — it returned ~1,637% at a Sharpe of 2.81. **The pattern was
   held constant. The filter was the entire result.** We have spent most of our engineering effort
   on the shape grammar of a bull flag and comparatively little on *which* flag to take.
2. **Our own data has told us this twice already and we have not fully acted on it.** The engine
   feature analysis (2026-07-31) found the gate set worth nothing over the whole population and
   +0.64R inside the window the book buys from; and found the largest single effect in the dataset
   — stop distance, +0.72R, the only contrast surviving multiplicity correction — to be a variable
   *the engine does not look at*. The Open Drive selection report (2026-08-02) turned the same
   month from −0.5% to +6.0% by changing only which candidate got picked. Both are the same
   finding as (1).
3. **We are almost certainly under-powered, and the honest fix is more independent bets, not
   better ones.** Sharpe scales with the square root of the number of independent bets. We take
   roughly 0.44 setups a session with ~60% of sessions producing none. At that rate no amount of
   rule refinement produces a statistically legible answer inside a year.
4. **At $500 the account, not the strategy, is the binding constraint** — and the arithmetic is
   published in `research/broker-costs.md`: ~9–13% per month of cost drag at $500 against ~2.9% at
   $2,000. A strategy needs to be very good to survive a 10%/month headwind, and no strategy we
   have measured is that good. **Any conclusion drawn about strategy quality at $500 is partly a
   conclusion about capital.**
5. **The base rate is brutal and it is mostly a story about the same people persisting.** In
   Taiwan, ~5% of active day traders had positive cumulative net returns; under 3% were
   *predictably* profitable. In Brazil, of those who persisted past 300 sessions, 97% lost money.
   The thing that separated the survivors was not conviction or effort — it was having already
   been profitable, with experience. Which is to say: **the base rate is a selection statistic
   about traders, exactly as (1) is a selection statistic about trades.**

The rest of this report is the evidence, organised by decision it should inform, followed by a
ranked list of what I would change and — as important — what the evidence does *not* support
changing.

---

## Part I — The base rate, and what it actually says

It is worth being precise about the discouraging numbers, because the imprecise version ("95% of
traders lose") is both useless and slightly wrong.

**Taiwan (Barber, Lee, Liu & Odean).** The cleanest dataset in the field: every transaction on the
Taiwan Stock Exchange, 1992–2006, 3.7 billion trades, individuals accounting for >99% of day
traders. The findings:

- Around **5% of active day traders held positive cumulative net returns** over 1995–2006.
- Day traders lost an average of **23.9 basis points per day net of fees**.
- Traders who were both *previously profitable* **and** had **40+ days of prior-year experience**
  went on to earn reliable profits — but this group was **under 3% of active day traders**.
- Survival: **44% at one year, 24% at two, 15% at three.**
- **Unprofitable traders generated 72% of aggregate day-trading volume** (rising toward 80% in
  later years); the predictably-profitable group generated 9.81%.
- Losing did not deter continuation. **95.3% of unprofitable traders with 50+ prior trading days
  kept trading within 12 months** — statistically indistinguishable from the 96.4% continuation
  rate of profitable traders at the same experience level.

That last pair of numbers is the paper's real contribution. It is evidence *against* rational
learning: people update on wins and not on losses, so the population of active traders is
persistently dominated by people the data says should stop.

**Brazil (Chague, De-Losso & Giovannetti, "Day Trading for a Living?").** 19,646 individuals who
began day-trading mini-Ibovespa futures 2013–2015, followed to 2017. Restricting to those who
**persisted at least 300 sessions** — i.e. removing everyone who quit early, the most generous
possible sample:

- **97% lost money.**
- **0.4%** earned more than a Brazilian bank teller (~US$54/day).
- The single best performer made US$310/day — with a daily standard deviation of **US$2,560**.
- Regression on the 300+ day cohort found **no evidence of learning from experience.**

**Prop-firm evaluations** give a modern, if less rigorous, read on the same question. Vendor-
published aggregates (treat with caution — these are marketing-adjacent numbers, not audited
research) put challenge pass rates at **5–14%**, with one analysis of 300,000+ accounts across ten
firms finding 14% passed; roughly **7% of all entrants ever receive a payout**; and around **71% of
first-phase failures come from daily drawdown breaches** rather than from a lack of gross edge.

**The FTC vs Warrior Trading** matters here because it is the base rate for *the specific strategy
family we are trading*. Warrior marketed that "over 90% of retail traders fail" while "our
community is over 80% profitable". The FTC examined actual customer brokerage records and found
the vast majority made no money or lost money; the case settled in 2022 for $3M, with $2.9M
returned to consumers in January 2023. Ross Cameron's own trading results were not alleged to be
false — the deception was the *implication that they transferred*.

### What I take from Part I

Three things, none of them "give up":

- **Profitability is a selection statistic, not an effort statistic.** The one robust positive
  predictor in the Taiwan data is *prior measured profitability plus experience*. That is a
  statement about identifying which traders (and by extension which setups, which days, which
  symbols) are in the good tail — not about trying harder within an undifferentiated population.
- **Persistence is not evidence.** 97% of the Brazilian 300-day cohort lost money. "We've stuck
  with it and believe it's there" is the exact prior the data warns about. This is not an argument
  against continuing; it *is* an argument for making the continue/stop decision on a
  pre-registered statistical criterion rather than on belief.
- **A systematic system's real advantage over the retail base rate is behavioural, and we already
  have it.** The dominant documented failure mode is biased updating and overtrading under
  pressure. A system that decides mechanically, logs everything and reviews on a schedule
  structurally cannot make those errors. That is worth a lot, and it is banked — it is not
  something more work will buy us again.

---

## Part II — Where edge actually comes from: selection, not the pattern

This is the most actionable section in the report.

### The natural experiment: ORB with and without a selection filter

Zarattini, Barbon & Aziz (2024, Swiss Finance Institute RP 24-98) tested a five-minute opening-
range breakout across **7,000+ US stocks, 2016–2023**. Because they published both the unfiltered
and the filtered version, the study is effectively a controlled experiment on the value of
selection:

| Version | Universe | Result |
|---|---|---|
| Plain 5-min ORB | all 7,000+ stocks | **29% total return** over ~8 years |
| Same rule + "Stocks in Play" | abnormal opening volume, top 20 by rank | **~1,637% net, Sharpe 2.81, ~36% annualised alpha** |
| Benchmark | S&P 500, same period | ~198% |

The entry logic, the exit logic and the risk model were held constant. The only change was *which
stocks were eligible*. The paper's own conclusion is that abnormal opening volume is the critical
filter — the edge is **event-driven stock selection**, and the breakout is merely the timing
mechanism that converts a selection view into a trade.

The companion paper (Zarattini & Aziz 2023) on QQQ/TQQQ is much weaker evidence and should be
discounted: it reports 676%/1,484% against QQQ's 169%, but with a **24% win rate** carried entirely
by a small number of large winners, **assumed no slippage**, no clearly separated out-of-sample
period, and an independent replication finding **break-even at roughly 2.2¢/share of slippage**.
That is a strategy that exists only in a frictionless world. The Stocks-in-Play paper is the one
worth learning from; even it lacks a clean out-of-sample split and models costs incompletely.

### The same idea in the systematic literature

- **Cross-sectional ranking beats binary gating.** The learning-to-rank literature (Poh, Roberts et
  al.) finds that framing the problem as "rank today's candidates against each other" outperforms
  both binary classification and absolute return forecasting. A gate answers *is this
  acceptable*; a ranker answers *is this the best one available today*, which is the question a
  capacity-constrained book actually faces.
- **Signal strength carries information that a threshold throws away.** Forecast accuracy rises
  monotonically with how far a model's score sits from the decision boundary. Converting a
  continuous score to a pass/fail discards exactly that.
- **Meta-labelling** (López de Prado): separate the decision of *side* from the decision of
  *size/take-or-skip*. A primary model proposes; a secondary model, trained on whether the
  primary's signals actually worked, decides confidence and sizing. It is designed to raise
  precision without sacrificing recall, and it is the formal version of "the pattern proposes, the
  selector disposes".
- **Triple-barrier labelling**: label each candidate by which of {profit target, stop, time limit}
  is hit first. This is *already* essentially what our `rmetrics` Max R and `simulate_exit` do —
  we are one relabelling away from having a supervised dataset in the standard form.

### What our own data already says

Two of our published reports are the same finding, arrived at independently:

- **"Engine feature analysis" (2026-07-31).** Across all 787 triggered setups, takeable and
  non-takeable are statistically indistinguishable (+0.04R, p = 0.80). Inside the pre-market price
  window the book buys from, the same gates are worth **+0.64R** per trade. The gates are not a
  general truth about bull flags; they are a conditional truth about a sub-population. Meanwhile
  the biggest effect in the whole dataset — **stop distance, +0.72R, CI [+0.50, +0.97], the only
  contrast to survive multiplicity correction** — is not a feature the engine considers, and the 17
  setups with a sub-5¢ stop went **0-for-17**. And the 0–1 quality score does not rank
  profitability at all: ρ = +0.053, flat expectancy across quintiles, with ~0.38 of the weight
  budget a constant offset that ranks nothing.
- **"Open Drive: picking the day's stock" (2026-08-02).** Same signals, same month, same costs.
  Changing only *which* of the day's candidates was committed to — from "first to trigger", which
  turned out to be an alphabetical lottery landing on cap-crushed ~1%-risk trades, to a banded
  sequential commit ranked by planned stop distance — moved the month from **−0.5% to +6.0% at
  4.2% drawdown**.

Both say the same sentence: **we have a proposal mechanism that works well enough and a selection
mechanism that is barely doing anything.** The engine's score is a gate dressed as a ranker. The
literature and our own measurements agree that this is where the money is.

---

## Part III — The arithmetic that decides whether an edge survives

### Break-even and cost

Expectancy = (win rate × avg win) − (loss rate × avg loss). Break-even win rate = 1/(1+R:R): a 1:2
needs 33.3% gross, but roughly **35–37% net** once realistic costs are added; a 1:1 needs 50% gross
and ~53–55% net. That gap between gross and net is the whole game for a small account.

Our own cost work (`research/broker-costs.md`) puts the drag at **~9–13% per month at $500, versus
~2.9% at $2,000**, because IBKR's *per-order minimum* — not the per-share rate — dominates at small
order sizes. Costs scale with order count and share count, **not** with capital. Two consequences
that I think are under-weighted in how we currently reason:

1. **Every strategy conclusion drawn at $500 is confounded with capital.** Open Drive made +5.67R
   over 13 trades and still ended the month down on its own $500. A strategy that is *right* and
   still loses money is telling you about the account, not the signal. We should be evaluating
   strategy quality in **R**, and capital adequacy separately in **dollars**, and never let the
   dollar result veto a positive R result.
2. **Fewer, larger orders beat more, smaller ones**, mechanically, at this account size. This
   argues against anything that increases order count (scaling in, partial exits, multiple
   concurrent positions) until capital rises.

### Frequency, and why it is the constraint

Sharpe is proportional to the square root of the number of **independent** bets per year. We
currently produce ~0.44 book-eligible setups per session with ~60% of sessions producing none —
roughly 100 trades a year if nothing changes.

Practitioner rules of thumb for statistical legibility: **30 trades** is the bare minimum for any
inference, **100** makes data usable, **200** convincing, **500** strong. More formally: for a
strategy whose true information ratio is 1, expected statistical significance grows as √t, and
**two standard deviations takes about four years**. Our true IR is very unlikely to be 1.

**At the current trade rate we cannot resolve the question we are asking, on any timescale we care
about.** That is not a reason to lower standards; it is a reason to treat *raising the number of
independent bets* as a first-class objective alongside raising expectancy — and to accept that a
modest, well-evidenced edge taken 400 times a year beats a large, unmeasurable one taken 60 times.

### Sizing and survival

- **Kelly** gives the growth-optimal fraction, and essentially nobody trades it. Full Kelly
  routinely produces **50–80% drawdowns** along the path even with genuine positive expectancy
  (Gehm 1983 documented >50% drawdowns in futures). **Half-Kelly captures roughly 75% of the
  growth rate at a fraction of the drawdown**, and common practice is a quarter to a third of the
  Kelly fraction.
- The prop-firm data point is relevant here despite its weak provenance: **~71% of evaluation
  failures are drawdown breaches**, not absence of edge. Ruin is a separate failure mode from
  being wrong, and it kills more accounts.
- Our book currently sizes at **full buying power** (`research/strategy.md` §3). That is not a
  risk-fraction rule at all — it is 1.0× notional with the risk fraction falling out of wherever
  the stop happens to sit, which is precisely why the Open Drive report found trades landing at
  ~1% effective risk while others sat far higher. **Sizing that does not target a risk fraction
  cannot be Kelly-anything**; it is a capital constraint masquerading as a sizing rule. At $500
  that may be unavoidable — but it should be *named* as a constraint we are accepting, not carried
  as if it were a design choice.
- Relevant regulatory change: **FINRA's $25,000 pattern-day-trader minimum was replaced in June
  2026** with a risk-based intraday margin framework, with a $2,000 minimum for margin accounts.
  This does not directly bind us (a UK cash account avoids PDT anyway, and T+1 settled-cash is our
  actual constraint), but it changes the landscape for any future decision about account
  structure.

---

## Part IV — How you know an edge is real

This is where systematic trading has genuinely rigorous answers, and where the discipline is worth
more than any individual rule.

### Multiple testing is the dominant risk

- **The expected maximum Sharpe grows with the number of trials.** Among N independent backtests of
  strategies with *zero* true edge, the best observed Sharpe rises roughly with **√(2 ln N)**
  standard errors. Try 45 variants and the winner looks good by construction.
- **Deflated Sharpe Ratio** (Bailey & López de Prado 2014) adjusts an observed Sharpe for the
  number of trials, the sample length, skewness and kurtosis. **Probability of Backtest
  Overfitting (PBO)** asks, via combinatorial cross-validation, whether your *selection procedure*
  tends to pick strategies that underperform the median out of sample.
- **Minimum Backtest Length**: as summarised by the authors, ~45 independent trials require on the
  order of **3.3 years of daily data** before an in-sample annual Sharpe of 1 is credible at 95%
  confidence.

**We should be counting our trials, and we are not.** Every gate threshold tried, every window
tested, every knob in `config.py` that was tuned rather than derived, is a trial. The
`decisions.md` log is an excellent record of *what we chose*; it is not a record of *how many
things we looked at before choosing*. A running trials counter — even an approximate one — would
change how we read every subsequent result.

### Validation discipline

- **Walk-forward analysis** is the practitioner gold standard: optimise on a window, evaluate on
  the immediately following unseen window, roll forward. Every evaluated trade was produced by
  parameters fitted without it. **Walk-Forward Efficiency** (out-of-sample performance ÷ in-sample
  performance) **> 0.5** is a reasonable robustness bar.
- **Parameter plateaus, not peaks.** A robust rule performs acceptably across a *neighbourhood* of
  parameter values. A threshold that only works at exactly its chosen value is an artefact.
- **Block bootstrap by day, not by trade.** We already do this — the engine feature analysis
  resamples days with replacement (3,000 draws) and permutes labels within day, with Holm
  correction over ten pre-registered contrasts. That is genuinely better practice than most of
  what is published in this space, and it should be kept as the house standard for every future
  claim.
- **Pre-registration.** Deciding the hypothesis, the metric and the decision rule *before* looking
  is the cheapest single defence against the above. Ten pre-registered contrasts is exactly right;
  the discipline should extend to strategy-level go/no-go criteria.

### And even a real edge decays

McLean & Pontiff found returns to 97 published characteristics decline by **~58% after
publication**, of which roughly 26 percentage points is attributable to statistical bias/data-
mining and the remainder to genuine crowding. Later work finds the Sharpe decay of newly published
factors worsening by about 5 percentage points per year of publication vintage.

The pre-market small-cap momentum setup is not an obscure academic anomaly — it is taught by a
company the FTC sued for overstating how well it transfers, and it is traded by a large, well-
equipped retail cohort plus the market makers who serve them. **The prior that this specific
pattern retains a large, easily-harvested edge should be low.** Which returns us to Part II: if
there is money here, it is in the selection layer that everyone else is doing badly, not in the
flag.

---

## Part V — The gap between the simulation and the fill

A 20–50% haircut from backtest to live is the commonly cited range, and short-horizon strategies
suffer the most because they are the most sensitive to per-trade friction.

The mechanisms that matter for our universe specifically:

- **Microcap spreads and depth.** Wider spreads, thinner books, fewer participants. A historical
  bar tells you where price traded — not whether your order would have been filled there, nor how
  much size sat ahead of you.
- **Our exits are limit orders in the pre-market** (`research/phase-2-roadmap.md`: pre-market is
  limit-only, so the app fires every entry and exit itself). The **exit-limit fill policy — how
  far through the bid a stop exit is priced — is the single parameter most likely to make live
  results diverge from the book**, and it is unfalsifiable until we have live fills. Simulated
  2-tick exit slippage is a guess, and a fast drop in a low-float name will not respect it.
- **Halts, SSR and borrow** are real constraints in this universe. A halt makes a stop unusable
  (we already reject flags whose breakout doesn't exceed their stop for exactly this reason).
  SSR triggers at −10% from prior close and restricts short sales to upticks. Borrow on small-cap
  runners migrates from easy-to-borrow to hard-to-borrow *within a session*.
- **Data quality biases.** Survivorship and delisting bias inflate equity backtests; point-in-time
  data has been estimated to cut overestimation by ~2%/year. Our capture-raw/compute-derived design
  is the correct defence and should be defended — it is the reason we can re-ask methodology
  questions retroactively at all.

**Credit where due:** the prefix-stability work (2026-08-08) — 2,018 of 2,018 fired runs matching
the full-day answer exactly across 81 sessions — is precisely the right kind of pre-live
validation, and it is the discipline that separates a system that will survive contact with live
fills from one that will not. The report's own caveat is the important one: it clears the
algorithm, not the inputs.

---

## Part VI — Diversification is the one free lunch, and we are not eating it

Sharpe scales with **√N** in the number of *independent* bets. Carver's practical framing: four
genuinely uncorrelated return streams roughly **double** your Sharpe; two uncorrelated streams give
a diversification multiplier of √2 ≈ 1.4.

The ceiling matters too: with average pairwise correlation ρ̄, the maximum achievable Sharpe
multiple for an equally weighted combination is bounded by **1/√ρ̄**. Adding a tenth variant of the
same idea buys almost nothing; adding a second *genuinely different* idea buys a lot.

We have one traded strategy and one specified-but-not-traded second strategy (Open Drive) whose
correlation to the first is plausibly low — different time of day, different trigger, different
holding period. The reports found merging it costly to the adaptive book at $500, which is a
*capital* finding, not a *diversification* finding. **The √N argument says a second, third and
fourth uncorrelated stream is the highest-expected-value structural change available to us** — and
it also directly addresses the frequency problem in Part III, because independent bets is the same
quantity in both arguments.

Candidate additional streams, in rough order of how different they are from what we have:

1. **Open Drive** (already specified and measured).
2. **The short side of the same universe** (see Part VII).
3. **A different time-of-day regime** — our own time-of-day report found forward excursion peaking
   in an hour the engine ignores.
4. **A different holding period** — an overnight or multi-day continuation on the same scan
   universe would be nearly uncorrelated with an intraday trigger.

---

## Part VII — The uncomfortable question: are we on the right side of the trade?

I want to put this on the record because the evidence is one-directional and we have never
formally considered it.

Every characterisation of the pre-market small-cap gapper in the literature and in the vendor data
describes a **structurally fading asset**:

- Vendor statistics from SmallCapLab (unaudited, treat as a hypothesis and not a result) report
  **71.5% fade rates for gappers with 5M+ pre-market volume** against 56.2% for lower-volume names;
  **~75.2% fade rates in the $5–10 price bucket**; and that when a 30%+ gapper closes below the
  prior close it averages **12.5% beneath** it, with 100–150% gappers averaging a **32% high-of-
  day-to-close drop**.
- S3 Partners reported that in 2023 shorting small caps produced **+15.11%** mark-to-market
  (~$12.9bn on $85.6bn average short interest) while mega-, large- and mid-cap shorts lost money.
- The academic literature on intraday reversal finds short-term reversal returns **considerably
  larger in small caps** (1.41%/month vs 0.84% for large caps), and that gaps of ~50–300% are
  followed by strong reversals.
- The practitioner literature on this exact universe is explicit about dilution, toxic financing
  and pre-announcements engineered to sell into strength — i.e. an informed seller is often on the
  other side of our buy.

**We are systematically buying the instrument that the weight of evidence says is the better
short.** That does not make our long strategy wrong — buying a pre-market bull flag at 07:00 and
fading a failed high at 10:00 are different trades — but it does mean the long side is fighting a
structural current, and it explains a lot about why an otherwise well-built engine keeps producing
marginal expectancy.

**The honest counterweight:** shorting this universe is not available to us in any near-term form.
It needs a margin account (we run cash, by T+1 necessity), borrow and locates on names that go
hard-to-borrow intraday, SSR-aware order handling, and it carries genuinely unbounded risk in
exactly the tail — the squeeze — that this universe specialises in. **I am not recommending we
short.** I am recommending that the fade evidence be treated as information about the long
strategy: it argues for *shorter holds, earlier targets, and a bias toward the pre-market window
over the regular session*, all of which our own time-of-day report already found independently
(entries decay monotonically through the session, significantly after 11:00).

---

## Part VIII — Behaviour and process, including for a machine

Even a fully systematic operation runs on human decisions — which rules to change, when to stop,
how to read a result. The documented failure modes apply to those decisions as much as to trades.

- **Overtrading.** Barber & Odean: the most active 20% of investors underperformed the least active
  by ~6 percentage points a year; in aggregate, Taiwanese retail trading losses ran to roughly 2%
  of GDP.
- **Disposition effect.** Selling winners early and holding losers is among the best-replicated
  findings in behavioural finance, and it directly destroys the right tail that momentum strategies
  live on. Our fixed-R exits are structurally immune — this is a real, banked advantage.
- **Biased learning.** The Taiwan finding again: people over-weight successes and under-weight
  failures when updating beliefs about their own ability. The organisational version of this is
  keeping a rule because it worked in a memorable month.
- **Deliberate practice and journalling.** The performance literature (Steenbarger and others) is
  consistent that *structured review of recorded decisions* accelerates skill acquisition far more
  than raw repetition. Our review workbench, the ~167 hand-made annotation files on `review-data`
  and the 25 golden fixtures are exactly this asset, and they are irreplaceable.

The process practices that most reliably correlate with survival, across every source read:

1. **A written plan per trade class, followed without discretion.** (We have this by construction.)
2. **Risk defined before entry, never widened after.** (We have this.)
3. **A review cadence that examines process quality separately from outcome.** (We have the
   workbench; we do not have an explicit process-vs-outcome scorecard.)
4. **A pre-committed stopping rule.** (We do **not** have this, and I think it is the most
   important missing piece of governance in the project.)

On (4): the Brazilian study is a warning about exactly the failure mode of a well-run, sincere,
persistent effort with no stopping criterion. We should write down, *now*, before the next
result — the number of trades, the confidence interval, and the expectancy threshold at which we
would conclude the pre-market bull flag does not carry an edge worth trading. Not because I think
we are there. Because the value of that criterion is destroyed if it is written after we see the
data.

---

## What I would change, ranked

Each item names the evidence behind it and what would falsify it. None of these are decisions —
they are proposals for the review this report was commissioned for.

### 1. Convert selection from a gate to a ranker *(highest expected value)*

**Evidence:** the ORB Stocks-in-Play natural experiment (29% → 1,637% from the filter alone); the
learning-to-rank literature; our own two reports showing selection outperforming rule refinement.
**Concretely:** the engine emits candidates with a score; the book takes the top-ranked candidate
available at commit time rather than the first that triggers. The Open Drive banded-sequential
commit is already a working prototype of this — and its lookahead-free property came from every
OD-5/5 setup being final at 09:40, which is *not* true of the bull-flag, so the bull-flag version
needs a genuinely causal ranking at trigger time.
**Falsified by:** a ranked book failing to beat first-by-trigger-time out of sample, or by no
causal ranking existing that beats random selection among same-bar candidates.

### 2. Make stop distance a first-class selection variable, not an afterthought

**Evidence:** +0.72R, CI [+0.50, +0.97], the only contrast in our own dataset to survive
multiplicity correction; 0-for-17 on sub-5¢ stops; the Open Drive result was *itself* a
stop-distance ranking. A `select_min_stop_pct` exists in the spec; the finding says stop distance
belongs in the **ranking**, not only in a floor.
**Falsified by:** the effect failing to replicate on data collected after 2026-07-30 (this is
directly testable now — we have another month of data than the report used).

### 3. Write down the stopping rule and the trials counter, before the next result

**Evidence:** Brazil's 300-day cohort; the deflated-Sharpe / MinBTL literature; the fact that our
`decisions.md` records choices but not the search that produced them.
**Concretely:** (a) an approximate count of distinct rule variants evaluated to date, maintained
going forward; (b) a pre-registered go/no-go: N trades, expectancy threshold, CI requirement.
**Falsified by:** nothing — this is governance, not a hypothesis. It costs a day and it is the
cheapest risk reduction available.

### 4. Treat "independent bets per year" as an explicit design objective

**Evidence:** Sharpe ∝ √N; 0.44 setups/session with 60% empty sessions cannot resolve the question;
√N diversification and statistical power are the *same* argument.
**Concretely:** measure and publish bets/year alongside expectancy for every proposed rule change,
and stop treating a rule that raises expectancy while halving frequency as automatically good.
**Falsified by:** demonstrating that additional candidates are so much worse that expectancy falls
faster than √N rises — which is exactly what a ranker (item 1) is designed to prevent.

### 5. Add a second uncorrelated stream, and measure the correlation before merging

**Evidence:** √N and the 1/√ρ̄ ceiling; Open Drive already specified, measured and positive in R.
**Concretely:** run Open Drive as a **separate book** with its own equity, not merged into the
bull-flag book at $500. Measure the realised correlation of daily R between the two. The prior
"merging is costly" finding was a capital constraint at a shared $500, which is a different
question from whether the stream is additive.
**Falsified by:** measured daily-R correlation above ~0.5, which would make it a variant rather
than a diversifier.

### 6. Separate strategy evaluation (in R) from capital adequacy (in dollars)

**Evidence:** Open Drive's +5.67R / −$2.33 month; broker-costs' 9–13% vs 2.9% drag.
**Concretely:** a rule change is judged on R and on bets/year. Dollar results at $500 are reported
as a capital diagnostic and are never allowed to veto a positive-R finding. This is a change in how
we read the dashboard, not in the code.

### 7. Re-examine the exit, with the trend/mean-reversion evidence in hand

**Evidence:** trailing stops carry positive expectancy essentially only in trending regimes (Hurst
> ~0.55) and are negative-expectancy in mean-reverting ones; the fade statistics in Part VII say
this universe is mean-reverting intraday; our time-of-day report says excursion decays through the
session. **This is a coherent case that our fixed-R target is the right family of exit and that the
trailing stop we declined to adopt was correctly declined** — and equally a case for testing
*earlier* targets and a time-based exit, since the vertical barrier is the one we have never
tuned.
**Falsified by:** a target sweep showing the current level dominating shorter ones out of sample.

### 8. Treat capital as a strategy variable

**Evidence:** the cost curve is a step function in account size, and the per-order minimum
dominates. **Not a recommendation to add money** — a recommendation that "what would this system
look like at $2,000" be a standing analysis rather than an afterthought, because every conclusion
we draw at $500 is partly about the account.

---

## What the evidence does *not* support

Being explicit about this matters as much as the recommendations, because the temptation after a
report like this is to change everything.

- **Not more gates.** Our own analysis found roughly a third of the score's weight already inert and
  the gate set worth nothing outside a narrow window. Adding shape rules is the activity with the
  best ratio of effort to feeling-of-progress and the worst ratio of effort to measured edge.
- **Not machine learning, yet.** Meta-labelling and learning-to-rank are the right *concepts*, but
  with ~100 trades a year and a 21–80 session history, any fitted model is a multiple-testing
  machine. Take the ideas (rank rather than gate; separate side from size) and implement them as
  simple, inspectable rules. Revisit models at 500+ trades.
- **Not the trailing stop.** Declining it looks correct given the mean-reverting character of the
  universe. Leave it declined.
- **Not float or news gates.** These remain collected-not-gated by design, and our own float report
  found a tail effect rather than a gradient, with the break nowhere near the historically proposed
  threshold. If float enters the system it should enter as a **ranking term**, consistent with item
  1 — not as a gate.
- **Not lowering the statistical bar because the sample is small.** The correct response to
  under-powered data is more bets and pre-registration, not weaker criteria. Every source in Part
  IV agrees, and it is the single most common way a sincere systematic effort talks itself into an
  edge that is not there.

---

## Sources

Academic and primary:

- [Barber, Lee, Liu & Odean — *Do Day Traders Rationally Learn About Their Ability?*](https://faculty.haas.berkeley.edu/odean/papers/Day%20Traders/Day%20Trading%20and%20Learning%20110217.pdf) ([summary](https://www.tradicted.com/research/barber-learning-2020/))
- [Barber, Lee, Liu & Odean — *Do Individual Day Traders Make Money? Evidence from Taiwan*](https://www.researchgate.net/publication/238220682_Do_Individual_Day_Traders_Make_Money_Evidence_from_Taiwan)
- [Chague, De-Losso & Giovannetti — *Day Trading for a Living?* (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3423101) ([summary](https://www.tradicted.com/research/chagu-day-2020/), [QuantPedia](https://quantpedia.com/retail-day-trading-is-an-uphill-battle/))
- [Zarattini, Barbon & Aziz — *A Profitable Day Trading Strategy For The U.S. Equity Market* (SFI RP 24-98)](https://concretumgroup.com/a-profitable-day-trading-strategy-for-the-u-s-equity-market/) · [critical review of both ORB papers](https://danfin.net/opening-range-breakout-research)
- [Zarattini & Aziz — *Can Day Trading Really Be Profitable?* (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416622) · [independent replication with cost stress-testing](https://github.com/giovannibrusco/zarattini-2023-orb-qqq)
- [Bailey & López de Prado — *The Deflated Sharpe Ratio*](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551), [overview](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio))
- [Poh, Lim, Zohren & Roberts — *Building Cross-Sectional Systematic Strategies By Learning to Rank*](https://arxiv.org/pdf/2012.07149) ([thesis](https://www.robots.ox.ac.uk/~sjrob/Theses/D_Poh_DPhil_Thesis_final.pdf))
- [Meta-labeling — overview](https://en.wikipedia.org/wiki/Meta-Labeling) · [Singh & Joubert, *Does Meta Labeling Add to Signal Efficacy?*](https://hudsonthames.org/wp-content/uploads/2022/04/Does-Meta-Labeling-Add-to-Signal-Efficacy.pdf)
- [*Why and how systematic strategies decay*](https://arxiv.org/pdf/2105.01380) · [*When do systematic strategies decay?*](https://www.tandfonline.com/doi/full/10.1080/14697688.2022.2098810) · [*Not All Factors Crowd Equally*](https://arxiv.org/pdf/2512.11913)
- [*Trading Costs of Asset Pricing Anomalies*](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2019/09/TradingCostofAssetPricingAnomalies.pdf)
- [Intraday price reversals after extreme gaps (Aalto)](https://aaltodoc.aalto.fi/server/api/core/bitstreams/e6417a4e-352d-4f72-8c20-980cefd3ac58/content) · [Short-term reversal in small caps — Alpha Architect](https://alphaarchitect.com/short-term-momentum/)

Regulatory and industry:

- [FTC v. Warrior Trading — case page](https://www.ftc.gov/legal-library/browse/cases-proceedings/2023198-warrior-trading-inc-ftc-v) · [complaint announcement](https://www.ftc.gov/news-events/news/press-releases/2022/04/federal-trade-commission-cracks-down-warrior-trading-misleading-consumers-false-investment-promises) · [$2.9M returned](https://www.ftc.gov/news-events/news/press-releases/2023/01/ftc-returns-more-29-million-consumers-harmed-warrior-trading)
- [SEC approval of the FINRA PDT replacement](https://www.sec.gov/files/rules/sro/finra/2026/34-105226.pdf) ([Schwab summary](https://www.schwab.com/learn/story/sec-approves-scrapping-25000-day-trader-minimum))
- [S3 Partners — *Small Caps Make The Best Shorts*](https://www.s3partners.com/articles/Small-Caps-Make-The-Best-Shorts) · [US stock borrow fees](https://www.s3partners.com/articles/us-stock-borrow-fees)
- [Short Sale Restriction mechanics](https://www.tradingsim.com/blog/the-short-seller-restriction-rule-ssr-explained)

Practitioner (weaker provenance — cited as hypothesis, not evidence):

- [SmallCapLab — gap-up fade rate research](https://www.smallcaplab.com/research)
- [Prop firm pass-rate aggregates](https://thepropfirmguide.com/prop-firm-statistics/) · [second aggregate](https://chartwhisperer.ca/prop-firm-statistics)
- [Walk-forward optimisation](https://blog.quantinsti.com/walk-forward-optimization-introduction/) · [Walk-Forward Efficiency](https://medium.com/@NFS303/walk-forward-analysis-a-production-ready-comparison-of-three-validation-approaches-69cd25fc9fc7)
- [Kelly and fractional Kelly in practice](https://coriva.eu.org/en/kelly-criterion-position-sizing/) · [break-even win rate tables](https://www.pnlledger.com/break-even-win-rate-by-risk-reward-table/)
- [Carver on diversification and √N](https://investresolve.com/podcasts/resolve-riffs-with-rob-carver-on-smart-portfolios-and-the-evolution-of-systematic-trading/) · [the limits of diversification](https://www.ludgerhentschel.com/PDFs/Hentschel%20'25.pdf)
- [Break-even and trailing stops in trading systems](https://quant.fish/wiki/the-truth-break-even-and-trailing-stops-in-trading-systems/)
- [Survivorship bias primer](https://www.quantrocket.com/blog/survivorship-bias/) · [backtest-to-live divergence](https://linetrades.com/articles/backtesting-trading-why-paper-profits/)
- [Steenbarger on deliberate practice](https://quantstrategy.io/blog/the-importance-of-deliberate-practice-in-professional/)

Internal, cited above: `docs/reports/2026-07-31-engine-feature-analysis-…`, `2026-07-31-float-vs-max-r`, `2026-07-31-time-of-day-…`, `2026-08-01-the-2-trade-a-day-cap-…`, `2026-08-02-open-drive-picking-the-days-stock`, `2026-08-08-prefix-stability`; `research/strategy.md`, `research/broker-costs.md`, `research/phase-2-roadmap.md`.
