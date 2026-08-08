# Phase 2 — paper trading: roadmap

How we get from the Phase-1 tracker to paper orders. Companion to `decisions.md` (which locks the
*execution parameters*) — this file locks the *sequence*. Epic: **#308**.

## Where we actually are (2026-07-17)

The **brain is built**; the **body is not**.

- ✅ **Engine v2 is live.** `rmetrics.py:114` and `charts.py:104` call `detect_day_with_settings`.
  (CLAUDE.md's "not yet live — lands with #180" was stale; #297 fixes the docs.)
- ✅ **The paper book exists** — `portfolio/` (`sim`, `exit`, `costs`, `adaptive`, `ledgers`)
  selects, sizes, and simulates exits over the captured dataset, exhaustively tested.
- ❌ **Nothing in the live runtime detects a setup.** `app.py` runs scanner → capture → *one EOD
  bar batch*. The bull-flag engine has zero callers in the live path; it runs compute-on-read for
  the dashboard and review workbench only.
- ❌ **No order code exists.** `grep -r 'placeOrder|bracketOrder|LimitOrder' src/` → nothing.
  `ibkr/` has transport, supervisor, retry, errors, and stops there.
- ❌ **No real-time data.** `marketdata.py:4`: *"the account's feed is ~15 min delayed, so bars are
  **not streamed**"*. `keepUpToDate` was deliberately removed as fragile.

So `decisions.md` §D-21's framing — that only "simulate exit from bars" gets swapped for "place
bracket + capture fill" — is true of the *sizing/selection brain* but understates the gap. Gates 5–7
below are greenfield.

## Preconditions for live data (D-43)

Two rules Gates 5 and 6 are written against, adopted 2026-08-08 before any streaming or order code
existed — which is the only point at which they are free:

- **Streamed bars never enter the `bars` dataset.** An unfinalised `keepUpToDate` bar colliding with
  the EOD batch's finalised one is resolved by a dedup that keeps whichever file sorted first *by
  random UUID*, making the surviving bar non-deterministic and retroactively so. Streamed bars go to
  **`live_bars`**, which is the comparison arm and is never read by the detector, the EOD report or
  the paper book. Pinned by `tests/test_live_data_preconditions.py`.
- **The virtual ledger moves only on `execDetails` / `commissionReport`.** Updating it on submission
  lets an unfilled resting exit close a trade that is still open, free a concurrency slot, and size
  the next day off a fictional exit — which breaks D-21's settled-cash invariant.

## Locked premises

- **Pre-market is limit-only** (#37, confirmed by the trader from live IBKR experience). The app
  fires every entry and exit itself; there are no broker-native stops before 09:30 ET. Switch to
  native stops/brackets in the regular session.
- Engine **v2** is the live engine.
- **≤2 concurrent positions, ≤2 entries/day**, $500 virtual book (`decisions.md` §D-21/#237).
- ⚠️ **The box is in Falkenstein, Germany (`fsn1`) — ~95 ms transatlantic, NOT Ashburn (#323).**
  This bullet asserted "Ashburn, VA … ~10ms" until 2026-08-08 and reasoned about reaction time from
  it; a Phase-2 ramp drafted that day inherited the wrong number straight from here. #323 recommends
  measuring, then rebuilding in `ash` **before** Phase 2 goes live — and that ordering is
  load-bearing rather than advisory, because every fill measured in paper trading before a move is
  measured on a box you are then going to replace.
  The conclusion the old bullet drew still holds and is worth keeping: **milliseconds are not
  achievable from any app-side loop**, so a faster feed means learning sooner, not acting sooner.
  From `fsn1` the floor is ~95 ms rather than ~10 ms, which widens the gap but does not change the
  kind of thing app-side triggering can do.

## The gates

| # | Gate | Issue | Deliverable | Blocked by |
|---|---|---|---|---|
| **0** | Truth debt | #302 · #297 · #270 | Settings flip · docs · spike import | — |
| **1** | Spread capture | **#309** | `whatToShow="BID_ASK"` in the EOD batch → new `quotes` table | 4 |
| **2** | Go/no-go criteria | **#310** | The bar for entering P2, written in `decisions.md` (+ #273 payload OOM) | — |
| **3** | Validation | **#462** | The sample clears Gate 2's bar, and the reconstruction is trusted to carry it | 2 |
| **4** | Market data | **#311** | $10/mo L1 bundle — unblocks everything real-time | 2, 3 |
| **5** | Live detection (shadow) | **#312** | `live_detect.py` — streams bars, detects, **logs only** | 0, 4 |
| **6** | Execution | **#313** | `ibkr/orders.py` + `execution.py` — LMT entry/exit, app-side stop, OMS | 5 |
| **7** | Paper live | **#314** | Reconciliation, live-vs-sim divergence report, order/fill observability | 3, 6 |

**Gate 2 is the only one open today, and money sits in the middle of the ladder.** The account is
not funded until the bar is written (2) and the sample clears it (3); the feed follows the funding
(4); spread capture reads that feed, so gate 1 — numbered before 4 — actually runs after it. Gate
numbers are labels, not order. Gates 5–7 are the build and start whenever Gate 4 lands.

**Gate 3 is no longer a calendar wait (#49, closed 2026-08-07).** It was "3 months of live
collection completes (~2026-10-01)", on the premise that there wasn't enough data to judge the
strategy. The harvest (#431) retired that premise: it rebuilt 31 pre-market sessions in ~3 nights
against 29 the live tracker managed in five weeks, out of ~501 in its two-year window, and #428's
out-of-sample check found **31/31 same trade** (decision + entry bar + stop) on days the calibration
fixtures never saw. Waiting until October would produce a smaller sample, from one regime, later.
The gate is now the *trust* question — #462, is the strategy overfitted or does the reconstruction
diverge — not the *quantity* one. Live collection keeps running regardless: it is the only thing
that can keep validating recon against reality, and 09:30–11:59 exists in live data and nowhere
else (the recon store is pre-market only).

> This table is **mirrored on the dashboard's Plan page** (`docs/plan.js`, `GATES`), which is where
> the trader reads it. The mirror carries each gate's **name, issue numbers and dependencies** only
> — its *status* is derived from whether those issues are closed on GitHub (#414), so a merged gate
> closes on the page by itself. Change a gate's name, issues or blockers here and change them there
> in the same PR; don't add a status field back.
>
> *How the derivation works* (moved here from `CLAUDE.md` in #540): `docs/js/gh.js` reads issue
> state over **unauthenticated REST**, cached 30 min in `sessionStorage`, and falls back to this
> table's `after` dependency graph when GitHub is unreachable. The Plan page also renders the phase
> spine, the live collection countdown, the harvest's progress (#454, from `status.json.harvest`)
> and the Phase-1 checks — all computed at render time from `index.json` / `portfolio.json` /
> `status.json`, never from a committed value.

## The three things that will actually bite

### 1. The exit-limit fill policy (Gate 6)

Limit-only means the app-side stop fires a **limit** order — which can simply *not fill* in a fast
drop, leaving the book holding a loser well through its stop. The mitigation is a marketable limit
priced *through* the bid; **how far through is a parameter that costs money on every exit**. This —
not feed latency — is where "accuracy at the stop matters more than at the target" actually bites.
Gate 1's spread data is what lets us set it from evidence instead of guesswork.

### 2. Prefix stability (Gate 5) — the sleeper

> ## ✅ MEASURED 2026-08-08 (#675) — the detector is causal; this risk is retired
>
> **2,018 of 2,018 fired runs match the full-day answer exactly, with zero churn at any
> intermediate prefix**, over 81 sessions (1,220 recon runs / 909 fired; 1,454 live runs / 1,109
> fired). At minute resolution **762 of 909 fires happen on a partially formed bar** and all 909
> still match. Harness: `spikes/prefix_stability.py`. Report:
> `docs/reports/2026-08-08-prefix-stability.md`.
>
> It is structural. `day.py`'s candidate loop takes the **earliest** cycle with a valid trigger and
> breaks; `entry_trigger`/`entry_fill` come from `bars[cons_end].high` and `stop` from the
> consolidation lows — closed bars strictly before the trigger — and gates, score, exhaustion and
> both selection rules read only bars ≤ trigger. The paragraph below describes `segment_at_end`, the
> end-anchored segmenter; the **live** path is the greedy cycle walk, which does not re-segment.
>
> ⚠️ **It clears the algorithm, not the inputs.** Both arms use the same bars, truncated. Live bar
> formation and revision, missing or late bars, feed restarts, and run/`first_hit` segmentation from
> live scanner hits are all untested. **So Gate 5's question becomes "are the live bars the same
> bars", not "is the detector prefix-stable"** — which wants a hash of the bar series carried with
> each live detection, so a disagreement can be attributed to data rather than logic.
>
> **Gate 5 stays log-only and still precedes order code.** The reason moved; the sequence did not.
>
> The original concern is kept below rather than deleted: it was correct to hold before anyone had
> measured it, and it is why the gate was ordered this way in the first place.

The v2 detector segments the **longest valid** pole+consolidation over a day's bars. Run live
against a *growing prefix*, the segmentation it picks at 08:35 may differ from the one it picks at
16:00. Every R-metric ever recorded, and the entire portfolio sim, is built on the full-day answer —
so live and replay disagreeing would **silently invalidate the sim as a predictor of the live book**.

Gate 5 is log-only and comes *before* any order code precisely to measure this: detect live, diff
against the EOD replay, and either prove they agree or characterise where they can't.

> **The second strategy is immune to this (#418).** `open-drive.md`'s two candles are fixed by the
> clock — the opening range is 09:30–09:35, the consolidation 09:35–09:40 — so both are final at
> 09:40 and the entry/stop levels never move. There is no growing prefix and nothing to re-segment,
> so live and replay cannot diverge. If prefix stability turns out to be expensive to prove for the
> bull-flag, that is a point in Open Drive's favour that has nothing to do with its expectancy.
> It would also be the first strategy able to use **broker-native brackets**, trading after the bell
> rather than under the pre-market limit-only constraint (#37).

Use `reqHistoricalData(..., keepUpToDate=True)` for the live bars (the path `tradepilot.md`
already proved). Second-order benefit: those are **IBKR's own bars**, identical to the stored
history — aggregating our own from ticks would add a live-vs-replay *bar* mismatch on top of the
segmentation question. One divergence source, not two.

### 3. Feed tiers (Gate 5/6)

| Tier | Scope | Feed |
|---|---|---|
| Detection | all open opportunities | 5-min bars (`keepUpToDate`) |
| Armed | setups awaiting trigger | tick-by-tick |
| Position | ≤2 open | tick-by-tick, **never downgraded** |

`reqMktData` is **not** tick-by-tick — it is ~250ms throttled snapshots. Only `reqTickByTickData`
gives per-trade data. (tradepilot used `reqMktData`, so inheriting its exit brain inherits 250ms.)

**Never downgrade an open position's feed.** `ibkr-integration.md:181`: no more than 1 tick-by-tick
request per instrument per **15s**. Step down, and a reversal toward the stop can find us locked out
of the fast feed for 15 seconds — exactly the fast-fall case the tiering exists to protect against.
At ≤2 positions, two fast feeds cost nothing. Keep both.

The line budget bites at the **armed** tier, not the position tier: `ibkr-integration.md:178` — max
msgs/sec = lines ÷ 2, default 100 lines → 50 msg/sec, against a scanner returning ≤50 rows. That
tier needs a budget and an eviction policy.

## Gates 5–7 assume one strategy (#418)

`live_detect.py`, `ibkr/orders.py`, the OMS and the ≤2-concurrent guard are all written in the
singular. #418 specified a second strategy, and although it concluded **not to trade it**, the
shape of the problem is now on the record: a second strategy either shares those gates (and needs a
strategy tag on the candidate, the analysis row and the order, so attribution survives) or forks
them. It also must **not** share the adaptive book — the daily target re-fit and the risk ladder see
the merged candidate stream, which cost the bull-flag leg $218 in replay. Nothing to build now;
worth knowing before Gate 6 is designed as if there were only ever one signal.

## Open questions

- **Exit-limit aggressiveness** — how far through the bid? Needs Gate 1's data.
- **Armed-tier eviction** — which setups get a tick-by-tick line when candidates exceed the budget?
- **Virtual $500 on a $1M paper account** — IB paper funds default to $1M; equity, sizing, and the
  settled-cash invariant must be enforced app-side against a virtual ledger, not read off the
  account.
- **Crash recovery** — the app dying while holding an open position is untested and the scenario
  that hurts most. `capture.py`'s `_ensure_hydrated` is the pattern: the persisted row *is* the
  state.
- **Account reconciliation** — `tradepilot.md:116` records that its account/positions were
  mock/hardcoded and never wired to real TWS APIs. Do not inherit that.
