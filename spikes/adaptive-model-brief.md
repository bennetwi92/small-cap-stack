# Brief: rules, risk and target for the small-cap book

A statement of what I want built, written so that someone can work it out **independently**.

---

## Read this first

Earlier analysis of this exact question already exists — in `spikes/regime_panel.py`,
`spikes/regime_scan.py`, `spikes/rule_sweep.py`, `spikes/adaptive_book_sweep.py`, the `#690`
sections of `spikes/README.md`, and the comments on GitHub issue **#690**.

**Do not read any of it until you have your own answer.** The point of this brief is a second
opinion, and a second opinion that has read the first one is not worth having. Reach your own
conclusion, write it down, *then* go and compare. Where you disagree, say so — I want to know.

Same goes for `docs/reports/2026-08-13-the-adaptive-model-*.md` and `research/decisions.md`
§D-23 / §D-38 / §D-39 / §D-40. Those record what was decided before and why. Useful afterwards,
biasing beforehand.

---

## What I want

Three things, and they work **together** rather than separately:

1. **A set of rules that pick the best opportunities** out of everything the scanner surfaces.
2. **An adaptive risk model** — step risk up and down as things go well or badly. What I had in
   mind is what a discretionary day trader does: back off after losing days, size up after winning
   ones, and stop entirely during a bad stretch. I want to know when to stop and when to push.
3. **An adaptive target** — if trades are running to +4R lately, take +4R, provided the maths
   supports it.

My assumption behind 2 and 3 is that **recent performance tells you something about the near
future**. That is a hypothesis, not a given. Test it rather than assume it.

Treat all three as one system. A filter changes which trades you get, which changes what target
suits them, which changes what risk is sensible. Optimising any one of them with the other two held
fixed will give a misleading answer.

---

## What I expect from it

- **About 0.8 trades per day.** That is the capacity I want to run at. A result that only works by
  taking one trade a fortnight is not useful to me, and neither is one that needs five a day.
- **Net of costs.** The account is $500 and commission has a per-side minimum, so costs are a real
  fraction of every trade rather than a rounding error. See `spikes/excel-fees-brief.md` for how the
  cost model works.

---

## The data

- Two stores of collected sessions: the live tracker's `/data`, and the reconstructed history in
  `/data/recon`. Together they cover roughly 200 trading sessions.
- **Pre-market only.** I trade names the scanner surfaces before 09:15 ET. Opportunities first seen
  after that are in-market and are not what this system is for.
- `CLAUDE.md` explains how to reach the box and how the stores are laid out.

---

## Ground rules

**The rules currently in the engine and the book are not a starting point.** The price band, the
minimum stop distance, the trigger-time window, the exhaustion cap, the shape gates, the trade-per-
day cap — all of them were fitted on a much smaller sample than we now have. Measure them like any
other candidate. If one earns its place, keep it. If it does not, say so. Do not assume any of them
is correct because it is currently switched on.

**Only use what was knowable at the time.** Every rule has to be decidable at the moment the trade
would fire, using information that existed then. Ranking a day's opportunities against each other,
or using anything that depends on how the day turned out, is not a valid rule.

**Tell me how you know a result is real.** If you try enough ideas, something will look good by
accident — that is how the current rules got here. I want to see the difference between "this
works" and "this is the best of everything I tried". How you demonstrate that is up to you.

**No jargon.** I am a trader, not a statistician. Explain findings in plain English — "about 30 in
every 100 trades reach the target", not a table of test statistics. Keep the maths in the code and
the write-up on the issue.

---

## What I want at the end

- The rules, stated plainly enough that I could apply them by hand.
- The target, and why.
- The risk model, and when it backs off and when it pushes.
- What it would have made, net of costs, and what the worst losing stretch looks like.
- An honest statement of what you are unsure about.

If the answer is that something I have asked for cannot be done with this data, tell me that
plainly and tell me what would change it. I would rather know than be given a result that does not
hold up.
