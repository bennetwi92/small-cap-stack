// Results table (#222/#223/#224): a cross-day, bird's-eye table of every opportunity the tracker
// collected, filterable by time-of-day and engine verdict, with a per-row link into the review
// chart. No build step, no framework — plain fetch + DOM, mirroring app.js / review.js idioms.
//
// Pure frontend: reads the SAME published `dashboard-data` JSON the review workbench does — the
// cross-day `index.json` for the list of dates, then each `charts/<date>.json` for that day's
// opportunities (float / engine / levels / markers / Max R / full-day bars). Every display column
// is derived on read (store-raw / compute-on-read), so there is no producer-side change and the
// page ships on the ordinary GitHub Pages deploy.

const REPO = "bennetwi92/small-cap-stack";
const BRANCH = "dashboard-data";

const rawUrl = (file) =>
  `https://raw.githubusercontent.com/${REPO}/${BRANCH}/${file}?t=${Date.now()}`;

const el = (id) => document.getElementById(id);
const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// Shared colour map (mirrors app.js / review.js MK): engine pass = green, reject = red, Max R = amber.
const MK = { up: "#3fb950", down: "#f85149", maxR: "#d4a72c", entry: "#2f81f7" };

// ET time-of-day: the market opens 09:30 ET (the scan window itself is 04:00–11:59 ET). We classify
// each opportunity by its first scanner appearance (`markers.first_hit`) rendered in ET.
const MARKET_OPEN_MIN = 9 * 60 + 30; // 09:30 ET, in minutes past ET-midnight
const _etHM = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
});
// Minutes-past-ET-midnight for a UNIX-seconds instant; null when absent. 24:00 (some locales emit
// "24" for midnight) folds back to 0.
function etMinutes(sec) {
  if (sec == null) return null;
  const parts = _etHM.formatToParts(new Date(sec * 1000));
  const h = +parts.find((p) => p.type === "hour").value % 24;
  const m = +parts.find((p) => p.type === "minute").value;
  return h * 60 + m;
}
const etClock = (sec) => (sec == null ? "—" : _etHM.format(new Date(sec * 1000)));

