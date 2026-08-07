# small-cap-stack

this repo will deliver an automated systematic trading system that trades US small cap stocks in a manner similar to warrior trading.

📊 **Live tracker dashboard:** https://bennetwi92.github.io/small-cap-stack/ — scanner activity, task status, data collected, and EOD statistics (refreshes ~every 15 min).

There are two old repos that I worked on that could provide useful code and background:
- "https://github.com/bennetwi92/entresys_light"
- "https://github.com/bennetwi92/tradepilot" - when i built this repo i was actually trading this very same strategy.

Explore these codebases and record in detail anything that could be useful.

> ⚠️ **Educational/research project — not financial advice.** See [DISCLAIMER.md](./DISCLAIMER.md). Trading involves substantial risk of loss; use at your own risk.

---

## Status

**Phase 1 — live, collecting.** The tracker has been deployed on a Hetzner VPS since
**2026-07-01**, scanning every trading session and writing raw data it can re-derive from later.
Phase 2 is paper trading and Phase 3 is live; **it places no orders today.**

Phase 1 was originally a three-month calendar wait (~2026-10-01). That is no longer the gate
(#49, closed 2026-08-07): the harvest rebuilt more pre-market sessions in three nights than the
live tracker collected in five weeks, so the question stopped being *how much data* and became
*do we trust it* — is the strategy overfitted, does the reconstruction diverge from what actually
happened. Live collection keeps running regardless: it is the only thing that can keep validating
the reconstruction against reality.

What that means in practice:

- The **paper book** is *compute-on-read*, not a running account — every trade and every R is
  re-derived from stored bars whenever the rules change, so a methodology fix reprices all of
  history rather than only what comes next.
- A nightly **harvest** rebuilds pre-market sessions from purchased vendor bars into a separate
  store, so the record reaches back before collection started without vendor rows ever mixing
  into live ones.
- Progress against the Phase-2 gates is on the dashboard's
  [Plan page](https://bennetwi92.github.io/small-cap-stack/plan.html), computed from published
  data and live issue state rather than hand-maintained.

Work is tracked via [GitHub issues](https://github.com/bennetwi92/small-cap-stack/issues) and
[project board #3](https://github.com/users/bennetwi92/projects/3). See [`research/`](./research)
for the full record — start with [`research/strategy.md`](./research/strategy.md) (the canonical
spec), then [`research/decisions.md`](./research/decisions.md) for why each rule is what it is.

## Getting started

```bash
make setup     # create .venv, install package + dev tools (Python 3.11)
make check     # run all CI gates: lint, format-check, type-check, tests + coverage
make help      # list all commands
```

Contributors: see [CONTRIBUTING.md](./CONTRIBUTING.md) and the working agreement in [CLAUDE.md](./CLAUDE.md).

**On Claude Code web/mobile:** a `SessionStart` hook runs `make setup` automatically, so a fresh
cloud session can `make check` on turn one — the test suite is fully offline (no IB Gateway needed).
Deploys are driven from GitHub (see [`deploy/RUNBOOK.md`](./deploy/RUNBOOK.md) §11 "Operating from
mobile").

## The strategy

**[`research/strategy.md`](./research/strategy.md) is the canonical spec** — the scan universe, the
bull-flag engine and the paper book's own rules, **generated from `config.py`** so it cannot go
stale. Read that before the product brief further down this file, which is the *original 2026-06-29
ask* and no longer matches what was built.

Two things the brief gets wrong and the spec gets right: the price band has moved, and **float and
"breaking news" are collected but never gated** — they are enrichment written after a name is
flagged, and nothing downstream filters on them.

## Repo layout

| Path | What |
|---|---|
| `src/small_cap_stack/` | The package (typed, tested) |
| `tests/` | Pytest suite, incl. 25 real-market regression fixtures |
| `spikes/` | De-risking experiments (run against IBKR locally / on the VPS) |
| `research/` | ⚠️ **The documentation** — `strategy.md` (the spec), `decisions.md` (the log), `findings-index.md` |
| `docs/` | ⚠️ **NOT documentation** — the GitHub Pages dashboard frontend (HTML/CSS/JS). Its one prose exception is `docs/reports/`, the published analyses |
| `deploy/` | Host runbook and systemd units for the VPS |
| `data/` | Local runtime data — **gitignored**, never committed |
| `scripts/` | Repo helpers (e.g. `board.sh`) |
| `.github/` | CI (`ci`), the deploy/backfill/publish workflows, issue templates |

---

> ⚠️ **Everything below is the original product brief, written 2026-06-29 before anything was
> built. It is kept as the record of what was asked for — it is _not_ a description of what runs.**
> Several rules changed during Phase 1 and some were never implemented at all. The built system is
> specified in **[`research/strategy.md`](./research/strategy.md)**, generated from `config.py`;
> the reasoning behind each change is logged in
> [`research/decisions.md`](./research/decisions.md).

## mile high architecture


## Requirements

- application should be running in headless state on a vps
- broker and services provided by IBKR
- application should be organised in terms of processes that spawn tasks and then tasks are managed.
- Tasks can have dependnancies
- application and connection to ibkr should run unsupervised.
- CI/CD should be set up from the outset.
- branching strategy should be decided upon at the start.
- I have a Claude Max Subscription
- I want to produce as much as possible for free. I shouldn't need any subscriptions. Choose free tier services.
- I will need to deploy the service somewhere in the cloud to ensure uptime and easy maintenance for me.
- split delivery into phases.
    - Phase 1 will deliver an application that merely tracks the trades. This will run for 3 months to collect enough data to inform actual trading.
    - Phase 2 will deliver paper trading.
    - Phase 3 will deliver live trading. This shouldn't be too different to phase 2 but will likely have fixes required.
- Project should rely on github issues to track the project and github project too if this is possible.
- As this is a trading, real time application, testing requirements should be very stringent. Equally monitoring and observability must be established from the outset.



## Strategy details

_As originally asked for. **Five of these ten lines are not what shipped** — see
[`research/strategy.md`](./research/strategy.md) for the live rules._

- ~~trade stocks priced between $2 - $10.~~ → **widened** (#126)
- ~~float should be less than $20million.~~ → **collected, never gated** (and measured in *shares*, not dollars)
- ~~There should be breaking news on the stock.~~ → **collected, never gated**
- 5 min volume should be greater than 100,000 → shipped, as IBKR's native *trailing* 5-min volume
- Change % (i.e. today's change) should be greater than 10% → shipped
- bull flag pattern → shipped
- ~~max 2 Green extension candles~~ → **the pole is a capped run of higher highs, colour-gated** (#127)
- ~~Max 2 red consolidation candles.~~ → **the flag is a capped pullback making lower highs** (#127)
- Trading window runs between US 4am to 11:59am. → shipped as the *scan* window; the paper book trades a narrower one
- Exit strategy needs to be established → **established** (#230): fixed R target re-fit from trailing expectancy



## Process

_As originally asked for. **Steps 2–4 did not ship as filters**: float and short interest are
captured but gate nothing, news is captured but gates nothing, and the daily-chart check was never
built. The live pipeline is scan → capture everything → detect on read → size and simulate._

1. Scanner identifies low priced stocks experiencing a volume spike.
2. Checks stock's float (yfinance is suitable resource), also short interest %.
3. Checks news (could we use Claude to do this?) - I will provide guidance on what constitutes good news. Would like to use IBKR news feed if possible. The presence of recent news on the spcecific stock could be enough.
4. Daily chart check (need to research what this step would do).
5. Look for Bull Flag Pattern. Also includes checking prior activity through day. We only use 5 min bars. Volume is very important
6. Plan position (Risk, sizing, entry)
7. Execute entry of position
8. Manage take profit / stop loss in real time
