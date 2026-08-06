// Review workbench (#142): a mobile-first, single-screen page for cycling back through any day's
// opportunities. Reads the same published JSON as the dashboard (#141): `index.json` for the
// date/symbol navigation and per-date `charts/<date>.json` for the full-day (04:00–16:00 ET) bars.
// No build step, no framework — plain fetch + DOM. Write-back commits review JSON to the
// `review-data` branch: per-opportunity notes (#143) and tap-to-place chart annotations
// (pole/consolidation/entry/stop) with an auto-computed Max R (#144).
//
// Since #478 the chart itself is NOT this page's — `js/inspector.js` owns the candles, the volume,
// the engine-v2 overlay and every readout string, so Results and Portfolio can dock the same view
// instead of navigating here. What stays is what only a workbench can own: the annotation editor
// (tap-to-place, drag-to-refine), the unsaved-changes lifecycle, and the GitHub write-back.
//
// This page deliberately keeps its own chrome: NO bottom status bar (the readout strip is this
// page's status line) and a hand-driven control bar in options-bar clothing, because navigation
// here is guarded by the unsaved-annotation check. See review.html.

import "./js/nav.js";
import { REPO, fetchJson } from "./js/data.js";
import { esc } from "./js/fmt.js";
import {
  MK,
  chartsFor,
  createChartView,
  engineDetailHtml,
  newsCount,
  newsHtml,
  optionLabel,
  readoutHtml,
  round2,
} from "./js/inspector.js";

const REVIEW_BRANCH = "review-data"; // write-back reviews live here (#143), off the force-pushed BRANCH
const DEFAULT_BRANCH = "main"; // base the review-data branch off this on first save
const API = "https://api.github.com";
const PAT_KEY = "rv_pat"; // localStorage key for the phone-local GitHub token

const el = (id) => document.getElementById(id);

// Date-picker label: "2026-07-01" -> "2026-07-01 · Wed" so the day of week reads at a glance.
// Parse the ISO parts directly (local Date from y/m/d, no UTC parse) so the weekday never tz-shifts.
const _DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const dateLabel = (iso) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso));
  if (!m) return String(iso);
  const dow = _DOW[new Date(+m[1], +m[2] - 1, +m[3]).getDay()];
  return `${iso} · ${dow}`;
};

let chartsData = null; // last-fetched charts/<date>.json payload for the selected date
// The shared chart view (js/inspector.js), built once on first use. `undefined` = not yet built,
// `null` = the charting library failed to load, so don't keep retrying.
let view;
let currentOpp = null; // the opportunity chart object currently drawn (for the notes sheet)
let currentDate = null; // the trading date currently loaded (to restore the picker on a cancelled nav)
const noteCache = new Map(); // opportunity_id -> loaded/saved review, so re-opening is instant

// Whether the engine layer is shown (on-chart overlay + the readout badge). Default ON; the setting
// persists across opportunities so the trader can park it off and draw unbiased.
let engineOn = true;

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
// Reviewer verdict (#155): "no trigger" means this wasn't a tradeable setup — distinct from the
// engine's `triggered:false` (a valid setup that never reached entry). When set, the engine's
// entry/stop context lines are dropped, the drawing toolbar is disabled, and no annotations apply.
let noTrigger = false;
let drag = null; // in-flight drag of a placed level (UX #152): { kind, field, edge } or null
const DRAG_HIT_PX = 16; // touch-friendly grab radius (CSS px) around a line/edge

// --- Chart ---------------------------------------------------------------------------------

// The chart view is built once, on first use, and reused for every opportunity thereafter.
function ensureView() {
  if (view !== undefined) return view;
  view = createChartView(el("rv-chart"));
  if (view) {
    view.onClick(onChartClick);
    view.setEngineOn(engineOn);
  }
  return view;
}

function buildChart(c) {
  const v = ensureView();
  if (!v) return;
  v.draw(c);
  v.setEngineLevels(!noTrigger);
  renderReadout(c);
  renderEngineDetail(c);
  updateEngineToggleUI();
}

function renderReadout(c) {
  if (!c) return;
  el("rv-readout").innerHTML = readoutHtml(c, { engineOn, noTrigger });
}

function clearChart(message) {
  if (view) view.clear();
  el("rv-readout").innerHTML = `<span class="muted">${esc(message)}</span>`;
}

