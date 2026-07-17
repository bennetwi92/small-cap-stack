// Shared bottom status bar (#288): the box-health card and freshness lines,
// rebuilt as a terminal status line pinned to the bottom of every page. Left to
// right: session chip (gold pre-market / cyan open), box connection, trading
// mode, scan window, deployed commit, data freshness, a page-owned slot, and
// the repo link. Self-mounting; polls status.json on the same ~60s cadence the
// dashboard always has.

import { fetchJson } from "./data.js";
import { esc } from "./fmt.js";
import { sessionNow, etClockNow } from "./session.js";

const POLL_MS = 60_000;
const STALE_MS = 30 * 60 * 1000;

const SESS_LABEL = { pre: "PRE", open: "OPEN", closed: "CLOSED" };

function ago(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return `${Math.round(s)}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

const _etTime = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
});
const etTime = (iso) => (iso ? _etTime.format(new Date(iso)) + " ET" : "—");

let bar = null;

function mount() {
  if (bar) return bar;
  bar = document.createElement("footer");
  bar.className = "statusbar";
  bar.innerHTML =
    `<span class="sb-field sb-sess" id="sb-sess"><span class="sb-dot"></span><span id="sb-sess-txt">—</span></span>` +
    `<span class="sb-field" id="sb-box"><span class="sb-dot"></span><span id="sb-box-txt">box —</span></span>` +
    `<span class="sb-field" id="sb-mode" hidden></span>` +
    `<span class="sb-field" id="sb-window" hidden></span>` +
    `<span class="sb-field" id="sb-commit" hidden></span>` +
    `<span class="sb-field" id="sb-tick" hidden></span>` +
    `<span class="sb-field" id="sb-data" hidden></span>` +
    `<span class="sb-field" id="sb-page" hidden></span>` +
    `<span class="sb-field"><a href="https://github.com/bennetwi92/small-cap-stack" title="Phase-1 = tracking only, no orders. Times in ET.">repo</a></span>`;
  document.body.appendChild(bar);
  return bar;
}

function renderSession() {
  const sess = sessionNow();
  const chip = bar.querySelector("#sb-sess");
  chip.classList.remove("pre", "open", "closed");
  chip.classList.add(sess);
  bar.querySelector("#sb-sess-txt").textContent = `${SESS_LABEL[sess]} ${etClockNow()} ET`;
}

function setField(id, html, cls) {
  const f = bar.querySelector(id);
  f.hidden = !html;
  if (html != null) f.innerHTML = html;
  if (cls != null) f.className = "sb-field " + cls;
}

function renderStatus(s) {
  const box = bar.querySelector("#sb-box");
  if (!s || !s.service) {
    box.className = "sb-field";
    bar.querySelector("#sb-box-txt").textContent = "box: waiting for first tick…";
    return;
  }
  const svc = s.service;
  box.className = "sb-field " + (svc.connected ? "sb-ok" : "sb-bad");
  bar.querySelector("#sb-box-txt").textContent = svc.connected ? "connected" : "disconnected";
  setField("#sb-mode", esc(svc.trading_mode || "—"));
  setField("#sb-window", svc.in_scan_window ? "in-window" : "off-window");
  setField("#sb-commit", "commit " + esc(svc.deployed_commit || "—"));
  renderTick(s);
  const stale = s.generated_utc && Date.now() - new Date(s.generated_utc).getTime() > STALE_MS;
  setField("#sb-data", `data ${esc(etTime(s.generated_utc))} (${esc(ago(s.generated_utc))})`,
    stale ? "sb-warn" : "");
}

// Tick health (#321): the last completed tick's duration vs its interval budget, plus skipped
// jobs. An over-budget tick means the scheduler will silently skip ticks (scanner gaps), so it
// gets warn colour at half the budget and bad past the budget — visible here, no SSH needed.
function renderTick(s) {
  const t = s.timings || {};
  const missed = (s.health || {}).jobs_missed_total || 0;
  if (t.tick_seconds_last == null) return setField("#sb-tick", null);
  const budget = t.tick_budget_sec || 60;
  let cls = "";
  if (t.tick_seconds_last > budget || missed > 0) cls = "sb-bad";
  else if (t.tick_seconds_last > 0.5 * budget) cls = "sb-warn";
  const txt = `tick ${t.tick_seconds_last.toFixed(1)}s` + (missed ? ` · ${missed} missed` : "");
  setField("#sb-tick", esc(txt), cls);
}

async function poll() {
  try {
    renderStatus(await fetchJson("status.json"));
  } catch {
    renderStatus(null);
  }
}

// Page-owned slot (e.g. Results row counts, last-refresh time).
export function setStatusPage(html) {
  mount();
  setField("#sb-page", html);
}

mount();
renderSession();
setInterval(renderSession, 10_000);
poll();
setInterval(poll, POLL_MS);
