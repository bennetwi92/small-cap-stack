// Results (#222/#223/#224, cockpit #288): every opportunity across every
// collected day, as a full-bleed Tabulator grid — virtual scrolling, header
// sorting, frozen symbol column. Same published `dashboard-data` JSON and the
// same row/filter semantics as before the redesign; only the rendering changed.
//
// The grid is the engine's scatter-plot in table form: the COLUMNS toggle folds
// in every engine-v2 feature the detector gated and scored on (charts.py
// `engine.features`), grouped by feature area, so sorting one column against Max
// R / Max % is enough to see whether that feature separates runners from duds.
//
// Since #479 the chart comes to the grid rather than the other way round: a row
// click (or ↑/↓) draws that opportunity in a dock along the bottom, using the
// shared inspector (js/inspector.js). Clicking used to navigate to the review
// workbench, which threw away the sort, the filter and your place in the list.
//
// DATA `+ History` (#488) folds in the reconstructed sessions the overnight
// harvest rebuilt from vendor minute bars, mirroring the Portfolio page's scope
// control. Those rows come from a separate index and a separate chart namespace,
// carry a `recon` tag on the date, and are fetched only when the scope is
// switched on — 30 extra days of full-day bars is not a cost to pay by default.

import "./js/nav.js";
import { createOptionsBar } from "./js/options-bar.js";
import { setStatusPage } from "./js/status-bar.js";
import { fetchJson } from "./js/data.js";
import {
  esc,
  fmtShares,
  fmtPrice,
  fmtR,
  fmtPct,
  fmtPctPlain,
  fmtNum,
  paintR,
  etClockSec,
  etMinutesSec,
} from "./js/fmt.js";
import {
  chartsFor,
  chartsUrl,
  clearChartCache,
  createChartView,
  engineDetailHtml,
  findChart,
  hasReview,
  newsCount,
  newsHtml,
  optionLabel,
  readoutHtml,
  reviewFor,
  reviewHtml,
} from "./js/inspector.js";
import { MARKET_OPEN_MIN } from "./js/session.js";
import { TabulatorFull as Tabulator } from "https://cdn.jsdelivr.net/npm/tabulator-tables@6.5.2/dist/js/tabulator_esm.min.js";

const el = (id) => document.getElementById(id);

/* ---------- dock state ----------
   Declared up here because the options bar (built below, at module evaluation)
   reads `dockOn()` for the CHART segment's initial value. */
const DOCK_ON_KEY = "rs_dock_on";
const DOCK_H_KEY = "rs_dock_h";
const DOCK_H_DEFAULT = 320;
const DOCK_H_MIN = 140;
const DRAW_DEBOUNCE_MS = 80; // holding ↓ must not queue one full redraw per row

let selectedOid = null;
let view; // undefined = not built, null = charting library missing
let sideMode = null; // null | "gates" | "news" | "note"
let sideReview = null; // the saved review for the drawn opportunity, once it has loaded
let engineOn = true;
let drawTimer = null;
let drawToken = 0; // guards an async payload fetch that lands after another selection

function dockOn() {
  return localStorage.getItem(DOCK_ON_KEY) !== "off";
}

/* ---------- provenance scope (#488) ----------
   `recon_index.json` is the reconstructed history's own index — written by
   dashboard_recon.py, never merged into `index.json`, so a page that doesn't ask
   for it can't get vendor-derived days by accident. It's small (nav rows only),
   so it's fetched every load to decide whether the DATA control exists at all;
   the multi-megabyte chart payloads behind it are fetched only on first switch. */

let SCOPE = "live"; // "live" (captured only) | "all" (+ reconstructed history)
let reconIndex = null; // the parsed recon_index.json, or null when nothing is harvested
let reconRows = null; // lazily-loaded rows for the reconstructed dates
let reconLoading = null; // in-flight load, so a double-click doesn't fetch twice

const hasRecon = () => !!(reconIndex && (reconIndex.dates || []).length);

/* ---------- row model (unchanged from the pre-cockpit page) ---------- */

// The Max R price = the peak favourable price the notional trade reached.
// `markers.max_r` is the epoch of the bar that set peak excursion; its high IS
// that peak (charts.py sets the marker to the max-favourable bar).
function maxRPrice(c) {
  const t = c && c.markers && c.markers.max_r;
  if (t == null || !Array.isArray(c.bars)) return null;
  const bar = c.bars.find((b) => b.t === t);
  return bar ? bar.h : null;
}

// "pass" (setup formed AND passed every gate), "reject" (a gate rejected it),
// or "nosetup" (no v2 pole formed; pre-#216 charts have no `engine` block).
function engineVerdict(c) {
  const e = c && c.engine;
  if (!e || !e.setup) return "nosetup";
  return e.passed ? "pass" : "reject";
}

