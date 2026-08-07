# Deployment runbook — Phase-1 tracker (Hetzner Cloud, x86)

One-time provisioning + deploy for the unattended IBKR tracker. Steps marked **[YOU]** need a
human (cloud console, IBKR account, secrets); everything else is `docker compose` + systemd.
The Docker images are multi-arch, so any always-on Linux host works — the default is **Hetzner
Cloud** (instant provisioning, no capacity queue). Oracle Ampere Always-Free is a $0 alternative
(appendix §12).

## 0. Prerequisites (the human-only blockers)
- **[YOU] A host:** a Hetzner Cloud account (or any always-on Linux VPS / a Pi you own).
- **[YOU] IBKR market-data subscription** (real-time, incl. pre-market) in Account Management →
  Market Data Subscriptions. Without it, bars are ~15 min delayed (works, but not live).
- **[YOU] IBKR Mobile (IB Key)** installed for the **weekly 2FA** tap (Sun ~01:00 ET token reset).

## 1. [YOU] Provision the VM (Hetzner Cloud → console.hetzner.cloud)
- Add your SSH public key: Project → **Security → SSH Keys**.
- **Create Server:** Location **Ashburn, VA (US-East)** · Image **Ubuntu 24.04** · Type
  **Shared vCPU → x86 → CX23** (2 vCPU / 4 GB / 40 GB, ~€6.59/mo) · keep **Public IPv4** · select your
  SSH key · name `small-cap-stack`. Optional cloud **Firewall**: allow inbound SSH/22 only.
- Hetzner Ubuntu logs in as **`root`**; there is no idle-reclamation (unlike Oracle).

## 2. Host setup (SSH in as root)
```bash
ssh -i ~/.ssh/<your-key> root@<PUBLIC_IP>
apt-get update && apt-get install -y git curl ca-certificates
curl -fsSL https://get.docker.com | sh          # Docker CE + compose v2 plugin
systemctl enable --now docker
git clone https://github.com/bennetwi92/small-cap-stack /opt/small-cap-stack
cd /opt/small-cap-stack
```
> `docker-compose-plugin` is **not** in Ubuntu's default repos — the official get.docker.com script
> installs Docker CE **and** the `docker compose` v2 plugin. On a non-root host, prefix with `sudo`
> and add your user to the `docker` group.

## 3. [YOU] Secrets
```bash
cp .env.example .env && nano .env   # fill TWS_USERID, TWS_PASSWORD, HEALTHCHECKS_PING_URL
```
- Start with `IBKR_TRADING_MODE=paper`. `.env` is gitignored — never commit it.

## 4. Launch via systemd
```bash
cp deploy/small-cap-stack.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now small-cap-stack
```

