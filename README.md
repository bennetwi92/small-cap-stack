# small-cap-stack

this repo will deliver an automated systematic trading system that trades US small cap stocks in a manner similar to warrior trading.

There are two old repos that I worked on that could provide useful code and background:
- "https://github.com/bennetwi92/entresys_light"
- "https://github.com/bennetwi92/tradepilot" - when i built this repo i was actually trading this very same strategy.

Explore these codebases and record in detail anything that could be useful.

> ⚠️ **Educational/research project — not financial advice.** See [DISCLAIMER.md](./DISCLAIMER.md). Trading involves substantial risk of loss; use at your own risk.

---

## Status

Pre-build. Research and de-risking complete; foundation (CI, scaffolding) in place. Tracked via
[GitHub issues](https://github.com/bennetwi92/small-cap-stack/issues) and
[project board #3](https://github.com/users/bennetwi92/projects/3). Phase 1 (opportunity tracker)
is next. See [`research/`](./research) for the full record — start with
[`research/findings-index.md`](./research/findings-index.md) and [`research/decisions.md`](./research/decisions.md).

## Getting started

```bash
make setup     # create .venv, install package + dev tools (Python 3.11)
make check     # run all CI gates: lint, format-check, type-check, test
make help      # list all commands
```

Contributors: see [CONTRIBUTING.md](./CONTRIBUTING.md) and the working agreement in [CLAUDE.md](./CLAUDE.md).

## Repo layout

| Path | What |
|---|---|
| `src/small_cap_stack/` | The package (typed, tested) |
| `tests/` | Pytest suite |
| `spikes/` | De-risking experiments (run against IBKR locally / on the VPS) |
| `research/` | Research reports, `findings-index.md`, `decisions.md` |
| `scripts/` | Repo helpers (e.g. `board.sh`) |

> Everything below is the original product brief.

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

- trade stocks priced between $2 - $10.
- float should be less than $20million.
- There should be breaking news on the stock.
- 5 min volume should be greater than 100,000
- Change % (i.e. today's change) should be greater than 10%
- bull flag pattern
- max 2 Green extension candles
- Max 2 red consolidation candles.
- Trading window runs between US 4am to 11:59am.
- Exit strategy needs to be established



## Process

1. Scanner identifies low priced stocks experiencing a volume spike.
2. Checks stock's float (yfinance is suitable resource), also short interest %.
3. Checks news (could we use Claude to do this?) - I will provide guidance on what constitutes good news. Would like to use IBKR news feed if possible. The presence of recent news on the spcecific stock could be enough.
4. Daily chart check (need to research what this step would do).
5. Look for Bull Flag Pattern. Also includes checking prior activity through day. We only use 5 min bars. Volume is very important
6. Plan position (Risk, sizing, entry)
7. Execute entry of position
8. Manage take profit / stop loss in real time
