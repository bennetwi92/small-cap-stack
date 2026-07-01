# Deployment runbook — Phase-1 tracker (Hetzner Cloud)

One-time provisioning + deploy for the unattended IBKR tracker. Steps marked **[YOU]** need a
human (cloud console, IBKR account, secrets); everything else is `docker compose` + systemd.
The Docker images are multi-arch, so any always-on Linux host works — we use **Hetzner Cloud**
(instant provisioning, no capacity queue). Oracle Ampere Always-Free is a viable $0 alternative
if you can get A1 capacity (see §11).

## 0. Prerequisites (the human-only blockers)
- **[YOU] A host:** a Hetzner Cloud account (or any always-on Linux VPS / a Pi you own).
- **[YOU] IBKR market-data subscription** (real-time, incl. pre-market) in Account Management →
  Market Data Subscriptions. Without it, bars are ~15 min delayed (works, but not live).
- **[YOU] IBKR Mobile (IB Key)** installed for the **weekly 2FA** tap (Sun ~01:00 ET token reset).

## 1. [YOU] Provision the VM (Hetzner Cloud → console.hetzner.cloud)
- Add your SSH public key: Project → **Security → SSH Keys**.
- **Create Server:** Location **Ashburn, VA (US-East)** · Image **Ubuntu 24.04** · Type
  **Shared vCPU → x86 → CX22** (2 vCPU / 4 GB / 40 GB, ~€4/mo) · keep **Public IPv4** · select
  your SSH key · name `small-cap-stack`. Optional cloud **Firewall**: allow inbound SSH/22 only.
- No idle-reclamation to worry about (unlike Oracle). Hetzner Ubuntu logs in as **`root`**.

## 2. Host setup (SSH in as root)
```bash
ssh -i ~/.ssh/oracle_scs root@<PUBLIC_IP>
apt-get update && apt-get install -y docker.io docker-compose-plugin git curl
systemctl enable --now docker
install -d /opt/small-cap-stack
git clone https://github.com/bennetwi92/small-cap-stack /opt/small-cap-stack
cd /opt/small-cap-stack
```
(On a non-root host, prefix the above with `sudo` and add your user to the `docker` group.)

## 3. [YOU] Secrets
```bash
cp .env.example .env && nano .env   # fill TWS_USERID, TWS_PASSWORD, HEALTHCHECKS_PING_URL
```
- Start with `IBKR_TRADING_MODE=paper`. `.env` is gitignored — never commit it.

## 4. Launch via systemd
```bash
sudo cp deploy/small-cap-stack.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now small-cap-stack
```

## 5. [YOU] First-run 2FA
On first Gateway login, approve the **IBKR Mobile** push. Thereafter IBC auto-restarts daily
without 2FA; expect **one manual tap each Sunday** after the token reset. (Optional, later: a
second username with relaxed 2FA — your call.)

## 6. Verify
```bash
docker compose -f /opt/small-cap-stack/docker-compose.yml ps      # both services up/healthy
docker compose -f /opt/small-cap-stack/docker-compose.yml logs -f app | grep -E 'app.started|ibkr.connected|scan.candidates'
curl -s localhost:9090/metrics | grep scs_      # metrics served
```
Expect `app.started` → `ibkr.connected` → during 04:00–11:59 ET, `scan.candidates` and
`capture.opportunity_opened`; an `eod_<date>.md` report appears under the data volume after 16:00.

## 7. Monitoring
- **Healthchecks.io**: create a check, paste its ping URL into `.env` (`HEALTHCHECKS_PING_URL`).
  The app pings each tick; you get alerted if it goes silent. Set the period to a few minutes.
- **Grafana Cloud** (optional): run Grafana Alloy/agent on the host to scrape `localhost:9090/metrics`
  (`scs_ibkr_connected`, `scs_scan_ticks_total`, `scs_opportunities_total`, `scs_cold_disconnects_total`).
- (Oracle only) it reclaims idle Always-Free VMs after ~30 days — add a weekly keep-alive cron.
  Hetzner has no such reclamation.

## 8. Data + backups
- Data lives in the `scs-data` Docker volume (`/data` in the container): DuckDB + Parquet + EOD reports.
- Back it up off-box (issue #48 automates this): e.g. nightly
  `docker run --rm -v scs-data:/d -v /backup:/b alpine tar czf /b/scs-$(date +%F).tgz /d`
  then sync `/backup` to cheap/free object storage (Hetzner Storage Box, Backblaze B2 10 GB free, etc.).

## 9. Operations
- **Update:** `cd /opt/small-cap-stack && git pull && sudo systemctl restart small-cap-stack`.
- **Logs:** `docker compose logs -f app` (JSON in prod).
- **Daily Gateway restart:** handled by IBC (`AUTO_RESTART_TIME`); the app auto-reconnects + resyncs.
- **Go live (Phase 3, later):** set `IBKR_TRADING_MODE=live`, `IBKR_PORT=4001`, restart.

## 10. Reminders
- Phase 1 places **no orders** — it only records opportunities for ~3 months.
- Re-validate symbol tradability (#25) and any execution paths on a **live** account before Phase 3.

## 11. Alternative host — Oracle Ampere Always Free ($0, if you can get capacity)
Same steps, different provisioning: create a **VM.Standard.A1.Flex** (aarch64, 1–4 OCPU / 6–24 GB),
Ubuntu 22.04 **aarch64** image. Our images are multi-arch so ARM is fine. Caveats: free A1 capacity
is heavily contended ("Out of host capacity") — upgrading the account to **Pay-As-You-Go** (still
$0 within Always-Free limits) and trying a smaller shape / cycling Availability Domains usually
clears it; and Oracle reclaims idle free VMs (add the keep-alive cron). Login user is `ubuntu`.
