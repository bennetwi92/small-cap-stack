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
const etFromEpoch = (sec) => _etTime.format(new Date(sec * 1000)); // candlestick axis (UNIX seconds)

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
  // Kept in step with review.js `fmtShares` (a shared module is pending, #163): same tiers, casing
  // and boundary promotion, so the same float renders identically on the dashboard and workbench.
  if (n == null || !isFinite(n)) return "—";
  const a = Math.abs(n);
  if (a >= 999.95e6) return (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
  if (a >= 999.5e3) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (a >= 1e3) return Math.round(n / 1e3) + "k";
  return String(Math.round(n));
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
  // "Last completed session" header + opportunity summary. stats.json/charts.json are written
  // only at EOD, so this is the last finished US session — it stays put all day (through the UK
  // day) until the next close overwrites it. Mirrors the live "Opportunities today" card above.
  const opps = (st && st.opportunities) || [];
  const symbols = [...new Set(opps.map((o) => o.symbol))].sort();
  el("session-date").textContent = st && st.trading_date ? st.trading_date : "no completed session yet";
  // Count opportunities (segmented runs), not distinct symbols, so this agrees with the aggregate
  // line's "opps N" and the one-row-per-run table below — a symbol can run more than once (#163-C4).
  el("session-opps-count").textContent = opps.length;
  el("session-opps-symbols").textContent = symbols.join(" · ") || "none";

  if (!opps.length) {
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
        // Suffix the run (#2, #3, …) for a symbol that ran more than once, matching the EOD report,
        // so repeat runs don't render as indistinguishable identical rows (#163-C4).
        `<tr><td><strong>${esc(o.run_count > 1 ? `${o.symbol}#${o.run}` : o.symbol)}</strong></td>` +
        `<td>${etTime(o.first_hit)}</td>` +
        `<td>${o.bars}</td><td>${o.news_count}</td>` +
        `<td>${shares(o.float_shares)}</td><td>${o.bull_flag ? "✓" : "—"}</td>` +
        `<td>${o.triggered ? "✓" : "—"}</td><td>${o.max_r ?? "—"}</td><td>${o.mae_r ?? "—"}</td>` +
        `<td>${o.stopped_out ? "✓" : "—"}</td></tr>`,
    )
    .join("");
  el("stats").innerHTML =
    `<p class="muted">as of ${esc(st.trading_date)} · opps ${agg.opportunities ?? 0} · ` +
    `triggered ${agg.triggered ?? 0} · ≥1R ${agg.reached_1r ?? 0} · ≥2R ${agg.reached_2r ?? 0} · ≥3R ${agg.reached_3r ?? 0}</p>` +
    `<div class="scroll"><table><thead><tr>` +
    `<th>symbol</th><th>seen</th><th>bars</th><th>news</th><th>float</th><th>flag</th>` +
    `<th>trig</th><th>MaxR</th><th>MAE</th><th>stop</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

// --- Trade charts (#113): annotated 5-min candlesticks per opportunity ---------------------
const MK = {
  up: "#1a7f37", down: "#c0362c",
  entry: "#2f81f7", stop: "#c0362c", firstHit: "#8957e5", maxR: "#d4a72c",
  volUp: "rgba(26,127,55,0.5)", volDown: "rgba(192,54,44,0.5)", // muted volume bars
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
  // Hidden until the first EOD produces charts (charts.json 404s) or if the CDN lib didn't load.
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
    layout: { background: { color: "transparent" }, textColor: "#9aa4b2", fontSize: 11 },
    grid: {
      vertLines: { color: "rgba(255,255,255,0.05)" },
      horzLines: { color: "rgba(255,255,255,0.05)" },
    },
    rightPriceScale: { borderColor: "rgba(255,255,255,0.15)" },
    timeScale: {
      borderColor: "rgba(255,255,255,0.15)",
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

  // Volume histogram, overlaid on its own scale in the bottom ~20% so it never
  // crowds price. `v` is captured per 5-min bar (charts.py); bars may predate it,
  // so guard on presence. Coloured by the candle direction.
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

  // Markers carry epoch timestamps (#141) so they place correctly on the full-day series, whose
  // indices differ from the run window's — use the time value directly.
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
    `<span class="mk" style="color:${MK.maxR}">● Max R</span>` +
    `<span class="mk" style="color:${MK.stop}">▼ stop</span>` +
    (hasVolume ? `<span class="mk" style="color:${MK.up}">▮ volume</span>` : "") +
    `<span class="muted">entry ${c.levels.entry ?? "—"} · stop ${c.levels.stop ?? "—"}</span>`;
}

async function refresh() {
  el("updated").textContent = "updating…";
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

// Step the chart selection by ±1 with wrap-around, so prev/next cycle the
// opportunities without ever getting stuck at an end (mirrors the dropdown).
function stepChart(delta) {
  const sel = el("chart-select");
  const n = sel.options.length;
  if (!n) return;
  sel.selectedIndex = (sel.selectedIndex + delta + n) % n;
  drawSelected();
}

el("refresh").addEventListener("click", refresh);
el("chart-select").addEventListener("change", drawSelected);
el("chart-prev").addEventListener("click", () => stepChart(-1));
el("chart-next").addEventListener("click", () => stepChart(1));
refresh();
setInterval(refresh, POLL_MS);
