// Review workbench (#142): a mobile-first, single-screen page for cycling back through any day's
// opportunities. Reads the same published JSON as the dashboard (#141): `index.json` for the
// date/symbol navigation and per-date `charts/<date>.json` for the full-day (04:00–16:00 ET) bars.
// No build step, no framework — plain fetch + DOM, reusing app.js's `buildChart` idiom. Write-back
// commits review JSON to the `review-data` branch: per-opportunity notes (#143) and tap-to-place
// chart annotations (pole/consolidation/entry/stop) with an auto-computed Max R (#144).

const REPO = "bennetwi92/small-cap-stack";
const BRANCH = "dashboard-data";
const REVIEW_BRANCH = "review-data"; // write-back reviews live here (#143), off the force-pushed BRANCH
const DEFAULT_BRANCH = "main"; // base the review-data branch off this on first save
const API = "https://api.github.com";
const PAT_KEY = "rv_pat"; // localStorage key for the phone-local GitHub token

const rawUrl = (file) =>
  `https://raw.githubusercontent.com/${REPO}/${BRANCH}/${file}?t=${Date.now()}`;

const el = (id) => document.getElementById(id);
const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const _etTime = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
});
const etFromEpoch = (sec) => _etTime.format(new Date(sec * 1000)); // candlestick axis (UNIX seconds)

// Date-picker label: "2026-07-01" -> "2026-07-01 · Wed" so the day of week reads at a glance.
// Parse the ISO parts directly (local Date from y/m/d, no UTC parse) so the weekday never tz-shifts.
const _DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const dateLabel = (iso) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso));
  if (!m) return String(iso);
  const dow = _DOW[new Date(+m[1], +m[2] - 1, +m[3]).getDay()];
  return `${iso} · ${dow}`;
};

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
  // trader's annotations (#144) — solid lines so they read distinctly from the engine's dashed ones.
  annEntry: "#3fb950", annStop: "#db6d28",
  poleBand: "rgba(137,87,229,0.18)", consBand: "rgba(212,167,44,0.20)",
  // opaque band-edge colours: bright vertical grab-handles at each band boundary so a placed band
  // reads clearly and its edges are visibly draggable (UX #152). Also used for the pending-edge line.
  poleEdge: "rgba(137,87,229,0.95)", consEdge: "rgba(212,167,44,0.98)",
  // engine-v2 overlay (#216) — the DETECTOR's read, kept visually distinct from the trader's own
  // annotations above: faint full-height fills + a solid top cap bar per band (vs the trader's
  // heavier translucent fills), and coloured H/L/E token letters (green/red/grey) along the top.
  engPole: "#3584e4", engCons: "#e5a50a", engBase: "#3584e4", engPeak: "#e5a50a",
  engPoleFill: "rgba(53,132,228,0.12)", engConsFill: "rgba(229,165,10,0.14)",
  engPrior: "rgba(139,148,161,0.10)", engPriorLbl: "#8b949e",
  tokH: "#3fb950", tokL: "#f85149", tokE: "#8b949e",
};

let chartsData = null; // last-fetched charts/<date>.json payload for the selected date
let chartApi = null; // LightweightCharts instance (recreated per drawn opportunity)
let candleSeries = null;
let volumeSeries = null;
let currentOpp = null; // the opportunity chart object currently drawn (for the notes sheet)
let currentDate = null; // the trading date currently loaded (to restore the picker on a cancelled nav)
const noteCache = new Map(); // opportunity_id -> loaded/saved review, so re-opening is instant

// Unsaved-changes tracking (#156): the review only persists on an explicit Save, and Save writes the
// whole review (verdict + annotations + note), not just the note. Mark dirty on any user edit so the
// Save controls can signal it and navigation can warn before discarding.
let dirty = false;
function markDirty() {
  if (dirty) return;
  dirty = true;
  updateSaveState();
}
function markClean() {
  dirty = false;
  updateSaveState();
}
// Reflect dirty state on both Save controls: an amber tint + a "•" so unsaved work is obvious.
function updateSaveState() {
  const top = el("rv-save-top");
  const sheet = el("rv-save");
  if (top) {
    top.textContent = dirty ? "Save •" : "Save";
    top.classList.toggle("dirty", dirty);
  }
  if (sheet) {
    sheet.textContent = dirty ? "Save review •" : "Save review";
    sheet.classList.toggle("dirty", dirty);
  }
}
// Guard a navigation that would discard unsaved edits; true = proceed.
function confirmDiscard() {
  return !dirty || window.confirm("Discard unsaved review changes?");
}

// --- Annotations (#144) --------------------------------------------------------------------
// The trader's read of the setup, drawn by tapping the chart: pole/consolidation time bands,
// entry/stop price lines, and an auto-computed Max R. Held per drawn opportunity, persisted into
// the review JSON's `annotations` block and round-tripped through the review-data branch.
const emptyAnn = () => ({
  pole: null, // { t0, t1, low, high }
  consolidation: null, // { t0, t1, high, low }
  entry: null, // price
  stop: null, // price
  entry_t: null, // epoch secs of the entry tap — needed to recompute Max R after a reload
});
let ann = emptyAnn();
let armed = null; // which element the next chart tap sets: 'pole' | 'cons' | 'entry' | 'stop' | null
let bandPending = null; // { mode, t0 } after the first of a band's two taps
let annEntryLine = null; // createPriceLine handles, so we can remove/replace on change
let annStopLine = null;
// Reviewer verdict (#155): "no trigger" means this wasn't a tradeable setup — distinct from the
// engine's `triggered:false` (a valid setup that never reached entry). When set, the engine's
// entry/stop context lines are dropped, the drawing toolbar is disabled, and no annotations apply.
let noTrigger = false;
let engineEntryLine = null; // handles for the engine's dashed entry/stop levels, so no-trigger can
let engineStopLine = null; //   strip them and toggling the verdict off can restore them.
let bandPrimitive = null; // BandPrimitive attached to the candle series (translucent bands)
let drag = null; // in-flight drag of a placed level (UX #152): { kind, field, edge } or null
const DRAG_HIT_PX = 16; // touch-friendly grab radius (CSS px) around a line/edge

const round2 = (x) => Math.round(x * 100) / 100;

