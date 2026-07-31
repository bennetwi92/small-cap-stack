// Results (#222/#223/#224, cockpit #288): every opportunity across every
// collected day, as a full-bleed Tabulator grid — virtual scrolling, header
// sorting, frozen symbol column. Same published `dashboard-data` JSON and the
// same row/filter semantics as before the redesign; only the rendering changed.
//
// The grid is the engine's scatter-plot in table form: the COLUMNS toggle folds
// in every engine-v2 feature the detector gated and scored on (charts.py
// `engine.features`), grouped by feature area, so sorting one column against Max
// R / Max % is enough to see whether that feature separates runners from duds.

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
import { MARKET_OPEN_MIN } from "./js/session.js";
import { TabulatorFull as Tabulator } from "https://cdn.jsdelivr.net/npm/tabulator-tables@6.5.2/dist/js/tabulator_esm.min.js";

const el = (id) => document.getElementById(id);

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
function toRow(date, c) {
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

createOptionsBar("optbar", {
  primary: [
    {
      type: "seg", id: "rs-session", label: "SESSION", value: "all",
      options: [
        { value: "all", label: "All" },
        { value: "premarket", label: "Pre" },
        { value: "market", label: "Open" },
      ],
    },
    {
      type: "seg", id: "rs-engine", label: "ENGINE", value: "all",
      options: [
        { value: "all", label: "All" },
        { value: "pass", label: "Pass" },
        { value: "reject", label: "Reject" },
      ],
    },
    {
      type: "seg", id: "rs-cols", label: "COLUMNS", value: "core",
      options: [
        { value: "core", label: "Core" },
        { value: "features", label: "+ Engine" },
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
        "Times in ET. Phase-1 = tracking only, no orders.",
    },
  ],
  onChange: (id, value) => {
    if (id === "rs-refresh") return load();
    if (id === "rs-cols") return grid.setColumns(columnDefs(value === "features"));
    if (id === "rs-session") want.session = value;
    if (id === "rs-engine") want.engine = value;
    grid.refreshFilter();
  },
});

/* ---------- grid ---------- */

// Same compose(AND) semantics as the old page: "Reject" folds in no-setup rows;
// unknown-session rows surface only under "All".
function rowVisible(row) {
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
const chartFmt = (cell) => {
  const d = cell.getRow().getData();
  const link = `review.html?date=${encodeURIComponent(d.date)}&oid=${encodeURIComponent(d.oid)}`;
  return (
    `<a href="${link}" title="Open this opportunity in the review chart">` +
    `<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" style="vertical-align:-2px">` +
    `<polyline points="1,11 5,7 8,9 14,3" fill="none" stroke="currentColor" stroke-width="1.6"/></svg></a>`
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
  { title: "DATE", field: "date", width: 106, sorter: dateSorter },
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
  height: "calc(100vh - 76px)", // fill between the bars; a fixed height turns on virtual scrolling
  placeholder: "Loading…",
  initialSort: [{ column: "date", dir: "desc" }],
  columns: columnDefs(false),
});

grid.on("dataFiltered", (filters, rows) => {
  el("rs-count").textContent = `${rows.length} of ${grid.getData().length} shown`;
});

/* ---------- load ---------- */

async function load() {
  el("rs-error").hidden = true;
  el("rs-count").textContent = "loading…";
  try {
    const index = await fetchJson("index.json");
    const dates = ((index && index.dates) || [])
      .filter((d) => Array.isArray(d.opportunities) && d.opportunities.length > 0)
      .map((d) => d.date);
    // Pull every date's chart file in parallel; a missing/failed day degrades to
    // no rows for that day rather than failing the whole table.
    const perDate = await Promise.all(
      dates.map(async (date) => {
        const payload = await fetchJson(`charts/${date}.json`);
        const charts = (payload && payload.charts) || [];
        return charts.map((c) => toRow(date, c));
      }),
    );
    const rows = perDate.flat();
    if (!rows.length) {
      grid.setPlaceholder("No review data published yet.");
    }
    grid.setData(rows);
    grid.setFilter(rowVisible);
    const now = new Intl.DateTimeFormat("en-US", {
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    }).format(new Date());
    setStatusPage(`${rows.length} opps · ${dates.length} days · fetched ${esc(now)}`);
  } catch (e) {
    el("rs-error").hidden = false;
    el("rs-error").textContent = `Failed to load results: ${e && e.message ? e.message : e}`;
  }
}

grid.on("tableBuilt", load);
