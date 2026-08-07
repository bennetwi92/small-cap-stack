# Throughput & estimation — calibration for "how long will this take"

Moved out of `CLAUDE.md` in #540: this is **calibration data**, not a rule, and it is read in full
at the start of every task where it is needed on perhaps one in twenty. `CLAUDE.md` keeps the tier
names (the board's **Size** field uses them) and points here.

Use these as **estimation anchors, not targets** — they are what one focused agent day actually
delivers on this repo's trunk-based, one-issue-per-PR flow.

**Baseline sample: 2026-07-17** — **40 merged PRs** in a ~9-hour window (~4–5 PRs/hr, one every
~13 min average), **110 files** touched, **+8.4k / −2.8k** lines (~5.6k net).

## The size tiers

Estimate a task by mapping each PR to a tier. These are the same four values as the project board's
**Size** field, so a slice of the board can be costed and these anchors checked against reality.

| tier | shape | lines / files | time | examples |
|---|---|---|---|---|
| **XS** | one-liner, doc or config tweak | ≤50 lines, 1–5 files | ~5–10 min | a cost correction (#277), a health-gate fix (#357), an `id-token` permission add (#370) |
| **S** | one focused change, tests included | 50–250 lines | ~10–15 min | dt-scoping a hot read (#324/#325), one guarded workflow (#362), a roadmap doc (#315) |
| **M** | new module, non-trivial refactor, one dashboard screen | 250–850 lines + tests | ~20–30 min | the calendar gate (#326), tick instrumentation (#327), a cockpit view (#293/#294) |
| **L** | foundational / cross-cutting | 850–1300 lines, many files | ~30–45 min | the cockpit foundation (#292), extracting shared bull-flag primitives + deleting the legacy detector (#301), the infra watchdog (#358) |

S is the bulk of a day.

## Rules of thumb

Median PR ≈ **110 lines / ~4 files**. Whole *themes* — the cockpit rebuild (#287–295), the
automation layer (#332–370) — land in ~1.5–2 hrs each.

## Three caveats that keep estimates honest

1. **Fixed overhead per PR.** Issue + board move + `make check` + CI's `lint-typecheck-test` +
   squash-merge. So **ten XS PRs cost more than one M PR of the same total diff** — prefer batching
   trivia.
2. **Apparent cadence overstates serial speed.** PRs are often built in parallel and **merged in
   bursts** — the 10 automation PRs merged in ~50 min on 2026-07-17 were authored beforehand. Don't
   promise 40/day as a linear rate.
3. **Box / IBKR / spike work is not estimable from this table.** It is gated by runtime and market
   hours, not authoring speed. See `CLAUDE.md` → "Working remotely" and `deploy/RUNBOOK.md`.
