# Free-Tier Cloud Infrastructure for an Always-On IBKR Trading System

**Researched:** 2026-06-29
**Hard constraint:** Maximize what runs for $0. No subscriptions. Offerings change frequently — every claim below is dated and cited; re-verify before committing.

## The core problem (maps to README requirements)

The trading system needs a **persistent, stateful, long-lived process** (IB Gateway + bundled Java + the trading app), realistically **1.5–2 GB RAM**, running **24/5 during market days, ideally always-on**. This rules out:

- **Serverless / ephemeral** platforms (Lambda, Cloud Run cold-start models) — Gateway holds a stateful socket session and must stay logged in.
- **Sleep-on-idle "free web service" platforms** (Render free, old Heroku) — they spin down after minutes of inactivity, which kills the Gateway session.

So the anchor decision is a **real always-free Linux VPS you control**. Everything else (CI/CD, monitoring, storage) layers on top.

---

## 1. Free-tier VPS / compute (the anchor decision)

### Does IB Gateway run on ARM? — YES (important enabler)

IBKR now ships an official `linux-arm` (aarch64) installer. aarch64 support landed in **IB Gateway 10.37.1l / 10.39.1e**, covering Raspberry Pi and M-series Macs; the package bundles a proprietary JRE, and BellSoft Liberica JDK 17 LTS ARM works as a drop-in if a separate JVM is needed. Multi-arch (amd64 + arm64) Docker images exist and are actively maintained.

- https://www.interactivebrokers.com/en/trading/ibgateway-stable.php
- https://github.com/gnzsnz/ib-gateway-docker
- https://github.com/cslev/ibkr-docker
- https://github.com/nemozny/ibgateway-raspberry-64

**Conclusion: ARM is viable**, which unlocks the single best free offer (Oracle Ampere). x86 free options exist but are smaller.

### Option A — Oracle Cloud Always Free (Ampere A1, ARM) — RECOMMENDED, with caveats

- **Spec (historical/most-cited):** up to **4 ARM OCPUs + 24 GB RAM** + 200 GB block storage + up to 10 TB/mo egress, splittable across up to 4 VMs. Plus 2× always-free AMD x86 VMs (1/8 OCPU, 1 GB each — too small for Gateway).
- **Type:** **Always Free** (no 12-month expiry).
- **ARM/x86:** ARM (aarch64) — fine for IB Gateway per above.
- **⚠️ MAJOR 2026 CHANGE:** Beginning **June 2026**, the free-tier Ampere A1 allowance is being **cut in half to 2 OCPU / 12 GB total** across all A1 instances (billing: 1,500 OCPU-hrs + 9,000 GB-hrs/mo, down from 3,000 + 18,000). Reporting (June 22, 2026) says it currently applies to **free-tier** accounts; Pay-As-You-Go accounts may keep 4/24 free. Rollout is inconsistent and there was no formal announcement. **2 OCPU / 12 GB is still ample for IB Gateway + app**, so this hurts headroom, not viability.
- **⚠️ Reclamation risk:** Idle Always-Free compute can be reclaimed; accounts idle **30+ days** may be deemed abandoned and suspended/terminated. Mitigation: keep light CPU/network activity (the trading app itself does this during market hours; add a cron heartbeat for weekends).
- **⚠️ Signup difficulty:** Notoriously hard — capacity ("out of capacity" / "host capacity" errors) for A1 shapes in popular regions, card-verification rejections, and occasional unexplained account termination. Pick a less-congested home region; retry A1 creation via script.

Sources:
- https://www.oracle.com/cloud/free/faq/
- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- https://terminalbytes.com/oracle-cloud-free-tier-changes-2026/
- https://linuxiac.com/oracle-quietly-cuts-free-tier-ampere-a1-resources-in-half/
- https://community.oracle.com/customerconnect/discussion/964620/
- https://space-node.net/blog/oracle-cloud-always-free-limits-2026

### Option B — Google Cloud Always Free (e2-micro, x86) — viable fallback, RAM-tight

