---
name: log-spike
description: Run and record a de-risking spike for small-cap-stack — create/locate the spike issue, keep the harness in spikes/, record findings on the issue, and sync the project board. Use when starting or wrapping up a spike experiment in this repo.
---

# log-spike

Standard spike procedure for this repo (full conventions in `CLAUDE.md`).

1. **Issue.** Ensure a `spike`-labelled issue exists (add `ibkr` / `data` / `strategy` as relevant). If new, create it and add to the board:
   `gh project item-add 3 --owner bennetwi92 --url <issue-url>`
2. **Start.** Mark it In Progress: `scripts/board.sh <issue#> "In Progress"`.
3. **Build.** Put the harness in `spikes/`; write outputs to `data/spikes/` (gitignored). Run `make lint`. Spikes are exempt from mypy/tests but must be ruff-clean.
4. **Run.** Execute against a **live IB Gateway** — local Mac or the VPS, **never** from Claude Code on mobile/web (no Gateway/credentials there). Ports: Gateway paper 4002 / live 4001; TWS 7497 / 7496.
5. **Record.** Post findings as a **comment on the issue** (`gh issue comment <issue#>`) — go/no-go, the achievable approach, and build implications. Don't leave findings only in chat.
6. **Decisions.** If the result changes a choice, update `research/decisions.md`.
7. **Land.** Open a PR for the harness (`Refs #<issue#>`). After merge/acceptance: `scripts/board.sh <issue#> Done` and close the issue.