// One row = one opportunity/run. Everything the engine measured is flattened onto
// it (rather than reached through `engine.features` per cell) so Tabulator can
// sort/filter each feature natively. A chart that predates a field — or one where
// no setup formed — simply carries `undefined`, which every formatter renders "—".
function toRow(date, c, source = "live") {
  const first = c.markers ? c.markers.first_hit : null;
  const mins = etMinutesSec(first);
  const floats = (c.floats || []).filter((f) => f.float != null);
  const e = c.engine || {};
  const f = e.features || {};
  const seg = e.segment || {};
  const lv = e.levels || {};
  const failed = (e.gates || []).filter((g) => !g.passed).map((g) => g.name);
  const fill = lv.entry_fill ?? null;
  return {
    date,
    // Which store the day came from (#488). Decides the chart namespace the dock
    // reads, the `recon` tag on the date, and whether the workbench link applies.
    source,
    oid: c.opportunity_id,
    label: c.run_count > 1 ? `${c.symbol}#${c.run}` : c.symbol,
    symbol: c.symbol,
    firstHit: first,
    firstMin: mins, // minutes past ET midnight — sorts the TIME column by time of DAY, not by date
    session: mins == null ? "unknown" : mins < MARKET_OPEN_MIN ? "premarket" : "market",
    verdict: engineVerdict(c),
    maxR: c.max_r == null ? null : c.max_r,
    maxPct: c.max_gain_pct ?? null,
    maeR: c.mae_r ?? null,
    triggered: c.triggered ?? null,
    stoppedOut: c.stopped_out ?? null,
    float: floats.length ? floats[0].float : null,
    entry: c.levels ? c.levels.entry : null,
    maxRPrice: maxRPrice(c),

    // engine verdict context
    score: e.score ?? null,
    cycleNum: e.cycle_num ?? null,
    totalCycles: e.total_significant_cycles ?? null,
    exhausted: e.exhausted ?? null,
    failedGates: failed.length ? failed.join(" ") : e.setup ? "" : null,

    // SHAPE
    poleLen: f.pole_len ?? seg.pole_len ?? null,
    consLen: f.cons_len ?? seg.cons_len ?? null,
    consStrictness: f.cons_strictness ?? null,
    tokens: seg.token_string ?? f.token_string ?? null,
    // VOL
    volRatio: f.vol_ratio ?? null,
    peakGtCons: f.peak_gt_cons ?? null,
    consVolReducing: f.cons_vol_reducing ?? null,
    poleVolConc: f.pole_vol_concentration ?? null,
    // WICK
    peakUpperWick: f.peak_upper_wick ?? null,
    peakIsGreen: f.peak_is_green ?? null,
    poleHasBigGreen: f.pole_has_big_green ?? null,
    poleAvgBody: f.pole_avg_body ?? null,
    consIndecision: f.cons_indecision ?? null,
    // POLE
    poleHeightPct: f.pole_height_pct ?? null,
    poleHeightAbs: f.pole_height_abs ?? null,
    poleVelocity: f.pole_velocity ?? null,
    poleExtAtr: f.pole_extension_atr ?? null,
    // CONS (the flag)
    retracement: f.retracement ?? null,
    holdsBase: f.holds_base ?? null,
    consTightness: f.cons_tightness ?? null,
    consDrift: f.cons_drift_slope ?? null,
    // LEVELS / LOC
    breakout: lv.breakout ?? null,
    fill,
    stop: lv.stop ?? null,
    risk: fill != null && lv.stop != null ? Math.round((fill - lv.stop) * 1e4) / 1e4 : null,
    inWindow: f.trigger_in_window ?? null,
  };
}

/* ---------- options bar: the filters that used to float over the table ---------- */

let want = { session: "all", engine: "all" };
let cols = "core"; // survives an options-bar rebuild, which reconstructs every control

const SCOPE_NOTE =
  "DATA “+ History” folds in sessions the overnight harvest rebuilt from purchased vendor minute " +
  "bars, tagged “recon” on the date. Those days were never watched live, so three columns are " +
  "ABSENT rather than zero: FLOAT (the vendor sells no share count), News (no headlines were " +
  "captured), and the saved review (the workbench annotates live days only). TIME is a " +
  "reconstructed appearance — the same gates replayed over the minute tape, not an observed " +
  "scanner hit. The most recent reconstructed sessions are published; older ones are not.";

