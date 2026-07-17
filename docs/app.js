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
const etFromEpoch = (sec) => _etTime.format(new Date(sec * 1000)); // candlestick axis (UNIX seconds)

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
    { type: "btn", id: "chart-prev", label: "‹", title: "Previous symbol" },
    { type: "select", id: "chart-select", label: "CHART", options: [] },
    { type: "btn", id: "chart-next", label: "›", title: "Next symbol" },
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
    if (id === "chart-select") drawSelected();
    if (id === "chart-prev") stepChart(-1);
    if (id === "chart-next") stepChart(1);
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

/* ---------- trade chart (#113): annotated 5-min candlesticks ---------- */

const MK = {
  up: "#3ec07e", down: "#f06673",
  entry: "#4fe3ef", stop: "#f06673", firstHit: "#8957e5", maxR: "#e3b452",
  volUp: "rgba(62,192,126,0.45)", volDown: "rgba(240,102,115,0.45)",
};

let chartsData = null; // last-fetched charts.json payload
let chartApi = null; // LightweightCharts instance (recreated when the drawn opportunity changes)
let candleSeries = null;
let volumeSeries = null;
let renderedKey = null; // opportunity_id currently drawn
let renderedGen = null; // charts.json generated_utc currently drawn

function renderCharts(data) {
  chartsData = data;
  const card = el("charts-card");
  const list = (data && data.charts) || [];
  // Hidden until the first EOD produces charts, or if the CDN lib didn't load.
  if (!window.LightweightCharts || !list.length) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const sel = el("chart-select");
  const ids = list.map((c) => c.opportunity_id);
  // Repopulate options only when the set of opportunities changes, to preserve the user's pick.
  if (Array.from(sel.options).map((o) => o.value).join("|") !== ids.join("|")) {
    const prev = sel.value;
    sel.innerHTML = list
      .map((c) => {
        const label = c.run_count > 1 ? `${c.symbol} #${c.run}` : c.symbol;
        const tag = c.triggered
          ? c.stopped_out
            ? " · stopped"
            : ` · ${c.max_r ?? "?"}R`
          : " · no trigger";
        return `<option value="${esc(c.opportunity_id)}">${esc(label + tag)}</option>`;
      })
      .join("");
    if (ids.includes(prev)) sel.value = prev;
  }
  drawSelected();
}

function drawSelected() {
  const list = (chartsData && chartsData.charts) || [];
  const c = list.find((x) => x.opportunity_id === el("chart-select").value) || list[0];
  if (!c) return;
  // charts.json only changes at EOD — skip redundant redraws so 60s polls don't reset zoom/pan.
  if (renderedKey === c.opportunity_id && renderedGen === chartsData.generated_utc) return;
  renderedKey = c.opportunity_id;
  renderedGen = chartsData.generated_utc;
  buildChart(c);
}

function buildChart(c) {
  const LC = window.LightweightCharts;
  const container = el("chart");
  if (chartApi) chartApi.remove();
  chartApi = LC.createChart(container, {
    autoSize: true,
    layout: {
      background: { color: "transparent" },
      textColor: "#9aa0b5",
      fontSize: 10,
      fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
    },
    grid: {
      vertLines: { color: "rgba(255,255,255,0.04)" },
      horzLines: { color: "rgba(255,255,255,0.04)" },
    },
    rightPriceScale: { borderColor: "#2e2e42" },
    timeScale: {
      borderColor: "#2e2e42",
      timeVisible: true,
      secondsVisible: false,
      tickMarkFormatter: (t) => etFromEpoch(t),
    },
    localization: { timeFormatter: (t) => etFromEpoch(t) + " ET" },
  });
  candleSeries = chartApi.addCandlestickSeries({
    upColor: MK.up, downColor: MK.down,
    borderUpColor: MK.up, borderDownColor: MK.down,
    wickUpColor: MK.up, wickDownColor: MK.down,
  });
  candleSeries.setData(
    c.bars.map((b) => ({ time: b.t, open: b.o, high: b.h, low: b.l, close: b.c })),
  );

  // Volume histogram in the bottom ~20%; bars may predate `v`, so guard on presence.
  const hasVolume = c.bars.some((b) => b.v != null);
  if (hasVolume) {
    volumeSeries = chartApi.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chartApi.priceScale("vol").applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });
    volumeSeries.setData(
      c.bars.map((b) => ({
        time: b.t,
        value: b.v ?? 0,
        color: b.c >= b.o ? MK.volUp : MK.volDown,
      })),
    );
  } else {
    volumeSeries = null;
  }

  // Entry-trigger + stop levels (shown even when the setup never triggered — where a fill'd be).
  if (c.levels.entry != null)
    candleSeries.createPriceLine({
      price: c.levels.entry, color: MK.entry, lineStyle: 2, lineWidth: 1,
      axisLabelVisible: true, title: "entry",
    });
  if (c.levels.stop != null)
    candleSeries.createPriceLine({
      price: c.levels.stop, color: MK.stop, lineStyle: 2, lineWidth: 1,
      axisLabelVisible: true, title: "stop",
    });

  // Markers carry epoch timestamps (#141) so they place correctly on the full-day series.
  const m = c.markers;
  const markers = [];
  if (m.first_hit != null)
    markers.push({ time: m.first_hit, position: "belowBar", color: MK.firstHit, shape: "circle", text: "scan" });
  if (m.entry != null)
    markers.push({ time: m.entry, position: "belowBar", color: MK.entry, shape: "arrowUp", text: "entry" });
  // Max-R marker only when there was a real favourable excursion (skip the 0R same-bar stop).
  if (m.max_r != null && c.max_r != null && c.max_r > 0)
    markers.push({ time: m.max_r, position: "aboveBar", color: MK.maxR, shape: "circle", text: `${c.max_r}R` });
  if (m.stop != null)
    markers.push({ time: m.stop, position: "aboveBar", color: MK.stop, shape: "arrowDown", text: "stop" });
  markers.sort((a, b) => a.time - b.time); // lightweight-charts needs ascending marker times
  candleSeries.setMarkers(markers);
  chartApi.timeScale().fitContent();

  el("chart-legend").innerHTML =
    `<span class="mk" style="color:${MK.firstHit}">● scan</span>` +
    `<span class="mk" style="color:${MK.entry}">▲ entry</span>` +
    `<span class="mk" style="color:${MK.maxR}">● max r</span>` +
    `<span class="mk" style="color:${MK.stop}">▼ stop</span>` +
    (hasVolume ? `<span class="mk" style="color:${MK.up}">▮ vol</span>` : "") +
    `<span class="muted">entry ${c.levels.entry ?? "—"} · stop ${c.levels.stop ?? "—"}</span>`;
}

/* ---------- poll loop ---------- */

async function refresh() {
  setStatusPage("updating…");
  try {
    const [status, stats, charts] = await Promise.all([
      fetchJson("status.json"),
      fetchJson("stats.json"),
      fetchJson("charts.json"),
    ]);
    renderStatus(status);
    renderStats(stats);
    renderCharts(charts);
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

// Step the chart selection by ±1 with wrap-around (mirrors the dropdown).
function stepChart(delta) {
  const sel = el("chart-select");
  const n = sel.options.length;
  if (!n) return;
  sel.selectedIndex = (sel.selectedIndex + delta + n) % n;
  drawSelected();
}

refresh();
setInterval(refresh, POLL_MS);
