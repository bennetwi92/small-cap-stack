// Opportunity inspector (#478): the review chart as a *mountable component* rather than a page.
//
// Everything here used to live inside review.js, welded to that page's `rv-*` element ids and a
// pile of module-level singletons — which is why Results and Portfolio had to navigate away to
// show a chart. Split into two halves, both host-agnostic:
//
//   * `createChartView(container)` — the stateful half. Owns one LightweightCharts instance,
//     the candles/volume series, a keyed price-line manager, the trader's annotation bands and
//     the engine-v2 detection overlay. Draws a new opportunity by swapping series DATA, never by
//     tearing the chart down and rebuilding it (review.js used to `remove()` per opportunity,
//     which is invisible at one-a-minute and jarring when a grid selection is arrow-keyed).
//   * `readoutHtml` / `engineDetailHtml` / `newsHtml` — pure string builders, so each host places
//     the same content in whatever chrome it has (a bottom strip, a drawer, a dock panel).
//
// The annotation *editor* (tap-to-place, drag-to-refine, save) is deliberately NOT here: it stays
// on the review workbench, which is the only surface that owns an unsaved-changes lifecycle.

import { REPO, fetchJson } from "./data.js";
import { esc, etClockSec, fmtShares } from "./fmt.js";

export const round2 = (x) => Math.round(x * 100) / 100;

/* ---------- palette ----------
   Chart colours, shared so the trader's read and the engine's read never drift apart between
   pages. Three families: the price series, the trader's own annotations (solid), and the
   engine-v2 overlay (faint fills + caps), kept visually distinct on purpose. */
export const MK = {
  up: "#1a7f37", down: "#c0362c",
  entry: "#2f81f7", stop: "#c0362c", firstHit: "#8957e5", maxR: "#d4a72c",
  volUp: "rgba(26,127,55,0.5)", volDown: "rgba(192,54,44,0.5)",
  // trader's annotations (#144) — solid lines so they read distinctly from the engine's dashed ones.
  annEntry: "#3fb950", annStop: "#db6d28",
  poleBand: "rgba(137,87,229,0.18)", consBand: "rgba(212,167,44,0.20)",
  // opaque band-edge colours: bright vertical grab-handles at each band boundary so a placed band
  // reads clearly and its edges are visibly draggable (UX #152). Also used for the pending-edge line.
  poleEdge: "rgba(137,87,229,0.95)", consEdge: "rgba(212,167,44,0.98)",
  // engine-v2 overlay (#216) — the DETECTOR's read: faint full-height fills + a solid top cap bar
  // per band (vs the trader's heavier translucent fills), and coloured H/L/E token letters.
  engPole: "#3584e4", engCons: "#e5a50a", engBase: "#3584e4", engPeak: "#e5a50a",
  engPoleFill: "rgba(53,132,228,0.12)", engConsFill: "rgba(229,165,10,0.14)",
  engPrior: "rgba(139,148,161,0.10)", engPriorLbl: "#8b949e",
  tokH: "#3fb950", tokL: "#f85149", tokE: "#8b949e",
};

/* ---------- published chart data ---------- */

// One in-session cache of `charts/<date>.json` across every host on the page. The payloads are
// 1.5–3 MB each (full-day bars for every opportunity), so a dock that redraws on each row click
// must never refetch: Results already holds every day in memory and passes chart objects straight
// in, while Portfolio pulls a date the first time a trade on it is selected.
const _payloads = new Map(); // "<source>|<date>" -> Promise<payload|null>

// Reconstructed sessions (#488) live in their own directory rather than under a flag on the live
// one — see dashboard_recon.py for why. `source` is the provenance the caller already holds: the
// Portfolio row carries it on the trade (`t.source`), Results carries it on the row it built from
// `recon_index.json`. Defaulting to "live" keeps every existing call site unchanged.
export const chartsUrl = (date, source = "live") =>
  source === "recon" ? `charts/recon/${date}.json` : `charts/${date}.json`;

