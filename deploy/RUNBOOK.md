# Deployment runbook — Phase-1 tracker on Oracle Cloud (Always Free, Ampere ARM)

One-time provisioning + deploy for the unattended IBKR tracker. Steps marked **[YOU]** need a
human (Oracle console, IBKR account, secrets); everything else is `docker compose` + systemd.

## 0. Prerequisites (the human-only blockers)
- **[YOU] Oracle Cloud account** with an Always Free **Ampere A1** VM. (IB Gateway has an official
  aarch64 build, so ARM is fine.)
- **[YOU] IBKR market-data subscription** (real-time, incl. pre-market) in Account Management →
  Market Data Subscriptions. Without it, bars are ~15 min delayed (works, but not live).
- **[YOU] IBKR Mobile (IB Key)** installed for the **weekly 2FA** tap (Sun ~01:00 ET token reset).

## 1. [YOU] Provision the VM (Oracle console)
- Create instance → Ubuntu 22.04, shape **VM.Standard.A1.Flex**, **2–4 OCPU / 12–24 GB** (verify
  your account's current A1 limit; the June-2026 reduction may cap at 2/12).
- Boot volume ≥ 50 GB. Add an SSH key. No inbound ports required (metrics bind to localhost).
- ⚠️ Oracle reclaims idle Always-Free VMs after ~30 days — the app's own activity plus a small
  weekly cron keeps it busy (see §7).

## 2. Host setup (SSH in)
```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin git
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" && newgrp docker
sudo install -d -o "$USER" /opt/small-cap-stack
git clone https://github.com/bennetwi92/small-cap-stack /opt/small-cap-stack
cd /opt/small-cap-stack
```

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

## 7. Monitoring + keep-alive
- **Healthchecks.io**: create a check, paste its ping URL into `.env` (`HEALTHCHECKS_PING_URL`).
  The app pings each tick; you get alerted if it goes silent. Set the period to a few minutes.
- **Grafana Cloud** (optional): run Grafana Alloy/agent on the host to scrape `localhost:9090/metrics`
  (`scs_ibkr_connected`, `scs_scan_ticks_total`, `scs_opportunities_total`, `scs_cold_disconnects_total`).
- **Keep-alive** (avoid Oracle idle reclamation): `crontab -e` →
  `*/30 * * * * /usr/bin/uptime > /dev/null` (the app itself also keeps the box busy on weekdays).

## 8. Data + backups
- Data lives in the `scs-data` Docker volume (`/data` in the container): DuckDB + Parquet + EOD reports.
- Back it up off-box (see issue for automation): e.g. nightly
  `docker run --rm -v scs-data:/d -v /backup:/b alpine tar czf /b/scs-$(date +%F).tgz /d`
  then sync `/backup` to free object storage (Oracle Object Storage 10 GB always-free).

## 9. Operations
- **Update:** `cd /opt/small-cap-stack && git pull && sudo systemctl restart small-cap-stack`.
- **Logs:** `docker compose logs -f app` (JSON in prod).
- **Daily Gateway restart:** handled by IBC (`AUTO_RESTART_TIME`); the app auto-reconnects + resyncs.
- **Go live (Phase 3, later):** set `IBKR_TRADING_MODE=live`, `IBKR_PORT=4001`, restart.

## 10. Reminders
- Phase 1 places **no orders** — it only records opportunities for ~3 months.
- Re-validate symbol tradability (#25) and any execution paths on a **live** account before Phase 3.
