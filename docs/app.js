// Dashboard (#70, cockpit #289): polls the box's published JSON (#68/#69) and
// renders it. No build step, no framework — plain fetch + DOM on the shared
// cockpit chrome. Box health renders in the shared status bar (js/status-bar.js),
// not here; this page owns the live-day rail, the last completed session and
// the trade chart.

import "./js/nav.js";
import { createOptionsBar } from "./js/options-bar.js";
import { setStatusPage } from "./js/status-bar.js";
import { fetchJson } from "./js/data.js";
import { esc, fmtShares, rRampClass } from "./js/fmt.js";

const POLL_MS = 60_000;

const _etTime = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
});
const _etDateTime = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", month: "short", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hour12: false,
});
// en-CA renders YYYY-MM-DD — the ET trading date for the options bar.
const _etDate = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" });

const el = (id) => document.getElementById(id);
const etTime = (iso) => (iso ? _etTime.format(new Date(iso)) + " ET" : "—");
const etDateTime = (iso) => (iso ? _etDateTime.format(new Date(iso)) + " ET" : "—");

function relIn(iso) {
  if (!iso) return "";
  const s = (new Date(iso).getTime() - Date.now()) / 1000;
  if (s <= 0) return "due";
  if (s < 90) return `in ${Math.round(s)}s`;
  if (s < 5400) return `in ${Math.round(s / 60)}m`;
  return `in ${Math.round(s / 3600)}h`;
}

/* ---------- options bar: ET date, chart stepper, refresh ---------- */

createOptionsBar("optbar", {
  primary: [
    { type: "readout", id: "et-date", label: "ET DATE", value: _etDate.format(new Date()) },
    { type: "btn", id: "refresh", label: "Refresh", title: "Refresh now" },
  ],
  extra: [
    {
      type: "note",
      value:
        "Auto-refreshes from the box (~15 min publish cadence, 60s poll). Times in ET. " +
        "Last session persists until the next US close. Phase-1 = tracking only, no orders.",
    },
  ],
  onChange: (id) => {
    if (id === "refresh") refresh();
  },
});

/* ---------- live-day rail ---------- */

function renderStatus(s) {
  // Box health (connection / mode / window / commit / freshness) lives in the
  // shared status bar now — this renders only the page-owned panels.
  const scn = (s && s.scanner) || {};
  const opp = (s && s.opportunities) || {};
  const data = (s && s.data) || {};

  const cands = (scn.latest_candidates || [])
    .map((c) => `<strong>${esc(c.symbol)}</strong><span class="muted">#${esc(c.rank)}</span>`)
    .join(" · ") || '<span class="muted">none</span>';
  el("scanner").innerHTML =
    `<p class="dash-syms">${cands}</p>` +
    `<p class="dash-agg muted">last scan ${esc(etTime(scn.last_scan_utc))} · ticks ${scn.scan_ticks_total ?? 0}</p>`;

  const jobs = ((s && s.service && s.service.jobs) || [])
    .map(
      (j) =>
        `<tr><td>${esc(j.id)}</td><td>${esc(etDateTime(j.next_run_utc))}</td>` +
        `<td class="muted">${esc(relIn(j.next_run_utc))}</td></tr>`,
    )
    .join("");
  el("tasks").innerHTML =
    `<table class="tbl"><thead><tr><th>job</th><th>next run</th><th></th></tr></thead><tbody>${jobs}</tbody></table>`;

  const order = ["opportunities", "scanner_hits", "bars", "news", "fundamentals"];
  el("data").innerHTML = order
    .filter((k) => data[k])
    .map(
      (k) =>
        `<div class="tile"><div class="tile-l">${esc(k)}</div>` +
        `<div class="tile-v">${data[k].today}</div>` +
        `<div class="tile-s">total ${data[k].total}</div></div>`,
    )
    .join("");

  el("opps-count").textContent = opp.open_today ?? 0;
  el("opps-symbols").innerHTML =
    (opp.symbols || []).map((x) => `<strong>${esc(x)}</strong>`).join(" · ") ||
    '<span class="muted">none</span>';
}