export function chartsFor(date, source = "live") {
  const key = `${source}|${date}`;
  if (!_payloads.has(key)) {
    // Evict on rejection rather than memoising it (#509). `fetchJson` already answers `null` for a
    // missing or unparsable file, so the only way this rejects is a transport failure — offline,
    // DNS, CORS — which is exactly the transient case that must not be cached. Caching it made one
    // dropped request permanent for that date: every later open of the dock re-awaited the same
    // rejected promise, and both hosts await it inside an unguarded `async` after painting
    // "loading…", so the panel sat on that word until a reload.
    //
    // Resolving to `null` rather than re-throwing keeps the callers' contract unchanged (they
    // already handle a null payload) while making the *next* attempt a real retry.
    const pending = fetchJson(chartsUrl(date, source)).catch(() => {
      // Identity-guarded: evict only the entry THIS call created. An unconditional delete lets a
      // slow rejection throw away a newer entry — a Refresh's in-flight refetch, and for reviews
      // the post-save seed below.
      if (_payloads.get(key) === pending) _payloads.delete(key);
      return null;
    });
    _payloads.set(key, pending);
  }
  return _payloads.get(key);
}

// Drop the memo so the next read goes back to the branch — what a page's Refresh control means.
// (`data.js` already cache-busts each request; the memo is the only thing that can go stale.)
export function clearChartCache() {
  _payloads.clear();
}

// The opportunity with this id within a date's payload, or null. `opportunity_id` is
// "<date>:<SYMBOL>", suffixed "#<run>" when the symbol ran more than once that day.
export function findChart(payload, oid) {
  const list = (payload && payload.charts) || [];
  return list.find((c) => c.opportunity_id === oid) || null;
}

// Compact "SYMBOL #run · 2.3R" label, used by every picker and dock header.
export function optionLabel(c) {
  if (!c) return "—";
  const label = c.run_count > 1 ? `${c.symbol} #${c.run}` : c.symbol;
  const tag = c.triggered
    ? c.stopped_out
      ? " · stopped"
      : ` · ${c.max_r ?? "?"}R`
    : " · no trigger";
  return label + tag;
}

/* ---------- readout strip ---------- */

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

// The engine verdict chip that leads the readout: PASS/REJECT · score · cycle (or "no setup"),
// tappable to open the engine detail. Empty when the layer is off or the chart has no engine block
// (a chart published before #216). Kept in sync with the on-chart overlay via the same toggle.
export function engineBadgeHtml(c, engineOn = true) {
  const e = c && c.engine;
  if (!engineOn || !e) return "";
  if (!e.setup)
    return '<span class="mk rv-eng-badge muted" title="engine: no v2 setup formed">v2 no setup</span>';
  const verdict = e.passed ? "PASS" : "REJECT";
  const cyc = e.cycle_num != null ? ` · cyc ${e.cycle_num}${e.exhausted ? "⚠" : ""}` : "";
  const score = e.score != null ? ` · ${round2(e.score)}` : "";
  return (
    `<span class="mk rv-eng-badge rv-eng-${verdict.toLowerCase()}"` +
    ' title="tap for engine gates + score">' +
    `v2 ${verdict}${score}${cyc}</span>`
  );
}

// The one-line status for an opportunity: engine verdict, entry/stop/Max R, recorded float and the
// trigger bar's volume. `noTrigger` is the REVIEWER's verdict (#155) — this wasn't a tradeable setup
// at all, distinct from the engine's `triggered:false` — and collapses the levels, which then don't
// apply. Hosts that can't set a verdict simply never pass it.
export function readoutHtml(c, { engineOn = true, noTrigger = false } = {}) {
  if (!c) return '<span class="muted">—</span>';
  const context = floatChip(c) + volChip(c); // shown in both states
  if (noTrigger) {
    return (
      engineBadgeHtml(c, engineOn) +
      `<span class="mk" style="color:${MK.stop}">no trigger</span>` +
      '<span class="muted">entry / stop N/A</span>' +
      context
    );
  }
  return (
    engineBadgeHtml(c, engineOn) +
    `<span class="mk" style="color:${MK.entry}">entry ${c.levels.entry ?? "—"}</span>` +
    `<span class="mk" style="color:${MK.stop}">stop ${c.levels.stop ?? "—"}</span>` +
    `<span class="mk" style="color:${MK.maxR}">Max R ${c.max_r != null ? c.max_r + "R" : "—"}</span>` +
    (c.triggered
      ? c.stopped_out
        ? '<span class="muted">stopped out</span>'
        : ""
      : '<span class="muted">no trigger</span>') +
    context
  );
}

/* ---------- engine detail ---------- */