// Compact share/volume formatter — same tiers/casing/boundary-promotion as app.js `shares` and
// review.js `fmtShares` so a float renders identically across all three pages (#163).
function fmtShares(n) {
  if (n == null || !isFinite(n)) return "—";
  const a = Math.abs(n);
  if (a >= 999.95e6) return (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
  if (a >= 999.5e3) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (a >= 1e3) return Math.round(n / 1e3) + "k";
  return String(Math.round(n));
}
const fmtPrice = (x) => (x == null || !isFinite(x) ? "—" : "$" + Number(x).toFixed(2));
const fmtR = (x) => (x == null || !isFinite(x) ? "—" : Number(x).toFixed(2) + "R");

async function fetchJson(file) {
  const res = await fetch(rawUrl(file), { cache: "no-store" });
  if (!res.ok) return null; // e.g. index.json before the first EOD -> 404
  return res.json();
}

// --- Row model -----------------------------------------------------------------------------------
// One flat row per opportunity/run, projected from a `charts/<date>.json` chart object.

// The Max R price = the peak favourable price the notional trade reached. `markers.max_r` is the
// epoch of the bar that set peak excursion; its high IS that peak (charts.py sets the marker to the
// max-favourable bar). Match the bar by exact timestamp, mirroring review.js's volChip lookup.
function maxRPrice(c) {
  const t = c && c.markers && c.markers.max_r;
  if (t == null || !Array.isArray(c.bars)) return null;
  const bar = c.bars.find((b) => b.t === t);
  return bar ? bar.h : null;
}

// Engine verdict: "pass" (a setup formed AND passed every gate), "reject" (a setup formed but a gate
// rejected it), or "nosetup" (no v2 pole formed). Charts published before the engine overlay (#216)
// have no `engine` block -> treated as unknown/nosetup.
function engineVerdict(c) {
  const e = c && c.engine;
  if (!e || !e.setup) return "nosetup";
  return e.passed ? "pass" : "reject";
}

function toRow(date, c) {
  const first = c.markers ? c.markers.first_hit : null;
  const mins = etMinutes(first);
  const floats = (c.floats || []).filter((f) => f.float != null);
  return {
    date,
    oid: c.opportunity_id,
    label: c.run_count > 1 ? `${c.symbol}#${c.run}` : c.symbol,
    symbol: c.symbol,
    firstHit: first,
    // null first_hit -> "unknown"; it only surfaces under the "All" time-of-day filter.
    session: mins == null ? "unknown" : mins < MARKET_OPEN_MIN ? "premarket" : "market",
    verdict: engineVerdict(c),
    maxR: c.max_r == null ? null : c.max_r,
    float: floats.length ? floats[0].float : null,
    entry: c.levels ? c.levels.entry : null,
    maxRPrice: maxRPrice(c),
  };
}

// --- State ---------------------------------------------------------------------------------------
let rows = []; // every collected opportunity, flattened across all dates
// Default sort: newest day first, then biggest Max R within the day (nulls last).
let sortKey = "date";
let sortDir = -1; // 1 = ascending, -1 = descending

const CMP = {
  date: (r) => r.date,
  symbol: (r) => r.symbol,
  session: (r) => r.session,
  engine: (r) => r.verdict,
  maxR: (r) => r.maxR,
  float: (r) => r.float,
  entry: (r) => r.entry,
  maxRPrice: (r) => r.maxRPrice,
};

function sortRows(list) {
  const get = CMP[sortKey] || CMP.date;
  const sorted = list.slice().sort((a, b) => {
    const av = get(a);
    const bv = get(b);
    // Nulls always sink to the bottom, regardless of direction.
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    let d;
    if (typeof av === "number" && typeof bv === "number") d = av - bv;
    else d = String(av) < String(bv) ? -1 : String(av) > String(bv) ? 1 : 0;
    return d * sortDir;
  });
  // Stable tie-break so same-day rows read biggest-Max-R first when sorting by date.
  if (sortKey === "date") {
    sorted.sort((a, b) => {
      if (a.date !== b.date) return a.date < b.date ? 1 : -1; // newest day first
      const am = a.maxR == null ? -Infinity : a.maxR;
      const bm = b.maxR == null ? -Infinity : b.maxR;
      return bm - am;
    });
  }
  return sorted;
}

const SESSION_LABEL = { premarket: "pre", market: "mkt", unknown: "—" };
const VERDICT = {
  pass: { text: "PASS", cls: "rs-pass" },
  reject: { text: "REJECT", cls: "rs-reject" },
  nosetup: { text: "no setup", cls: "rs-muted" },
};

function rColor(x) {
  if (x == null) return "";
  return ` style="color:${x >= 0 ? MK.up : MK.down}"`;
}

const COLS = [
  { key: "date", label: "Date" },
  { key: "symbol", label: "Symbol" },
  { key: "session", label: "Session" },
  { key: "engine", label: "Engine" },
  { key: "maxR", label: "Pred Max R" },
  { key: "float", label: "Float" },
  { key: "entry", label: "Entry" },
  { key: "maxRPrice", label: "Max R price" },
];

function render() {
  const wantSession = el("rs-session").value;
  const wantEngine = el("rs-engine").value;
  const filtered = rows.filter((r) => {
    if (wantSession !== "all" && r.session !== wantSession) return false;
    if (wantEngine === "pass" && r.verdict !== "pass") return false;
    // "Reject" folds in the no-setup rows (a formed setup that failed a gate, or no setup at all).
    if (wantEngine === "reject" && r.verdict === "pass") return false;
    return true;
  });
  el("rs-count").textContent = `${filtered.length} of ${rows.length} shown`;

  if (!rows.length) {
    el("rs-table").innerHTML = '<p class="muted">No review data published yet.</p>';
    return;
  }
  if (!filtered.length) {
    el("rs-table").innerHTML = '<p class="muted">No opportunities match the current filters.</p>';
    return;
  }

  const head = COLS.map((col) => {
    const arrow = sortKey === col.key ? (sortDir === 1 ? " ▲" : " ▼") : "";
    return `<th class="rs-sortable" data-key="${col.key}">${esc(col.label)}${arrow}</th>`;
  }).join("");

  const body = sortRows(filtered)
    .map((r) => {
      const v = VERDICT[r.verdict] || VERDICT.nosetup;
      const link = `review.html?date=${encodeURIComponent(r.date)}&oid=${encodeURIComponent(r.oid)}`;
      return (
        `<tr>` +
        `<td>${esc(r.date)}</td>` +
        `<td><strong>${esc(r.label)}</strong></td>` +
        `<td><span class="rs-sess rs-sess-${r.session}" title="first seen ${esc(etClock(r.firstHit))} ET">${SESSION_LABEL[r.session]}</span></td>` +
        `<td><span class="rs-badge ${v.cls}">${v.text}</span></td>` +
        `<td${rColor(r.maxR)}>${fmtR(r.maxR)}</td>` +
        `<td>${fmtShares(r.float)}</td>` +
        `<td>${fmtPrice(r.entry)}</td>` +
        `<td>${fmtPrice(r.maxRPrice)}</td>` +
        `<td><a class="rs-chart-link" href="${link}" title="Open this opportunity in the review chart">chart ›</a></td>` +
        `</tr>`
      );
    })
    .join("");

  el("rs-table").innerHTML =
    `<table><thead><tr>${head}<th></th></tr></thead><tbody>${body}</tbody></table>`;

  for (const th of document.querySelectorAll("#rs-table .rs-sortable")) {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (sortKey === key) sortDir = -sortDir;
      else {
        sortKey = key;
        // Numbers default high-to-low, text A-to-Z — the useful first glance for each.
        sortDir = ["maxR", "float", "entry", "maxRPrice", "date"].includes(key) ? -1 : 1;
      }
      render();
    });
  }
}

async function load() {
  el("rs-error").hidden = true;
  el("rs-count").textContent = "loading…";
  el("rs-table").innerHTML = '<p class="muted">loading…</p>';
  const index = await fetchJson("index.json");
  const dates = ((index && index.dates) || [])
    .filter((d) => Array.isArray(d.opportunities) && d.opportunities.length > 0)
    .map((d) => d.date);
  if (!dates.length) {
    rows = [];
    render();
    return;
  }
  // Pull every date's chart file in parallel; a missing/failed day degrades to no rows for that day
  // rather than failing the whole table.
  const perDate = await Promise.all(
    dates.map(async (date) => {
      const payload = await fetchJson(`charts/${date}.json`);
      const charts = (payload && payload.charts) || [];
      return charts.map((c) => toRow(date, c));
    }),
  );
  rows = perDate.flat();
  render();
}

el("rs-session").addEventListener("change", render);
el("rs-engine").addEventListener("change", render);
el("rs-refresh").addEventListener("click", load);

load().catch((e) => {
  el("rs-error").hidden = false;
  el("rs-error").textContent = `Failed to load results: ${e && e.message ? e.message : e}`;
});
