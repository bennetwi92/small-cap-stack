# IBKR Unattended Runtime — what's off-the-shelf vs. custom (issue #11)

Research date: 2026-06-29. Verified against current docs/repos.

Goal: make the custom connection code for issue #11 as thin as possible by leaning on
established components (`ib_async.Watchdog`, IBC, the gnzsnz Docker image) and only
writing what genuinely has no off-the-shelf equivalent.

---

## TL;DR stack recommendation

Run **IB Gateway + IBC inside the `gnzsnz/ib-gateway-docker` container** (aarch64 build),
with the container handling login, the **mandatory daily restart (`AUTO_RESTART_TIME`)**,
and 2FA-timeout policy. Run the **Python `ib_async` app in a separate container** that
connects to the gateway over TCP (port 4001 live / 4002 paper).

Key architectural finding: **in this Docker-separated model, `ib_async.Watchdog` is *not*
the right reconnect mechanism** — Watchdog's whole job is to *launch and restart the
Gateway process itself via a local `IBC` object*, which it cannot do when the Gateway lives
in another container. So the restart/login/2FA concerns are owned by IBC + the container,
and the app side just needs a **thin reconnect-and-resync loop**. (Watchdog only makes sense
if you run Gateway as a child process on the *same* host as Python — not the chosen design.)

---

## 1. `ib_async.Watchdog`

Source: <https://ib-api-reloaded.github.io/ib_async/_modules/ib_async/ibcontroller.html>,
API: <https://ib-api-reloaded.github.io/ib_async/api.html> (ib_async 2.1.0, current).

What it does:
- Starts the Gateway/TWS **via an `IBC` object it owns**, connects an `IB` instance, and
  watches the connection.
- Disconnect detection two ways: (a) **idle detection** — if no network traffic for
  `appTimeout` seconds it fires a probe historical request (`probeContract`, default
  EURUSD) to check liveness; (b) reacts **directly to error codes 1100 and 100**.
- On soft timeout it probes; on hard timeout / probe failure or disconnect it **restarts
  the Gateway through IBC and reconnects**.
- Emits events: `startingEvent`, `startedEvent`, `stoppingEvent`, `stoppedEvent`,
  `softTimeoutEvent`, `hardTimeoutEvent`.

Parameters (defaults): `appStartupTime=30`, `appTimeout=20`, `retryDelay=2`,
`connectTimeout=2`, `probeContract=Forex('EURUSD')`, `probeTimeout=4`.

Usage pattern (host-local model only):
```python
ibc = IBC(1019, gateway=True, tradingMode='paper')   # Watchdog launches Gateway via IBC
ib = IB()
ib.connectedEvent += onConnected                       # YOU re-init state here
watchdog = Watchdog(ibc, ib, port=4002)
watchdog.start()
ib.run()
```

What it does **NOT** do (must be custom regardless of model):
- Does **not** re-subscribe market data after reconnect.
- Does **not** resync orders/positions/account state — you do that in your
  `connectedEvent`/`startedEvent` handler.