/* ---------- last completed session ---------- */

const check = (b) => (b ? '<span class="up">✓</span>' : '<span class="muted">—</span>');
// MAE is an *adverse* excursion stored positive (1 = the full stop distance
// against), so its ramp runs inverted: high MAE wears the loss colours.
const rCell = (v, invert = false) =>
  `<td class="r ${v == null ? "muted" : rRampClass(invert ? -v : v)}">${v ?? "—"}</td>`;

function renderStats(st) {
  // stats.json/charts.json are written only at EOD, so this is the last
  // finished US session — it stays put all day until the next close.
  const opps = (st && st.opportunities) || [];
  const symbols = [...new Set(opps.map((o) => o.symbol))].sort();
  el("session-date").textContent = st && st.trading_date ? st.trading_date : "no completed session yet";
  // Count opportunities (segmented runs), not distinct symbols (#163-C4).
  el("session-opps-count").textContent = opps.length;
  el("session-opps-symbols").innerHTML =
    symbols.map((x) => `<strong>${esc(x)}</strong>`).join(" · ") || '<span class="muted">none</span>';

  if (!opps.length) {
    el("session-agg").textContent = "";
    el("stats").innerHTML =
      '<p class="muted">No EOD statistics yet — generated after 16:30 ET.</p>';
    return;
  }
  const agg = st.aggregates || {};
  el("session-agg").textContent =
    `opps ${agg.opportunities ?? 0} · triggered ${agg.triggered ?? 0} · ` +
    `≥1R ${agg.reached_1r ?? 0} · ≥2R ${agg.reached_2r ?? 0} · ≥3R ${agg.reached_3r ?? 0}`;
  const rows = st.opportunities
    .slice()
    .sort((a, b) => (b.max_r ?? -999) - (a.max_r ?? -999))
    .map(
      (o) =>
        // Suffix the run (#2, #3, …) for a symbol that ran more than once (#163-C4).
        `<tr><td><strong>${esc(o.run_count > 1 ? `${o.symbol}#${o.run}` : o.symbol)}</strong></td>` +
        `<td>${esc(etTime(o.first_hit))}</td>` +
        `<td class="r">${o.bars}</td><td class="r">${o.news_count}</td>` +
        `<td class="r">${fmtShares(o.float_shares)}</td><td>${check(o.bull_flag)}</td>` +
        `<td>${check(o.triggered)}</td>${rCell(o.max_r)}${rCell(o.mae_r, true)}` +
        `<td>${o.stopped_out ? '<span class="down">✓</span>' : '<span class="muted">—</span>'}</td></tr>`,
    )
    .join("");
  el("stats").innerHTML =
    `<table class="tbl"><thead><tr>` +
    `<th>symbol</th><th>seen</th><th class="r">bars</th><th class="r">news</th><th class="r">float</th>` +
    `<th>flag</th><th>trig</th><th class="r">max r</th><th class="r">mae</th><th>stop</th>` +
    `</tr></thead><tbody>${rows}</tbody></table>`;
}

/* ---------- poll loop ---------- */

async function refresh() {
  setStatusPage("updating…");
  try {
    const [status, stats] = await Promise.all([
      fetchJson("status.json"),
      fetchJson("stats.json"),
    ]);
    renderStatus(status);
    renderStats(stats);
    el("error").hidden = true;
    el("et-date").textContent = _etDate.format(new Date());
    const now = new Intl.DateTimeFormat("en-US", {
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    }).format(new Date());
    setStatusPage(`updated ${esc(now)}`);
  } catch (e) {
    el("error").hidden = false;
    el("error").textContent = "Failed to load dashboard data: " + e.message;
    setStatusPage("update failed");
  }
}

refresh();
setInterval(refresh, POLL_MS);