// Rebuilt rather than built once: the DATA control only exists when the harvest
// has landed reconstructed days, which isn't known until `recon_index.json` has
// been fetched. createOptionsBar clears its mount, so re-calling it with the
// current values in hand is the whole mechanism.
function buildOptbar() {
  createOptionsBar("optbar", {
    primary: [
      {
        type: "seg", id: "rs-session", label: "SESSION", value: want.session,
        options: [
          { value: "all", label: "All" },
          { value: "premarket", label: "Pre" },
          { value: "market", label: "Open" },
        ],
      },
      {
        type: "seg", id: "rs-engine", label: "ENGINE", value: want.engine,
        options: [
          { value: "all", label: "All" },
          { value: "pass", label: "Pass" },
          { value: "reject", label: "Reject" },
        ],
      },
      {
        type: "seg", id: "rs-cols", label: "COLUMNS", value: cols,
        options: [
          { value: "core", label: "Core" },
          { value: "features", label: "+ Engine" },
        ],
      },
      // Provenance scope (#488), mirroring the Portfolio page's. Only offered once the harvest has
      // published reconstructed days, so a box that has harvested nothing looks exactly as before.
      ...(hasRecon()
        ? [
            {
              type: "seg", id: "rs-scope", label: "DATA", value: SCOPE,
              options: [
                { value: "live", label: "Live", title: "Days the tracker captured in real time" },
                { value: "all", label: "+ History", title: "Live days plus reconstructed history" },
              ],
            },
          ]
        : []),
      {
        type: "seg", id: "rs-dock-toggle", label: "CHART", value: dockOn() ? "on" : "off",
        options: [
          { value: "on", label: "On" },
          { value: "off", label: "Off" },
        ],
      },
      { type: "readout", id: "rs-count", value: "loading…" },
      { type: "btn", id: "rs-refresh", label: "Refresh", title: "Refresh now" },
    ],
    extra: [
      {
        type: "note",
        value:
          "Session: Pre < 09:30 ET · Open ≥ 09:30 ET (first scanner appearance; unknowns only under All). " +
          "Engine Reject folds in no-setup rows. Pred Max R is the engine's measured Max R (3-tick fill, " +
          "stop-first); Max % is that same peak as a plain move off the fill, so a wide stop can't hide a " +
          "big run. Time is the first scanner appearance (sorts by time of day, across dates). " +
          "“+ Engine” adds every feature the detector gated and scored on, by area — sort one against " +
          "Max R to see whether it separates anything. Score contributions are omitted: each is just " +
          "weight × the feature beside it. Reads the same published data as the review workbench. " +
          "Chart: click a row (or press ↑/↓) to draw it below; Enter opens the review workbench, Esc " +
          "closes the dock, and the divider drags to re-split. " +
          (hasRecon() ? SCOPE_NOTE + " " : "") +
          "Times in ET. Phase-1 = tracking only, no orders.",
      },
    ],
    onChange: (id, value) => {
      if (id === "rs-refresh") {
        clearChartCache(); // Refresh means the branch, not the memo
        reconRows = null; // and the lazily-loaded history, which is a memo of its own
        reconLoading = null;
        return load();
      }
      if (id === "rs-cols") {
        cols = value;
        return grid.setColumns(columnDefs(value === "features"));
      }
      if (id === "rs-dock-toggle") return setDock(value === "on");
      if (id === "rs-scope") return setScope(value);
      if (id === "rs-session") want.session = value;
      if (id === "rs-engine") want.engine = value;
      grid.refreshFilter();
    },
  });
}

buildOptbar();

/* ---------- grid ---------- */

// Same compose(AND) semantics as the old page: "Reject" folds in no-setup rows;
// unknown-session rows surface only under "All".
function rowVisible(row) {
  // Reconstructed rows are loaded once and then filtered, not refetched: the payloads are the
  // expensive part and toggling the scope back and forth must not re-download them.
  if (SCOPE !== "all" && row.source === "recon") return false;
  if (want.session !== "all" && row.session !== want.session) return false;
  if (want.engine === "pass" && row.verdict !== "pass") return false;
  if (want.engine === "reject" && row.verdict === "pass") return false;
  return true;
}

// Numeric sorter with nulls pinned to the bottom regardless of direction
// (Tabulator flips the return for desc, so compensate via `dir`).
function numNullsLast(a, b, aRow, bRow, col, dir) {
  if (a == null && b == null) return 0;
  if (a == null) return dir === "asc" ? 1 : -1;
  if (b == null) return dir === "asc" ? -1 : 1;
  return a - b;
}

// Same, for the tick/cross columns: false < true, absent (no setup) pinned last.
function boolNullsLast(a, b, aRow, bRow, col, dir) {
  return numNullsLast(a == null ? null : +a, b == null ? null : +b, aRow, bRow, col, dir);
}

// Date sort keeps the old default read: within a day, biggest Max R first when
// the day column is descending (the ascending comparator mirrors that).
function dateSorter(a, b, aRow, bRow) {
  if (a !== b) return a < b ? -1 : 1;
  const am = aRow.getData().maxR ?? -Infinity;
  const bm = bRow.getData().maxR ?? -Infinity;
  return am - bm;
}

const SESSION_LABEL = { premarket: "pre", market: "mkt", unknown: "—" };
const SESSION_CLS = { premarket: "sess-pre", market: "sess-mkt", unknown: "sess-unk" };
const VERDICT = {
  pass: { text: "PASS", cls: "pill pill-pass" },
  reject: { text: "REJECT", cls: "pill pill-reject" },
  nosetup: { text: "no setup", cls: "pill" },
};

const symFmt = (cell) => `<strong>${esc(cell.getRow().getData().label)}</strong>`;
const sessFmt = (cell) => {
  const d = cell.getRow().getData();
  cell.getElement().title = `first seen ${etClockSec(d.firstHit)} ET`;
  return `<span class="${SESSION_CLS[d.session]}">${SESSION_LABEL[d.session]}</span>`;
};
const verdictFmt = (cell) => {
  const v = VERDICT[cell.getValue()] || VERDICT.nosetup;
  return `<span class="${v.cls}">${v.text}</span>`;
};
const rFmt = (cell) => {
  const v = cell.getValue();
  paintR(cell.getElement(), v);
  return fmtR(v);
};
const priceFmt = (cell) => fmtPrice(cell.getValue());
const floatFmt = (cell) => fmtShares(cell.getValue());
// The dock draws the chart in place now (#479), so this column is the escape hatch to the
// annotate-and-save workbench rather than the way to see a chart at all.
const reviewUrl = (d) =>
  `review.html?date=${encodeURIComponent(d.date)}&oid=${encodeURIComponent(d.oid)}`;