// Draw whichever opportunity the symbol dropdown currently points at.
function drawSelected() {
  const list = (chartsData && chartsData.charts) || [];
  const c = list.find((x) => x.opportunity_id === el("rv-symbol").value) || list[0];
  if (!ensureView()) {
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
  chartsData = await chartsFor(date);
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
  if (!view) return;
  if (bandPending) {
    view.setPending(bandPending.t0, bandPending.mode === "pole" ? MK.poleEdge : MK.consEdge);
  } else {
    view.setPending(null, null);
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
  if (!armed || !currentOpp || !view || !param.point) return;
  const price = view.priceAt(param.point.y);
  const time = view.timeAt(param.point.x);
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
  if (view) {
    view.setLine("ann-entry",
      ann.entry == null ? null : {
        price: ann.entry, color: MK.annEntry, lineStyle: 0, lineWidth: 2,
        axisLabelVisible: true, title: "my entry",
      });
    view.setLine("ann-stop",
      ann.stop == null ? null : {
        price: ann.stop, color: MK.annStop, lineStyle: 0, lineWidth: 2,
        axisLabelVisible: true, title: "my stop",
      });
    const bands = [];
    if (ann.pole)
      bands.push({ t0: ann.pole.t0, t1: ann.pole.t1, color: MK.poleBand, edge: MK.poleEdge });
    if (ann.consolidation)
      bands.push({
        t0: ann.consolidation.t0, t1: ann.consolidation.t1,
        color: MK.consBand, edge: MK.consEdge,
      });
    view.setBands(bands);
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
  if (view) view.setEngineLevels(!noTrigger);
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
  if (!view) return null;
  const cands = [];
  for (const field of ["entry", "stop"]) {
    if (ann[field] == null) continue;
    const yc = view.yOf(ann[field]);
    if (yc != null) cands.push({ kind: "price", field, dist: Math.abs(yc - y) });
  }
  for (const [field, band] of [["pole", ann.pole], ["cons", ann.consolidation]]) {
    if (!band) continue;
    for (const edge of ["t0", "t1"]) {
      const xc = view.xOf(band[edge]);
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
  view.setInteraction(false); // freeze pan/zoom while dragging
  const chartEl = el("rv-chart");
  if (chartEl.setPointerCapture) chartEl.setPointerCapture(e.pointerId);
  chartEl.classList.add("rv-dragging");
  e.preventDefault();
}

function onPointerMove(e) {
  if (!drag) return;
  const { x, y } = chartXY(e);
  if (drag.kind === "price") {
    const p = view.priceAt(y);
    if (p != null) ann[drag.field] = round2(p);
  } else {
    const t = view.timeAt(x);
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
  view.setInteraction(true);
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
function toggleEngine() {
  engineOn = !engineOn;
  if (view) view.setEngineOn(engineOn);
  updateEngineToggleUI();
  renderReadout(currentOpp); // show/hide the badge
}
function updateEngineToggleUI() {
  const btn = el("rv-engine-toggle");
  if (!btn) return;
  btn.classList.toggle("armed", engineOn);
  btn.setAttribute("aria-pressed", engineOn ? "true" : "false");
}

function renderEngineDetail(c) {
  const box = el("rv-engine-detail");
  const title = el("rv-engine-title");
  if (!box) return;
  if (title) title.textContent = c ? `Engine · ${optionLabel(c)}` : "Engine";
  box.innerHTML = engineDetailHtml(c);
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
  const n = newsCount(c);
  btn.textContent = `News ${n}`;
  btn.disabled = n === 0;
}
function openNewsSheet() {
  el("rv-news-title").textContent = currentOpp ? `News · ${currentOpp.symbol}` : "News";
  el("rv-news-list").innerHTML = newsHtml(currentOpp);
  el("rv-scrim").hidden = false;
  el("rv-news-sheet").classList.add("open");
  el("rv-news-sheet").setAttribute("aria-hidden", "false");
}
function closeNewsSheet() {
  el("rv-scrim").hidden = true;
  el("rv-news-sheet").classList.remove("open");
  el("rv-news-sheet").setAttribute("aria-hidden", "true");
}

// --- Deep links ----------------------------------------------------------------------------
// Results (#224) links `review.html?date=<date>&oid=<opportunity_id>`; the Portfolio's trade and
// skipped rows link on the trade's `seg_id`, which IS that id. `sym` (+ optional `run`) is accepted
// too — it was what the portfolio sent before #478, and review.js silently ignored it, landing the
// reader on the right day but the wrong opportunity. Unknown/absent params fall through to the
// default (newest date, first opportunity), so plain `review.html` is unchanged.
function wantedOid(params, list) {
  const direct = params.get("oid") || params.get("seg_id");
  if (direct) return direct;
  const sym = params.get("sym");
  if (!sym) return null;
  const matches = list.filter((c) => String(c.symbol).toUpperCase() === sym.toUpperCase());
  if (!matches.length) return null;
  const run = params.get("run");
  const exact = run == null ? null : matches.find((c) => String(c.run) === String(run));
  return (exact || matches[0]).opportunity_id;
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
    dateSel.innerHTML = "<option>—</option>";
    clearChart("No review data published yet.");
    return;
  }
  // index.json dates are already sorted newest-first (#141).
  dateSel.innerHTML = dates
    .map((d) => `<option value="${esc(d.date)}">${esc(dateLabel(d.date))}</option>`)
    .join("");
  const params = new URLSearchParams(location.search);
  const wantDate = params.get("date");
  if (wantDate && dates.some((d) => d.date === wantDate)) dateSel.value = wantDate;
  await loadDate(dateSel.value);
  // `oid` carries a `#` for multi-run ids, so it arrives URL-decoded.
  const oid = wantedOid(params, (chartsData && chartsData.charts) || []);
  if (oid) {
    const sym = el("rv-symbol");
    if ([...sym.options].some((o) => o.value === oid)) {
      sym.value = oid;
      drawSelected();
    }
  }
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
// once here (the container is stable across opportunities); handlers read the live chart view.
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
