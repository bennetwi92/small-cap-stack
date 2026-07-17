# CLAUDE.md — working agreement for small-cap-stack

Automated systematic trading system for US small-cap momentum (Warrior-style), via IBKR.
Read `research/decisions.md` for locked decisions and `research/findings-index.md` for the
research record. This file documents **how we work** — follow it on every task.

## Project shape
- **Phases:** P1 = tracker only (no orders, 3 months data collection) · P2 = paper trading · P3 = live.
- **Strategy (live — engine v2):** price $1–50 (widened from $2–10, #126) · float < 20M **shares** ·
  breaking news · trailing 5-min volume > 100k · change > 10% · window 04:00–11:59 ET.
  Bull-flag: **pole = a run of higher highs**, colour-gated to green thrust bars (≤4, a red peak is
  allowed) · **flag = a pullback** (≤4 candles) making **lower highs**, retracing **≤50%** of the
  pole · pole must clear a **2% minimum move** · pole peak-bar volume **>** consolidation volume
  (#127) · the peak must close strong (upper wick ≤50%).
  **Entry splits in two (#182/#190):** a **1-tick** mechanical trigger above the last consolidation
  candle's high decides *when* the setup fires; R is measured against a separate, deliberately
  conservative **3-tick** fill. Stop = the consolidation low.
  The live detector is the **full-day** `bullflag/day.py::detect_day` (compute-on-read over a whole
  day, gated by scanner-appearance time + staleness, with exhaustion flagged on the 3rd+ cycle) —
  consumed by `rmetrics.py` and `charts.py`. The superseded anchored detector was deleted in #296.
  Read `research/bull-flag.md` (the *what*) and `research/engine-v2.md` (the *how*) for the full spec.
  ⚠️ **The v2 caps live as `detect_day` defaults, not in `config.py`** — `config`'s
  `bull_flag_max_pole=8` / `max_flag=6` / `entry_offset_ticks=5` are stale legacy leftovers that no
  live code reads (the #180 settings flip never landed; **#302**). Trust `day.py`, not `config.py`.
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
- `tests/` — pytest suite (incl. `fixtures/review_cases/` — 25 real-market regression cases).
- `spikes/` — de-risking experiments (see `spikes/README.md`).
- `research/` — the **documentation root**: `decisions.md` (locked decisions) + `findings-index.md`
  (the research record) + the specs (`bull-flag.md` = the *what*, `engine-v2.md` = the *how*) and
  the standing reports. `research/archive/` holds one-off reports that already did their job (the
  2026-06-29 `arch-*` set) — kept as the record, not as live docs.
- ⚠️ **`docs/` is NOT documentation** — it is the **GitHub Pages dashboard frontend** (HTML/CSS/JS;
  `cockpit.css` + `docs/js/` modules). The name is forced: Pages is on `build_type: legacy`, whose
  source path may only be `/` or `/docs`, so renaming it takes the live dashboard offline. Docs live
  in `research/`; only root keeps `README`/`CLAUDE`/`CONTRIBUTING`/`DISCLAIMER` (#300).
- `data/` — local runtime data (gitignored).
- `scripts/` — repo helpers (e.g. `board.sh`).
- `deploy/` — host runbook + systemd units.

## Quick commands
`make help` lists everything. Common ones: `make setup` (venv + deps), `make check` (all CI gates), `make lint` / `make fmt` / `make typecheck` / `make test`. Run `make check` before every push.

## Helper scripts
- `scripts/board.sh <issue#> <Todo|"In Progress"|Done>` — set an issue's status on project board #3 (encapsulates the project/field IDs). Use it instead of re-deriving `gh project item-edit` calls.

## Box access — YOU HAVE IT from the Mac (do not claim otherwise)
When running on the **Mac** (the primary working dir, not a cloud/web session), you can operate the live box directly — don't tell the user "I have no box access":
- **Trigger GitHub Actions** (deploy, backfill, data-export, publish-dashboard) with `gh workflow run <name>.yml --field k=v`; they run on the self-hosted `vps` runner. Deploy: `gh workflow run deploy.yml --field ref=main`.
- **SSH into the box**: `ssh -i ~/.ssh/oracle_scs root@138.199.151.179` (root; repo `/opt/small-cap-stack`; app container `small-cap-stack-app-1`; systemd unit `small-cap-stack`). Full details in **`deploy/host.local.md`** (gitignored). ICMP is firewalled so `ping` always fails — that's normal, not a symptom.
- ⚠️ **The box is small (Hetzner CX22: 2 vCPU / 4 GB).** Heavy jobs will OOM/thrash it until sshd can't even complete its banner and the runner drops **offline (busy)** — and then you can't cancel or SSH in (recovery = OOM-killer reaping the job, or a hard reboot from the Hetzner console). **NEVER run `backfill-dashboard --all` (all dates + every chart) on it** — recompute **per date** instead (`--field date=YYYY-MM-DD`, one at a time), or SSH in and `docker exec … python -m small_cap_stack.dashboard_backfill --date <d>` sequentially. `build_eod_report` is compute-on-read, so per-date backfill is cheap (~4 s/day locally).
- ⚠️ **`--all` now requires `--force`** (#261), and the `backfill-dashboard` / `deploy-backfill-publish`
  workflows require a separate `force` input on top of `all` — two deliberate actions, because a
  confirmation the caller auto-answers protects nobody. The rule above is unchanged: don't.
- ⚠️ **Per-date backfill is not automatically safe either.** On 2026-07-16 a plain `--date <today>`
  run grew to 1.5 GB RSS and got OOM-killed after 13 min, taking the CI runner offline for 5h37m
  (#264). **`--date` is still exposed** — `build_portfolio_payload` holds *every* collected day's
  bars in memory regardless of which date you asked for, and that grows daily (**#273**, the actual
  driver; #243's cache made single-date extraction O(1 day) of *work*, not of *memory*). So treat
  **any** backfill as a job that can OOM the box: prefer a **past** date over the live day, run one
  at a time, and watch `free -m`.
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
