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

So `decisions.md` §230's framing — that only "simulate exit from bars" gets swapped for "place
bracket + capture fill" — is true of the *sizing/selection brain* but understates the gap. Gates 5–7
below are greenfield.

## Locked premises

- **Pre-market is limit-only** (#37, confirmed by the trader from live IBKR experience). The app
  fires every entry and exit itself; there are no broker-native stops before 09:30 ET. Switch to
  native stops/brackets in the regular session.
- Engine **v2** is the live engine.
- **≤2 concurrent positions, ≤2 entries/day**, $500 virtual book (`decisions.md` §230/#237).
- Box is **Ashburn, VA** (`deploy/RUNBOOK.md:17`) — ~10ms to IBKR's NY/NJ servers. App-side
  triggering can realistically react in ~10–25ms; **milliseconds are not achievable** from any
  app-side loop, so a faster feed means learning sooner, not acting sooner.

## The gates

| # | Gate | Issue | Deliverable | Blocked by |
|---|---|---|---|---|
| **0** | Truth debt | #302 · #297 · #270 | Settings flip · docs · spike import | — |
| **1** | Spread capture | **#309** | `whatToShow="BID_ASK"` in the EOD batch → new `quotes` table | — |
| **2** | Go/no-go criteria | **#310** | The bar for entering P2, written in `decisions.md` (+ #273 payload OOM) | — |
| **3** | Validation | #49 | 3-month collection completes (~2026-10-01); sim clears Gate 2's bar | 1, 2 |
| **4** | Market data | **#311** | $10/mo L1 bundle — unblocks everything real-time | — |
| **5** | Live detection (shadow) | **#312** | `live_detect.py` — streams bars, detects, **logs only** | 0, 4 |
| **6** | Execution | **#313** | `ibkr/orders.py` + `execution.py` — LMT entry/exit, app-side stop, OMS | 5 |
| **7** | Paper live | **#314** | Reconciliation, live-vs-sim divergence report, order/fill observability | 3, 6 |

Gates 0–2 are unblocked today and need no data subscription. Gate 3 is a calendar wait. Gates 5–7
are the build and start whenever Gate 4 lands.

> This table is **mirrored on the dashboard's Plan page** (`docs/plan.js`, `GATES`), which is where
> the trader reads it. The mirror carries each gate's **name, issue numbers and dependencies** only
> — its *status* is derived from whether those issues are closed on GitHub (#414), so a merged gate
> closes on the page by itself. Change a gate's name, issues or blockers here and change them there
> in the same PR; don't add a status field back.

## The three things that will actually bite

### 1. The exit-limit fill policy (Gate 6)

Limit-only means the app-side stop fires a **limit** order — which can simply *not fill* in a fast
drop, leaving the book holding a loser well through its stop. The mitigation is a marketable limit
priced *through* the bid; **how far through is a parameter that costs money on every exit**. This —
not feed latency — is where "accuracy at the stop matters more than at the target" actually bites.
Gate 1's spread data is what lets us set it from evidence instead of guesswork.

### 2. Prefix stability (Gate 5) — the sleeper

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