- Does **not** handle 2FA (that's IBC).
- Docs explicitly warn: *"Do not expect Watchdog to magically shield you from reality...
  Do not use Watchdog unless you understand what it does and doesn't do."* Intended for
  event-driven apps, **not** notebooks/imperative code.

Limitation for our design: Watchdog assumes it can spawn/kill the Gateway locally via IBC.
With Gateway in a separate Docker container it cannot, so we don't use it for restart — we
use a plain reconnect loop + `disconnectedEvent`. (Known issue: Watchdog restart loops where
it keeps restarting an actually-healthy Gateway — ib_insync #93 — another reason to keep the
app-side logic simple and let IBC/container own lifecycle.)

---

## 2. IBC (IbcAlpha/IBC)

Source: <https://github.com/IbcAlpha/IBC>, user guide:
<https://github.com/IbcAlpha/IBC/blob/master/userguide.md>. Current release line **3.24.0**
(as bundled by gnzsnz). Runs on Windows/macOS/Linux.

Handles unattended operation:
- **Auto-login**: fills username/password and clicks Login on Gateway/TWS startup.
- **Daily restart without re-auth**: `AutoRestartTime` in `config.ini` (or the TWS
  "Lock and Exit" → Auto restart setting). This restarts Gateway daily but only requires a
  fresh login **once per week, after 01:00 ET Sunday** — IB invalidates the session weekly.
- Alternative `ClosedownAt` / auto-logoff fully shuts down (then needs manual re-auth) —
  not what we want.
- **2FA settings**: `ReloginAfterSecondFactorAuthenticationTimeout=yes` (re-attempts login
  if the user misses the ~3-min IBKR Mobile alert), `SecondFactorAuthenticationExitInterval`
  (how long IBC waits after acknowledgement before giving up). Start-script env
  `TWOFA_TIMEOUT_ACTION = restart|exit`.
- API/security config: `AcceptIncomingConnectionAction` (set `reject` + IP allowlist),
  `OverrideTwsApiPort`, `ReadOnlyApi`, `AcceptNonBrokerageAccountWarning`.

**2FA hard limitation:** IBC can enter credentials but **cannot complete second-factor
itself**. With IBKR Mobile push you must acknowledge on your phone; with a security
card/device you must type the code. So the **weekly Sunday ~01:00 ET event is inherently
manual** — best IBC can do is keep retrying the login prompt and not crash while it waits.
This is the one event that always needs a human → drives the cold-restart alert requirement.

---

## 3. gnzsnz/ib-gateway-docker (and alternatives)

Source: <https://github.com/gnzsnz/ib-gateway-docker>,
Hub: <https://hub.docker.com/r/gnzsnz/ib-gateway>.

Bundles: IB Gateway + **IBC** + **Xvfb** (headless X) + **x11vnc** (optional GUI for
debugging) + **socat** (remaps internal localhost API port to external container port) +
optional **SSH tunnel** for secure remote API/VNC.

Current tags: **latest** = Gateway 10.48.1c / IBC 3.24.0; **stable** = Gateway 10.45.1h /
IBC 3.24.0. **aarch64/ARM supported since 10.37.1l (stable) / 10.39.1e (latest)** — works on
Oracle Ampere, Raspberry Pi, Apple Silicon.

Relevant env vars (set IBC config for you): `TWS_USERID`, `TWS_PASSWORD` /
`TWS_PASSWORD_FILE`, `TRADING_MODE` (paper/live/both), `READ_ONLY_API`, `BYPASS_WARNING`,
`TWOFA_TIMEOUT_ACTION` (default `exit`), `RELOGIN_AFTER_TWOFA_TIMEOUT` (default `no`),
**`AUTO_RESTART_TIME`** (e.g. `"11:59 PM"` — does the mandatory daily restart *inside* the
container, no daily 2FA), `AUTO_LOGOFF_TIME`, `TIME_ZONE` (default `Etc/UTC`),
`SAVE_TWS_SETTINGS`.

Ops behavior:
- **No built-in Docker `HEALTHCHECK`** in the image — you add `restart: always` in compose,
  and should add your own healthcheck (e.g. TCP probe on 4001/4002) if you want container
  auto-recovery on hang.
- Recommended daily-restart strategy: **use `AUTO_RESTART_TIME` (IBC-managed in-container
  restart)**, not a container bounce — preserves the weekly session and avoids extra 2FA.
- `ib_async` connects over TCP to the mapped port; the README notes the image pairs well with
  ib_insync/ib_async-based app containers.

Alternatives: `waytrade/ib-gateway` and `UnusualAlpha/ib-gateway-docker` are the older
lineage that gnzsnz forked/superseded; gnzsnz is the actively maintained one with current
Gateway versions and ARM support. `tdeblsh/ib-gateway-docker-python` bundles Python in the
same image (single-container variant).

**Verdict on this layer:** the Docker image removes essentially all the OS/login/restart ops
work — Xvfb, IBC wiring, daily restart, 2FA-timeout policy, port relay, ARM build are all
handled. You write **zero** of that.

---

## 4. The thin custom layer still required for #11

After the image + IBC + (optionally) Watchdog, what's genuinely left is **app-side connection
state management** — there is no off-the-shelf component for any of these:

1. **Reconnect loop / connection supervisor.** Because Gateway is in another container,
   write a small supervisor: `ib.connect(...)` with retry/backoff, subscribe to
   `ib.disconnectedEvent` to trigger reconnect, and a periodic liveness check (e.g.
   `ib.reqCurrentTime()` or a probe) to detect half-open sockets. This replaces Watchdog's
   role in our design. ~50-100 lines.

2. **Survive the daily restart gracefully.** During the `AUTO_RESTART_TIME` window the API
   socket drops for ~10-60s. The reconnect loop must treat this as expected (no human alert),
   distinguishing it from a cold failure. Optionally suppress alerts during the known restart
   minute.

3. **Order/position/account resync on (re)connect.** In the connect handler call
   `ib.reqOpenOrders()` / `ib.reqAllOpenOrders()`, `ib.reqPositions()`,
   `ib.reqAccountUpdates()` and reconcile against your own intended state (detect fills/changes
   that happened while disconnected). ib_async repopulates its caches but does **not**
   diff against your strategy state.

4. **Market-data re-subscription bookkeeping.** Keep a registry of desired subscriptions
   (`reqMktData`/`reqRealTimeBars`/tickByTick) and **replay them on every reconnect** —
   subscriptions do not survive a disconnect and nothing re-issues them for you.

5. **Error-code handling.** Subscribe to `ib.errorEvent` and react to connectivity codes:
   **1100** (connectivity lost), **1101** (restored — *data lost*, must re-subscribe),
   **1102** (restored — *data maintained*), 2103/2104/2106/2158 (data farm status). 1101 vs
   1102 decides whether you must re-subscribe market data. Watchdog reacts to 1100 for restart
   but does not do your re-subscription.

6. **Cold-restart / human alert.** When reconnect fails beyond N attempts (the signature of
   the weekly Sunday 2FA needing a human, or `TWOFA_TIMEOUT_ACTION=exit` having killed the
   gateway), fire an alert (email/Telegram/PagerDuty). This is the only path that needs a
   person, so it must be unmistakable. ~20 lines.

7. **Idempotent startup / state reconciliation** so the supervisor restarting mid-session
   doesn't double-submit orders.

Everything else (Xvfb, login automation, daily restart scheduling, 2FA-timeout policy, ARM
build) is provided.

---

## 5. Known pitfalls (running this for months)

- **Weekly 2FA is unavoidably manual** with IBKR Mobile/security-device. Plan for a human
  ack every Sunday after 01:00 ET; set `RELOGIN_AFTER_TWOFA_TIMEOUT=yes` and a generous
  `SecondFactorAuthenticationExitInterval` so IBC keeps the prompt open instead of dying.
  Decide `TWOFA_TIMEOUT_ACTION` carefully: `restart` keeps retrying; `exit` needs container
  `restart: always` + your alert.
- **Watchdog restart loops** (ib_insync #93): it can keep restarting a healthy Gateway. Avoid
  by not using Watchdog in the container-separated design; if used, tune `appTimeout`/probe.
- **TWS/Gateway reliability** is the weak link, not the libraries — half-open sockets where
  the OS thinks the connection is alive; hence an active liveness probe, not just
  `disconnectedEvent`.
- **Memory**: bump Gateway JVM to ~4096 MB for bulk data; raise API timeouts for large
  historical requests to avoid spurious disconnects.
- **No HEALTHCHECK in the image** — add a TCP/port healthcheck so Docker can recover a hung
  container; `restart: always` alone won't catch a process that's up but wedged.
- **Time zone / restart timing**: set `TIME_ZONE` and pick `AUTO_RESTART_TIME` well clear of
  your trading windows and clear of the 01:00 ET Sunday boundary.
- **Version drift**: IB pushes Gateway updates frequently; pin to the `stable` tag and update
  deliberately rather than tracking `latest`.

---

## Sources

- ib_async Watchdog source: <https://ib-api-reloaded.github.io/ib_async/_modules/ib_async/ibcontroller.html>
- ib_async API / docs: <https://ib-api-reloaded.github.io/ib_async/> · <https://pypi.org/project/ib_async/>
- IBC repo: <https://github.com/IbcAlpha/IBC>
- IBC user guide: <https://github.com/IbcAlpha/IBC/blob/master/userguide.md>
- gnzsnz/ib-gateway-docker: <https://github.com/gnzsnz/ib-gateway-docker> · <https://hub.docker.com/r/gnzsnz/ib-gateway>
- ARM connect discussion: <https://github.com/gnzsnz/ib-gateway-docker/discussions/113>
- Watchdog restart-loop pitfall: <https://github.com/erdewit/ib_insync/issues/93>
