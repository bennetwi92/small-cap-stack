// Review workbench (#142): a mobile-first, single-screen page for cycling back through any day's
// opportunities. Reads the same published JSON as the dashboard (#141): `index.json` for the
// date/symbol navigation and per-date `charts/<date>.json` for the full-day (04:00–16:00 ET) bars.
// No build step, no framework — plain fetch + DOM, reusing app.js's `buildChart` idiom. Write-back
// (notes / annotations) is a follow-up (#143); this page is read + navigate only.

const REPO = "bennetwi92/small-cap-stack";
const BRANCH = "dashboard-data";

const rawUrl = (file) =>
  `https://raw.githubusercontent.com/${REPO}/${BRANCH}/${file}?t=${Date.now()}`;

const el = (id) => document.getElementById(id);
const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const _etTime = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
});
const etFromEpoch = (sec) => _etTime.format(new Date(sec * 1000)); // candlestick axis (UNIX seconds)

async function fetchJson(file) {
  const res = await fetch(rawUrl(file), { cache: "no-store" });
  if (!res.ok) return null; // e.g. index.json before the first EOD -> 404
  return res.json();
}

// --- Chart colours + state (mirrors docs/app.js) -------------------------------------------
const MK = {
  up: "#1a7f37", down: "#c0362c",
  entry: "#2f81f7", stop: "#c0362c", firstHit: "#8957e5", maxR: "#d4a72c",
  volUp: "rgba(26,127,55,0.5)", volDown: "rgba(192,54,44,0.5)",
};

let chartsData = null; // last-fetched charts/<date>.json payload for the selected date
let chartApi = null; // LightweightCharts instance (recreated per drawn opportunity)
let candleSeries = null;
let volumeSeries = null;

// Compact "SYMBOL #run · 2.3R" option label, mirroring the dashboard's chart picker.
function optionLabel(c) {
  const label = c.run_count > 1 ? `${c.symbol} #${c.run}` : c.symbol;
  const tag = c.triggered
    ? c.stopped_out
      ? " · stopped"
      : ` · ${c.max_r ?? "?"}R`
    : " · no trigger";
  return label + tag;
}

// Reuse the dashboard's buildChart idiom: candles + volume histogram + entry/stop price lines +
// timestamp-placed markers + fitContent(). Markers carry epoch timestamps (#141) so they land on
// the right bars of the full-day series even though its indices differ from the run window's.
function buildChart(c) {
  const LC = window.LightweightCharts;
  const container = el("rv-chart");
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

  // Volume histogram overlaid on its own scale in the bottom ~20%, coloured by candle direction.
  const hasVolume = c.bars.some((b) => b.v != null);
  if (hasVolume) {
    volumeSeries = chartApi.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chartApi.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    volumeSeries.setData(
      c.bars.map((b) => ({ time: b.t, value: b.v ?? 0, color: b.c >= b.o ? MK.volUp : MK.volDown })),
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

  const m = c.markers;
  const markers = [];
  if (m.first_hit != null)
    markers.push({ time: m.first_hit, position: "belowBar", color: MK.firstHit, shape: "circle", text: "scan" });
  if (m.entry != null)
    markers.push({ time: m.entry, position: "belowBar", color: MK.entry, shape: "arrowUp", text: "entry" });
  if (m.max_r != null && c.max_r != null && c.max_r > 0)
    markers.push({ time: m.max_r, position: "aboveBar", color: MK.maxR, shape: "circle", text: `${c.max_r}R` });
  if (m.stop != null)
    markers.push({ time: m.stop, position: "aboveBar", color: MK.stop, shape: "arrowDown", text: "stop" });
  markers.sort((a, b) => a.time - b.time); // lightweight-charts needs ascending marker times
  candleSeries.setMarkers(markers);
  chartApi.timeScale().fitContent();

  el("rv-readout").innerHTML =
    `<span class="mk" style="color:${MK.entry}">entry ${c.levels.entry ?? "—"}</span>` +
    `<span class="mk" style="color:${MK.stop}">stop ${c.levels.stop ?? "—"}</span>` +
    `<span class="mk" style="color:${MK.maxR}">Max R ${c.max_r != null ? c.max_r + "R" : "—"}</span>` +
    (c.triggered ? (c.stopped_out ? '<span class="muted">stopped out</span>' : "") : '<span class="muted">no trigger</span>');
}

function clearChart(message) {
  if (chartApi) {
    chartApi.remove();
    chartApi = null;
    candleSeries = null;
    volumeSeries = null;
  }
  el("rv-readout").innerHTML = `<span class="muted">${esc(message)}</span>`;
}

// Draw whichever opportunity the symbol dropdown currently points at.
function drawSelected() {
  const list = (chartsData && chartsData.charts) || [];
  const c = list.find((x) => x.opportunity_id === el("rv-symbol").value) || list[0];
  if (!window.LightweightCharts) {
    clearChart("Chart library failed to load.");
    return;
  }
  if (!c) {
    clearChart("No opportunities for this date.");
    return;
  }
  buildChart(c);
}

// Load a trading date's chart file, repopulate the symbol dropdown, and draw the first opportunity.
async function loadDate(date) {
  clearChart("loading…");
  chartsData = await fetchJson(`charts/${date}.json`);
  const list = (chartsData && chartsData.charts) || [];
  el("rv-symbol").innerHTML = list
    .map((c) => `<option value="${esc(c.opportunity_id)}">${esc(optionLabel(c))}</option>`)
    .join("");
  drawSelected();
}

// Step the symbol selection by ±1 with wrap-around (mirrors the dashboard's prev/next).
function stepSymbol(delta) {
  const sel = el("rv-symbol");
  const n = sel.options.length;
  if (!n) return;
  sel.selectedIndex = (sel.selectedIndex + delta + n) % n;
  drawSelected();
}

async function init() {
  const index = await fetchJson("index.json");
  const dates = (index && index.dates) || [];
  const dateSel = el("rv-date");
  if (!dates.length) {
    dateSel.innerHTML = '<option>—</option>';
    clearChart("No review data published yet.");
    return;
  }
  // index.json dates are already sorted newest-first (#141).
  dateSel.innerHTML = dates
    .map((d) => `<option value="${esc(d.date)}">${esc(d.date)}</option>`)
    .join("");
  await loadDate(dateSel.value);
}

el("rv-date").addEventListener("change", (e) => loadDate(e.target.value));
el("rv-symbol").addEventListener("change", drawSelected);
el("rv-prev").addEventListener("click", () => stepSymbol(-1));
el("rv-next").addEventListener("click", () => stepSymbol(1));
init();