// --- Translucent time-range bands via a Lightweight-Charts v4 series primitive --------------
// Full-height rectangles spanning [t0, t1] on the time scale (pole = purple, consolidation =
// amber). Coordinates are recomputed on every pan/zoom via updateAllViews() → paneView.update().
class BandRenderer {
  constructor(items, pending) {
    this._items = items;
    this._pending = pending; // { x, color } while the first edge of a two-tap band is placed
  }
  draw(target) {
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const hr = scope.horizontalPixelRatio;
      const h = scope.bitmapSize.height;
      const edgeW = Math.max(2, 2 * hr); // visible grab-handle width
      for (const it of this._items) {
        const x1 = Math.min(it.x1, it.x2) * hr;
        const x2 = Math.max(it.x1, it.x2) * hr;
        ctx.fillStyle = it.color;
        ctx.fillRect(x1, 0, Math.max(1, x2 - x1), h);
        // Bright opaque edges: make the band's boundaries obvious and signal they're draggable.
        ctx.fillStyle = it.edge;
        ctx.fillRect(x1 - edgeW / 2, 0, edgeW, h);
        ctx.fillRect(x2 - edgeW / 2, 0, edgeW, h);
      }
      // Pending first-tap edge: a dashed full-height line so you can see where edge 1 landed
      // before committing edge 2 (UX #152 — was invisible until both taps were placed).
      if (this._pending) {
        const x = this._pending.x * hr;
        ctx.save();
        ctx.strokeStyle = this._pending.color;
        ctx.lineWidth = Math.max(1, hr);
        ctx.setLineDash([5 * hr, 4 * hr]);
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
        ctx.stroke();
        ctx.restore();
      }
    });
  }
}
class BandPaneView {
  constructor(source) {
    this._source = source;
    this._items = [];
    this._pending = null;
  }
  update() {
    const src = this._source;
    const ts = src._chart && src._chart.timeScale();
    if (!ts) {
      this._items = [];
      this._pending = null;
      return;
    }
    this._items = src._bands
      .map((b) => ({
        x1: ts.timeToCoordinate(b.t0), x2: ts.timeToCoordinate(b.t1),
        color: b.color, edge: b.edge,
      }))
      .filter((it) => it.x1 !== null && it.x2 !== null);
    if (src._pendingTime != null) {
      const x = ts.timeToCoordinate(src._pendingTime);
      this._pending = x == null ? null : { x, color: src._pendingColor };
    } else {
      this._pending = null;
    }
  }
  renderer() {
    return new BandRenderer(this._items, this._pending);
  }
  zOrder() {
    return "bottom"; // behind the candles
  }
}
class BandPrimitive {
  constructor() {
    this._chart = null;
    this._bands = [];
    this._pendingTime = null; // epoch secs of an in-progress first band-edge tap, or null
    this._pendingColor = null;
    this._paneView = new BandPaneView(this);
    this._requestUpdate = null;
  }
  attached(params) {
    this._chart = params.chart;
    this._requestUpdate = params.requestUpdate;
  }
  detached() {
    this._chart = null;
    this._requestUpdate = null;
  }
  updateAllViews() {
    this._paneView.update();
  }
  paneViews() {
    return [this._paneView];
  }
  setBands(bands) {
    this._bands = bands;
    this._paneView.update();
    if (this._requestUpdate) this._requestUpdate();
  }
  setPending(time, color) {
    this._pendingTime = time;
    this._pendingColor = color;
    this._paneView.update();
    if (this._requestUpdate) this._requestUpdate();
  }
}

// --- Engine-v2 detection overlay (#216) ----------------------------------------------------
// The detector's read of the SAME full-day series the chart draws, published in charts.json's
// `engine` block (charts.py::_engine_block): per-bar H/L/E tokens, the pole/consolidation segment,
// the contiguous prior-cycle (exhaustion) run, gates/score and cycle context. Rendered as a layer
// visually distinct from the trader's own annotations so the two reads compare like-for-like — the
// same overlay the `viz_engine` spike shows. Toggle default ON; degrades to nothing when a chart
// predates the engine block.
let engineOn = true; // whether the engine layer is shown (toggled by the Engine button)
let engineData = null; // current opportunity's `engine` block (or null when absent / no setup)
let engineBands = null; // EngineLayer('bands') — full-height fills behind the candles
let engineMarks = null; // EngineLayer('marks') — caps/labels/tokens/base-peak on top of the candles

// Two Lightweight-Charts v4 series primitives share the module-level engine state: one draws the
// band fills UNDER the candles, the other the tokens/labels/caps OVER them (a primitive has a single
// z-order, so the readable text can't share a layer with the translucent fills).
class EngineLayer {
  constructor(role) {
    this._role = role; // 'bands' | 'marks'
    this._chart = null;
    this._paneView = new EnginePaneView(this);
    this._requestUpdate = null;
  }
  attached(params) {
    this._chart = params.chart;
    this._requestUpdate = params.requestUpdate;
  }
  detached() {
    this._chart = null;
    this._requestUpdate = null;
  }
  updateAllViews() {
    this._paneView.update();
  }
  paneViews() {
    return [this._paneView];
  }
  // Re-run the projection and request a repaint (called when the layer toggles or data changes).
  refresh() {
    this._paneView.update();
    if (this._requestUpdate) this._requestUpdate();
  }
}
class EnginePaneView {
  constructor(source) {
    this._source = source;
    this._items = null;
  }
  update() {
    const chart = this._source._chart;
    if (!chart || !engineOn || !engineData) {
      this._items = null;
      return;
    }
    const ts = chart.timeScale();
    const X = (t) => (t == null ? null : ts.timeToCoordinate(t));
    // No setup formed → seg is null, so only the H/L/E token row draws (bands/base/peak require a
    // segment), mirroring the spike's "no v2 setup" chart which still shows the token walk.
    const seg = engineData.segment || null;
    this._items = {
      pole: seg ? { x1: X(seg.base_t), x2: X(seg.peak_t) } : null,
      cons: seg ? { x1: X(seg.peak_t), x2: X(seg.cons_end_t) } : null,
      priors: (engineData.prior_cycles || []).map((c) => ({ x1: X(c.t0), x2: X(c.t1), n: c.n })),
      tokens: (engineData.tokens || []).map((tk) => ({ x: X(tk.t), tok: tk.tok })),
      base: seg ? X(seg.base_t) : null,
      peak: seg ? X(seg.peak_t) : null,
    };
  }
  renderer() {
    return new EngineRenderer(this._source._role, this._items);
  }
  zOrder() {
    return this._source._role === "bands" ? "bottom" : "top";
  }
}
class EngineRenderer {
  constructor(role, items) {
    this._role = role;
    this._items = items;
  }
  draw(target) {
    const it = this._items;
    if (!it) return;
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const hr = scope.horizontalPixelRatio;
      const vr = scope.verticalPixelRatio;
      const h = scope.bitmapSize.height;
      const px = (x) => x * hr;
      const fill = (x1, x2, color) => {
        if (x1 == null || x2 == null) return;
        ctx.fillStyle = color;
        ctx.fillRect(px(Math.min(x1, x2)), 0, Math.max(1, px(Math.abs(x2 - x1))), h);
      };
      if (this._role === "bands") {
        for (const p of it.priors) fill(p.x1, p.x2, MK.engPrior); // faint, drawn first (underneath)
        if (it.pole) fill(it.pole.x1, it.pole.x2, MK.engPoleFill);
        if (it.cons) fill(it.cons.x1, it.cons.x2, MK.engConsFill);
        return;
      }
      // marks layer: token row, band top-caps + labels, prior-cycle labels, base/peak.
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.font = `${10 * vr}px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif`;
      for (const tk of it.tokens) {
        if (tk.x == null) continue;
        ctx.fillStyle = tk.tok === "H" ? MK.tokH : tk.tok === "L" ? MK.tokL : MK.tokE;
        ctx.fillText(tk.tok, px(tk.x), 12 * vr);
      }
      const cap = (band, color, label) => {
        if (!band || band.x1 == null || band.x2 == null) return;
        const x1 = px(Math.min(band.x1, band.x2));
        const x2 = px(Math.max(band.x1, band.x2));
        ctx.fillStyle = color;
        ctx.fillRect(x1, 22 * vr, Math.max(1, x2 - x1), Math.max(2, 2 * vr));
        ctx.font = `600 ${9 * vr}px -apple-system,sans-serif`;
        ctx.fillText(label, (x1 + x2) / 2, 31 * vr);
      };
      cap(it.pole, MK.engPole, "POLE");
      cap(it.cons, MK.engCons, "CONS");
      ctx.font = `${9 * vr}px -apple-system,sans-serif`;
      ctx.fillStyle = MK.engPriorLbl;
      for (const p of it.priors) {
        if (p.x1 == null || p.x2 == null) continue;
        ctx.fillText(`cyc ${p.n}`, px((p.x1 + p.x2) / 2), 43 * vr);
      }
      if (it.base != null) {
        ctx.fillStyle = MK.engBase;
        ctx.fillText("▲base", px(it.base), 55 * vr);
      }
      if (it.peak != null) {
        ctx.fillStyle = MK.engPeak;
        ctx.fillText("▼peak", px(it.peak), 55 * vr);
      }
      ctx.restore();
    });
  }
}

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

  // Annotation layer (#144): fresh per opportunity — new series means the old handles are gone.
  annEntryLine = null;
  annStopLine = null;
  bandPrimitive = new BandPrimitive();
  candleSeries.attachPrimitive(bandPrimitive);
  chartApi.subscribeClick(onChartClick);

  // Engine-v2 overlay (#216): the detector's read of this opportunity. Two primitives — band fills
  // under the candles, tokens/labels/base-peak over them — both reading the module-level engineData.
  engineData = c.engine || null;
  engineBands = new EngineLayer("bands");
  engineMarks = new EngineLayer("marks");
  candleSeries.attachPrimitive(engineBands);
  candleSeries.attachPrimitive(engineMarks);

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
  // A "no trigger" verdict strips these later via applyVerdict(); we always draw them here so the
  // handles exist to restore when the verdict is toggled back off.
  engineEntryLine = null;
  engineStopLine = null;
  restoreEngineLevels(c);

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

  renderReadout(c);
  renderEngineDetail(c);
  updateEngineToggleUI();
}

