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
  **Shared vCPU → x86 → CX22** (2 vCPU / 4 GB / 40 GB, ~€4/mo) · keep **Public IPv4** · select your
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
- **Monitoring:** the backup pings a dedicated Healthchecks check (`HEALTHCHECKS_BACKUP_URL`) on
  start/success and `/fail` on error — so a silently-failing backup alerts you. Grafana's node
  metrics also show disk usage on the box.

## 9. Operations
- **Update:** use the phone-triggered `deploy` workflow (§11) — it recreates **only the app**
  container (`GIT_SHA=$(git rev-parse --short HEAD) docker compose up -d --build app`), so the
  Gateway keeps its session (no re-login) and the BuildKit pip cache keeps the rebuild quick (#72).
  `restart_only=true` does a full `systemctl restart` of both services (the wedged-Gateway case).
  The deployed short-SHA is baked into `DEPLOYED_COMMIT` and shown on the dashboard.
- **Logs:** `docker compose logs -f app` (JSON in prod).
- **Daily Gateway restart:** handled by IBC (`AUTO_RESTART_TIME`); the app auto-reconnects + resyncs.
- **Go live (Phase 3, later):** set `IBKR_TRADING_MODE=live`, `IBKR_PORT=4003` (the live socat port;
  paper is `4004`), restart.

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
  optional date range / symbol filter, and a `format`. The self-hosted runner `docker exec`s
  `scripts/analysis/export_query.py` against `/data` and commits the result to the **`data-export`**
  branch (`exports/<run_id>/…`), which the session reads back over GitHub. This is the read
  counterpart to `deploy.yml`'s write path — no inbound ports, no SSH key, no cloud secret. Driven by
  the `box-data` skill. On the Mac, use the direct `docker exec` recipe (`review-analysis` skill).
- **Deploy (needs the VM provisioned, #6):**
  1. Register a **self-hosted GitHub Actions runner** on the VM, labelled `self-hosted, vps`,
     as a systemd service (`./config.sh --labels vps && ./svc.sh install && ./svc.sh start`).
     The runner polls GitHub outbound — **no inbound ports, no SSH key off-box**.
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