// The workbench reads the LIVE chart namespace and writes annotations keyed by opportunity id, so
// it has nothing to open for a reconstructed day (#488). Say so in the cell rather than linking to
// a page that would load empty.
const isRecon = (d) => d && d.source === "recon";
const chartFmt = (cell) => {
  const d = cell.getRow().getData();
  if (isRecon(d))
    return '<span class="muted" title="Reconstructed session — the review workbench annotates live days only">–</span>';
  return (
    `<a href="${reviewUrl(d)}" title="Open in the review workbench to annotate">` +
    `<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" style="vertical-align:-2px">` +
    `<polyline points="1,11 5,7 8,9 14,3" fill="none" stroke="currentColor" stroke-width="1.6"/></svg></a>`
  );
};
// The date, tagged when the row came from reconstructed history — the same `.pf-src` chip the
// Portfolio trade table uses, so one row is never mistaken for a captured one on either page.
const dateFmt = (cell) => {
  const d = cell.getRow().getData();
  return (
    esc(cell.getValue()) +
    (isRecon(d)
      ? ' <span class="pf-src" title="Reconstructed from vendor minute bars, not captured live">recon</span>'
      : "")
  );
};

const timeFmt = (cell) => etClockSec(cell.getRow().getData().firstHit);
const pctFmt = (cell) => fmtPct(cell.getValue());
const fracFmt = (dp) => (cell) => fmtPctPlain(cell.getValue(), dp);
const numFmt = (dp, signed) => (cell) => fmtNum(cell.getValue(), dp, signed);
const textFmt = (cell) => {
  const v = cell.getValue();
  return v == null ? '<span class="muted">—</span>' : v === "" ? "" : `<code>${esc(v)}</code>`;
};
// Booleans read as a tick/cross rather than true/false — 20 of these columns sit
// side by side, so the eye needs shape, not words.
const boolFmt = (cell) => {
  const v = cell.getValue();
  if (v == null) return '<span class="muted">—</span>';
  return `<span class="${v ? "flag-y" : "flag-n"}">${v ? "✓" : "✗"}</span>`;
};

const R = "right";
const C = "center";
// A header must fit its own title: 10px mono + letter-spacing plus Tabulator's
// sort-arrow gutter. Sizing by content alone (these are 2–6 character values)
// clips half the feature headers to "PK CO…", which defeats the point of the
// table. Each column asks for the wider of "fits the value" and "fits the title".
const hw = (title, w) => Math.max(w, title.length * 7 + 46);
// Shorthands for the column shapes that repeat across the feature groups.
const num = (title, field, tip, { w = 78, dp = 2, signed = false } = {}) => ({
  title, field, width: hw(title, w), hozAlign: R, headerTooltip: tip,
  formatter: numFmt(dp, signed), sorter: numNullsLast,
});
const frac = (title, field, tip, { w = 78, dp = 0 } = {}) => ({
  title, field, width: hw(title, w), hozAlign: R, headerTooltip: tip,
  formatter: fracFmt(dp), sorter: numNullsLast,
});
const price = (title, field, tip, w = 80) => ({
  title, field, width: hw(title, w), hozAlign: R, headerTooltip: tip,
  formatter: priceFmt, sorter: numNullsLast,
});
const flag = (title, field, tip, w = 62) => ({
  title, field, width: hw(title, w), hozAlign: C, headerTooltip: tip,
  formatter: boolFmt, sorter: boolNullsLast,
});

// Always on: identity, when it was seen, the verdict, and the outcome it produced.
const CORE = [
  { title: "SYMBOL", field: "symbol", frozen: true, width: 90, formatter: symFmt },
  { title: "DATE", field: "date", width: 132, sorter: dateSorter, formatter: dateFmt },
  {
    title: "TIME", field: "firstMin", width: 74, hozAlign: R, formatter: timeFmt,
    sorter: numNullsLast,
    headerTooltip: "First scanner appearance (ET) — sorts by time of day, across every date",
  },
  { title: "SESS", field: "session", width: 76, formatter: sessFmt },
  { title: "ENGINE", field: "verdict", width: 88, formatter: verdictFmt },
  {
    title: "PRED MAX R", field: "maxR", width: 112, hozAlign: R, formatter: rFmt,
    sorter: numNullsLast,
    headerTooltip: "The engine's measured Max R (3-tick fill, stop-first)",
  },
  {
    title: "MAX %", field: "maxPct", width: 84, hozAlign: R, formatter: pctFmt,
    sorter: numNullsLast,
    headerTooltip: "That same peak as a plain move off the fill — R normalises by the stop, so a " +
      "wide-stop 0.9R and a tight-stop 0.9R read alike while being very different moves",
  },
  { title: "FLOAT", field: "float", width: 76, hozAlign: R, formatter: floatFmt, sorter: numNullsLast },
  { title: "ENTRY", field: "entry", width: 80, hozAlign: R, formatter: priceFmt, sorter: numNullsLast },
  {
    title: "MAX R PX", field: "maxRPrice", width: 96, hozAlign: R, formatter: priceFmt,
    sorter: numNullsLast, headerTooltip: "The peak favourable price the notional trade reached",
  },
];