// (Re)draw the engine's dashed entry/stop context lines for chart `c`, keeping the handles so a
// no-trigger verdict can remove them and toggling the verdict off can put them back.
function restoreEngineLevels(c) {
  if (!candleSeries || !c) return;
  if (engineEntryLine == null && c.levels.entry != null)
    engineEntryLine = candleSeries.createPriceLine({
      price: c.levels.entry, color: MK.entry, lineStyle: 2, lineWidth: 1,
      axisLabelVisible: true, title: "entry",
    });
  if (engineStopLine == null && c.levels.stop != null)
    engineStopLine = candleSeries.createPriceLine({
      price: c.levels.stop, color: MK.stop, lineStyle: 2, lineWidth: 1,
      axisLabelVisible: true, title: "stop",
    });
}

// Bottom-strip readout: engine entry/stop/Max-R, or a collapsed "no trigger" when the reviewer has
// marked the opportunity as not a tradeable setup (entry/stop then aren't applicable).
// Compact share/volume formatter: 12,300,000 -> "12.3M", 980,000 -> "980k".
function fmtShares(n) {
  if (n == null || !isFinite(n)) return "—";
  const a = Math.abs(n);
  // Promote at the rounding boundary so a value that would render as "1000M"/"1000k" rolls up to
  // the next unit ("1B"/"1M") instead (#163): B at >=999.95M, M at >=999.5k.
  if (a >= 999.95e6) return (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
  if (a >= 999.5e3) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (a >= 1e3) return Math.round(n / 1e3) + "k";
  return String(Math.round(n));
}
const SRC_LABEL = { fmp: "fmp", yfinance: "yf" };
const srcLabel = (s) => SRC_LABEL[s] || s;

// Float chip (#109): default to the highest-priority source (fmp, first in c.floats); when more than
// one source recorded a value, the chip toggles a compact "fmp 12.3M · yf 14.1M" all-sources line.
function floatChip(c) {
  const fs = ((c && c.floats) || []).filter((f) => f.float != null);
  if (!fs.length) return "";
  const all = fs.map((f) => `${srcLabel(f.source)} ${fmtShares(f.float)}`).join(" · ");
  const multi = fs.length > 1;
  return (
    `<span class="mk rv-float${multi ? " rv-float-toggle" : ""}" style="color:${MK.firstHit}"` +
    (multi ? ' title="tap for all sources"' : "") +
    `>float ${fmtShares(fs[0].float)}</span>` +
    (multi ? `<span class="rv-float-all muted hidden">${esc(all)}</span>` : "")
  );
}

// 5-min volume of the bar the scanner triggered on (first_hit; entry as a fallback) — a proxy for the
// scanner volume we don't record. Bars share timestamps with the markers, so match on exact `t`.
function volChip(c) {
  const t = (c && c.markers && (c.markers.first_hit ?? c.markers.entry)) ?? null;
  if (t == null || !(c && c.bars)) return "";
  const bar = c.bars.find((b) => b.t === t);
  if (!bar || bar.v == null) return "";
  return `<span class="mk rv-vol" title="volume of the 5-min bar when the scanner triggered">5m vol ${fmtShares(bar.v)}</span>`;
}

// The engine verdict chip that leads the readout strip: PASS/REJECT · score · cycle (or "no setup"),
// tappable to open the engine detail sheet. Empty when the layer is off or the chart has no engine
// block (a chart published before #216). Kept in sync with the on-chart overlay via the same toggle.
function engineBadgeHtml() {
  if (!engineOn || !engineData) return "";
  if (!engineData.setup)
    return '<span class="mk rv-eng-badge muted" title="engine: no v2 setup formed">v2 no setup</span>';
  const verdict = engineData.passed ? "PASS" : "REJECT";
  const cyc =
    engineData.cycle_num != null
      ? ` · cyc ${engineData.cycle_num}${engineData.exhausted ? "⚠" : ""}`
      : "";
  const score = engineData.score != null ? ` · ${round2(engineData.score)}` : "";
  return (
    `<span class="mk rv-eng-badge rv-eng-${verdict.toLowerCase()}"` +
    ' title="tap for engine gates + score">' +
    `v2 ${verdict}${score}${cyc}</span>`
  );
}

function renderReadout(c) {
  const out = el("rv-readout");
  if (!c) return;
  const context = floatChip(c) + volChip(c); // recorded float + trigger-bar volume (shown in both states)
  if (noTrigger) {
    out.innerHTML =
      engineBadgeHtml() +
      `<span class="mk" style="color:${MK.stop}">no trigger</span>` +
      '<span class="muted">entry / stop N/A</span>' +
      context;
    return;
  }
  out.innerHTML =
    engineBadgeHtml() +
    `<span class="mk" style="color:${MK.entry}">entry ${c.levels.entry ?? "—"}</span>` +
    `<span class="mk" style="color:${MK.stop}">stop ${c.levels.stop ?? "—"}</span>` +
    `<span class="mk" style="color:${MK.maxR}">Max R ${c.max_r != null ? c.max_r + "R" : "—"}</span>` +
    (c.triggered ? (c.stopped_out ? '<span class="muted">stopped out</span>' : "") : '<span class="muted">no trigger</span>') +
    context;
}

function clearChart(message) {
  if (chartApi) {
    chartApi.remove();
    chartApi = null;
    candleSeries = null;
    volumeSeries = null;
  }
  engineData = engineBands = engineMarks = null; // primitives died with the chart
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
  // Reset the annotation surface for the new opportunity before (re)building the chart. A freshly
  // loaded/reset opportunity starts clean; loadReview marks it clean again once its save resolves.
  ann = emptyAnn();
  noTrigger = false;
  bandPending = null;
  drag = null;
  setArmed(null);
  markClean();
  if (!c) {
    currentOpp = null;
    clearChart("No opportunities for this date.");
    loadReview(null);
    updateAnnReadout();
    updateNewsButton(null);
    return;
  }
  buildChart(c);
  currentOpp = c;
  updateNewsButton(c);
  applyVerdict(); // reset the toolbar/verdict surface (a prior opp may have left it disabled)
  loadReview(c); // pull this opportunity's saved note + annotations + verdict (if any)
}

// Load a trading date's chart file, repopulate the symbol dropdown, and draw the first opportunity.
async function loadDate(date) {
  currentDate = date;
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
  if (!confirmDiscard()) return; // keep the current opportunity if the user cancels
  sel.selectedIndex = (sel.selectedIndex + delta + n) % n;
  drawSelected();
}

// --- Notes write-back (#143) ---------------------------------------------------------------
// Save/load a per-opportunity review by committing JSON to the `review-data` branch via the
// GitHub REST API, using a fine-grained PAT kept only in this phone's localStorage. No backend.

const getPat = () => (localStorage.getItem(PAT_KEY) || "").trim();

// `:` and `#` are illegal-ish in paths and ids; map both to `_` (e.g. 2026-07-01:AHMA#2 -> ..._AHMA_2).
const sanitizeOid = (oid) => String(oid).replace(/[:#]/g, "_");
const reviewPath = (oid) => `reviews/${sanitizeOid(oid)}.json`;

// UTF-8-safe base64 for the file body (btoa alone mangles non-ASCII notes).
const b64 = (s) => btoa(unescape(encodeURIComponent(s)));

const ghHeaders = () => ({
  Authorization: `Bearer ${getPat()}`,
  Accept: "application/vnd.github+json",
  "X-GitHub-Api-Version": "2022-11-28",
});

function setStatus(msg, kind) {
  const s = el("rv-save-status");
  s.textContent = msg;
  s.className = "rv-save-status" + (kind ? " " + kind : " muted");
}

// Rebuild the annotation state from a review's persisted `annotations` block (tolerant of the
// Phase-1 empty `{}` and of partially-drawn setups).
function annFromJson(a) {
  const out = emptyAnn();
  if (!a) return out;
  if (a.pole && a.pole.t0 != null && a.pole.t1 != null)
    out.pole = { t0: a.pole.t0, t1: a.pole.t1, low: a.pole.low, high: a.pole.high };
  if (a.consolidation && a.consolidation.t0 != null && a.consolidation.t1 != null)
    out.consolidation = {
      t0: a.consolidation.t0, t1: a.consolidation.t1,
      high: a.consolidation.high, low: a.consolidation.low,
    };
  if (a.entry != null) out.entry = a.entry;
  if (a.stop != null) out.stop = a.stop;
  if (a.entry_t != null) out.entry_t = a.entry_t;
  return out;
}

// Apply a loaded/cached review to the sheet + chart, but only if the user is still on this
// opportunity (loads are async and they may have navigated away).
function applyLoadedReview(c, review) {
  if (!currentOpp || currentOpp.opportunity_id !== c.opportunity_id) return;
  el("rv-note").value = (review && review.note) || "";
  noTrigger = !!(review && review.no_trigger);
  // A no-trigger opportunity carries no annotations (they were cleared when the verdict was set).
  ann = noTrigger ? emptyAnn() : annFromJson(review && review.annotations);
  applyAnnotations();
  applyVerdict();
  markClean(); // just loaded persisted state — nothing unsaved
}

// Load an opportunity's saved review (note + annotations). Public branch -> raw fetch, no auth
// needed; 404 (or missing branch) simply means "no review yet" -> empty. In-session cache first.
async function loadReview(c) {
  if (!c) {
    el("rv-note").value = "";
    el("rv-sheet-title").textContent = "Notes";
    setStatus("", null);
    return;
  }
  el("rv-sheet-title").textContent = optionLabel(c);
  setStatus("", null);
  if (noteCache.has(c.opportunity_id)) {
    applyLoadedReview(c, noteCache.get(c.opportunity_id));
    return;
  }
  el("rv-note").value = "";
  const url =
    `https://raw.githubusercontent.com/${REPO}/${REVIEW_BRANCH}/` +
    `${reviewPath(c.opportunity_id)}?t=${Date.now()}`;
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (res.status === 404) {
      noteCache.set(c.opportunity_id, { note: "" }); // known-empty; don't refetch
      return;
    }
    if (!res.ok) throw new Error(`load failed (${res.status})`);
    const review = await res.json();
    noteCache.set(c.opportunity_id, review);
    applyLoadedReview(c, review);
  } catch (err) {
    setStatus(`Couldn't load saved note: ${err.message}`, "bad");
  }
}

// --- Tap-to-place annotations (#144) -------------------------------------------------------
// A mode toolbar arms which element the next chart tap sets; entry/stop are horizontal price
// lines (via coordinateToPrice), pole/consolidation are two-tap time ranges drawn as bands.

function setArmed(mode) {
  armed = mode;
  // Disarming (or switching to a different tool) abandons a half-drawn band — drop its pending edge.
  if (bandPending && bandPending.mode !== mode) bandPending = null;
  for (const btn of document.querySelectorAll(".rv-tool")) {
    btn.classList.toggle("armed", btn.dataset.mode === mode);
  }
  renderPending();
  updateAnnReadout();
}

// Show/hide the dashed line marking the first tap of an in-progress two-tap band.
function renderPending() {
  if (!bandPrimitive) return;
  if (bandPending) {
    bandPrimitive.setPending(bandPending.t0, bandPending.mode === "pole" ? MK.poleEdge : MK.consEdge);
  } else {
    bandPrimitive.setPending(null, null);
  }
}

// Highest/lowest traded price across the bars inside a [t0, t1] band — the derived price extent we
// persist alongside the time range (store-raw / compute-on-read), used later by the compare loop.
function bandExtremes(t0, t1) {
  const lo = Math.min(t0, t1);
  const hi = Math.max(t0, t1);
  let high = -Infinity;
  let low = Infinity;
  for (const b of currentOpp.bars) {
    if (b.t < lo || b.t > hi) continue;
    if (b.h > high) high = b.h;
    if (b.l < low) low = b.l;
  }
  if (high === -Infinity) return null;
  return { high, low, t0: lo, t1: hi };
}

// Live Max R from the drawn levels: (peak high after the fill − entry) / (entry − stop).
// Needs entry above stop (a long); otherwise undefined. Entry is a horizontal price LEVEL, so Max R
// must not depend on where along the x-axis the entry was tapped: the fill is the first bar *strictly
// after the drawn consolidation* (its `t1`) whose high reaches the entry — i.e. the breakout bar, per
// the strategy (entry = tick above the last consolidation candle's high, filled on the next break).
// `entry_t` (the entry tap's x, not separately controllable) is only a fallback when no consolidation
// is drawn. From the fill bar we use the engine's stop-first convention (rmetrics): once a bar's low
// breaches the stop the trade is closed on that bar, so no later high is credited.
function computeMaxR() {
  const { entry, stop, entry_t } = ann;
  if (entry == null || stop == null) return null;
  const risk = entry - stop;
  if (risk <= 0) return null;
  const anchor = ann.consolidation?.t1 ?? entry_t;
  if (anchor == null) return null;
  const bars = currentOpp.bars;
  // Fill = first bar after the consolidation whose high reaches the entry trigger. A break that
  // never comes back to fill (or one only before the anchor) leaves Max R undefined.
  const fill = bars.findIndex((b) => b.t > anchor && b.h >= entry);
  if (fill === -1) return null;
  // Fill bar: a same-bar stop (low already through the stop) credits no favourable excursion.
  let maxHigh = bars[fill].l <= stop ? entry : bars[fill].h;
  if (bars[fill].l > stop) {
    for (const b of bars.slice(fill + 1)) {
      if (b.l <= stop) break; // stop hit on a later bar — close before crediting this bar's high
      if (b.h > maxHigh) maxHigh = b.h;
    }
  }
  return (maxHigh - entry) / risk;
}

function onChartClick(param) {
  if (!armed || !currentOpp || !candleSeries || !param.point) return;
  const price = candleSeries.coordinateToPrice(param.point.y);
  const time = chartApi.timeScale().coordinateToTime(param.point.x);
  if (price == null || time == null) return;

  if (armed === "entry") {
    ann.entry = round2(price);
    ann.entry_t = time;
    setArmed(null);
  } else if (armed === "stop") {
    ann.stop = round2(price);
    setArmed(null);
  } else if (armed === "pole" || armed === "cons") {
    if (!bandPending || bandPending.mode !== armed) {
      bandPending = { mode: armed, t0: time }; // first tap: remember the start
      renderPending(); // show a dashed edge line so the first tap is visible immediately
      updateAnnReadout();
      return;
    }
    const ext = bandExtremes(bandPending.t0, time); // second tap: close the range
    bandPending = null;
    renderPending();
    if (ext) {
      if (armed === "pole") ann.pole = { t0: ext.t0, t1: ext.t1, low: ext.low, high: ext.high };
      else ann.consolidation = { t0: ext.t0, t1: ext.t1, high: ext.high, low: ext.low };
    }
    setArmed(null);
  }
  markDirty();
  applyAnnotations();
}

// Render the current annotations onto the chart: entry/stop price lines + pole/cons bands.
function applyAnnotations() {
  if (candleSeries) {
    if (annEntryLine) {
      candleSeries.removePriceLine(annEntryLine);
      annEntryLine = null;
    }
    if (annStopLine) {
      candleSeries.removePriceLine(annStopLine);
      annStopLine = null;
    }
    if (ann.entry != null)
      annEntryLine = candleSeries.createPriceLine({
        price: ann.entry, color: MK.annEntry, lineStyle: 0, lineWidth: 2,
        axisLabelVisible: true, title: "my entry",
      });
    if (ann.stop != null)
      annStopLine = candleSeries.createPriceLine({
        price: ann.stop, color: MK.annStop, lineStyle: 0, lineWidth: 2,
        axisLabelVisible: true, title: "my stop",
      });
  }
  if (bandPrimitive) {
    const bands = [];
    if (ann.pole)
      bands.push({ t0: ann.pole.t0, t1: ann.pole.t1, color: MK.poleBand, edge: MK.poleEdge });
    if (ann.consolidation)
      bands.push({
        t0: ann.consolidation.t0, t1: ann.consolidation.t1,
        color: MK.consBand, edge: MK.consEdge,
      });
    bandPrimitive.setBands(bands);
  }
  updateAnnReadout();
}

// Compact live status for the tools row: the pending-band hint, else my entry/stop/Max R.
function updateAnnReadout() {
  const out = el("rv-ann");
  if (!out) return;
  if (noTrigger) {
    out.innerHTML = '<span class="muted">no trigger — entry / stop not applicable</span>';
    return;
  }
  if (bandPending) {
    out.innerHTML = `<span class="muted">tap ${bandPending.mode === "pole" ? "pole" : "cons"} end</span>`;
    return;
  }
  if (armed) {
    out.innerHTML = `<span class="muted">tap to set ${armed === "cons" ? "consolidation" : armed}</span>`;
    return;
  }
  const r = computeMaxR();
  const parts = [];
  if (ann.entry != null) parts.push(`<span class="mk" style="color:${MK.annEntry}">e ${ann.entry}</span>`);
  if (ann.stop != null) parts.push(`<span class="mk" style="color:${MK.annStop}">s ${ann.stop}</span>`);
  if (r != null) parts.push(`<span class="mk" style="color:${MK.maxR}">${round2(r)}R</span>`);
  // Once something is placed, remind that lines/edges can be dragged to refine (UX #152).
  const draggable = ann.entry != null || ann.stop != null || ann.pole || ann.consolidation;
  if (draggable) parts.push('<span class="muted rv-hint">drag to adjust</span>');
  out.innerHTML = parts.length ? parts.join("") : '<span class="muted">tap a tool to draw</span>';
}

// Wipe the current opportunity's annotations (leaves the note untouched).
function clearAnnotations() {
  ann = emptyAnn();
  bandPending = null;
  drag = null;
  setArmed(null);
  markDirty();
  applyAnnotations();
}

// --- No-trigger verdict (#155) -------------------------------------------------------------
// Reflect the current verdict on the chart + toolbar: strip the engine's entry/stop context lines
// and disable the drawing tools while "no trigger" is set, restore them when it's cleared.
function applyVerdict() {
  const btn = el("rv-notrigger");
  if (btn) {
    btn.classList.toggle("armed", noTrigger);
    btn.setAttribute("aria-pressed", noTrigger ? "true" : "false");
  }
  // Drawing tools (pole/cons/entry/stop/clear) are meaningless for a non-setup — grey them out.
  for (const t of document.querySelectorAll(".rv-tool")) t.disabled = noTrigger;
  if (candleSeries) {
    if (noTrigger) {
      if (engineEntryLine) {
        candleSeries.removePriceLine(engineEntryLine);
        engineEntryLine = null;
      }
      if (engineStopLine) {
        candleSeries.removePriceLine(engineStopLine);
        engineStopLine = null;
      }
    } else {
      restoreEngineLevels(currentOpp);
    }
  }
  renderReadout(currentOpp);
  updateAnnReadout();
}

// Toggle the verdict. Turning it on clears every annotation — a "no trigger" opportunity has no
// pole/consolidation/entry/stop to keep — and disarms any in-progress drawing.
function toggleNoTrigger() {
  noTrigger = !noTrigger;
  if (noTrigger) {
    ann = emptyAnn();
    bandPending = null;
    drag = null;
    setArmed(null);
    applyAnnotations();
  }
  markDirty();
  applyVerdict();
}

// --- Drag-to-refine placed levels (UX #152) ------------------------------------------------
// Lightweight-Charts price lines / primitives aren't natively interactive, so we run our own
// pointer loop over the chart container: grab the nearest entry/stop line (vertical drag) or
// pole/consolidation band edge (horizontal drag) within DRAG_HIT_PX and move it live. Chart
// pan/zoom is suspended for the duration so the drag doesn't scroll the view underneath.

// Pointer position in chart-container CSS px.
function chartXY(e) {
  const rect = el("rv-chart").getBoundingClientRect();
  return { x: e.clientX - rect.left, y: e.clientY - rect.top };
}

// Nearest draggable level under (x, y), within the grab radius — or null. Entry/stop are matched
// by vertical distance to their price line; band edges by horizontal distance to t0/t1.
function pickDragTarget(x, y) {
  if (!candleSeries || !chartApi) return null;
  const ts = chartApi.timeScale();
  const cands = [];
  for (const field of ["entry", "stop"]) {
    if (ann[field] == null) continue;
    const yc = candleSeries.priceToCoordinate(ann[field]);
    if (yc != null) cands.push({ kind: "price", field, dist: Math.abs(yc - y) });
  }
  for (const [field, band] of [["pole", ann.pole], ["cons", ann.consolidation]]) {
    if (!band) continue;
    for (const edge of ["t0", "t1"]) {
      const xc = ts.timeToCoordinate(band[edge]);
      if (xc != null) cands.push({ kind: "edge", field, edge, dist: Math.abs(xc - x) });
    }
  }
  let best = null;
  for (const c of cands) {
    if (c.dist > DRAG_HIT_PX) continue;
    if (!best || c.dist < best.dist) best = c;
  }
  return best;
}

const bandOf = (field) => (field === "pole" ? ann.pole : ann.consolidation);

function onPointerDown(e) {
  // Arming mode owns taps (tap-to-place); only refine by drag when nothing is armed.
  if (armed || (e.button != null && e.button !== 0)) return;
  const { x, y } = chartXY(e);
  const target = pickDragTarget(x, y);
  if (!target) return;
  drag = target;
  chartApi.applyOptions({ handleScroll: false, handleScale: false }); // freeze pan/zoom while dragging
  const chartEl = el("rv-chart");
  if (chartEl.setPointerCapture) chartEl.setPointerCapture(e.pointerId);
  chartEl.classList.add("rv-dragging");
  e.preventDefault();
}

function onPointerMove(e) {
  if (!drag) return;
  const { x, y } = chartXY(e);
  if (drag.kind === "price") {
    const p = candleSeries.coordinateToPrice(y);
    if (p != null) ann[drag.field] = round2(p);
  } else {
    const t = chartApi.timeScale().coordinateToTime(x);
    const band = bandOf(drag.field);
    if (t != null && band) {
      band[drag.edge] = t; // move just this edge; keep raw order, normalise on release
      const ext = bandExtremes(band.t0, band.t1); // refresh the derived high/low for the new span
      if (ext) {
        band.high = ext.high;
        band.low = ext.low;
      }
    }
  }
  markDirty();
  applyAnnotations();
  e.preventDefault();
}

function endDrag(e) {
  if (!drag) return;
  if (drag.kind === "edge") {
    const band = bandOf(drag.field);
    const ext = bandExtremes(band.t0, band.t1); // normalise t0<=t1 and finalise extremes
    if (ext) Object.assign(band, ext);
  }
  drag = null;
  chartApi.applyOptions({ handleScroll: true, handleScale: true });
  const chartEl = el("rv-chart");
  if (e && e.pointerId != null && chartEl.releasePointerCapture) {
    try {
      chartEl.releasePointerCapture(e.pointerId);
    } catch (_) {
      /* pointer already released */
    }
  }
  chartEl.classList.remove("rv-dragging");
  applyAnnotations();
}

// Build the review JSON's `annotations` block from the drawn levels, stamping the live Max R.
// Only-set fields are emitted, so a partially-drawn setup round-trips faithfully.
function serializeAnnotations() {
  const a = {};
  if (ann.pole) a.pole = { ...ann.pole };
  if (ann.consolidation) a.consolidation = { ...ann.consolidation };
  if (ann.entry != null) a.entry = ann.entry;
  if (ann.stop != null) a.stop = ann.stop;
  if (ann.entry_t != null) a.entry_t = ann.entry_t;
  const r = computeMaxR();
  if (r != null) a.max_r = round2(r);
  return a;
}

// Ensure the review-data branch exists, creating it off DEFAULT_BRANCH's HEAD on first ever save.
async function ensureReviewBranch() {
  const ref = await fetch(`${API}/repos/${REPO}/git/ref/heads/${REVIEW_BRANCH}`, {
    headers: ghHeaders(),
  });
  if (ref.ok) return;
  if (ref.status !== 404) throw new Error(`branch check failed (${ref.status})`);
  const base = await fetch(`${API}/repos/${REPO}/git/ref/heads/${DEFAULT_BRANCH}`, {
    headers: ghHeaders(),
  });
  if (!base.ok) throw new Error(`can't read ${DEFAULT_BRANCH} (${base.status})`);
  const baseSha = (await base.json()).object.sha;
  const created = await fetch(`${API}/repos/${REPO}/git/refs`, {
    method: "POST",
    headers: ghHeaders(),
    body: JSON.stringify({ ref: `refs/heads/${REVIEW_BRANCH}`, sha: baseSha }),
  });
  // 422 = ref already exists (someone raced us) — fine.
  if (!created.ok && created.status !== 422)
    throw new Error(`can't create ${REVIEW_BRANCH} (${created.status})`);
}

// Save the current opportunity's note: GET current SHA on review-data -> PUT the file back.
async function saveNote() {
  const c = currentOpp;
  if (!c) {
    setStatus("No opportunity selected.", "bad");
    return;
  }
  if (!getPat()) {
    setStatus("Enter a GitHub token first.", "bad");
    el("rv-pat-details").open = true;
    el("rv-pat").focus();
    return;
  }
  const btns = [el("rv-save"), el("rv-save-top")].filter(Boolean);
  for (const b of btns) {
    b.setAttribute("aria-busy", "true");
    b.disabled = true;
  }
  setStatus("Saving…", null);
  try {
    await ensureReviewBranch();
    const path = reviewPath(c.opportunity_id);

    // Current SHA (required to overwrite an existing file); 404 -> first write, no sha.
    let sha;
    const cur = await fetch(`${API}/repos/${REPO}/contents/${path}?ref=${REVIEW_BRANCH}`, {
      headers: ghHeaders(),
    });
    if (cur.ok) sha = (await cur.json()).sha;
    else if (cur.status !== 404) throw new Error(`SHA check failed (${cur.status})`);

    const review = {
      schema_version: 1,
      opportunity_id: c.opportunity_id,
      symbol: c.symbol,
      trading_date: el("rv-date").value || String(c.opportunity_id).split(":")[0],
      note: el("rv-note").value,
      no_trigger: noTrigger,
      annotations: noTrigger ? {} : serializeAnnotations(),
      updated_utc: new Date().toISOString(),
    };
    const body = {
      message: `review: ${c.opportunity_id}`,
      content: b64(JSON.stringify(review, null, 2)),
      branch: REVIEW_BRANCH,
    };
    if (sha) body.sha = sha;

    const put = await fetch(`${API}/repos/${REPO}/contents/${path}`, {
      method: "PUT",
      headers: ghHeaders(),
      body: JSON.stringify(body),
    });
    if (!put.ok) {
      let detail = `${put.status}`;
      try {
        detail = (await put.json()).message || detail;
      } catch (_) {
        /* non-JSON error body */
      }
      throw new Error(detail);
    }
    noteCache.set(c.opportunity_id, review);
    markClean(); // persisted — clear the unsaved-changes signal
    setStatus("Saved ✓", "ok");
  } catch (err) {
    setStatus(`Save failed: ${err.message}`, "bad");
  } finally {
    for (const b of btns) {
      b.removeAttribute("aria-busy");
      b.disabled = false;
    }
  }
}

// --- Engine overlay controls (#216) --------------------------------------------------------
// Toggle the whole engine layer (on-chart bands/tokens/base-peak + the readout badge). Default ON;
// the setting persists across opportunities so the trader can park it off and draw unbiased.
function toggleEngine() {
  engineOn = !engineOn;
  if (engineBands) engineBands.refresh();
  if (engineMarks) engineMarks.refresh();
  updateEngineToggleUI();
  renderReadout(currentOpp); // show/hide the badge
}
function updateEngineToggleUI() {
  const btn = el("rv-engine-toggle");
  if (!btn) return;
  btn.classList.toggle("armed", engineOn);
  btn.setAttribute("aria-pressed", engineOn ? "true" : "false");
}

// Build the engine detail sheet: verdict, score, cycle/exhaustion, the segment, entry/stop levels,
// the per-gate pass/fail table and the score contributions — the explainable ranking (#182, folded
// into #216) behind the on-chart overlay.
function renderEngineDetail(c) {
  const box = el("rv-engine-detail");
  const title = el("rv-engine-title");
  if (!box) return;
  if (title) title.textContent = c ? `Engine · ${optionLabel(c)}` : "Engine";
  const e = c && c.engine;
  if (!e) {
    box.innerHTML = '<p class="muted">No engine data for this opportunity (chart predates the overlay).</p>';
    return;
  }
  if (!e.setup) {
    box.innerHTML = '<p class="muted">No v2 setup formed — the tokeniser found no pole into a consolidation.</p>';
    return;
  }
  const verdict = e.passed
    ? '<span class="rv-eng-badge rv-eng-pass">PASS</span>'
    : '<span class="rv-eng-badge rv-eng-reject">REJECT</span>';
  const cyc =
    e.cycle_num != null
      ? `cycle ${e.cycle_num}${e.total_significant_cycles != null ? ` / ${e.total_significant_cycles}` : ""}` +
        (e.exhausted ? ' <span class="rv-eng-exh">exhausted</span>' : "")
      : "—";
  const seg = e.segment || {};
  const lv = e.levels || {};
  const gatesRows = (e.gates || [])
    .map(
      (g) =>
        `<tr class="${g.passed ? "ok" : "no"}"><td>${esc(g.name)}</td>` +
        `<td>${g.passed ? "✓" : "✗"}</td></tr>`,
    )
    .join("");
  const contribRows = Object.entries(e.contributions || {})
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${round2(v)}</td></tr>`)
    .join("");
  box.innerHTML =
    `<div class="rv-eng-head">${verdict}` +
    `<span class="rv-eng-score">score ${e.score != null ? round2(e.score) : "—"}</span>` +
    `<span class="muted">${cyc}</span></div>` +
    '<dl class="rv-eng-kv">' +
    `<dt>segment</dt><dd>pole ${seg.pole_len ?? "—"} · cons ${seg.cons_len ?? "—"} · <code>${esc(seg.token_string ?? "")}</code></dd>` +
    `<dt>entry</dt><dd>trigger ${lv.entry_trigger ?? "—"} · fill ${lv.entry_fill ?? "—"}</dd>` +
    `<dt>stop</dt><dd>${lv.stop ?? "—"}</dd>` +
    "</dl>" +
    '<h4 class="rv-eng-h">Gates</h4>' +
    `<table class="rv-eng-gates">${gatesRows}</table>` +
    (contribRows
      ? '<h4 class="rv-eng-h">Score contributions</h4>' +
        `<table class="rv-eng-gates rv-eng-contrib">${contribRows}</table>`
      : "");
}

function openEngineSheet() {
  renderEngineDetail(currentOpp);
  el("rv-scrim").hidden = false;
  el("rv-engine-sheet").classList.add("open");
  el("rv-engine-sheet").setAttribute("aria-hidden", "false");
}
function closeEngineSheet() {
  el("rv-scrim").hidden = true;
  el("rv-engine-sheet").classList.remove("open");
  el("rv-engine-sheet").setAttribute("aria-hidden", "true");
}

function openSheet() {
  el("rv-scrim").hidden = false;
  el("rv-sheet").classList.add("open");
  el("rv-sheet").setAttribute("aria-hidden", "false");
  el("rv-pat-details").open = !getPat(); // nudge the token field only when it's not set yet
}
function closeSheet() {
  el("rv-scrim").hidden = true;
  el("rv-sheet").classList.remove("open");
  el("rv-sheet").setAttribute("aria-hidden", "true");
}

// News drawer (#109): the headlines captured when the scanner triggered, so the catalyst is on hand
// while writing notes. Reuses the notes sheet's slide-up markup/CSS and the shared scrim.
function updateNewsButton(c) {
  const btn = el("rv-news-toggle");
  if (!btn) return;
  const n = (c && c.news && c.news.length) || 0;
  btn.textContent = `News ${n}`;
  btn.disabled = n === 0;
}
function renderNews(c) {
  el("rv-news-title").textContent = c ? `News · ${c.symbol}` : "News";
  const list = el("rv-news-list");
  const items = (c && c.news) || [];
  if (!items.length) {
    list.innerHTML = '<p class="muted rv-news-empty">No news captured for this opportunity.</p>';
    return;
  }
  list.innerHTML = items
    .map((n) => {
      const when = n.ts != null ? `${etFromEpoch(n.ts)} ET` : "undated";
      const meta = [when, n.provider || ""].filter(Boolean).join(" · ");
      return (
        '<div class="rv-news-item">' +
        `<div class="rv-news-meta muted">${esc(meta)}</div>` +
        `<div class="rv-news-head">${esc(n.headline)}</div>` +
        "</div>"
      );
    })
    .join("");
}
function openNewsSheet() {
  renderNews(currentOpp);
  el("rv-scrim").hidden = false;
  el("rv-news-sheet").classList.add("open");
  el("rv-news-sheet").setAttribute("aria-hidden", "false");
}
function closeNewsSheet() {
  el("rv-scrim").hidden = true;
  el("rv-news-sheet").classList.remove("open");
  el("rv-news-sheet").setAttribute("aria-hidden", "true");
}

async function init() {
  const index = await fetchJson("index.json");
  // Hide days that captured no opportunities — the live/per-date refresh can upsert an empty day
  // into the index, and there is nothing to review there.
  const dates = ((index && index.dates) || []).filter(
    (d) => Array.isArray(d.opportunities) && d.opportunities.length > 0,
  );
  const dateSel = el("rv-date");
  if (!dates.length) {
    dateSel.innerHTML = '<option>—</option>';
    clearChart("No review data published yet.");
    return;
  }
  // index.json dates are already sorted newest-first (#141).
  dateSel.innerHTML = dates
    .map((d) => `<option value="${esc(d.date)}">${esc(dateLabel(d.date))}</option>`)
    .join("");
  await loadDate(dateSel.value);
}

// Navigation guards (#156): a date/symbol change discards any unsaved review, so confirm first and
// restore the picker to the current selection if the user cancels (setting .value fires no change).
el("rv-date").addEventListener("change", (e) => {
  if (!confirmDiscard()) {
    e.target.value = currentDate;
    return;
  }
  loadDate(e.target.value);
});
el("rv-symbol").addEventListener("change", (e) => {
  if (!confirmDiscard()) {
    if (currentOpp) e.target.value = currentOpp.opportunity_id;
    return;
  }
  drawSelected();
});
el("rv-prev").addEventListener("click", () => stepSymbol(-1));
el("rv-next").addEventListener("click", () => stepSymbol(1));

// Annotation toolbar (#144): each tool arms its element; tapping an armed tool again disarms.
for (const btn of document.querySelectorAll(".rv-tool")) {
  btn.addEventListener("click", () => setArmed(armed === btn.dataset.mode ? null : btn.dataset.mode));
}
el("rv-clear").addEventListener("click", clearAnnotations);
el("rv-notrigger").addEventListener("click", toggleNoTrigger);
// Engine overlay (#216): toggle the layer; open the detail sheet from the readout badge / its close.
el("rv-engine-toggle").addEventListener("click", toggleEngine);
el("rv-engine-close").addEventListener("click", closeEngineSheet);

// Drag-to-refine (UX #152): our own pointer loop on the chart container. Listeners are attached
// once here (the container is stable across opportunities); handlers read the live chart globals.
const rvChart = el("rv-chart");
rvChart.addEventListener("pointerdown", onPointerDown);
rvChart.addEventListener("pointermove", onPointerMove);
rvChart.addEventListener("pointerup", endDrag);
rvChart.addEventListener("pointercancel", endDrag);
rvChart.addEventListener("pointerleave", endDrag);

// Notes sheet + write-back (#143).
el("rv-pat").value = getPat(); // restore the phone-local token across reloads
el("rv-pat").addEventListener("input", (e) => localStorage.setItem(PAT_KEY, e.target.value.trim()));
el("rv-notes-toggle").addEventListener("click", openSheet);
el("rv-sheet-close").addEventListener("click", closeSheet);
// Shared scrim closes whichever sheet is open (notes / news / engine); all are idempotent.
el("rv-scrim").addEventListener("click", () => {
  closeSheet();
  closeNewsSheet();
  closeEngineSheet();
});
// News drawer (#109).
el("rv-news-toggle").addEventListener("click", openNewsSheet);
el("rv-news-close").addEventListener("click", closeNewsSheet);
// Readout strip taps: the engine badge opens the detail sheet (#216); the float chip toggles its
// all-sources breakdown (#109).
el("rv-readout").addEventListener("click", (e) => {
  if (e.target.closest(".rv-eng-badge")) {
    openEngineSheet();
    return;
  }
  const chip = e.target.closest(".rv-float-toggle");
  if (!chip) return;
  const all = chip.parentElement.querySelector(".rv-float-all");
  if (all) all.classList.toggle("hidden");
});
el("rv-save").addEventListener("click", saveNote);
// Save is also in the always-visible strip (#156) so a verdict/levels persist without opening Notes.
el("rv-save-top").addEventListener("click", saveNote);
// Typing a note is an unsaved edit too (programmatic value sets on load don't fire 'input').
el("rv-note").addEventListener("input", markDirty);
// Last-ditch guard: warn before a reload/close/back that would drop unsaved review edits.
window.addEventListener("beforeunload", (e) => {
  if (!dirty) return;
  e.preventDefault();
  e.returnValue = "";
});
updateSaveState(); // paint the Save controls' initial (clean) label

init();
