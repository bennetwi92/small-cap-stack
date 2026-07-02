// Dashboard SPA (#70): polls the box's published JSON (#68/#69) and renders it.
// No build step, no framework — plain fetch + DOM. Data lives on the `dashboard-data`
// branch; CORS on raw.githubusercontent.com allows the cross-origin fetch.

const REPO = "bennetwi92/small-cap-stack";
const BRANCH = "dashboard-data";
const POLL_MS = 60_000;

const rawUrl = (file) =>
  `https://raw.githubusercontent.com/${REPO}/${BRANCH}/${file}?t=${Date.now()}`;

const _etTime = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
});
const _etDateTime = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", month: "short", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hour12: false,
});

const el = (id) => document.getElementById(id);
const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const etTime = (iso) => (iso ? _etTime.format(new Date(iso)) + " ET" : "—");
const etDateTime = (iso) => (iso ? _etDateTime.format(new Date(iso)) + " ET" : "—");

function ago(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return `${Math.round(s)}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

function relIn(iso) {
  if (!iso) return "";
  const s = (new Date(iso).getTime() - Date.now()) / 1000;
  if (s <= 0) return "due";
  if (s < 90) return `in ${Math.round(s)}s`;
  if (s < 5400) return `in ${Math.round(s / 60)}m`;
  return `in ${Math.round(s / 3600)}h`;
}

function shares(n) {
  if (n == null) return "—";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(0) + "K";
  return String(n);
}

async function fetchJson(file) {
  const res = await fetch(rawUrl(file), { cache: "no-store" });
  if (!res.ok) return null; // e.g. stats.json before the first EOD -> 404
  return res.json();
}

function renderStatus(s) {
  if (!s || !s.service) {
    el("health").innerHTML = '<span class="muted">Waiting for the first tick…</span>';
    return;
  }
  const svc = s.service;
  const scn = s.scanner || {};
  const opp = s.opportunities || {};
  const data = s.data || {};
  const stale = s.generated_utc && Date.now() - new Date(s.generated_utc).getTime() > 30 * 60 * 1000;

  el("health").innerHTML =
    `<span class="badge ${svc.connected ? "ok" : "bad"}">${svc.connected ? "connected" : "disconnected"}</span>` +
    `<span class="tag">${esc(svc.trading_mode)}</span>` +
    `<span class="tag">${svc.in_scan_window ? "in-window" : "off-window"}</span>` +
    `<span class="muted">commit ${esc(svc.deployed_commit || "—")}</span>` +
    `<span class="muted ${stale ? "warn" : ""}">data ${etTime(s.generated_utc)} (${ago(s.generated_utc)})</span>`;

  const cands =
    (scn.latest_candidates || [])
      .map((c) => `<tr><td>#${esc(c.rank)}</td><td><strong>${esc(c.symbol)}</strong></td></tr>`)
      .join("") || '<tr><td colspan="2" class="muted">none</td></tr>';
  el("scanner").innerHTML =
    `<p class="muted">last scan ${etTime(scn.last_scan_utc)} · ticks ${scn.scan_ticks_total ?? 0}</p>` +
    `<table><tbody>${cands}</tbody></table>`;

  const jobs = (svc.jobs || [])
    .map(
      (j) =>
        `<tr><td>${esc(j.id)}</td><td>${etDateTime(j.next_run_utc)}</td><td class="muted">${relIn(j.next_run_utc)}</td></tr>`,
    )
    .join("");
  el("tasks").innerHTML =
    `<table><thead><tr><th>job</th><th>next run</th><th></th></tr></thead><tbody>${jobs}</tbody></table>`;

  const order = ["opportunities", "scanner_hits", "bars", "news", "fundamentals"];
  el("data").innerHTML = order
    .filter((k) => data[k])
    .map(
      (k) =>
        `<div class="stat"><div class="k">${k}</div><div class="v">${data[k].today}</div><div class="muted">total ${data[k].total}</div></div>`,
    )
    .join("");

  el("opps-count").textContent = opp.open_today ?? 0;
  el("opps-symbols").textContent = (opp.symbols || []).join(" · ") || "none";
}

function renderStats(st) {
  if (!st || !st.opportunities || !st.opportunities.length) {
    el("stats").innerHTML =
      '<p class="muted">No EOD statistics yet — generated after 16:30 ET.</p>';
    return;
  }
  const agg = st.aggregates || {};
  const rows = st.opportunities
    .slice()
    .sort((a, b) => (b.max_r ?? -999) - (a.max_r ?? -999))
    .map(
      (o) =>
        `<tr><td><strong>${esc(o.symbol)}</strong></td><td>${o.bars}</td><td>${o.news_count}</td>` +
        `<td>${shares(o.float_shares)}</td><td>${o.bull_flag ? "✓" : "—"}</td>` +
        `<td>${o.triggered ? "✓" : "—"}</td><td>${o.max_r ?? "—"}</td><td>${o.mae_r ?? "—"}</td>` +
        `<td>${o.stopped_out ? "✓" : "—"}</td></tr>`,
    )
    .join("");
  el("stats").innerHTML =
    `<p class="muted">as of ${esc(st.trading_date)} · opps ${agg.opportunities ?? 0} · ` +
    `triggered ${agg.triggered ?? 0} · ≥1R ${agg.reached_1r ?? 0} · ≥2R ${agg.reached_2r ?? 0} · ≥3R ${agg.reached_3r ?? 0}</p>` +
    `<div class="scroll"><table><thead><tr>` +
    `<th>symbol</th><th>bars</th><th>news</th><th>float</th><th>flag</th>` +
    `<th>trig</th><th>MaxR</th><th>MAE</th><th>stop</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function refresh() {
  el("updated").textContent = "updating…";
  try {
    const [status, stats] = await Promise.all([fetchJson("status.json"), fetchJson("stats.json")]);
    renderStatus(status);
    renderStats(stats);
    el("error").hidden = true;
    el("updated").textContent =
      "updated " +
      new Intl.DateTimeFormat("en-US", {
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
      }).format(new Date());
  } catch (e) {
    el("error").hidden = false;
    el("error").textContent = "Failed to load dashboard data: " + e.message;
    el("updated").textContent = "update failed";
  }
}

el("refresh").addEventListener("click", refresh);
refresh();
setInterval(refresh, POLL_MS);
