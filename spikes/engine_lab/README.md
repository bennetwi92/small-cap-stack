# Engine lab — three parallel investigations into rules, risk and targets

Follow-on from #690, which found no day-level regime and pointed at the **opportunity level** as
where a signal might live. Three agents work in parallel on one shared population, each owning one
question, each in its own folder. Nothing here is production code; it is a search for a rule set.

    spikes/engine_lab/common.py     the SHARED harness — dataset, replay, book, costs, scoring
    spikes/engine_lab/rules/        agent A — which setups to take
    spikes/engine_lab/risk/         agent B — how much to bet and how many
    spikes/engine_lab/exits/        agent C — where the stop and target go

Outputs (never committed) go to `data/spikes/engine-lab/<folder>/`.

---

## The objective

**Consistent positive return, net of costs, at a believable trade frequency.** In order:

1. **Net R per trade > 0** on a held-out period the rule was not fitted on. Net, not gross —
   costs turn the shipped book's +11.0R into +0.9R.
2. **At least ~0.5 trades per session** (≈100 trades over the 197 sessions). The shipped rules
   already do this; a rule that only fires 20 times is not confidence-building however good it
   looks.
3. **Positive in most sub-periods**, not positive once and flat forever. Six walk-forward blocks,
   most of them green, beats one big win.
4. **Shallow drawdown in R.** A curve you would actually keep trading through.

Fixed by the user, not up for optimisation:

- **$500 account, 5% risk, 50% notional cap, net of IBKR costs.** Real money, real drag.
- **Simple bracket exits only** — one stop and one target per trade, placed at entry. No trailing
  stops, no scale-outs, no time stops. It has to be an OCA order you can leave alone.
- **Pre-market only.** In-market entries are dropped from both halves.

## The population — 3,639 rows, 197 sessions

`common.load_panel()`. Live and recon combined and treated as one dataset. Both halves are cut to
pre-market triggers (`trigger_et_min < 570`) and to rows with a real consolidation range.

Base rate: **−0.25R per trade** at any fixed target between 0.5R and 4R. The pool is a loser; the
whole question is whether a decidable-at-entry rule carves a positive subset out of it.

`passed` (the shape gate) is **not** applied and is not a given — on this population it is *worse*
than the raw pool (−0.278 vs −0.247 R/trade over 270 rows). Test it, don't assume it.

## The splits — and the one rule that matters

    DEV      2025-10-30 .. 2026-04-30    125 sessions, 2,012 rows     fit here
    VAL      2026-05-01 .. 2026-06-30     41 sessions,   977 rows     check here, freely
    HOLDOUT  2026-07-01 .. 2026-08-13     31 sessions,   650 rows     DO NOT TOUCH

**Nobody looks at HOLDOUT.** Not once, not "just to see". It is spent in the synthesis step, on one
composed engine, and it is the only number that will be believed. If you report a holdout figure
you have destroyed the only out-of-sample evidence this project has.

## Anti-overfitting — mandatory, not advisory

There are 3,639 rows but only ~100–400 *takeable* trades under a 2-a-day cap. That supports very
few free parameters. Every proposal must come with all four of these:

1. **A complexity budget.** ≤5 thresholds in the final rule set. Say what each one costs you.
2. **Walk-forward** (`walk_forward()`): fit on the past, trade the next block, six times. Report
   how many blocks were positive. A rule that needs all 197 sessions to be fitted is a description
   of the past.
3. **Sensitivity** (`sensitivity()`): move each threshold ±20% on its own. A real edge sits on a
   plateau. A sign flip is a hole in the data.
4. **Permutation** (`permutation_pvalue()`): does a random rule taking the same number of trades on
   the same days do as well? If yes, you selected a trade count, not a population.

Additionally: **any rule must work on both `recon` and `live` halves.** `score()` splits by source.
A rule that only works on one half is a coincidence.

## No-lookahead — the three ways it sneaks in here

1. **Outcome columns.** `max_r`, `stopped_out`, `mae_r`, `bars_to_max_r`, and the whole-session
   aggregates `day_volume` / `day_dollar_volume` / `day_high` / `day_low` / `run_count` are all
   unknowable at 07:00. `assert_no_lookahead()` checks a column list for you. Use
   `cum_dollar_vol_to_trigger` and `ext_at_trigger` instead of their day-level cousins.
2. **Within-day ranking.** Taking "the best 2 of the day" needs the day to have finished. The book
   takes the **earliest** N triggers that pass, always. `build_book()` enforces it.
3. **Choosing a threshold by looking at the answer.** That is what the walk-forward is for.

## Working agreement

- Read `common.py` before writing anything. Do not fork it, do not edit another agent's folder.
- If `common.py` needs a new capability, add it **backwards-compatibly** and note it in your
  write-up — three agents share it and a changed number moves everyone's results.
- Stay in `spikes/` and `data/spikes/`. Do not touch `src/`, do not run `make check`, do not
  commit, do not push, do not open issues or PRs.
- Everything runs locally with `.venv/bin/python`. Nothing touches the VPS.
- Print your headline numbers. Write your findings to `<folder>/FINDINGS.md` and your proposed
  configuration to `data/spikes/engine-lab/<folder>/result.json`.