- **Spec:** 1× **e2-micro** VM/month = **2 vCPU (shared) / 1 GB RAM**, 30 GB standard persistent disk, 1 GB North-America egress/mo.
- **Regions (must use one of these for free):** `us-west1` (Oregon), `us-central1` (Iowa), `us-east1` (South Carolina). US-only.
- **Type:** **Always Free, no end date** (Google reserves the right to change with 30 days' notice).
- **ARM/x86:** x86 — no architecture concern.
- **⚠️ RAM trap:** **1 GB is below the realistic 1.5–2 GB need** for IB Gateway + JRE + app. Workable only with an aggressive swapfile (SSD swap on the 30 GB disk) and a lean app; expect GC pressure / OOM risk. Marginal, not comfortable.
- **⚠️ Egress trap:** only 1 GB/mo free North-America egress (vs Oracle's 10 TB). Market-data ingress is free; watch outbound (e.g., shipping logs/metrics off-box).

Sources:
- https://docs.cloud.google.com/free/docs/free-cloud-features
- https://gcloud-compute.com/e2-micro.html

### Option C — AWS Free Tier — AVOID for this use case (expiry trap)

- **Legacy accounts (created before 2025-07-15):** 750 hrs/mo t2/t3.micro (1 GB RAM) but **only for 12 months**, then billed.
- **New accounts (after 2025-07-15):** the 12-month EC2/RDS/S3 free allowances are **gone**, replaced by a **credit model**: $100 on signup + up to $100 more via onboarding tasks, and the **Free Plan expires after 6 months or when credits run out**.
- **Verdict:** Either path **expires** (12 months legacy, 6 months/credits new). Unsuitable for a long-lived always-on system. Classic expiry trap.

Sources:
- https://infratally.com/articles/aws-free-tier-2026/
- https://aws.amazon.com/free/free-tier-faqs/
- https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html

### Option D — Fly.io — NO free tier for new users

Free allowances were replaced (2024) by a **2-hour / 7-day trial**, whichever ends first; then pay-as-you-go for every resource. Legacy free VMs persist only for pre-change accounts. Not an option for new signups.

- https://www.saaspricepulse.com/blog/flyio-free-tier-2026
- https://community.fly.io/t/does-fly-io-have-a-free-tier/27430

### Option E — Render free Hobby — UNSUITABLE (sleeps on idle)

Free web services (512 MB RAM, 0.1 CPU) **sleep after 15 min of inactivity** (tightened from 30 min) and cold-start on wake — fatal for a persistent Gateway session. Free Postgres has a **30-day expiry**; free Redis is 25 MB. Fine for occasional dashboards, not for the Gateway host.

- https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026
- https://agentdeals.dev/vendor/render

**VPS verdict:** Oracle Ampere A1 (ARM) is the only free offer with enough RAM and no expiry. GCP e2-micro is the x86 fallback if Oracle signup/capacity fails (accept 1 GB + swap). AWS/Fly/Render are out for the always-on Gateway.

---

## 2. Free CI/CD — GitHub Actions

- **Public repos:** GitHub-hosted runners are **free and unlimited** (standard runners).
- **Private repos (Free plan):** **2,000 CI/CD minutes/month** + **500 MB artifact storage**. Overage is billed (you can set spending limit to $0 to hard-stop).
- **2026 pricing note:** Rates updated 2026-01-01 — hosted-runner per-minute rates cut up to ~39%, plus a new $0.002/min "Actions cloud platform" charge (only matters past the free quota).
- **Concurrency:** Free/Pro accounts have a cap on concurrent jobs (around 20 concurrent jobs for Free, with a separate macOS sub-cap) — not a concern for a single-repo trading project.
- **Mapping to requirement:** Keep the repo **private** (trading logic/keys) → 2,000 min/mo is plenty for lint/test/build + deploy-over-SSH to the VPS. Or self-host a runner *on the Oracle box* for unlimited minutes (uses your free CPU).

Sources:
- https://docs.github.com/en/actions/concepts/billing-and-usage
- https://github.com/pricing
- https://github.blog/changelog/2025-12-16-coming-soon-simpler-pricing-and-a-better-experience-for-github-actions/

---

## 3. Free monitoring / observability / alerting

| Service | Free tier | Best for | Key trap |
|---|---|---|---|
| **Healthchecks.io** | **20 checks**, 3 team members | Cron / heartbeat / dead-man's-switch (did the bar-collector run? is Gateway alive?) | SMS quota cut to **0** on free; email/Telegram/Discord/Slack/webhook integrations still free. Self-hostable (open source) for unlimited. |
| **UptimeRobot** | **50 monitors**, 5-min interval, basic status page | HTTP/port up-checks | **Free = personal/non-commercial only since 2024-12-01.** A revenue-generating trading app is arguably commercial → ToS risk. 5-min interval = up to ~5-min detection gap. |
| **Better Stack** | **10 monitors + heartbeats**, 1 status page, Slack/email alerts | Combined uptime + incident + some logs/metrics | 30s intervals, phone/SMS, on-call escalation are paid ($29/mo). Limited free log/metric retention. |
| **Grafana Cloud (free)** | **10k active metric series**, **50 GB logs/mo**, 50 GB traces, 14-day retention, 3 users | Dashboards + Prometheus-compatible metrics + Loki logs + alerting, no card | **14-day retention** only; 10k-series and 50 GB caps. Plenty for one trading box. |
| **Prometheus + Grafana (self-hosted on the VPS)** | Free (uses your CPU/RAM/disk) | Full control, unlimited retention (disk-bound) | Consumes the VPS's scarce RAM; you maintain it. Better to ship metrics to Grafana Cloud and keep the box lean. |

**Free alerting channels:** All of the above can fire to **email, Telegram, and Discord webhooks** for $0 (Telegram bot + Discord webhook are free and ideal for trade/error alerts). SMS/phone is the common paywall — avoid relying on it.

**Recommended monitoring combo (all free):**
- **Healthchecks.io** dead-man's-switch: the app pings on each successful market-hours loop; if it stops, you get a Telegram/email alert (catches Gateway disconnects, crashes, VPS reclamation).
- **Grafana Cloud free** for metrics/dashboards (P&L, fill latency, scanner counts) + log shipping (Grafana Agent/Alloy).
- **Better Stack or UptimeRobot** for an external up-check on a health endpoint (note UptimeRobot's non-commercial clause — prefer Better Stack's 10 free monitors here).

Sources:
- https://healthchecks.io/pricing/
- https://uptimerobot.com/pricing/ , https://dev.to/r0tten0x/uptimerobot-free-plan-in-2026-the-limits-thatll-actually-bite-you-445g
- https://freetier.co/directory/products/better-stack
- https://grafana.com/pricing/ , https://grafana.com/docs/grafana-cloud/cost-management-and-billing/manage-invoices/understand-your-invoice/usage-limits/

---

## 4. Free data storage — mapped to "3 months of trade + bar data"

**Data-volume sanity check (Phase 1 = collect data for 3 months):**
- Trade logs: trivial (KBs–MBs).
- Scanner snapshots: small if stored as compact rows.
- **5-min bars: the driver.** 5-min bars = 78 bars/symbol/trading-day. For ~200 small-cap symbols × ~63 trading days/quarter ≈ **~1M rows/quarter**. At ~50–100 bytes/row that's **~50–150 MB/quarter** in SQLite, less if you aggregate. **This fits inside every free tier below**, and easily on the VPS disk.

### Recommended: SQLite on the VPS (primary)

- **Cost:** $0, on the 200 GB (Oracle) or 30 GB (GCP) free disk you already have.
- **Why:** Zero network egress, zero auto-pause, zero expiry, single-file, perfect for an append-heavy single-writer logger. 3 months of bars is well under 1 GB.
- **Backup:** nightly `VACUUM INTO` + copy to free object storage (below) or commit compressed snapshots to a private GitHub repo / GitHub Release.

### Off-box / managed Postgres options (optional, for querying/dashboards)

| Service | Free tier | Auto-pause / expiry trap |
|---|---|---|
| **Neon** | 0.5 GB/project, up to ~100 projects (~5 GB total ceiling), serverless Postgres, branching | **Scales to zero after 5 min idle, resumes ~1 s** — pause is graceful, not a wipe. Good for intermittent dashboard queries. |
| **Supabase** | 500 MB DB + 1 GB file storage, full platform (auth/storage/funcs) | **Pauses the whole project after 1 week of inactivity (tightened Feb 2026); manual unpause required.** Two free projects max. Riskier for a quiet logger. |

For Phase 1, **Neon** is the better managed-Postgres choice (graceful scale-to-zero vs Supabase's week-idle full pause), but **you likely don't need it** — SQLite-on-VPS covers Phase 1 entirely. Add Neon only if you want SQL dashboards decoupled from the box.

### Free object storage (backups / cold bar archive)

- **Google Cloud Storage Always Free:** **5 GB-months** regional storage (US regions `us-east1`/`us-west1`/`us-central1`), 5k Class-A + 50k Class-B ops, 100 GB NA egress/mo. No end date.
- **Cloudflare R2** (verify current free allowance separately) and **GitHub Releases** (2 GB/file, free on public/private) are good zero-egress-cost alternatives for compressed SQLite snapshots.

Sources:
- https://agentdeals.dev/neon-vs-supabase
- https://aiagencyplus.com/supabase-free-tier-limits/
- https://docs.cloud.google.com/free/docs/free-cloud-features

---

## 5. Recommended free-tier stack

| Layer | Pick | Why |
|---|---|---|
| **VPS (Gateway host)** | **Oracle Cloud Always Free — Ampere A1 ARM, 2–4 OCPU / 12–24 GB** | Only no-expiry free offer with enough RAM; IB Gateway runs on aarch64. |
| VPS fallback | GCP e2-micro (x86, 1 GB + swap) | If Oracle signup/capacity fails. RAM-tight. |
| Runtime | IB Gateway + IBC (auto-login) + app, via Docker (gnzsnz/ib-gateway-docker, multi-arch) | Reproducible, ARM-ready, auto-restart/login. |
| **CI/CD** | GitHub Actions, **private repo (2,000 free min/mo)**; optional self-hosted runner on the VPS | Build/test + deploy-over-SSH; keep trading code private. |
| **Heartbeat alerting** | **Healthchecks.io (20 checks)** → Telegram + email | Dead-man's-switch catches Gateway disconnects / crashes / reclamation. |
| **Metrics/logs/dashboards** | **Grafana Cloud free** (10k series, 50 GB logs, 14-day) | No card, Prometheus/Loki compatible, alerting built in. |
| **External up-check** | **Better Stack free (10 monitors)** | Avoids UptimeRobot's non-commercial clause. |
| **Primary data store** | **SQLite on the VPS** | $0, no egress, no pause/expiry; 3 months of bars < 1 GB. |
| **Backups / archive** | GCS Always Free 5 GB + GitHub Releases | Off-box durability for SQLite snapshots. |
| Optional SQL/dashboard DB | **Neon free** (graceful scale-to-zero) | Only if you want managed Postgres for queries. |

**Net result:** a fully always-on IBKR Gateway + trading app + CI/CD + monitoring + 3 months of data collection for **$0/month**, no subscriptions.

---

## 6. Expiry / limit traps table

| Service | Trap | Impact | Mitigation |
|---|---|---|---|
| **Oracle Ampere A1** | **June 2026: free A1 cut 4 OCPU/24 GB → 2 OCPU/12 GB** | Over-limit instances get shut down until downsized | Provision ≤ 2 OCPU/12 GB now; still enough for Gateway |
| **Oracle Always Free** | **30-day idle → account reclamation/termination** | Lose the whole VPS | Keep CPU/net active (app does during market hrs; add weekend cron heartbeat) |
| **Oracle signup** | Capacity errors, card rejections, account bans | Can't create A1 at all | Retry/region-hop; have GCP fallback ready |
| **GCP e2-micro** | **1 GB RAM** (< 1.5–2 GB need) + **1 GB/mo egress** + US-region-only | OOM risk; limited outbound | Large swapfile, lean app, minimize off-box shipping |
| **AWS Free Tier** | **Expires** (12 mo legacy / 6 mo + credits for new accts) | Box starts billing | Don't use for always-on |
| **Fly.io** | **No free tier** (2-hr/7-day trial only) | N/A | Skip |
| **Render free** | **Sleeps after 15 min idle**; free Postgres **30-day expiry** | Kills Gateway session; DB deleted | Don't host Gateway/primary DB here |
| **GitHub Actions** | Private-repo **2,000 min/mo** cap; overage billed | CI stops / charges | Set $0 spend limit; or self-host runner on VPS |
| **UptimeRobot free** | **Personal/non-commercial only** (since 2024-12-01); 5-min interval | ToS risk for revenue app; slow detection | Use Better Stack instead for up-checks |
| **Healthchecks.io free** | 20 checks; **SMS = 0** | No SMS alerts | Use Telegram/email/Discord (free) |
| **Better Stack free** | 10 monitors; 30s interval/SMS/on-call are paid | Limited alerting depth | Pair with Healthchecks.io heartbeat |
| **Grafana Cloud free** | **14-day retention**, 10k series, 50 GB logs | Short history | Long-term data lives in SQLite, not Grafana |
| **Neon free** | Scales to zero after 5 min idle (graceful); ~0.5 GB/project | Cold-query latency (~1 s) | Fine; primary store is SQLite anyway |
| **Supabase free** | **Pauses entire project after 1 week idle (Feb 2026), manual unpause** | Data unreachable until you click unpause | Prefer Neon if you need managed Postgres |
| **GCS Always Free** | 5 GB-months, US regions only | Small archive cap | Compress SQLite snapshots; rotate |