// Every engine-v2 input, grouped by the feature areas of research/bull-flag.md §3
// (SHAPE / VOL / WICK / POLE / CONS / LOC) plus the verdict context and the
// measured outcome. Gate inputs are marked "[gate]" in their tooltip.
const FEATURE_GROUPS = [
  {
    title: "OUTCOME",
    columns: [
      num("MAE R", "maeR", "Worst adverse excursion after entry, in R", { w: 78 }),
      flag("TRIG", "triggered", "The entry actually fired (price broke the trigger in time)"),
      flag("STOP'D", "stoppedOut", "The stop was breached (stop-first intrabar convention)", 74),
    ],
  },
  {
    title: "VERDICT",
    columns: [
      num("SCORE", "score", "0–1 quality score — ranks passing setups, never rejects one", { w: 76, dp: 3 }),
      num("CYC", "cycleNum", "1 = a fresh move; N = the Nth contiguous pump of the day", { w: 58, dp: 0 }),
      num("CYCS", "totalCycles", "Significant cycles across the whole day (context, not a gate)", { w: 62, dp: 0 }),
      flag("EXH", "exhausted", "Cycle number over the exhaustion cap — a late entry into a worn move", 58),
      {
        title: "FAILED GATES", field: "failedGates", width: 250, formatter: textFmt,
        headerTooltip: "The gates that rejected this shape (blank = every gate passed)",
      },
    ],
  },
  {
    title: "SHAPE",
    columns: [
      num("POLE N", "poleLen", "Higher highs in the pole [gate: ≤ bull_flag_max_pole]", { w: 74, dp: 0 }),
      num("CONS N", "consLen", "Candles in the consolidation [gate: ≤ bull_flag_max_cons]", { w: 74, dp: 0 }),
      frac("STRICT", "consStrictness", "Share of consolidation steps that are strict lower highs (vs flat)", { w: 74 }),
      { title: "TOKENS", field: "tokens", width: 100, formatter: textFmt, headerTooltip: "The H/L/E token walk of the segment" },
    ],
  },
  {
    title: "VOLUME",
    columns: [
      num("V RATIO", "volRatio", "Pole peak-bar volume ÷ the consolidation's max [gate input]", { w: 84 }),
      flag("PK>CONS", "peakGtCons", "Peak-bar volume beats consolidation volume [gate — the #127 rule]", 84),
      flag("DRYING", "consVolReducing", "Consolidation volume non-increasing (soft signal)", 74),
      frac("PK CONC", "poleVolConc", "Peak-bar volume as a share of all thrust volume", { w: 82 }),
    ],
  },
  {
    title: "WICK",
    columns: [
      frac("PK WICK", "peakUpperWick", "Upper-wick fraction of the peak bar [gate: ≤ bull_flag_max_peak_wick]", { w: 84 }),
      flag("PK GRN", "peakIsGreen", "The peak bar closes green [gate — peak_green, #196]", 74),
      flag("BIG GRN", "poleHasBigGreen", "A strong-bodied green candle in the pole (soft signal)", 80),
      frac("AVG BODY", "poleAvgBody", "Mean body fraction across the pole bars", { w: 90 }),
      frac("DOJI", "consIndecision", "Share of consolidation bars that are small-bodied / doji", { w: 68 }),
    ],
  },
  {
    title: "POLE",
    columns: [
      frac("HEIGHT %", "poleHeightPct", "Pole run off the base [gate: ≥ bull_flag_min_pole_pct]", { w: 90, dp: 1 }),
      price("HEIGHT $", "poleHeightAbs", "Pole run in dollars (peak high − base low)"),
      frac("VELOCITY", "poleVelocity", "Pole height % per higher-high — how steep the thrust was", { w: 90, dp: 1 }),
      num("ATR EXT", "poleExtAtr", "Pole height ÷ the trailing 14-bar ATR (blank = no baseline)", { w: 84 }),
    ],
  },
  {
    title: "FLAG",
    columns: [
      frac("RETRACE", "retracement", "Pullback into the pole [gate: ≤ bull_flag_max_retracement]", { w: 84 }),
      flag("HOLDS", "holdsBase", "The flag low stays above the pole base [gate — cons_holds_base]", 70),
      frac("TIGHT", "consTightness", "Consolidation range ÷ pole high — lower is tighter", { w: 72, dp: 1 }),
      num("DRIFT", "consDrift", "Per-step change in consolidation highs, $ (≤ 0 preferred)", { w: 78, dp: 3, signed: true }),
    ],
  },
  {
    title: "LEVELS",
    columns: [
      price("BREAKOUT", "breakout", "High of the last consolidation candle"),
      price("FILL", "fill", "The conservative 3-tick fill R is measured against"),
      price("STOP", "stop", "The consolidation low"),
      price("RISK $", "risk", "1R in dollars (fill − stop)"),
      flag("IN WIN", "inWindow", "The breakout lands inside the 04:00–11:59 ET strategy window"),
    ],
  },
];