// The detector's verdict, per-gate reasons, score contributions and cycle/exhaustion context — the
// explainable ranking (#182, folded into #216) behind the on-chart overlay.
export function engineDetailHtml(c) {
  const e = c && c.engine;
  if (!e)
    return '<p class="muted">No engine data for this opportunity (chart predates the overlay).</p>';
  if (!e.setup)
    return '<p class="muted">No v2 setup formed — the tokeniser found no pole into a consolidation.</p>';
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
  return (
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
      : "")
  );
}

/* ---------- the trader's saved review (#481) ----------
   The workbench writes one JSON per opportunity to the `review-data` branch: a note, the
   "no trigger" verdict, and the pole/consolidation/entry/stop the trader drew by hand. Public
   branch, so a plain raw fetch reads it — no token, which is why every host can show it and only
   the workbench can write it. A 404 is the ordinary case ("not reviewed yet"), cached as such so
   an unreviewed opportunity isn't refetched every time you step past it. */

export const REVIEW_BRANCH = "review-data";

// `:` and `#` are illegal-ish in paths and ids; map both to `_`
// (e.g. 2026-07-01:AHMA#2 -> reviews/2026-07-01_AHMA_2.json).
export const sanitizeOid = (oid) => String(oid).replace(/[:#]/g, "_");
export const reviewPath = (oid) => `reviews/${sanitizeOid(oid)}.json`;

const _reviews = new Map(); // opportunity_id -> Promise<review|null>

async function _fetchReview(oid) {
  const url =
    `https://raw.githubusercontent.com/${REPO}/${REVIEW_BRANCH}/${reviewPath(oid)}?t=${Date.now()}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) return null; // 404 = never reviewed; anything else, treat the same and stay quiet
  try {
    return await res.json();
  } catch {
    return null; // unparsable review file — same answer as "none", and cacheable (cf. fetchJson)
  }
}

export function reviewFor(oid) {
  if (!_reviews.has(oid)) {
    // Same eviction-on-rejection as `chartsFor` (#509), for the same reason. This one already
    // caught — but caching the caught failure still made a dropped request permanent, so a
    // transient blip hid an opportunity's saved annotations until a reload. A *legitimate* "no
    // review saved" resolves null without rejecting, and is still cached, which is the point.
    const pending = _fetchReview(oid).catch(() => {
      // Identity-guarded — see `chartsFor`. Without it, a stalled fetch that rejects AFTER the
      // trader saves would delete `cacheReview`'s seed, and the note they just wrote would read
      // back as absent until the branch propagated.
      if (_reviews.get(oid) === pending) _reviews.delete(oid);
      return null;
    });
    _reviews.set(oid, pending);
  }
  return _reviews.get(oid);
}

// Seed the cache after a save, so the workbench and any dock agree without a refetch.
export function cacheReview(oid, review) {
  _reviews.set(oid, Promise.resolve(review));
}

// Was this opportunity actually reviewed? A verdict, a note or a drawn level all count; the
// workbench writes `{note: ""}` into its own cache for a 404, which does not.
export function hasReview(r) {
  if (!r) return false;
  if (r.no_trigger) return true;
  if (r.note && r.note.trim()) return true;
  const a = r.annotations || {};
  return !!(a.pole || a.consolidation || a.entry != null || a.stop != null);
}

// The saved review, read-only: what the trader concluded, next to what the engine did.
export function reviewHtml(r) {
  if (!hasReview(r))
    return '<p class="muted">Not reviewed yet — open the review workbench to annotate this one.</p>';
  const a = r.annotations || {};
  const bits = [];
  if (a.entry != null) bits.push(`<span class="mk" style="color:${MK.annEntry}">entry ${a.entry}</span>`);
  if (a.stop != null) bits.push(`<span class="mk" style="color:${MK.annStop}">stop ${a.stop}</span>`);
  if (a.max_r != null) bits.push(`<span class="mk" style="color:${MK.maxR}">${a.max_r}R</span>`);
  const when = r.updated_utc ? `<span class="muted">${esc(String(r.updated_utc).slice(0, 10))}</span>` : "";
  return (
    '<div class="rv-eng-head">' +
    (r.no_trigger
      ? `<span class="rv-eng-badge rv-eng-reject">NO TRIGGER</span>`
      : '<span class="rv-eng-badge rv-eng-pass">REVIEWED</span>') +
    bits.join("") +
    when +
    "</div>" +
    (r.note && r.note.trim()
      ? `<p class="rv-note-read">${esc(r.note)}</p>`
      : '<p class="muted">No note.</p>')
  );
}

/* ---------- news ---------- */

export const newsCount = (c) => (c && c.news && c.news.length) || 0;

// Headlines captured when the scanner triggered (#109), so the catalyst is on hand while reviewing.
export function newsHtml(c) {
  const items = (c && c.news) || [];
  if (!items.length)
    return '<p class="muted rv-news-empty">No news captured for this opportunity.</p>';
  return items
    .map((n) => {
      const when = n.ts != null ? `${etClockSec(n.ts)} ET` : "undated";
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

/* ---------- translucent time-range bands (a Lightweight-Charts v4 series primitive) ----------
   Full-height rectangles spanning [t0, t1] on the time scale (pole = purple, consolidation =
   amber). Coordinates are recomputed on every pan/zoom via updateAllViews() → paneView.update(). */

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

/* ---------- engine-v2 detection overlay (#216) ----------
   The detector's read of the SAME full-day series the chart draws, published in charts/<date>.json's
   `engine` block (charts.py::_engine_block): per-bar H/L/E tokens, the pole/consolidation segment,
   the contiguous prior-cycle (exhaustion) run, gates/score and cycle context. Two primitives share
   one state object — a primitive has a single z-order, so the readable text can't share a layer
   with the translucent fills. Degrades to nothing when a chart predates the engine block. */

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

class EnginePaneView {
  constructor(source) {
    this._source = source;
    this._items = null;
  }
  update() {
    const src = this._source;
    const chart = src._chart;
    const state = src._state;
    if (!chart || !state.on || !state.data) {
      this._items = null;
      return;
    }
    const ts = chart.timeScale();
    const X = (t) => (t == null ? null : ts.timeToCoordinate(t));
    // No setup formed → seg is null, so only the H/L/E token row draws (bands/base/peak require a
    // segment), mirroring the spike's "no v2 setup" chart which still shows the token walk.
    const seg = state.data.segment || null;
    this._items = {
      pole: seg ? { x1: X(seg.base_t), x2: X(seg.peak_t) } : null,
      cons: seg ? { x1: X(seg.peak_t), x2: X(seg.cons_end_t) } : null,
      priors: (state.data.prior_cycles || []).map((c) => ({ x1: X(c.t0), x2: X(c.t1), n: c.n })),
      tokens: (state.data.tokens || []).map((tk) => ({ x: X(tk.t), tok: tk.tok })),
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

class EngineLayer {
  constructor(role, state) {
    this._role = role; // 'bands' | 'marks'
    this._state = state; // { on, data } — shared with the owning chart view
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

/* ---------- the chart view ---------- */

// Mount a chart into `container`. Returns null when the charting library failed to load, so the
// host can say so in its own chrome rather than rendering an empty box.
//
// The instance is built ONCE and reused for every opportunity: `draw()` swaps series data, resets
// the price lines and re-projects the overlay. Nothing else on the page should hold a reference to
// the underlying series — the returned handle exposes everything the annotation editor needs.
export function createChartView(container) {
  const LC = window.LightweightCharts;
  if (!LC || !container) return null;

  const api = LC.createChart(container, {
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
      tickMarkFormatter: (t) => etClockSec(t),
    },
    localization: { timeFormatter: (t) => etClockSec(t) + " ET" },
  });

  const candles = api.addCandlestickSeries({
    upColor: MK.up, downColor: MK.down,
    borderUpColor: MK.up, borderDownColor: MK.down,
    wickUpColor: MK.up, wickDownColor: MK.down,
  });
  // Volume histogram on its own scale in the bottom ~20%, coloured by candle direction. Created
  // unconditionally (an opportunity whose bars carry no volume just gets an empty series) so the
  // instance stays stable across draws.
  const volume = api.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "vol",
    lastValueVisible: false,
    priceLineVisible: false,
  });
  api.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

  const bandPrim = new BandPrimitive();
  candles.attachPrimitive(bandPrim);
  const engState = { on: true, data: null };
  const engBands = new EngineLayer("bands", engState);
  const engMarks = new EngineLayer("marks", engState);
  candles.attachPrimitive(engBands);
  candles.attachPrimitive(engMarks);

  // Keyed price lines, so a host never has to keep raw handles alive across draws: setting the same
  // key twice replaces the line, and `draw()` clears the lot. Keys in use: "eng-entry"/"eng-stop"
  // (the detector's dashed context levels) and "ann-entry"/"ann-stop" (the trader's own).
  const lines = new Map();
  function clearLine(key) {
    const h = lines.get(key);
    if (!h) return;
    candles.removePriceLine(h);
    lines.delete(key);
  }
  function clearLines() {
    for (const key of [...lines.keys()]) clearLine(key);
  }
  function setLine(key, opts) {
    clearLine(key);
    if (opts && opts.price != null) lines.set(key, candles.createPriceLine(opts));
  }

  const clickCbs = [];
  api.subscribeClick((param) => {
    for (const cb of clickCbs) cb(param);
  });

  let current = null;

  function draw(c) {
    current = c;
    engState.data = (c && c.engine) || null;
    clearLines();
    bandPrim.setBands([]);
    bandPrim.setPending(null, null);
    if (!c) {
      candles.setData([]);
      volume.setData([]);
      candles.setMarkers([]);
      engBands.refresh();
      engMarks.refresh();
      return;
    }
    candles.setData(
      c.bars.map((b) => ({ time: b.t, open: b.o, high: b.h, low: b.l, close: b.c })),
    );
    volume.setData(
      c.bars.some((b) => b.v != null)
        ? c.bars.map((b) => ({ time: b.t, value: b.v ?? 0, color: b.c >= b.o ? MK.volUp : MK.volDown }))
        : [],
    );

    // Markers carry epoch timestamps (#141) so they land on the right bars of the full-day series
    // even though its indices differ from the run window's.
    const m = c.markers || {};
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
    candles.setMarkers(markers);

    api.timeScale().fitContent();
    engBands.refresh();
    engMarks.refresh();
  }

  // The engine's dashed entry/stop context lines, drawn even when the setup never triggered (they
  // say where a fill would have been). A "no trigger" verdict strips them; passing `false` restores
  // nothing, which is how the review page toggles them.
  function setEngineLevels(on) {
    const c = current;
    if (!on || !c || !c.levels) {
      clearLine("eng-entry");
      clearLine("eng-stop");
      return;
    }
    setLine("eng-entry", {
      price: c.levels.entry, color: MK.entry, lineStyle: 2, lineWidth: 1,
      axisLabelVisible: true, title: "entry",
    });
    setLine("eng-stop", {
      price: c.levels.stop, color: MK.stop, lineStyle: 2, lineWidth: 1,
      axisLabelVisible: true, title: "stop",
    });
  }

  // The TRADER's read — pole/consolidation bands plus "my entry"/"my stop" — drawn from an
  // annotations block. Solid lines and heavier fills, so it never reads as the engine's dashed
  // context levels beside it. The workbench drives this live while drawing; a dock passes what
  // was saved (#481). Null/absent fields simply clear.
  function setAnnotations(ann) {
    const a = ann || {};
    setLine("ann-entry",
      a.entry == null ? null : {
        price: a.entry, color: MK.annEntry, lineStyle: 0, lineWidth: 2,
        axisLabelVisible: true, title: "my entry",
      });
    setLine("ann-stop",
      a.stop == null ? null : {
        price: a.stop, color: MK.annStop, lineStyle: 0, lineWidth: 2,
        axisLabelVisible: true, title: "my stop",
      });
    const bands = [];
    if (a.pole) bands.push({ t0: a.pole.t0, t1: a.pole.t1, color: MK.poleBand, edge: MK.poleEdge });
    if (a.consolidation)
      bands.push({
        t0: a.consolidation.t0, t1: a.consolidation.t1,
        color: MK.consBand, edge: MK.consEdge,
      });
    bandPrim.setBands(bands);
  }

  const ts = () => api.timeScale();

  return {
    draw,
    clear: () => draw(null),
    current: () => current,

    // engine overlay
    setEngineOn(on) {
      engState.on = on;
      engBands.refresh();
      engMarks.refresh();
    },
    setEngineLevels,

    // annotation surface
    setAnnotations,
    setPending: (time, color) => bandPrim.setPending(time, color),

    // input
    onClick: (cb) => clickCbs.push(cb),
    // Freeze pan/zoom for the duration of a drag, so refining a level doesn't scroll the view.
    setInteraction: (on) => api.applyOptions({ handleScroll: on, handleScale: on }),

    // coordinate helpers — the editor works in container CSS px and needs both directions
    priceAt: (y) => candles.coordinateToPrice(y),
    yOf: (price) => candles.priceToCoordinate(price),
    timeAt: (x) => ts().coordinateToTime(x),
    xOf: (time) => ts().timeToCoordinate(time),
  };
}