## 5. [YOU] First-run 2FA
On first Gateway login, approve the **IBKR Mobile** push (paper logins often don't require it).
Thereafter IBC auto-restarts daily without 2FA; expect at most **one manual tap each Sunday** after
the token reset. (Optional, later: a second username with relaxed 2FA — your call.)

## 6. Verify
```bash
cd /opt/small-cap-stack
docker compose ps                                  # both services up; ibgateway healthy
docker compose logs app | grep -E 'app.started|ibkr.connected'
curl -s localhost:9090/metrics | grep scs_         # metrics served
```
Expect `app.started` → `ibkr.connected` → during 04:00–11:59 ET, `scan.candidates` and
`capture.opportunity_opened`; an `eod_<date>.md` report appears under the data volume after 16:00.

## 7. Monitoring
- **Healthchecks.io**: create a check, paste its ping URL into `.env` (`HEALTHCHECKS_PING_URL`).
  The app pings each tick; you get alerted if it goes silent. Set the period to a few minutes.
- **Grafana Cloud** (optional): run Grafana Alloy/agent on the host to scrape `localhost:9090/metrics`
  (`scs_ibkr_connected`, `scs_scan_ticks_total`, `scs_opportunities_total`, `scs_cold_disconnects_total`).
- **Dashboard data** (#68/#69): the app writes `status.json`/`stats.json` under `/data/dashboard`; the
  `publish-dashboard` workflow (self-hosted runner, every ~15 min + manual dispatch) force-pushes them
  to the orphan **`dashboard-data`** branch for the Pages frontend (#70) to poll via `raw.githubusercontent.com`.
- **Data-quality canary** (#346): the app writes `canary.json` (float coverage / news recency /
  bar sanity verdicts, ~5-min throttle) alongside `status.json`, for the dashboard and for manual
  inspection. Nothing asserts it automatically — the CI watchdog that used to was rolled back
  (#377); read it when you want a second opinion on whether a day's capture looks sane.
- ⚠️ **GitHub auto-disables `schedule` workflows after ~60 days of repo inactivity** (public repos).
  `publish-dashboard` is the one schedule left, so if the dashboard stops refreshing, check its
  page under Actions for a "disabled" banner first — re-enable and dispatch a manual run. (The
  monthly `workflow-keepalive` that used to reset this timer was rolled back with the rest of the
  automation layer, #377.)
- (Oracle only) it reclaims idle Always-Free VMs after ~30 days — add a weekly keep-alive cron.
  Hetzner has no such reclamation.

## 8. Data + backups
- Data lives in the `scs-data` Docker volume (`/data` in the container): Parquet datasets + EOD
  reports + dashboard JSON. It is the 3-month Phase-1 dataset — **the product** — so it is backed
  up off-box nightly (#48).
- **Automated off-box backup (restic → Backblaze B2).** Incremental, encrypted, deduplicated:
  each night only new Parquet partitions upload; retention `keep-daily 7 / weekly 5 / monthly 4`.
  ```bash
  # [YOU] one-time: B2 bucket + application key; a Healthchecks check for the backup job.
  apt-get install -y restic
  cp deploy/scs-backup.env.example /etc/scs-backup.env && nano /etc/scs-backup.env   # fill creds
  chmod 600 /etc/scs-backup.env
  set -a && . /etc/scs-backup.env && set +a && restic init          # once, creates the repo
  cp deploy/scs-backup.{service,timer} /etc/systemd/system/
  systemctl daemon-reload && systemctl enable --now scs-backup.timer
  systemctl start scs-backup.service && journalctl -u scs-backup -n 20   # test run now
  ```
  > ⚠️ The **`RESTIC_PASSWORD`** encrypts the repo — store it in your password manager too. Without
  > it, backups can't be restored after a box loss.
- **Restore** (on any host with restic + the same `/etc/scs-backup.env`):
  ```bash
  set -a && . /etc/scs-backup.env && set +a
  restic snapshots                                   # list backups
  restic restore latest --target /restore            # pull the newest into /restore
  # then repopulate the compose-managed volume (created by `docker compose up`, so it is
  # project-prefixed: small-cap-stack_scs-data). Restore into it while the app is stopped:
  docker compose -f /opt/small-cap-stack/docker-compose.yml create   # makes the volume
  docker run --rm -v small-cap-stack_scs-data:/d -v /restore:/r alpine sh -c 'cp -a /r/_data/. /d/'
  ```
- The reconstructed-history store `/data/recon` (#430/#431) lives in the same volume and is backed
  up with it. It is re-purchasable rather than collected, so a restore that loses it costs API
  budget, not the Phase-1 record — see §13.
- **Monitoring:** the backup pings a dedicated Healthchecks check (`HEALTHCHECKS_BACKUP_URL`) on
  start/success and `/fail` on error — so a silently-failing backup alerts you. Grafana's node
  metrics also show disk usage on the box.

## 9. Operations
- **Update:** use the phone-triggered `deploy` workflow (§11) — it **pulls** the prebuilt image
  `ghcr.io/bennetwi92/small-cap-stack:sha-<short>` that `build-image.yml` pushed for that commit and
  recreates **only the app** container (`docker compose pull app && docker compose up -d --no-build
  app`), so the Gateway keeps its session (no re-login). The box **never builds** (#278): a build
  competed with the live tracker for its 2 vCPU / 4 GB and left ~10 GB of BuildKit cache behind.
  The deployed short-SHA is baked into `DEPLOYED_COMMIT` at build time and shown on the dashboard.
  - The deploy **waits** (≤10 min) for the image to appear — `build-image.yml` runs on the push and
    can still be building when the deploy starts.
  - It pins `IMAGE_TAG=sha-<short>` in `/opt/small-cap-stack/.env`, so a reboot (the systemd unit
    runs `docker compose up -d --no-build --pull missing`) brings back the **same** image rather
    than drifting to `:latest`.
  - **Rollback / a commit with no image:** commits merged before #278 were path-filtered and may
    have no image. Dispatch with `image_tag` set to a tag that exists (e.g. `latest`, or an older
    `sha-…`); browse tags at `https://github.com/bennetwi92/small-cap-stack/pkgs/container/small-cap-stack`.
  - `restart_only=true` does a full `systemctl restart` of both services (the wedged-Gateway case).
- **Compacting a dataset (#319).** `compact.py` rewrites each **closed** (strictly pre-today ET)
  `dt=` partition into a single Parquet file with verified-identical rows — the sanctioned
  exception to the store's append-only layout, for the small-file explosion (`scanner_hits` hit
  ~32k one-row files; for this store read cost tracks **file count**). **Sanctioned mode: app
  stopped.** The swap is two directory renames, but `Store.read`/`query` glob paths *then* open
  them, and `build_eod_report` in the EOD job is not error-wrapped — a racing reader can fail on a
  vanished path. Procedure (quiet window, outside 04:00–11:59 ET):
  ```bash
  set -a && . /etc/scs-backup.env && set +a && restic snapshots --latest 1   # verify a fresh snapshot FIRST
  cd /opt/small-cap-stack && docker compose stop app
  docker compose run --rm --no-deps app \
    python -m small_cap_stack.compact --dataset scanner_hits --data-dir /data \
    --start 2026-07-01 --end <yesterday>
  docker compose up -d --no-build app
  find /var/lib/docker/volumes/small-cap-stack_scs-data/_data/scanner_hits -name '*.parquet' | wc -l
  ```
  Post-run: file count ≈ number of partitions; the tick's `status_build_seconds` (status bar /
  status.json, #321) drops. The tool refuses today's partition and any range touching it, verifies
  row-multiset + schema equality per partition before swapping, and leaves the originals untouched
  on any failure. **Expected follow-on cost:** compaction renames files, so every compacted day's
  portfolio-candidate-cache fingerprint busts and the next EOD `build_portfolio_payload`
  re-extracts those days (~2.5s each) — accepted, no pre-warm needed.
- **Memory: swap + container limits (#320).** The box ran with **no swap and no container
  limits**, so any spike was a host-wide OOM hunt with the kernel picking the victim — on
  2026-07-17 a global OOM dropped the IBKR connection (~9 min scanner gap); #264 (runner offline
  5h37m) and #180 were the same shape. The backstop is two-part, and both halves are committed
  here — the box must not carry hand-edits:
  - **Swap:** `deploy/setup-swap.sh` (idempotent, run as root) creates a 2 GB `/swapfile`,
    persists it in `/etc/fstab`, and sets `vm.swappiness=10` via `/etc/sysctl.d/99-scs-swap.conf`
    — an emergency cushion, not a hot path. This alone turns the 2026-07-17 incident from *kill*
    into *slow*. Verify: `swapon --show` (active) and again after a reboot (persistent);
    `sysctl vm.swappiness` → 10. `/var/run/reboot-required` has been pending anyway — do the
    fstab-verify reboot and the kernel update in one quiet window (outside 04:00–11:59 ET), and
    afterwards confirm both containers are up **and the CI runner is back** (§11 — a dead runner
    queues CI silently).
  - **Container limits (`docker-compose.yml`):** app `mem_limit: 2g` / `memswap_limit: 3g` /
    `oom_score_adj: 500`; Gateway `mem_limit: 1500m` / `oom_score_adj: -500`. Basis: post-#318
    app steady state is ~400 MB and the tick spike is gone; the biggest legitimate consumer left
    is the EOD portfolio build (~1.5 GB observed at #264, grows with history until #273), so 2 GB
    caps it with headroom while guaranteeing the Gateway (~570 MB steady) + host 1.8 GB of the
    3.8 GB box. A breach now dies inside the app's own cgroup (after ~1 GB of slow swap), and
    `restart: unless-stopped` recovers it; the Gateway and sshd outrank it everywhere. Revisit the
    2 GB number when #273 lands or if `docker stats` shows the EOD build closing on it.
  - **Applying:** limits take effect on container **recreate**. The app picks them up on the next
    deploy; the Gateway needs a one-off `docker compose up -d ibgateway` in a quiet window (a
    recreate restarts the Gateway session — have IBKR Mobile ready for a possible 2FA tap).
  - **Verify the victim selection** (deliberately, on a quiet weekend, never in the scan window):
    `docker compose exec app python -c "x = bytearray(3 * 1024**3)"` — the allocation must die
    (cgroup kill or MemoryError), the app container must stay/come back up (`docker ps`,
    `RestartCount`), and the Gateway + sshd must be untouched (`docker ps`, `journalctl -k | tail`
    shows a cgroup-scoped kill, not `global_oom`).
- **Logs:** `docker compose logs -f app` (JSON in prod).
- **Daily Gateway restart:** handled by IBC (`AUTO_RESTART_TIME`); the app auto-reconnects + resyncs.
- **Go live (Phase 3, later):** set `IBKR_TRADING_MODE=live`, `IBKR_PORT=4003` (the live socat port;
  paper is `4004`), restart.

### 9.1 Out-of-band control — the `hcloud` CLI (Mac only)
When a job OOMs the box it can thrash hard enough that sshd never completes its banner, so the SSH
recipes above and the `deploy` workflow are both unavailable — the runner is offline too. That is
the case this section exists for: `hcloud` talks to the **Hetzner API**, not to the box, so it keeps
working when the box itself does not. It replaces the "hard reboot from the Hetzner console" step
with a command.

**Mac only, by design.** It needs a long-lived API token on disk, which is exactly what the mobile
control plane (§11) deliberately does not have. Do not wire this into a web session or a workflow.

```bash
brew install hcloud
# [YOU] Console → project → Security → API Tokens → generate a Read/Write token
hcloud context create small-cap-stack                      # interactive: prompts for the token
HCLOUD_TOKEN=<token> hcloud context create scs --token-from-env   # non-interactive (scripts, agents)
hcloud context list                                        # confirm it's active
```
Stored in `~/.config/hcloud/cli.toml`, which hcloud creates `0600`. The token env var is
**`HCLOUD_TOKEN`** — there is no `--token` flag.

> **Not in `.env`.** `.env` is the *app's* config, read into `Settings` by python-dotenv and
> mirrored (from `.env.example`) onto the box in §3. An infra token that can **delete the server and
> its data volume** does not belong in the file whose template ships boxward — and `docker-compose.yml`
> passes only explicit `environment:` keys, so a var parked there reaches nothing anyway. Keep it in
> `cli.toml`, in one place, and in your password manager.
> Read/Write is required: a Read-only token covers `list`/`describe`/`metrics` but **cannot** `reset`,
> which defeats the point of this section. `cli.toml` is plaintext — it inherits your Mac's disk
> encryption and nothing more.

```bash
hcloud server list                              # is it running at all?
hcloud server describe small-cap-stack          # status, IPs, type
hcloud server ssh small-cap-stack               # SSH via the API's known IP (still needs sshd alive)
hcloud server reboot small-cap-stack            # graceful ACPI — try this first
hcloud server reset small-cap-stack             # hard power-cycle; the console button
hcloud server request-console small-cap-stack   # VNC URL — works when sshd is dead
```

**OOM recovery order** (the #264 case — a backfill wedged the box for 5h37m):
1. `hcloud server describe` — confirm it's `running`, not something dumber (a stopped server).
2. `hcloud server reboot` — graceful. Give it a minute; the OOM-killer may reap the job first and
   hand the box back on its own.
3. `hcloud server reset` — only if the reboot doesn't take. This is a **power-cycle**: the app
   container dies uncleanly. Parquet writes are per-partition, so the exposure is the in-flight
   partition, not the dataset — but prefer the reboot.
4. After **any** of these, re-check the runner and the app container — an interrupted deploy can
   leave the app **stopped**. See §11 and `docker ps`; re-run `deploy.yml` if needed.

**Caveats.**
- `hcloud server metrics` is **`[ALPHA]`** and its `--type` is `cpu|disk|network` — there is **no
  memory type**, so it will *not* show you the OOM directly. Sustained CPU on an unreachable box is
  the proxy; `free -m` over SSH remains the only real memory read, and that's the thing you've lost.
  ```bash
  hcloud server metrics --type cpu --start 2026-07-16T13:00:00Z --end 2026-07-16T14:00:00Z small-cap-stack
  ```
  (`--start`/`--end` are required ISO 8601; `--type` is repeatable.)
- **Scope:** as of **v1.66** `hcloud` also manages **Storage Boxes** (`hcloud storage-box`) and
  **DNS zones** (`hcloud zone`) via a second endpoint (`--hetzner-endpoint`, `api.hetzner.com`) —
  older write-ups saying it is cloud-only are out of date. **Robot** (dedicated servers) is still
  not covered. None of that affects us: our box is plain Hetzner Cloud.
- This does not substitute for §8's backups: `reset` is a power button, not a recovery tool. The
  data volume survives a reset — it does not survive a `delete`.

## 10. Reminders
- Phase 1 places **no orders** — it only records opportunities for ~3 months.
- Re-validate symbol tradability (#25) and any execution paths on a **live** account before Phase 3.

## 11. Operating from mobile (Claude Code web/app)
The whole loop — code, test, fetch data, deploy — is driven from the phone with **GitHub as the
control plane**. The cloud container has full GitHub access but holds no long-lived secrets, can't
reach the VM's `127.0.0.1`, and can't run IB Gateway — so nothing here exposes a credential to it.
See `research/decisions.md` → "Phone-driven control plane".

- **Build/test:** a `SessionStart` hook (`.claude/hooks/session-setup.sh`) runs `make setup`
  idempotently, so a fresh web session can `make check` immediately. The test suite is fully
  offline (IBKR tests are mocked) — no Gateway required.
- **Data for dev:** `make fetch-fixtures` pulls a sanitized sample from object storage
  (`FIXTURES_URI`). The VPS-side producer that pushes the sample is part of the backup job (§8, #48).
- **Reading live `/data` from the phone (needs the runner, #6):** you **cannot** SSH into the box
  from a web session (HTTP-only allowlist proxy, no secret store). Instead trigger **Actions →
  `data-export` → Run workflow** (or the GitHub MCP `actions_run_trigger`). Inputs pick a dataset
  (`bars`/`opportunities`/`scanner_hits`/`news`/`fundamentals`/`analysis` or raw `query`), an
  optional date range / symbol filter, and a `format`. The self-hosted runner runs (#545, §15)
  `scripts/analysis/export_query.py` against `/data` and commits the result to the **`data-export`**
  branch (`exports/<run_id>/…`), which the session reads back over GitHub. This is the read
  counterpart to `deploy.yml`'s write path — no inbound ports, no SSH key, no cloud secret. Driven by
  the `box-data` skill. On the Mac, use the direct `docker exec` recipe (`review-analysis` skill).
- **Deploy (needs the VM provisioned, #6):**
  1. Register a **self-hosted GitHub Actions runner** on the VM, labelled `self-hosted, vps`,
     as a systemd service (`./config.sh --labels vps && ./svc.sh install && ./svc.sh start`).
     The runner polls GitHub outbound — **no inbound ports, no SSH key off-box**.
     Then install `deploy/actions-runner-restart.conf` as a drop-in (see the header in that file)
     — **the generated unit has no `Restart=`**, so an OOM-killed job leaves the runner `failed`
     forever and every later dispatch silently queues against an offline runner. Re-check this
     after any `svc.sh install`, which rewrites the unit.
  2. From the phone, trigger **Actions → `deploy` → Run workflow** (or via the GitHub MCP
     `actions_run_trigger`). Inputs: `ref` (branch/tag/SHA) and `restart_only`. The job updates the
     working tree, restarts the service, and asserts `:9090/metrics` is healthy.
  3. Optional pull-based path: `build-image` publishes a `linux/amd64` image to GHCR; point the VM
     at the tag instead of `build: .` once you wire it in.
- **Network policy:** pulling fixtures/images requires the web environment's network policy to
  allow egress to the object-storage / GHCR host — set this when creating the environment.

## 12. Alternative host — Oracle Ampere Always Free ($0, if you can get capacity)
Same steps, different provisioning: create a **VM.Standard.A1.Flex** (aarch64, 1–4 OCPU / 6–24 GB),
Ubuntu 22.04 **aarch64** image; login user is `ubuntu` (use `sudo`). Our images are multi-arch so ARM
is fine — but if you use the pull-based image path, build **`linux/arm64`** and label the runner to
match. Caveats: free A1 capacity is heavily contended ("Out of host capacity" — upgrading to
**Pay-As-You-Go**, still $0 within limits, plus a smaller shape / cycling Availability Domains usually
clears it), and Oracle reclaims idle free VMs (add a weekly keep-alive cron).

## 13. Overnight pre-market harvest (#431)
Rebuilds pre-market sessions the tracker never saw from purchased vendor minute bars, into the
**second** store `/data/recon` (#430). The paper book publishes them as `books_all` beside the
untouched live `books`; nothing that reads the live store can return vendor rows by accident.

Ingest is #430's decision — the vendor's **free tier**, no purchase — so the job is priced by a
**5 calls/min** limit: ~218 calls a session, ~18 sessions across the 12:30-03:00 ET day (#455) and
~29 across a market-closed day's 05:00-03:00 (#633). It runs newest-first and lands whole sessions as it goes, so the deliverable is a deeper
sample every morning rather than a backtest in six weeks. **Stopping it early is always safe.**

### The vendor key — set up from a phone, no SSH

The key ends up in **the box's `.env`**, because the nightly timer fires outside GitHub and cannot
be handed a secret at run time. But you never have to put it there by hand:

1. **[YOU, once]** Add `MASSIVE_API_KEY` as a **repository Actions secret** — github.com → the repo
   → Settings → Secrets and variables → Actions → New repository secret. That page is ordinary
   responsive web, so a phone browser does it. This is the only step that must be you: nothing in
   this repo, and no Claude session, should ever handle the key (a cloud session has no secret
   store — env vars are plaintext).
2. Run the **`harvest`** workflow with `command: install-key`. It executes on the box's self-hosted
   runner and writes the secret into `/opt/small-cap-stack/.env`, replacing any previous line, and
   `chmod 600`s the file. It prints the key's *length and last four characters* — never the key.
3. From then on the systemd timer works. Deploys don't disturb it: `deploy-app` only rewrites the
   `IMAGE_TAG=` line.

Ad-hoc runs work even *before* step 2: the workflow injects the secret into the run, and
`scripts/harvest.sh` prefers an ambient key over the stored one. So `install-key` is specifically
about the unattended nightly job.

(`spike-massive.yml` from #428 uses the same secret name. It runs on `ubuntu-latest`, has no
`/data`, and is unrelated — if you already added the secret for it, step 1 is done.)

**Rotating the key** is the same two steps: update the repo secret, re-run `install-key`. It
replaces rather than appends, so the old key cannot linger and silently win.

Then run the **`harvest`** workflow once with `command: install-units`. It installs
`scs-harvest.{service,timer}` from the dispatched ref and enables the timer, on the box's own
runner — so the whole setup, key included, is three dispatches and no SSH. Re-run it after any
change to the units; it is idempotent, and that is the upgrade path.

The equivalent by hand, if you happen to be on the box anyway:

```bash
cp deploy/scs-harvest.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now scs-harvest.timer
```

> ⚠️ Both bootstrap commands run the **checked-out** wrapper, not the box's deployed copy, so they
> work on a ref that has not been deployed yet. Everything else (`status`, `daily`, `run`) starts a
> container from the **deployed image**, so those need the change merged, built and deployed first —
> otherwise the image has no `small_cap_stack.harvest` module in it.

**Order of operations — phase 1 first, and it is not optional.** #428 established the previous
daily close as a *required* input: without it the appearance reconstruction fires a median 18 min
early, so every session harvested before phase 1 has run is wrong rather than merely incomplete.

⚠️ **`sweep` also comes AFTER `daily`, not before it.** It measures the floor against phase 1's
*stored* rows, so on a box that has never run `daily` it reports `dates: 0` and empty tables — a
pre-flight that looks like it ran and measured nothing. The timer's `auto` sequences the phases
itself; only a hand-run needs to care.

`status`, `sweep` and `prefilter` touch no vendor, spend nothing and take no lock, so they run at
**any** hour — checking on the harvest must not itself require waiting until 17:00. `charts` is not
window-gated either, but it *does* take the lock (it mutates the same dashboard files the nightly
hook writes), so it refuses while a harvest is in flight. `daily` and `run` are the two that cost
API budget and wall clock, and both refuse outside the window.

```bash
cd /opt/small-cap-stack
./scripts/harvest.sh status                    # any hour: what is done, what is left
./scripts/harvest.sh daily                     # phase 1 FIRST: ~500 calls, under 2h
./scripts/harvest.sh sweep                     # ...THEN sweep — it reads phase 1's stored rows
./scripts/harvest.sh run --limit 1             # phase 2 smoke test: ONE session, watch free -m
./scripts/harvest.sh auto                      # what the timer runs: phase 1 if needed, then 2
./scripts/harvest.sh charts                    # any hour: publish harvested days to the dashboard
```

- ⚠️ **Both vendor-spending commands (`daily` and `run`) refuse to start outside 12:30–03:00 ET**
  (**05:00–03:00 on a day the market is shut**, #633) and stop themselves at 03:00, clear of the
  03:15 `portfolio_refresh`, the 03:45 `eod_backfill` and the 04:00 scan window.
  Overriding takes two flags (`--ignore-window --force`) — don't, during market hours. Being
  *launched* at the right time and *refusing* the wrong one are different guarantees; only the
  second survives a late timer.
- **Why the window starts at 12:30 and the timer fires twice (#455).** At the free tier the
  harvest's calendar is set purely by hours-per-day, and 12:00–16:20 ET is the one block of the
  box's day nothing is scheduled in — worth ~4.5 hours, taking ~40 nights to ~27. But it puts
  `eod_bars_fetch` (16:20) and `eod_report` (16:30) **inside** the window, and `HostGuard` cannot
  protect them: it is checked once per *session*, and a session is ~47 min, so a 12:30 start puts
  boundaries at 15:38 and 16:25 — the harvest sits *inside* a session, holding 1 GB with no swap,
  across both EOD jobs while `build_portfolio_payload` (~1.5 GB, growing, #273) runs. So the
  afternoon run carries its own **16:10 recess** (`HARVEST_EOD_RECESS_ET`), enforced as a deadline
  — which, unlike the guard, bounds where the container may still be *running*. The timer then
  fires at **12:30, 17:15, 20:00 and 23:00**: 17:15 does the evening, and the later two recover a
  run the guard ended at an arbitrary boundary. Re-fires while a run is in flight cost nothing —
  systemd merges the duplicate start job, so nothing appears in the journal at all.
- **Why a market-closed day gets 05:00–03:00 and no recess (#633).** Everything the 12:30 opening
  ducks is trading-day-only: the scan window and both EOD jobs are gated on the same XNYS calendar
  the tracker uses, so on a Saturday, Sunday or holiday they do not run at all and the 16:10 recess
  costs eleven hours for nothing. What *is* daily is `portfolio_refresh` 03:15 and `eod_backfill`
  03:45 — hence the **unchanged 03:00 stop** and a 05:00 rather than 04:00 start, which leaves the
  backfill an hour to finish (a `HostGuard` trip at the first session boundary ends the *whole*
  run). ~22 usable hours instead of ~13.4, twice a week. The timer gets two extra `Sat,Sun` fires,
  **05:00** and **08:00**, mirroring the weekday opening-plus-recovery pair.
  ⚠️ A **holiday** gets the wider window and the skipped recess — both are decided in the app, from
  the calendar — but **no early fire**: systemd has no trading calendar, so the first fire is 12:30
  as usual. Deliberate; a second scheduling mechanism on the box is not worth one morning a year.
- ⚠️ **Memory.** The container gets a hard `--memory=1g` with **no swap**, one CPU, and
  `--oom-score-adj=800` so the kernel prefers it over everything else on the box. It is a separate
  `docker run`, **not** `docker exec` into the app — sharing the tracker's cgroup would spend the
  tracker's headroom and OOM the tracker instead of the harvest (#264/#273). The job also checks
  host headroom between sessions and stops cleanly when the box gets tight.
- ⚠️ **The limits live on `scs-harvest.slice`, not on the service (#452).** `docker run` hands
  container creation to the daemon, so the container lands in Docker's own scope under
  `system.slice` — measured: `/system.slice/docker-….scope` by default, versus
  `/scs.slice/scs-harvest.slice/docker-….scope` with `--cgroup-parent`. Before #452 the service's
  `MemoryMax`/`Nice`/`IOSchedulingClass` bounded a ~15 MB docker client and nothing else, and the
  wrapper's `nice`/`ionice` prefix deprioritised a process that spends its life blocked on a
  socket. The slice's `MemoryMax=1200M` is now the real backstop — it binds even a container given
  no `--memory` of its own, so a mis-set `HARVEST_MEM_LIMIT` in `.env` can no longer exceed it.
  Check it with `systemctl show scs-harvest.slice -p MemoryMax` and
  `systemd-cgls /scs.slice/scs-harvest.slice` while a harvest runs. Re-run the `harvest` workflow
  with `command: install-units` after changing any of the three units.
- ⚠️ **The vendor's window is shorter than the one you plan (#440).** `HARVEST_LOOKBACK_DAYS` (730)
  says how far back to *ask*; the free tier's ~2-year entitlement says how far back you *get*. Phase
  1 walks ascending and pays one extra call for the session **before** the oldest planned one, to
  seed its previous close — so a lookback set at the entitlement edge reaches one session past it.
  On 2026-08-04 that 403 killed the first night outright, and would have killed every night after.
  Now an entitlement 403 (matched on the vendor's message, so a revoked key still stops the night)
  records an **entitlement floor** on the checkpoint, and the plan trims itself to what is
  purchasable. `harvest status` reports `entitlement_floor` beside `lookback_days`: `null` means the
  lookback has never been refused, a date means the real window is shorter than configured. Nothing
  needs setting — but a floor that appears where you didn't expect one means the plan changed.
- **Seeing it.** The book is rebuilt at **03:15 ET** (`portfolio_refresh`, #458) as well as at the
  16:30 EOD, so a night's reconstructed days are on the Portfolio page **before the open** rather
  than after the close of the day they were harvested for. `publish-dashboard` runs every 15 min,
  so the page is live by ~03:30 ET. The slot is the only free one: the harvest hard-stops at 03:00,
  `eod_backfill` is at 03:45, the scan window opens at 04:00. Look for `portfolio.refresh_done` in
  the app log; a failure is logged and skipped, never fatal — it costs a day of visibility, not
  data. ⚠️ This runs `build_portfolio_payload`, the #273 memory driver — the same build the EOD
  already does daily, now also in the quiet pre-dawn window. Watch `coverage.recon` and
  `capped_days_dropped` in `portfolio.json` as the harvest deepens (#448).
- **Reviewing a harvested day (#488).** `run`/`auto` publish each completed session's chart payload
  as it lands — `/data/dashboard/charts/recon/<date>.json` plus `recon_index.json`, a namespace of
  their own so nothing reading the live `index.json` can serve vendor rows by accident.
  `publish-dashboard` copies the whole dashboard dir, so no workflow change is needed and the day
  is on the **Results** page (DATA → `+ History`) and in the Portfolio trade inspector within ~15
  min. At most `RECON_CHARTS_MAX_DATES` (30) sessions are resident, because that push is a full
  re-upload of the tree every quarter hour and a payload is 1.5–3 MB. **Eviction is by publish
  order, not by date** — the harvest walks backwards, so a newest-date window would have gone stale
  after ~2 nights and hidden the rest of the run; this way the page carries whatever last night
  rebuilt, and the index's `capped_dates_dropped` says how many are not resident.
  `./scripts/harvest.sh charts` fills the window on a box that harvested before this existed, and
  `./scripts/harvest.sh charts --dates 2026-05-04` brings a specific evicted session back (it moves
  to the front of the window). Idempotent — re-running it with everything published does nothing.
  A publish failure never costs the night: it is logged and the session stays checkpointed.
  ⚠️ `charts` **takes the `scs-harvest` lock**, so it refuses while a harvest is running: it and the
  per-session hook both read-modify-write `recon_index.json`, and interleaved they would orphan a
  payload. Run it in a gap, or let the nightly hook do it. ⚠️ The cap bounds the date *count*, not
  bytes — measure `du -sh /data/dashboard` and the publish job's duration once the first window has
  landed rather than trusting the 1.5–3 MB figure.
- **Resuming.** A checkpoint at `/data/recon/harvest-checkpoint.json` records completed sessions;
  every run resumes from it. A session is written as **one parquet file per dataset at the end**, so
  a kill leaves the date with no files and the checkpoint never claims it; the next run discards any
  leftovers for an unmarked date before redoing it. Never hand-edit the checkpoint — it is the
  record of up to 45 nights of API budget, and `Checkpoint.load` refuses a version it doesn't know
  rather than silently starting over.
- ⚠️ **A refused session is never marked done (#446).** `Store.append` writes no file for empty
  records, so a day the vendor refused and a day nothing traded are byte-identical on disk. The
  session loop counts *failures* — distinct from symbols that simply produced nothing — and
  abandons the date, writing nothing and leaving it pending, when every symbol failed, when 5 fail
  consecutively (the circuit breaker: a failing symbol costs 5 calls and ~95 s on the retry
  ladder), or when more than 20% of a ≥10-candidate session failed. Look for
  `harvest.session_abandoned` in `journalctl`; `failed=` is on every per-session line.
- **Watching it.** `journalctl -u scs-harvest -f` shows a per-session line with candidate count,
  opportunities, calls, **failures** and **peak RSS** — that last number is the early-warning signal for a memory
  regression. It is deliberately NOT wired to the tracker's Healthchecks dead-man's switch: a
  stalled harvest must never page as a tracker outage.
- **The universe is filtered to match the live scan (#443).** Phase 1 fetches the ETF/ETN/ETV
  ticker set from the vendor's reference endpoint once (~10-20 calls, both active *and* delisted —
  a two-year window covers dates on which now-dead ETNs were trading) and caches it at
  `/data/recon/excluded-symbols.json`, refreshed every 30 days. Without it the harvest's universe
  is a different population from the tracker's: leveraged single-stock ETNs are the market's most
  reliable producers of "+10%, $1-50, >100k shares" days, and the paper book takes the first two
  triggers of a day, so each one admitted displaces a real candidate. A fetch failure degrades to
  the cached set and logs `harvest.exclusions_fetch_failed` rather than losing the night — if you
  see that in `journalctl`, the universe for those sessions was filtered with a stale list.
- **Disk.** ~36M rows of 1-min bars over the full harvest. `harvest.sh status` and `df -h` are the
  check; `HARVEST_STORE_MINUTE_BARS=false` in `.env` drops the raw minute series (the 5-min bars the
  engine reads are written either way) at the cost of a full re-fetch if the reconstruction rules
  ever change.

### 13.1 Before the first full night: sweep the day-volume floor
The harvest's whole calendar comes from one number — ~217 candidates a session — and that comes
from a **day volume > 100k** prefilter that is airtight but ~12× looser than the loosest of the 25
committed review cases. `./scripts/harvest.sh sweep` re-runs the filter at several floors against
already-stored phase-1 rows, costing **no API calls**, and reports candidates/day and sessions/night
at each. If a tighter floor halves the candidate set, ~27 days becomes ~14. Change it by setting
`HARVEST_MIN_DAY_VOLUME` in `.env` — and record the measurement on the issue before you do.

## 15. On-demand box jobs — backfill and data-export (#545)

`backfill-dashboard`, `deploy-backfill-publish`'s backfill stage, and `data-export` all run through
**`scripts/box-job.sh`**, which starts its own container against the shared `/data` volume.

⚠️ **They used to `docker exec` into the app container**, which put the work inside the *tracker's*
2 GB cgroup (`mem_limit: 2g`, `oom_score_adj: 500`). A growing backfill — and `build_portfolio_payload`
holds every collected day's bars in memory regardless of which date you asked for (#273) — pushed
that cgroup over, and the kernel reaped the **live tracker** rather than the job. That is #264's
shape. `scripts/harvest.sh` was rewritten around this exact lesson in #452; the backfill never was.

Two things follow from the change:

- **Cancellation now works.** `docker exec` does not forward a signal inward, so the job timeouts
  added in #544 killed the client and left the work running. An attached `docker run` gets it.
- **There is a session-window guard.** The job refuses to start between **04:00 and 16:10 ET** — the
  live scan window through the harvest's own EOD recess (#455). Dispatch outside it, or set the
  workflow's `ignore_window` input if you accept the contention. It refuses rather than waits: a job
  that sleeps for six hours holds the single self-hosted runner, which is the outage #544 bounded.

### Installing the slice (a one-time box action)

```bash
scp -i ~/.ssh/oracle_scs deploy/scs-jobs.slice root@<host>:/etc/systemd/system/
ssh -i ~/.ssh/oracle_scs root@<host> 'systemctl daemon-reload && systemctl start scs-jobs.slice'
# and, on the box:  systemctl show scs-jobs.slice -p MemoryMax   # expect 1258291200
```

**This is a backstop, not the protection.** The container's own `--memory=1g` is what binds, and it
works on a host where the slice was never installed — Docker creates the named slice transiently
with no limits. So the failure mode of forgetting this step is "no second line of defence", never
"no limit at all". Verify with `systemd-cgls /scs.slice/scs-jobs.slice` while a job runs.

Separate from `scs-harvest.slice` deliberately: the harvest owns 12:30–03:00 ET, and a job
dispatched inside that window sharing one ceiling could push the harvest over and have the kernel
kill the night — which is exactly why `harvest.sh` keeps its own read-only commands out of its slice.
