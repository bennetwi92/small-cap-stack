// Results (#222/#223/#224, cockpit #288): every opportunity across every
// collected day, as a full-bleed Tabulator grid — virtual scrolling, header
// sorting, frozen symbol column. Same published `dashboard-data` JSON and the
// same row/filter semantics as before the redesign; only the rendering changed.

import "./js/nav.js";
import { createOptionsBar } from "./js/options-bar.js";
import { setStatusPage } from "./js/status-bar.js";
import { fetchJson } from "./js/data.js";
import { esc, fmtShares, fmtPrice, fmtR, paintR, etClockSec, etMinutesSec } from "./js/fmt.js";
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

function toRow(date, c) {
  const first = c.markers ? c.markers.first_hit : null;
  const mins = etMinutesSec(first);
  const floats = (c.floats || []).filter((f) => f.float != null);
  return {
    date,
    oid: c.opportunity_id,
    label: c.run_count > 1 ? `${c.symbol}#${c.run}` : c.symbol,
    symbol: c.symbol,
    firstHit: first,
    session: mins == null ? "unknown" : mins < MARKET_OPEN_MIN ? "premarket" : "market",
    verdict: engineVerdict(c),
    maxR: c.max_r == null ? null : c.max_r,
    float: floats.length ? floats[0].float : null,
    entry: c.levels ? c.levels.entry : null,
    maxRPrice: maxRPrice(c),
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
    { type: "readout", id: "rs-count", value: "loading…" },
    { type: "btn", id: "rs-refresh", label: "Refresh", title: "Refresh now" },
  ],
  extra: [
    {
      type: "note",
      value:
        "Session: Pre < 09:30 ET · Open ≥ 09:30 ET (first scanner appearance; unknowns only under All). " +
        "Engine Reject folds in no-setup rows. Pred Max R is the engine's measured Max R (3-tick fill, " +
        "stop-first). Reads the same published data as the review workbench. Times in ET. " +
        "Phase-1 = tracking only, no orders.",
    },
  ],
  onChange: (id, value) => {
    if (id === "rs-refresh") return load();
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

const R = "right";
const grid = new Tabulator("#rs-grid", {
  data: [],
  layout: "fitData",
  height: "calc(100vh - 76px)", // fill between the bars; a fixed height turns on virtual scrolling
  placeholder: "Loading…",
  initialSort: [{ column: "date", dir: "desc" }],
  columns: [
    { title: "SYMBOL", field: "symbol", frozen: true, width: 90, formatter: symFmt },
    { title: "DATE", field: "date", width: 106, sorter: dateSorter },
    { title: "SESS", field: "session", width: 66, formatter: sessFmt },
    { title: "ENGINE", field: "verdict", width: 88, formatter: verdictFmt },
    {
      title: "PRED MAX R", field: "maxR", width: 112, hozAlign: R, formatter: rFmt,
      sorter: numNullsLast,
      headerTooltip: "The engine's measured Max R (3-tick fill, stop-first)",
    },
    { title: "FLOAT", field: "float", width: 76, hozAlign: R, formatter: floatFmt, sorter: numNullsLast },
    { title: "ENTRY", field: "entry", width: 80, hozAlign: R, formatter: priceFmt, sorter: numNullsLast },
    {
      title: "MAX R PX", field: "maxRPrice", width: 96, hozAlign: R, formatter: priceFmt,
      sorter: numNullsLast, headerTooltip: "The peak favourable price the notional trade reached",
    },
    { title: "", field: "oid", width: 34, hozAlign: "center", headerSort: false, formatter: chartFmt },
  ],
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