const CHART_COL = {
  title: "", field: "oid", width: 34, hozAlign: C, headerSort: false, formatter: chartFmt,
};

const columnDefs = (withFeatures) =>
  withFeatures ? [...CORE, ...FEATURE_GROUPS, CHART_COL] : [...CORE, CHART_COL];

const grid = new Tabulator("#rs-grid", {
  data: [],
  layout: "fitData",
  // Fills the split's grid pane, which owns whatever height the dock leaves; a
  // resolved height is what turns on virtual scrolling.
  height: "100%",
  index: "oid", // lets the dock address a row by opportunity id
  placeholder: "Loading…",
  initialSort: [{ column: "date", dir: "desc" }],
  columns: columnDefs(false),
  // The selected row is the chart's source. Painted here rather than by toggling
  // a class directly, because Tabulator recycles row elements while scrolling —
  // a hand-applied class would drift onto whatever row inherits the element.
  rowFormatter: (row) => {
    row.getElement().classList.toggle("rs-sel", row.getData().oid === selectedOid);
  },
});

grid.on("dataFiltered", (filters, rows) => {
  el("rs-count").textContent = `${rows.length} of ${grid.getData().length} shown`;
});

grid.on("rowClick", (e, row) => {
  // The chart-icon column is a real link out to the workbench — let it navigate.
  if (e.target && e.target.closest("a")) return;
  select(row.getData().oid);
});

/* ---------- load ---------- */

// The dates an index offers, dropping days that published no opportunity at all.
const indexDates = (index) =>
  ((index && index.dates) || [])
    .filter((d) => Array.isArray(d.opportunities) && d.opportunities.length > 0)
    .map((d) => d.date);

// Pull every date's chart file in parallel; a missing/failed day degrades to no
// rows for that day rather than failing the whole table.
//
// Deliberately NOT `chartsFor` (which memoises): these payloads are 1.5–3 MB of
// full-day bars each, and holding all ~30 days alive for the whole session to
// service a dock that shows one at a time is a lot of resident memory for
// nothing. The rows keep what the grid needs; the dock re-reads the one date it
// is drawing, which the inspector then caches.
async function rowsForDates(dates, source) {
  const perDate = await Promise.all(
    dates.map(async (date) => {
      const payload = await fetchJson(chartsUrl(date, source));
      const charts = (payload && payload.charts) || [];
      return charts.map((c) => toRow(date, c, source));
    }),
  );
  return perDate.flat();
}

let liveRows = [];

async function load() {
  el("rs-error").hidden = true;
  el("rs-count").textContent = "loading…";
  try {
    // Both indexes, in parallel — the reconstructed one is nav rows only (tens of KB), and
    // fetching it up front is what decides whether the DATA control exists at all. Its multi-MB
    // chart payloads stay untouched until the scope is actually switched.
    const [index, recon] = await Promise.all([fetchJson("index.json"), fetchJson("recon_index.json")]);
    reconIndex = recon;
    const dates = indexDates(index);
    liveRows = await rowsForDates(dates, "live");
    if (!liveRows.length) {
      grid.setPlaceholder("No review data published yet.");
    }
    buildOptbar(); // the DATA control appears (or doesn't) now that recon_index has been read
    if (SCOPE === "all" && hasRecon()) {
      await ensureReconRows();
    } else {
      SCOPE = hasRecon() ? SCOPE : "live";
      applyRows();
    }
    restoreSelection();
  } catch (e) {
    el("rs-error").hidden = false;
    el("rs-error").textContent = `Failed to load results: ${e && e.message ? e.message : e}`;
  }
}

