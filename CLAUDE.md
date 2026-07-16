# CLAUDE.md — working agreement for small-cap-stack

Automated systematic trading system for US small-cap momentum (Warrior-style), via IBKR.
Read `research/decisions.md` for locked decisions and `research/findings-index.md` for the
research record. This file documents **how we work** — follow it on every task.

## Project shape
- **Phases:** P1 = tracker only (no orders, 3 months data collection) · P2 = paper trading · P3 = live.
- **Strategy (live, legacy engine, unchanged):** price $1–50 (widened from $2–10, #126) · float < 20M **shares** · breaking news · trailing 5-min volume > 100k · change > 10% · bull-flag: **pole = a run of higher highs** (1–8 bars — even a single higher-high bar; colour-agnostic) · **flag = a pullback** (≤6 candles) that makes **lower highs** and retraces **≤50%** of the pole · pole peak-bar volume **>** consolidation volume (redefined #127, 2026-07-03) · window 04:00–11:59 ET. Entry = 5 ticks above the high of the last complete consolidation candle; stop = low of the consolidation.
  **Engine v2 in progress (`bull-flag.md`, umbrella #176, not yet live — lands with #180):** pole is
  colour-gated (green thrust bars only) and entry splits into a 1-tick mechanical trigger + a
  separate 3-tick conservative fill for R (#182/#190, `research/decisions.md`). Read `bull-flag.md`
  for the full v2 spec.
- **Core principle:** *store raw, compute derived on read* — capture raw data at flag time; gate/stat logic is replayable pure functions so methodology can change retroactively.

## Branching & PRs (trunk-based)
- `main` is protected: **all changes go through a PR**; no direct pushes. Required check: `lint-typecheck-test`. Linear history (squash-merge), no force-push. Solo self-merge is allowed (0 approvals required).
- Branch names: `feat/…`, `fix/…`, `chore/…`, `spike/…`, `docs/…`.
- Commit/PR titles: conventional prefixes (`feat:`, `fix:`, `chore:`, `spike:`, `docs:`).
- Link issues in the PR body: `Closes #N` when the PR completes the issue, else `Refs #N`; always reference the epic (`Refs #1`) for Phase-1 work.
- End commit messages with the `Co-Authored-By:` trailer for Claude.
- Squash-merge and delete the branch after merge.

## CI / quality gates (run locally before pushing)
Toolchain lives in `.venv`. CI runs ruff + mypy + pytest on every PR.
```bash
.venv/bin/ruff check .          # lint
.venv/bin/ruff format --check . # format
.venv/bin/mypy                  # type-check (strict; package only)
.venv/bin/pytest                # tests + coverage
```
- Python **3.11**. mypy is `--strict` and only checks `src/small_cap_stack` (so `spikes/` is exempt).
- Trading logic (gates, sizing, stats) must be exhaustively unit-tested — it is the product.

## Issue & project hygiene (keep these current — every task)
- **Every unit of work is a GitHub issue** with labels: `epic`, `phase-1`, `spike`, `infra`, `setup`, `ibkr`, `data`, `strategy`. Epic is **#1**.
- **Project board:** `https://github.com/users/bennetwi92/projects/3` (project id `PVT_kwHOCGbB5M4Bb_HY`, Status field `PVTSSF_lAHOCGbB5M4Bb_HYzhWrRtM`; options Todo `f75ad846` / In Progress `47fc9ee4` / Done `98236657`).
  - When creating an issue: `gh issue create` then add it to the board (`gh project item-add 3 --owner bennetwi92 --url <issue-url>`) and set Status.
  - **Status lifecycle:** Todo → In Progress (when work starts) → Done (when its PR merges / issue closes).
  - Set status: `gh project item-edit --project-id <PROJ_ID> --id <itemId> --field-id <FIELD_ID> --single-select-option-id <optId>`.
- **Record findings on the issue**, not just in chat — spikes/experiments get a results comment on their issue (`gh issue comment N`).
- When a decision is made, update `research/decisions.md` (and memory).

## Spikes (de-risking experiments)
- Throwaway harnesses live in `spikes/`; documented in `spikes/README.md`; exempt from mypy/tests but ruff-linted.
- Outputs (CSV/JSON/XML) go to `data/spikes/` which is **gitignored** — never commit data.
- Each spike maps to an issue; record the go/no-go + findings as an issue comment.

## IBKR / runtime
- Library: **`ib_async`** (asyncio). Ports: TWS paper 7497 / live 7496 · IB Gateway paper 4002 / live 4001.
  In the docker-compose stack the app connects to the `gnzsnz/ib-gateway` container via **socat** (paper
  **4004** / live **4003**) — the raw 4002/4001 API binds localhost-only with `TrustedIPs=127.0.0.1`, so a
  cross-container client on those ports connects then gets dropped. Set `IBKR_PORT` to the socat port.
- `reqHistoricalData` uses `barSizeSetting=` (not `barSize`). Short-term volume is native: `stVolume5minAbove` etc. — do not derive 5-min volume from bars.
- Pacing: ≤50 scanner rows, ~50 msg/sec, historical < 60 req / 10 min. Always `outsideRth=True` for pre-market.
- Secrets via `.env` (gitignored); see `.env.example`. Never commit credentials.

## Repo layout
- `src/small_cap_stack/` — the package (typed, tested).
- `tests/` — pytest suite.
- `spikes/` — de-risking experiments.
- `research/` — research reports + `decisions.md` + `findings-index.md`.
- `data/` — local runtime data (gitignored).
- `scripts/` — repo helpers (e.g. `board.sh`).

## Quick commands
`make help` lists everything. Common ones: `make setup` (venv + deps), `make check` (all CI gates), `make lint` / `make fmt` / `make typecheck` / `make test`. Run `make check` before every push.

## Helper scripts
- `scripts/board.sh <issue#> <Todo|"In Progress"|Done>` — set an issue's status on project board #3 (encapsulates the project/field IDs). Use it instead of re-deriving `gh project item-edit` calls.

## Box access — YOU HAVE IT from the Mac (do not claim otherwise)
When running on the **Mac** (the primary working dir, not a cloud/web session), you can operate the live box directly — don't tell the user "I have no box access":
- **Trigger GitHub Actions** (deploy, backfill, data-export, publish-dashboard) with `gh workflow run <name>.yml --field k=v`; they run on the self-hosted `vps` runner. Deploy: `gh workflow run deploy.yml --field ref=main`.
- **SSH into the box**: `ssh -i ~/.ssh/oracle_scs root@138.199.151.179` (root; repo `/opt/small-cap-stack`; app container `small-cap-stack-app-1`; systemd unit `small-cap-stack`). Full details in **`deploy/host.local.md`** (gitignored). ICMP is firewalled so `ping` always fails — that's normal, not a symptom.
- ⚠️ **The box is small (Hetzner CX22: 2 vCPU / 4 GB).** Heavy jobs will OOM/thrash it until sshd can't even complete its banner and the runner drops **offline (busy)** — and then you can't cancel or SSH in (recovery = OOM-killer reaping the job, or a hard reboot from the Hetzner console). **NEVER run `backfill-dashboard --all` (all dates + every chart) on it** — recompute **per date** instead (`--field date=YYYY-MM-DD`, one at a time), or SSH in and `docker exec … python -m small_cap_stack.dashboard_backfill --date <d>` sequentially. `build_eod_report` is compute-on-read, so per-date backfill is cheap (~4 s/day locally).
- ⚠️ **Per-date backfill is not automatically safe either.** On 2026-07-16 a plain `--date <today>`
  run grew to 1.5 GB RSS and got OOM-killed after 13 min on the box (#243/#263 have since made
  single-date backfill O(1 day); before them it fanned out over full history). Treat *any*
  backfill as a job that can OOM the box: check the box is on a commit that includes those fixes,
  prefer a **past** date over the live day, and watch `free -m` if you run several.
- ⚠️ **After an OOM, check the runner is actually back.** A job OOM leaves the runner service
  `failed` and CI silently queues forever — `gh api repos/bennetwi92/small-cap-stack/actions/runners`
  shows `offline`. `deploy/actions-runner-restart.conf` (a `Restart=always` drop-in) should now
  self-heal this within 30 s; if it doesn't, the drop-in is missing — see `deploy/RUNBOOK.md` §11.
- ⚠️ **Never `systemctl restart` the runner while a job is in flight** — it cancels the job. If that
  job is a deploy, it can leave the app container **stopped** (compose has torn the old one down but
  not brought the new one up). Check `docker ps` and re-run `deploy.yml` before walking away.

## Working remotely (Claude Code on mobile / web)
The cloud environment has GitHub access (issues, PRs, board, CI all work) and can run `make setup`/`make check`, but it does **NOT** have: the local `.venv`, the local `gh` keyring token, the `.env` file, or any **live IBKR connection**. Therefore:
- ✅ Safe remotely: code, tests, docs, issues, PRs, reviewing CI.
- ❌ Not possible remotely: running `spikes/` or the trading app — anything needing IB Gateway must run on the **Mac or the VPS** (Gateway lives at `127.0.0.1`, with credentials + market-data entitlement that aren't in the cloud).
- 📊 **Reading box data from the cloud:** you **cannot** SSH into the box from a web session — the sandbox allows only HTTP/HTTPS through a domain-allowlist proxy (no port-22 egress) and has **no secret store** (env vars are plaintext). Pull `/data` instead via the on-demand **`data-export`** workflow (self-hosted `vps` runner queries `/data` → commits to the `data-export` branch → the session reads it back over GitHub). Drive it with the **`box-data`** skill. On the **Mac**, use the direct `docker exec` recipe (`review-analysis` skill).
- **Secrets** live in three places, never in git: `.env` (local dev), GitHub Actions secrets (CI), and the VPS environment (runtime). The cloud reads data through GitHub, so it needs **no** secret — **Trusted** network access is enough.