// Put whatever is loaded into the grid and restate the footer. Split out because the scope switch
// re-runs it without touching the network.
function applyRows() {
  const rows = SCOPE === "all" && reconRows ? [...liveRows, ...reconRows] : liveRows;
  grid.setData(rows);
  grid.setFilter(rowVisible);
  const days = new Set(rows.map((r) => r.date)).size;
  const now = new Intl.DateTimeFormat("en-US", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(new Date());
  const recon = SCOPE === "all" && reconRows ? ` · ${reconRows.length} reconstructed` : "";
  setStatusPage(`${rows.length} opps · ${days} days${recon} · fetched ${esc(now)}`);
}

// Fetch the reconstructed days once, on first switch. `reconLoading` de-dupes a
// second switch arriving while the first is still in flight.
function ensureReconRows() {
  if (reconRows) {
    applyRows();
    return Promise.resolve();
  }
  if (!reconLoading) {
    el("rs-count").textContent = "loading history…";
    reconLoading = rowsForDates(indexDates(reconIndex), "recon")
      .then((rows) => {
        reconRows = rows;
      })
      .catch(() => {
        reconRows = []; // a failed history load must not wedge the page on the live rows
      });
  }
  return reconLoading.then(applyRows);
}

function setScope(value) {
  SCOPE = value;
  if (value === "all" && hasRecon()) return ensureReconRows();
  applyRows();
  return Promise.resolve();
}

/* ---------- the chart dock (#479) ----------
   Master/detail: the grid selects, the dock draws. Everything below is host
   chrome — the chart, the readout, the engine detail and the news list all come
   from js/inspector.js, the same component the review workbench mounts. */

// Leave room for the grid: a dock taller than the window minus a few rows is a
// dock that has eaten the table it exists to serve.
function clampH(px) {
  const max = Math.max(DOCK_H_MIN, window.innerHeight - 220);
  return Math.min(max, Math.max(DOCK_H_MIN, px));
}

function dockHeight() {
  const saved = parseInt(localStorage.getItem(DOCK_H_KEY) || "", 10);
  return clampH(isFinite(saved) ? saved : DOCK_H_DEFAULT);
}

function applyDockHeight(px) {
  el("rs-dock").style.height = `${px}px`;
  grid.redraw(); // the grid pane just changed size; re-measure the virtual window
}

function setDock(on) {
  localStorage.setItem(DOCK_ON_KEY, on ? "on" : "off");
  el("rs-dock").hidden = !on;
  if (on) {
    applyDockHeight(dockHeight());
    if (selectedOid) drawNow(selectedOid);
  } else {
    grid.redraw();
  }
}

function ensureView() {
  if (view !== undefined) return view;
  view = createChartView(el("rs-chart"));
  if (view) view.setEngineOn(engineOn);
  return view;
}

function dockMessage(msg) {
  el("rs-dock-readout").innerHTML = `<span class="muted">${esc(msg)}</span>`;
}

// Select a row: paint it, park it in the URL, and schedule the draw. The paint is
// immediate and the draw is debounced, so holding ↓ scrubs the selection at full
// speed and only settles the chart when you stop.
function select(oid) {
  const prev = selectedOid;
  selectedOid = oid;
  for (const id of [prev, oid]) {
    if (!id) continue;
    const row = grid.getRow(id);
    if (row) row.reformat();
  }
  if (oid) {
    const d = grid.getRow(oid) ? grid.getRow(oid).getData() : null;
    // A reconstructed day has no workbench to open (#488) — hide the escape hatch rather than
    // point it at a page that would load empty.
    const open = el("rs-dock-open");
    open.classList.toggle("hidden", isRecon(d));
    if (d && !isRecon(d)) open.href = reviewUrl(d);
    // A shareable/reload-safe pointer at what you were looking at.
    history.replaceState(null, "", `#oid=${encodeURIComponent(oid)}`);
  }
  if (!dockOn() || !oid) return;
  el("rs-dock-title").textContent = oid;
  clearTimeout(drawTimer);
  drawTimer = setTimeout(() => drawNow(oid), DRAW_DEBOUNCE_MS);
}

// The provenance tag the dock header carries, so a chart drawn from vendor bars
// is never read as one the tracker watched.
const dockTitle = (text, recon) =>
  esc(text) +
  (recon
    ? ' <span class="pf-src" title="Reconstructed from vendor minute bars, not captured live">recon</span>'
    : "");

async function drawNow(oid) {
  if (!dockOn()) return;
  const v = ensureView();
  if (!v) {
    dockMessage("Chart library failed to load.");
    return;
  }
  const token = ++drawToken;
  const row = grid.getRow(oid) ? grid.getRow(oid).getData() : null;
  const recon = isRecon(row);
  const source = recon ? "recon" : "live";
  const date = String(oid).split(":")[0];
  sideReview = null;
  el("rs-dock-note").classList.remove("has");
  dockMessage("loading…");
  const payload = await chartsFor(date, source);
  if (token !== drawToken) return; // a later selection won the race
  const c = findChart(payload, oid);
  if (!c) {
    v.clear();
    el("rs-dock-title").innerHTML = dockTitle(oid, recon);
    dockMessage("No chart published for this opportunity.");
    updateSide(null);
    return;
  }
  v.draw(c);
  el("rs-dock-title").innerHTML = dockTitle(optionLabel(c), recon);
  el("rs-dock-readout").innerHTML = readoutHtml(c, { engineOn });
  const n = newsCount(c);
  const news = el("rs-dock-news");
  news.textContent = `News ${n}`;
  news.disabled = n === 0;
  if (sideMode === "news" && n === 0) sideMode = "gates";
  updateSide(c);
  // The workbench writes reviews for live opportunities only, so a reconstructed day has none to
  // fetch — skip the request rather than spend a round trip guaranteed to 404.
  if (!recon) loadSavedReview(oid, token);
}

// The trader's own read of this opportunity (#481), drawn over the engine's: the pole/consolidation
// bands and entry/stop they placed by hand, plus the note behind the Note button. Loaded after the
// chart so the draw is never held up by it, and token-guarded like the chart itself.
async function loadSavedReview(oid, token) {
  const r = await reviewFor(oid);
  if (token !== drawToken) return;
  sideReview = r;
  const marked = hasReview(r);
  el("rs-dock-note").classList.toggle("has", marked);
  if (view && marked && !r.no_trigger) view.setAnnotations(r.annotations);
  if (sideMode === "note") updateSide(current());
}

function updateSide(c) {
  const side = el("rs-side");
  side.hidden = !sideMode;
  for (const [mode, id] of [["gates", "rs-dock-gates"], ["news", "rs-dock-news"], ["note", "rs-dock-note"]])
    el(id).classList.toggle("on", sideMode === mode);
  if (!sideMode) return;
  side.innerHTML =
    sideMode === "news" ? newsHtml(c) : sideMode === "note" ? reviewHtml(sideReview) : engineDetailHtml(c);
}

function toggleSide(mode) {
  sideMode = sideMode === mode ? null : mode;
  updateSide(current()); // the chart auto-sizes to the width the panel leaves
}

// The chart object currently drawn, straight from the view — the dock keeps no
// second copy of it.
const current = () => (view ? view.current() : null);

// Step the selection through the grid's CURRENT order — `getRows("active")` is
// post-sort, post-filter, which is what the eye is following.
function step(delta) {
  const rows = grid.getRows("active");
  if (!rows.length) return;
  let i = rows.findIndex((r) => r.getData().oid === selectedOid);
  i = i < 0 ? (delta > 0 ? 0 : rows.length - 1) : (i + delta + rows.length) % rows.length;
  const row = rows[i];
  select(row.getData().oid);
  row.scrollTo("nearest", false).catch(() => {});
}

// Reopen on whatever the URL hash points at, once the rows exist. Scroll to it as well as select
// it: under the default date-desc sort a deep-linked row is usually far outside the rendered
// window, and a selection you can't see reads as no selection at all.
function restoreSelection() {
  const m = /^#oid=(.*)$/.exec(location.hash || "");
  if (!m) return;
  const oid = decodeURIComponent(m[1]);
  if (!grid.getRow(oid)) return;
  select(oid);
  grid.scrollToRow(oid, "nearest", false).catch(() => {});
}

/* ---------- dock wiring ---------- */

// ✕ and Esc go through the CHART segment rather than calling setDock directly:
// the options bar owns that control's state, and setting it behind its back
// leaves the segment thinking it is still "on" — after which clicking On does
// nothing, because the value hasn't changed as far as it knows.
function closeDock() {
  const off = el("rs-dock-toggle").querySelector('.opt-seg-btn[data-value="off"]');
  if (off) off.click();
}
el("rs-dock-close").addEventListener("click", closeDock);
el("rs-dock-note").addEventListener("click", () => toggleSide("note"));
el("rs-dock-gates").addEventListener("click", () => toggleSide("gates"));
el("rs-dock-news").addEventListener("click", () => toggleSide("news"));
el("rs-dock-engine").addEventListener("click", () => {
  engineOn = !engineOn;
  if (view) view.setEngineOn(engineOn);
  const btn = el("rs-dock-engine");
  btn.classList.toggle("armed", engineOn);
  btn.setAttribute("aria-pressed", engineOn ? "true" : "false");
  const c = current();
  if (c) el("rs-dock-readout").innerHTML = readoutHtml(c, { engineOn });
});
// The readout's float chip toggles its all-sources breakdown, as on the review page.
el("rs-dock-readout").addEventListener("click", (e) => {
  const chip = e.target.closest(".rv-float-toggle");
  if (!chip) return;
  const all = chip.parentElement.querySelector(".rv-float-all");
  if (all) all.classList.toggle("hidden");
});

// Drag the divider to re-split. Our own pointer loop rather than CSS `resize`,
// so the grid can be redrawn as it moves instead of only on release.
(() => {
  const handle = el("rs-dock-handle");
  let start = null;
  handle.addEventListener("pointerdown", (e) => {
    start = { y: e.clientY, h: el("rs-dock").getBoundingClientRect().height };
    handle.classList.add("dragging");
    handle.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  handle.addEventListener("pointermove", (e) => {
    if (!start) return;
    applyDockHeight(clampH(start.h + (start.y - e.clientY)));
  });
  const end = (e) => {
    if (!start) return;
    start = null;
    handle.classList.remove("dragging");
    if (e && e.pointerId != null) {
      try {
        handle.releasePointerCapture(e.pointerId);
      } catch (_) {
        /* already released */
      }
    }
    localStorage.setItem(DOCK_H_KEY, String(Math.round(el("rs-dock").getBoundingClientRect().height)));
  };
  handle.addEventListener("pointerup", end);
  handle.addEventListener("pointercancel", end);
})();

// Keyboard: the grid is a list you walk, so ↑/↓ (and j/k) step it with the chart
// following. Enter hands off to the workbench, Esc puts the dock away.
document.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "ArrowDown" || e.key === "j") {
    e.preventDefault();
    step(1);
  } else if (e.key === "ArrowUp" || e.key === "k") {
    e.preventDefault();
    step(-1);
  } else if (e.key === "Enter" && selectedOid) {
    const row = grid.getRow(selectedOid);
    // Reconstructed rows have no workbench page (#488) — Enter is a no-op rather than a dead end.
    if (row && !isRecon(row.getData())) location.href = reviewUrl(row.getData());
  } else if (e.key === "Escape") {
    closeDock();
  }
});

// A window resize can push a saved height past the clamp.
window.addEventListener("resize", () => {
  if (dockOn()) applyDockHeight(clampH(el("rs-dock").getBoundingClientRect().height));
});

grid.on("tableBuilt", () => {
  el("rs-dock").hidden = !dockOn();
  if (dockOn()) applyDockHeight(dockHeight());
  dockMessage("click a row — or press ↓ — to chart it");
  load();
});
