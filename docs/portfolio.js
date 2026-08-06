// Virtual-portfolio tracker (#230, cockpit #290): a *pre-shadow* paper book
// over the tracker's own data, on the shared cockpit chrome. All the trading
// logic (select → size → simulate-exit) lives in the tested Python package;
// this page just picks a book from the published portfolio.json and renders
// its equity curve / stats / trade log.

import "./js/nav.js";
import { createOptionsBar } from "./js/options-bar.js";
import { setStatusPage } from "./js/status-bar.js";
import { fetchJson } from "./js/data.js";
import { esc, etClockIso, fmtPct, fmtShares, rRampClass } from "./js/fmt.js";

const el = (id) => document.getElementById(id);

// Panel explainers. On a wide monitor cockpit.css clamps these to three lines so the
// rails fit one screen (#397); the full text always lives in the tooltip, and a click
// opens the note in place. Below that breakpoint the clamp doesn't apply and this is
// an ordinary innerHTML write. Takes an id or an element — the payout empty-state note
// is built inside the cash-flow markup, so it's adopted rather than written.
function setNote(target, html) {
  const n = typeof target === "string" ? el(target) : target;
  if (html != null) n.innerHTML = html;
  n.title = n.textContent.trim();
  n.classList.add("pf-clamp");
  n.classList.remove("open");
}
document.addEventListener("click", (e) => {
  const n = e.target.closest(".pf-clamp");
  if (n) n.classList.toggle("open");
});
// Cockpit tokens (cockpit.css): win/loss green+red stay reserved for P&L, so the two
// state charts wear the neutral accents — neither a target nor a risk rung is a win.
const MK = { up: "#3ec07e", down: "#f06673", flat: "#9aa0b5", line: "#4fe3ef", gold: "#e3b452" };

const fmtUsd = (x) => (x == null || !isFinite(x) ? "—" : "$" + Number(x).toFixed(2));
const fmtGbp = (x) => (x == null || !isFinite(x) ? "—" : "£" + Number(x).toFixed(2));
const fmtR = (x) => (x == null || !isFinite(x) ? "—" : (x >= 0 ? "+" : "") + Number(x).toFixed(2) + "R");
const fmtInt = (x) => (x == null || !isFinite(x) ? "—" : String(x));
const pct = (r) => (r * 100).toFixed(2).replace(/\.?0+$/, "") + "%"; // 0.025 -> "2.5%"

let PAYLOAD = null; // the whole portfolio.json
let BOOK = "adaptive"; // selected book key
let VIEW = "book"; // "book" (the record) | "projection" (the forward Monte-Carlo)
let SCOPE = "live"; // "live" (captured only) | "all" (+ reconstructed history, #430)

// Which set of books the scope selects. `books` is always the live-only record; `books_all` exists
// only once the harvest (#430) has landed reconstructed days, so the selector is hidden until then
// and every payload published before it simply has no second scope to offer.
const hasRecon = () => !!(PAYLOAD && PAYLOAD.books_all);
const booksFor = () => (SCOPE === "all" && hasRecon() ? PAYLOAD.books_all : PAYLOAD.books);

/* ---------- options bar: view + book selector + refresh; meta line under ··· ---------- */

function buildOptbar() {
  const books = PAYLOAD
    ? ["adaptive", ...PAYLOAD.targets].map((k) => ({
        value: k,
        label: k === "adaptive" ? "Adaptive" : `${k}R`,
      }))
    : [{ value: "adaptive", label: "Adaptive" }];
  createOptionsBar("optbar", {
    primary: [
      {
        // NOT "pf-view": createOptionsBar stamps this id onto the control, the book region
        // already owns `#pf-view`, and getElementById would then hand `render()` the segmented
        // button instead of the region it means to hide.
        type: "seg",
        id: "pf-viewsel",
        label: "VIEW",
        value: VIEW,
        options: [
          { value: "book", label: "Book" },
          { value: "projection", label: "Projection" },
        ],
      },
      // The book selector drives BOTH views — every book carries its own projection, since
      // each has its own return distribution to resample.
      { type: "seg", id: "pf-book", label: "BOOK", value: BOOK, options: books },
      // Provenance scope (#430). Only offered once the harvest has landed reconstructed days.
      ...(hasRecon()
        ? [
            {
              type: "seg",
              id: "pf-scope",
              label: "DATA",
              value: SCOPE,
              options: [
                { value: "live", label: "Live", title: "Days the tracker captured in real time" },
                { value: "all", label: "+ History", title: "Live days plus reconstructed history" },
              ],
            },
          ]
        : []),
      { type: "btn", id: "pf-refresh", label: "Refresh", title: "Refresh now" },
    ],
    extra: [{ type: "note", id: "pf-meta", value: "loading…" }],
    onChange: (id, value) => {
      if (id === "pf-refresh") return load();
      if (id === "pf-viewsel") VIEW = value;
      if (id === "pf-book") BOOK = value;
      if (id === "pf-scope") SCOPE = value;
      render();
    },
  });
}

/* ---------- Chart sizing ----------
   Charts are drawn at their container's pixel size — viewBox units ARE CSS px — rather
   than at a fixed 720×240 the browser then rescales. Two reasons (#397): the old fixed
   box scaled to ~0.55 in a 400px rail, shrinking the 11px axis labels to ~6px; and a
   box-sized chart can spend a tall monitor's spare height on the plot instead of leaving
   the rail half empty. Where no panel has spare height to give — phone, laptop, the
   single-rail fixed book — height comes from the aspect instead. */
// `maxAspect` is the height ceiling as a multiple of the width. It defaults to 1 (square) for the
// linear curves, where a tall narrow plot exaggerates every wobble into a crash; the projection's
// fan is log-scaled, so vertical distance is ratio and extra height buys real resolution instead
// of drama — it asks for more.
function chartBox(wrap, maxAspect = 1) {
  const W = Math.max(260, Math.round(wrap.clientWidth) || 640);
  // 3:1 was the old ratio of a 720-unit box the browser then squeezed into ~380px on a
  // phone — which shrank the axis type to ~6px too. At 1:1 units the labels are their
  // real 11px, so the plot needs a taller slice to keep them off each other.
  const aspect = Math.min(260, Math.max(150, Math.round(W / 2.2)));
  // cockpit.css makes the wrap `position:relative` in exactly the case where its height
  // is flex-driven and the SVG inside is out of flow — which is what makes clientHeight
  // safe to read. Anywhere else it's just the chart we drew last time, and measuring our
  // own output would ratchet the plot taller on every redraw.
  if (getComputedStyle(wrap).position !== "relative") return { W, H: aspect };
  // Square is the ceiling: a rail with height to spare would otherwise draw a 320×560
  // equity curve, and a plot that much taller than it is wide reads every wobble as a
  // crash. Past the cap the chart just centres in its panel.
  const avail = Math.round(wrap.clientHeight) || aspect;
  return { W, H: Math.max(110, Math.min(avail, W * maxAspect)) };
}

/* ---------- Equity curve (inline SVG) ---------- */

function equitySvg(curve, start, cashFlows, box) {
  const pts = [{ date: null, equity: start }, ...curve]; // anchor at the opening balance
  if (pts.length < 2) return '<p class="muted">Not enough data to chart yet.</p>';
  const { W, H } = box;
  const PAD = 34;
  const ys = pts.map((p) => p.equity);
  const yMin = Math.min(start, ...ys), yMax = Math.max(start, ...ys);
  const span = yMax - yMin || 1;
  const x = (i) => PAD + (i * (W - 2 * PAD)) / (pts.length - 1);
  const y = (v) => H - PAD - ((v - yMin) / span) * (H - 2 * PAD);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(" ");
  const area = `${line} L${x(pts.length - 1).toFixed(1)},${y(yMin).toFixed(1)} L${x(0).toFixed(1)},${y(yMin).toFixed(1)} Z`;
  const end = pts[pts.length - 1].equity;
  const stroke = end >= start ? MK.up : MK.down;
  const baseY = y(start).toFixed(1);
  // Mark each quarterly withdrawal on the curve so its step-down reads as a payout, not a loss.
  const idxByDate = new Map(curve.map((p, i) => [p.date, i + 1])); // +1 for the start anchor
  const marks = (cashFlows || [])
    .filter((c) => c.kind === "withdrawal" && idxByDate.has(c.date))
    .map((c) => {
      const mx = x(idxByDate.get(c.date)).toFixed(1);
      return (
        `<line x1="${mx}" x2="${mx}" y1="${PAD}" y2="${H - PAD}" stroke="${MK.line}" stroke-dasharray="2 3" stroke-width="1" opacity="0.55"/>` +
        `<text x="${mx}" y="${(PAD - 4).toFixed(1)}" text-anchor="middle" class="pf-axis">↓£${Number(c.gbp).toFixed(0)}</text>`
      );
    })
    .join("");
  return (
    `<svg viewBox="0 0 ${W} ${H}" class="pf-chart" role="img" aria-label="Equity curve">` +
    `<line x1="${PAD}" x2="${W - PAD}" y1="${baseY}" y2="${baseY}" stroke="${MK.flat}" stroke-dasharray="3 3" stroke-width="1"/>` +
    // Left-anchored: the end-of-curve label sits at the right, and a book that finishes
    // near its opening balance would otherwise print the two on top of each other.
    `<text x="${PAD}" y="${(+baseY - 4).toFixed(1)}" class="pf-axis">start ${fmtUsd(start)}</text>` +
    marks +
    `<path d="${area}" fill="${stroke}" opacity="0.10"/>` +
    `<path d="${line}" fill="none" stroke="${stroke}" stroke-width="2"/>` +
    `<circle cx="${x(pts.length - 1).toFixed(1)}" cy="${y(end).toFixed(1)}" r="3.5" fill="${stroke}"/>` +
    `<text x="${(x(pts.length - 1) - 6).toFixed(1)}" y="${(y(end) - 8).toFixed(1)}" text-anchor="end" class="pf-axis">${fmtUsd(end)}</text>` +
    `</svg>`
  );
}

/* ---------- Daily state curves: target + risk through time (inline SVG) ---------- */

// Target and risk are *daily state*: a value the book holds for a whole session and only ever
// changes between days. So they're drawn as **step** lines, not sloped ones — a slope would claim
// the target drifted through Tuesday, which it never does. Each day owns a slot of equal width,
// and the y-domain always spans the whole configured ladder so a move reads against what was
// available, not just against the days on screen. Same frame/typography as the equity curve.
function stepSvg(points, o, box) {
  if (!points.length) return '<p class="muted">Not enough data to chart yet.</p>';
  const { W, H } = box;
  const PAD = 40, BOT = 46; // BOT leaves room for the date axis
  const grid = o.grid || [];
  const dom = [...points.map((p) => p.value), ...grid.map((g) => g.v)];
  let lo = Math.min(...dom), hi = Math.max(...dom);
  if (hi === lo) {
    const p = Math.abs(hi) * 0.5 || 1; // a book with one constant value still needs a scale
    lo -= p;
    hi += p;
  }
  const room = (hi - lo) * 0.12; // headroom so the line never fuses with the frame
  lo -= room;
  hi += room;
  const w = (W - 2 * PAD) / points.length;
  const x0 = (i) => PAD + i * w;
  const x1 = (i) => PAD + (i + 1) * w;
  const y = (v) => H - BOT - ((v - lo) / (hi - lo)) * (H - PAD - BOT);

  const line = points
    .map((p, i) => {
      const yy = y(p.value).toFixed(1);
      return `${i ? "L" : "M"}${x0(i).toFixed(1)},${yy} L${x1(i).toFixed(1)},${yy}`;
    })
    .join(" ");
  const base = y(lo).toFixed(1);
  const area = `${line} L${x1(points.length - 1).toFixed(1)},${base} L${x0(0).toFixed(1)},${base} Z`;

  // Days the series is only *nominally* at its value — for the target chart, the ones where the
  // re-fit didn't run and the fallback stood in (#463). Overdrawn dashed and dimmed so a flat
  // stretch cannot be misread as a fit that kept choosing the same rung. Optional: a series with
  // no `dim` points draws nothing here.
  const dimmed = points
    .map((p, i) =>
      p.dim
        ? `M${x0(i).toFixed(1)},${y(p.value).toFixed(1)} L${x1(i).toFixed(1)},${y(p.value).toFixed(1)}`
        : ""
    )
    .filter(Boolean)
    .join(" ");
  const dimPath = dimmed
    ? `<path d="${dimmed}" fill="none" stroke="${MK.flat}" stroke-width="2" stroke-dasharray="4 3"/>`
    : "";

  // Recessive rules at each rung of the ladder, labelled in the axis gutter.
  const rules = grid
    .map((g) => {
      const gy = y(g.v).toFixed(1);
      return (
        `<line x1="${PAD}" x2="${W - PAD}" y1="${gy}" y2="${gy}" stroke="${MK.flat}" ` +
        `stroke-dasharray="3 3" stroke-width="1" opacity="0.3"/>` +
        `<text x="${PAD - 5}" y="${(+gy + 3.5).toFixed(1)}" text-anchor="end" class="pf-axis">${esc(g.label)}</text>`
      );
    })
    .join("");

  // Native per-day tooltips: a transparent hit slot per day, so hovering anywhere in a
  // column names the date and the value it held.
  const hits = points
    .map(
      (p, i) =>
        `<rect x="${x0(i).toFixed(1)}" y="${PAD}" width="${w.toFixed(1)}" height="${(H - BOT - PAD).toFixed(1)}" ` +
        `fill="transparent"><title>${esc(p.date)} · ${esc(o.fmt(p.value))}${p.note ? " · " + esc(p.note) : ""}</title></rect>`
    )
    .join("");

  const last = points[points.length - 1];
  const lastX = ((x0(points.length - 1) + x1(points.length - 1)) / 2).toFixed(1);
  const lastY = y(last.value).toFixed(1);
  const axisY = (H - BOT + 16).toFixed(1);
  return (
    `<svg viewBox="0 0 ${W} ${H}" class="pf-chart" role="img" aria-label="${esc(o.label)}">` +
    rules +
    `<path d="${area}" fill="${o.color}" opacity="0.10"/>` +
    `<path d="${line}" fill="none" stroke="${o.color}" stroke-width="2"/>` +
    dimPath +
    `<circle cx="${lastX}" cy="${lastY}" r="3.5" fill="${o.color}"/>` +
    `<text x="${lastX}" y="${(+lastY - 8).toFixed(1)}" text-anchor="end" class="pf-axis">${esc(o.fmt(last.value))}</text>` +
    `<text x="${PAD}" y="${axisY}" class="pf-axis">${esc(points[0].date)}</text>` +
    `<text x="${W - PAD}" y="${axisY}" text-anchor="end" class="pf-axis">${esc(last.date)}</text>` +
    hits +
    `</svg>`
  );
}

// The R multiple the book exits at, re-fit daily from the trailing window. Rules mark the
// candidate grid. Days the re-fit did NOT run are overdrawn dashed and dimmed (#463) — a flat
// stretch would otherwise read as "the fit kept choosing the same rung" when it can equally mean
// the fit never ran for want of samples, which is exactly what the live book did for 28 days.
function targetSvg(book, box) {
  const pts = (book.daily_targets || [])
    .filter((d) => d.target != null)
    .map((d) => ({
      date: d.date,
      value: d.target,
      dim: d.fitted === false,
      note: d.fitted == null ? "" : `${d.fitted ? "fitted" : "fallback"} (${d.n} trades)`,
    }));
  const grid = PAYLOAD.config.target_grid || [...new Set(pts.map((p) => p.value))].sort((a, b) => a - b);
  return stepSvg(
    pts,
    {
      color: MK.line,
      label: "Daily exit target, in R",
      fmt: (v) => Number(v).toFixed(1) + "R",
      grid: grid.map((v) => ({ v, label: Number(v).toFixed(1) + "R" })),
    },
    box
  );
}

// The kill-switch rung in force each day. Rules mark the ladder, including the 0% floor, so
// a stretch of sitting out is legible as a floor rather than as missing data.
function riskSvg(book, box) {
  const pts = (book.daily_risk || []).map((d) => ({ date: d.date, value: d.risk }));
  const ladder = PAYLOAD.config.risk_ladder || [];
  return stepSvg(
    pts,
    {
      color: MK.gold,
      label: "Daily risk per trade, as a share of equity",
      fmt: pct,
      grid: ladder.map((v) => ({ v, label: pct(v) })),
    },
    box
  );
}

/* ---------- Forward projection (bootstrap Monte-Carlo, computed in Python) ----------
   Everything below RENDERS `book.projection`; not one number is derived here. The tax and
   cost arithmetic behind the income ladder and the ramp lives in `portfolio/projection.py`
   where it is unit-tested — a second, untested copy in JavaScript in front of the one figure
   this view exists to produce is exactly the trade nobody should take. */

// Money the way this view needs it: no cents on four- and five-figure sums (they're the noise
// floor of a Monte-Carlo, not precision), cents below that where they still carry meaning.
const money = (sym) => (x, force) => {
  if (x == null || !isFinite(x)) return "—";
  const dp = force != null ? force : Math.abs(x) >= 1000 ? 0 : 2;
  return sym + Number(x).toLocaleString("en-GB", { minimumFractionDigits: dp, maximumFractionDigits: dp });
};
const usd = money("$");
const gbp = money("£");

// A compounding rate is not a percentage once it gets big: "+8,600%/yr" is unreadable and reads
// as a typo. Past 10× a year say it as a multiple, and past 1000× stop quoting a figure at all —
// precision there is a small-sample artifact (see `growth_implausible`), not information.
function fmtGrowth(g) {
  if (g == null || !isFinite(g)) return "—";
  if (g <= -0.999) return "wipeout";
  if (g >= 999) return "&gt;1000×/yr";
  if (g >= 9) return `${(1 + g).toFixed(1)}×/yr`;
  return `${g >= 0 ? "+" : ""}${(g * 100).toFixed(0)}%/yr`;
}

// Axis money: an equity axis has to survive $20 and $170,000,000 in the same chart, and a
// thousands-separated label at the top end simply runs off the left of the plot.
function compactMoney(sym, v) {
  const a = Math.abs(v);
  if (a >= 1e9) return sym + (v / 1e9).toFixed(a >= 1e10 ? 0 : 1).replace(/\.0$/, "") + "B";
  if (a >= 1e6) return sym + (v / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M";
  if (a >= 1e3) return sym + (v / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace(/\.0$/, "") + "k";
  return sym + Math.round(v);
}

// A near-breakeven book honestly needs ~690 years to reach the day rate. Printing "687.7 yr"
// dresses a "no" up as a plan and invites arithmetic on a number that is pure extrapolation —
// past a working lifetime the only true statement is that it doesn't get there.
const fmtYears = (y) =>
  y == null
    ? "—"
    : y === 0
      ? "already there"
      : y < 1
        ? `${Math.round(y * 12)} mo`
        : y > 50
          ? "beyond a lifetime"
          : `${y.toFixed(1)} yr`;

// A probability as plain odds. "1 in 3" beats "34%" for a thing you either sit through or don't.
function odds(p) {
  if (p == null || !isFinite(p)) return "—";
  if (p <= 0) return "never in this run";
  if (p >= 1) return "every run";
  return `${(p * 100).toFixed(0)}% of years`;
}

const isoDay = (s) => (s ? String(s).slice(0, 10) : "—");

// "Nice" 1/2/5×10^k gridline values inside a range — the same tick vocabulary a reader already
// has for money. Used on the log axis, where evenly-spaced ticks would land on 337, 1094, …
function niceTicks(lo, hi, max = 5) {
  const out = [];
  for (let k = Math.floor(Math.log10(lo)); k <= Math.ceil(Math.log10(hi)); k++) {
    for (const m of [1, 2, 5]) {
      const v = m * 10 ** k;
      if (v >= lo && v <= hi) out.push(v);
    }
  }
  // Thin evenly rather than truncating, so the labels still span the whole axis.
  const step = Math.ceil(out.length / max);
  return out.filter((_v, i) => i % step === 0);
}

/* --- Fan chart: the median path with two confidence bands ---
   ONE hue at graded opacity, not three colours: the bands are nested confidence, a sequential
   encoding, and painting them as separate categorical series would claim p25 and p95 are
   different *things* rather than the same thing at different certainty.

   The y-axis is LOGARITHMIC. A year of compounding spreads the 5th and 95th percentiles across
   one to two orders of magnitude, and on a linear axis the p95 tail sets the scale: the median —
   the line the reader actually came for — gets pressed into the bottom 5% of the plot as a flat
   smear, and a doubling looks identical to no change. Log is also the honest geometry for a
   compounding quantity, where equal vertical distance should mean equal *ratio*. */
function fanSvg(pj, box) {
  const days = pj.sample_days || [];
  if (days.length < 2) return '<p class="muted">Not enough data to project yet.</p>';
  const { W, H } = box;
  const PAD = 52, BOT = 30, RGT = 12;
  const b = pj.bands;
  const hi = Math.max(...b.p95, pj.start_equity) * 1.15 || 1;
  // A wiped-out path is a real outcome and log(0) is not a number, so the axis floors at a
  // decade below the smallest positive value on it and zeros are drawn sitting on that floor.
  const positives = [...b.p5, ...b.p50, pj.start_equity].filter((v) => v > 0);
  const lo = Math.max(Math.min(...positives, hi) / 3, hi / 1e4);
  const ly = Math.log10(lo), lh = Math.log10(hi);
  const x = (i) => PAD + (i * (W - PAD - RGT)) / (days.length - 1);
  const y = (v) => {
    const t = (Math.log10(Math.max(v, lo)) - ly) / (lh - ly || 1);
    return H - BOT - t * (H - PAD - BOT);
  };
  const band = (a, c) => {
    const up = a.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const down = c
      .map((_v, i) => `L${x(c.length - 1 - i).toFixed(1)},${y(c[c.length - 1 - i]).toFixed(1)}`)
      .join(" ");
    return `${up} ${down} Z`;
  };
  const mid = b.p50.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const startY = y(pj.start_equity).toFixed(1);
  const endV = b.p50[b.p50.length - 1];

  const grid = niceTicks(lo, hi)
    .map((v) => {
      const gy = y(v).toFixed(1);
      return (
        `<line x1="${PAD}" x2="${W - RGT}" y1="${gy}" y2="${gy}" stroke="${MK.flat}" ` +
        `stroke-width="1" opacity="0.18"/>` +
        `<text x="${PAD - 5}" y="${(+gy + 3.5).toFixed(1)}" text-anchor="end" class="pf-axis">${compactMoney("$", v)}</text>`
      );
    })
    .join("");

  // One hover slot per sampled week, naming all three lines — a fan is unreadable without it.
  const slot = (W - PAD - RGT) / (days.length - 1);
  const hits = days
    .map((d, i) => {
      const tip = `${d} · median ${usd(b.p50[i])} · 5–95% ${usd(b.p5[i])}–${usd(b.p95[i])}`;
      return (
        `<rect x="${(x(i) - slot / 2).toFixed(1)}" y="${PAD}" width="${slot.toFixed(1)}" ` +
        `height="${(H - BOT - PAD).toFixed(1)}" fill="transparent"><title>${esc(tip)}</title></rect>`
      );
    })
    .join("");

  return (
    `<svg viewBox="0 0 ${W} ${H}" class="pf-chart" role="img" aria-label="Projected balance on a log scale, median with 25-75 and 5-95 percentile bands">` +
    grid +
    `<path d="${band(b.p5, b.p95)}" fill="${MK.line}" opacity="0.12"/>` +
    `<path d="${band(b.p25, b.p75)}" fill="${MK.line}" opacity="0.22"/>` +
    `<line x1="${PAD}" x2="${W - RGT}" y1="${startY}" y2="${startY}" stroke="${MK.gold}" stroke-dasharray="3 3" stroke-width="1"/>` +
    `<text x="${PAD + 4}" y="${(+startY - 5).toFixed(1)}" class="pf-axis">today ${compactMoney("$", pj.start_equity)}</text>` +
    `<path d="${mid}" fill="none" stroke="${MK.line}" stroke-width="2"/>` +
    `<circle cx="${x(days.length - 1).toFixed(1)}" cy="${y(endV).toFixed(1)}" r="3.5" fill="${MK.line}"/>` +
    `<text x="${(x(days.length - 1) - 5).toFixed(1)}" y="${(y(endV) - 8).toFixed(1)}" text-anchor="end" class="pf-axis">${compactMoney("$", endV)}</text>` +
    `<text x="${PAD}" y="${(H - BOT + 15).toFixed(1)}" class="pf-axis">${esc(days[0])}</text>` +
    `<text x="${W - RGT}" y="${(H - BOT + 15).toFixed(1)}" text-anchor="end" class="pf-axis">${esc(days[days.length - 1])}</text>` +
    hits +
    `</svg>`
  );
}

/* --- Income ramp: sustainable take-home per year, against the day rate ---
   Three ordered quantiles of the same quantity, so again one hue with the median carrying the
   weight. The day-rate rule is the only other mark, in gold, because it is a different KIND of
   thing — a target, not a projection — and the crossing is the whole picture. */
function rampSvg(ramp, box) {
  const yrs = (ramp && ramp.years) || [];
  if (yrs.length < 2) return '<p class="muted">No growth rate to ramp from.</p>';
  const { W, H } = box;
  const PAD = 56, BOT = 30, RGT = 14;
  const series = ramp.series || {};
  const target = ramp.target_gbp || 1;
  // The y-domain is deliberately CLIPPED at 1.5× the day rate rather than fitted to the data.
  // Compounding means year 15 can be a hundred times the target, and fitting to that puts the
  // gold rule on the floor with every line flat against it until one vertical spike — a picture
  // in which the crossing, the only thing this chart is for, is invisible. Past the top the
  // lines simply leave the frame, which is the correct reading: more than enough.
  const hi = target * 1.5;
  const x = (i) => PAD + (i * (W - PAD - RGT)) / (yrs.length - 1);
  const y = (v) => H - BOT - (Math.min(v, hi * 1.2) / hi) * (H - PAD - BOT);
  const path = (vals) =>
    vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const line = (key, width, op) =>
    series[key]
      ? `<path d="${path(series[key])}" fill="none" stroke="${MK.line}" stroke-width="${width}" ` +
        `opacity="${op}" clip-path="url(#pj-ramp-clip)"/>`
      : "";
  const tY = y(target).toFixed(1);

  // The crossing is the answer, so mark it: the first year the median clears the day rate,
  // interpolated between the two bracketing years so the marker sits where the lines actually meet.
  const mid = series.p50 || [];
  let cross = "";
  // Already above the rule at year 0 — every line is clipped off the top and the loop below would
  // never fire, leaving a chart with a gold rule and nothing else on it. Say so instead.
  if (mid.length && mid[0] >= target) {
    cross =
      `<text x="${((PAD + W - RGT) / 2).toFixed(1)}" y="${(PAD + 14).toFixed(1)}" ` +
      `text-anchor="middle" class="pf-axis">already above the day rate at today's balance</text>`;
  }
  for (let i = 1; i < mid.length && !cross; i++) {
    if (mid[i - 1] < target && mid[i] >= target) {
      const f = (target - mid[i - 1]) / (mid[i] - mid[i - 1] || 1);
      const cx = (x(i - 1) + (x(i) - x(i - 1)) * f).toFixed(1);
      cross =
        `<line x1="${cx}" x2="${cx}" y1="${PAD}" y2="${H - BOT}" stroke="${MK.gold}" ` +
        `stroke-dasharray="2 3" stroke-width="1" opacity="0.7"/>` +
        `<circle cx="${cx}" cy="${tY}" r="4" fill="${MK.gold}"/>` +
        `<text x="${cx}" y="${(PAD - 6).toFixed(1)}" text-anchor="middle" class="pf-axis">` +
        `${(yrs[i - 1] + f).toFixed(1)} yr</text>`;
      break;
    }
  }

  const slot = (W - PAD - RGT) / (yrs.length - 1);
  const hits = yrs
    .map((yr, i) => {
      const tip =
        `year ${yr} · median ${gbp(series.p50 ? series.p50[i] : null, 0)}/yr` +
        (series.p25 && series.p75
          ? ` · slow ${gbp(series.p25[i], 0)} · fast ${gbp(series.p75[i], 0)}`
          : "");
      return (
        `<rect x="${(x(i) - slot / 2).toFixed(1)}" y="${PAD}" width="${slot.toFixed(1)}" ` +
        `height="${(H - BOT - PAD).toFixed(1)}" fill="transparent"><title>${esc(tip)}</title></rect>`
      );
    })
    .join("");
  // Year ticks every ~3 years so the crossing can be read off the axis, not just the marker.
  const step = Math.max(1, Math.round((yrs.length - 1) / 5));
  const xticks = yrs
    .filter((_v, i) => i % step === 0 || i === yrs.length - 1)
    .map(
      (yr) =>
        `<text x="${x(yrs.indexOf(yr)).toFixed(1)}" y="${(H - BOT + 15).toFixed(1)}" ` +
        `text-anchor="middle" class="pf-axis">${yr === 0 ? "now" : "+" + yr}</text>`
    )
    .join("");
  return (
    `<svg viewBox="0 0 ${W} ${H}" class="pf-chart" role="img" aria-label="Sustainable annual take-home by year, against the day rate">` +
    `<defs><clipPath id="pj-ramp-clip"><rect x="${PAD}" y="${PAD - 8}" width="${(W - PAD - RGT).toFixed(1)}" height="${(H - BOT - PAD + 8).toFixed(1)}"/></clipPath></defs>` +
    `<line x1="${PAD}" x2="${W - RGT}" y1="${tY}" y2="${tY}" stroke="${MK.gold}" stroke-dasharray="4 3" stroke-width="1.5"/>` +
    `<text x="${PAD - 5}" y="${(+tY + 3.5).toFixed(1)}" text-anchor="end" class="pf-axis">${compactMoney("£", target)}</text>` +
    `<text x="${PAD + 4}" y="${(+tY - 6).toFixed(1)}" class="pf-axis">day rate, after tax</text>` +
    line("p25", 1, 0.5) +
    line("p75", 1, 0.5) +
    line("p50", 2, 1) +
    cross +
    xticks +
    hits +
    `</svg>`
  );
}

/* --- Tiles, verdict, ladder ---
   Tooltips here define what a figure IS (so a number is never ambiguous); they don't argue
   about what it means. The reasoning about this projection lives in a report (#414). */

function projectionTiles(pj) {
  const e = pj.end_equity;
  const grew = e.p50 >= pj.start_equity;
  const ret = pj.start_equity > 0 ? e.p50 / pj.start_equity - 1 : null;
  return (
    tile("Median balance", usd(e.p50), grew ? "pf-pos" : "pf-neg", `50th percentile balance a year out. Today: ${usd(pj.start_equity)}.`) +
    tile("Median return", fmtPct(ret, 0), grew ? "pf-pos" : "pf-neg", "Median year's total return on today's balance, after costs, tax reserve and payouts.") +
    tile("Bad year (5th %ile)", usd(e.p5), "pf-neg", "5th percentile ending balance — one year in twenty ends at or below it.") +
    tile("Good year (95th %ile)", usd(e.p95), "pf-pos", "95th percentile ending balance — one year in twenty ends at or above it.") +
    tile("Ends up", (pj.p_profit * 100).toFixed(0) + "%", pj.p_profit >= 0.5 ? "pf-pos" : "pf-neg", "Share of simulated years finishing above today's balance.") +
    tile("Growth", fmtGrowth(pj.growth.p50), grew ? "pf-pos" : "pf-neg", `Median compound annual growth, reinvesting everything. Quartiles ${fmtGrowth(pj.growth.p25)} to ${fmtGrowth(pj.growth.p75)}.`)
  );
}

function drawdownTiles(pj) {
  const d = pj.drawdown;
  const pctOf = (v) => "-" + (v * 100).toFixed(0) + "%";
  return (
    tile("Typical year", pctOf(d.p50), "pf-neg", "Median worst peak-to-trough within a year.") +
    tile("1 year in 10", pctOf(d.p90), "pf-neg", "90th percentile of the worst peak-to-trough.") +
    tile("Worst drawn", pctOf(d.max), "pf-neg", "Deepest drawdown in any simulated path.") +
    tile("Halves at least once", (d.p_halved * 100).toFixed(0) + "%", d.p_halved > 0.1 ? "pf-neg" : "", "Share of years containing a 50% peak-to-trough drawdown.")
  );
}

function payoutTiles2(pj) {
  const w = pj.first_withdrawal, t = pj.first_tax;
  return (
    tile("First withdrawal", isoDay(w.median_date), w.probability > 0 ? "pf-pos" : "pf-neg", `Median date of the first payout, across the ${(w.probability * 100).toFixed(0)}% of years that reach one.`) +
    tile("Any payout at all", odds(w.probability), w.probability > 0.5 ? "pf-pos" : "", "Share of simulated years producing any withdrawal at all.") +
    tile("First CGT bill", isoDay(t.median_date), t.probability > 0 ? "pf-neg" : "", `CGT settles at the 6-April boundary on gains above the allowance. Reached in ${(t.probability * 100).toFixed(0)}% of years.`) +
    tile("Take-home, yr 1", gbp(pj.take_home_gbp.p50), pj.take_home_gbp.p50 > 0 ? "pf-pos" : "", `Median total paid out over the year. Quartiles ${gbp(pj.take_home_gbp.p25)} to ${gbp(pj.take_home_gbp.p75)}.`)
  );
}

// The day-rate question, answered as a status and five figures rather than a paragraph. The
// four states are all derived from the payload: no growth to compound, a growth rate the
// sample can't support (`growth_implausible` — dividing by it collapses the capital column
// toward zero), a horizon past a working lifetime, or a real answer.
function verdictState(pj) {
  const g = pj.growth.p50;
  const target = pj.ladder[pj.ladder.length - 1];
  if (!(g > 0)) return { label: "No growth to compound", cls: "pf-neg", reachable: false };
  if (pj.growth_implausible) return { label: "Sample too small", cls: "warn", reachable: false };
  if (target.years == null || target.years > 50)
    return { label: "Beyond a lifetime", cls: "pf-neg", reachable: false };
  return { label: `Reachable · ${fmtYears(target.years)}`, cls: "pf-pos", reachable: true };
}

function verdictTiles(pj, v) {
  const dr = pj.day_rate;
  const target = pj.ladder[pj.ladder.length - 1];
  const yrs = pj.day_rate_years;
  const dim = v.reachable ? "" : "muted";
  const na = (value) => (v.reachable ? value : "—");
  const range =
    yrs && yrs.p25 != null && yrs.p75 != null
      ? ` Growth quartiles bracket it between ${fmtYears(yrs.p75)} and ${fmtYears(yrs.p25)}.`
      : "";
  return (
    tile("Day rate, net", gbp(dr.net_annual_gbp, 0), "",
      `£${dr.gbp_per_day}/day × ${dr.days_per_year} days at a ${(dr.net_fraction * 100).toFixed(0)}% take-home fraction — ${gbp(dr.net_annual_gbp / 12, 0)}/mo, from ${gbp(dr.gross_annual_gbp, 0)} gross.`) +
    tile("Balance today", usd(pj.start_equity), "", "The book's current balance — where the compounding starts.") +
    tile("Growth used", fmtGrowth(pj.growth.p50), pj.growth.p50 > 0 ? "pf-pos" : "pf-neg",
      `Median compound annual growth from the projection. Quartiles ${fmtGrowth(pj.growth.p25)} to ${fmtGrowth(pj.growth.p75)}.`) +
    tile("Capital needed", na(usd(target.capital_usd, 0)), dim,
      "Account size whose annual profit sustains that income indefinitely — capital held flat, after CGT and running costs.") +
    tile("Years to there", na(fmtYears(target.years)), dim,
      `Years of reinvesting everything to compound from today's balance to that capital.${range}`) +
    tile("Position at target", na(usd(target.position_usd, 0)), dim,
      "Notional one position would carry at that capital, at the 50% position cap.")
  );
}

function ladderRows(pj) {
  return pj.ladder
    .map((m) => {
      const isTarget = m === pj.ladder[pj.ladder.length - 1];
      const cls = isTarget ? ' class="pj-target-row"' : "";
      if (m.capital_usd == null) {
        return `<tr${cls}><td>${esc(m.label)}</td><td class="r">${gbp(m.gbp_per_year, 0)}</td>` +
          `<td class="r muted" colspan="3">no positive growth rate — unreachable</td></tr>`;
      }
      return (
        `<tr${cls}>` +
        `<td>${esc(m.label)}</td>` +
        `<td class="r">${gbp(m.gbp_per_year, 0)}</td>` +
        `<td class="r">${usd(m.capital_usd, 0)}</td>` +
        `<td class="r muted">${usd(m.position_usd, 0)}</td>` +
        `<td class="r">${fmtYears(m.years)}</td>` +
        "</tr>"
      );
    })
    .join("");
}

// What the model was fed, as values. Every row is read straight out of the payload or the
// config it was built with, so this panel can't drift from the numbers above it. Where these
// inputs stop being safe is a report, not a panel — the link under the table goes there.
function inputRows(pj, cfg) {
  const rows = [
    ["Sample", `${pj.sample.trading_days} trading days · ${pj.sample.trades} trades`],
    ["Horizon", `${pj.sessions} sessions · ${isoDay(pj.start_date)} → ${isoDay(pj.end_date)}`],
    ["Paths", `${pj.paths} · resampled in ${pj.block_days}-day blocks`],
    ["Start balance", usd(pj.start_equity)],
    ["Risk / trade", `${pct(cfg.risk_fraction)} · ladder ${(cfg.risk_ladder || []).map(pct).join(" / ")}`],
    ["Trades / day", `≤ ${cfg.max_trades_per_day} · ${pct(cfg.position_fraction)} position cap`],
    [
      "Withdrawals",
      `${pct(cfg.withdraw_fraction)} of profit above the high-water mark · every ` +
        `${cfg.withdraw_cadence_months} mo · floor ${usd(cfg.withdraw_floor_usd, 0)}`,
    ],
    ["CGT", `${pct(cfg.cgt_rate)} above ${gbp(cfg.cgt_annual_exempt_gbp, 0)} · settled 6 April`],
    ["FX", `£1 = $${Number(PAYLOAD.gbpusd_rate).toFixed(2)}`],
    [
      "Day rate",
      `£${cfg.day_rate_gbp}/day × ${cfg.day_rate_days_per_year} days · ` +
        `${pct(cfg.day_rate_net_fraction)} net`,
    ],
    [
      "Running costs",
      `${gbp(cfg.vps_gbp_per_month)}/mo box · ${usd(cfg.market_data_usd_per_month, 0)}/mo market data`,
    ],
  ];
  return rows
    .map(([k, v]) => `<tr><th scope="row">${esc(k)}</th><td class="r">${esc(v)}</td></tr>`)
    .join("");
}

// Shows whichever of the two projection regions applies: `#pj-view` when there is a projection to
// draw, the standalone `#pj-none` panel when there isn't.
//
// The reason goes in its own element rather than over `#pj-view.innerHTML` (#464). `#pj-view` holds
// every `pj-*` element the branch below writes into, so writing the reason over it deleted them all
// — permanently, since nothing rebuilds them. That was harmless while the branch was terminal for
// the session, but #430's combined book carries no projection by construction, so DATA `+ History`
// → back to `Live` now reaches the available branch with the elements gone: `el("pj-tiles")` is
// null, `render()` throws mid-way, and the view stays broken until a reload.
function renderProjection(book) {
  const pj = book.projection;
  const available = !!(pj && pj.available);
  el("pj-view").hidden = !available;
  el("pj-none").hidden = available;
  if (!available) {
    // A combined book carries no projection by construction (#430): the forward view resamples the
    // returns the tracker actually observed, so it is built for the live scope only. Say that,
    // rather than falling through to "not built yet", which would be wrong here.
    el("pj-none-reason").textContent =
      SCOPE === "all" && hasRecon()
        ? "The projection is built from live days only. Switch DATA to Live to see it."
        : (pj && pj.reason) ||
          "No projection in this payload yet — it is built at the end-of-day report.";
    return;
  }
  const cfg = PAYLOAD.config;
  const v = verdictState(pj);
  el("pj-tiles").innerHTML = projectionTiles(pj);
  el("pj-dd-tiles").innerHTML = drawdownTiles(pj);
  el("pj-pay-tiles").innerHTML = payoutTiles2(pj);
  el("pj-verdict-pill").textContent = v.label;
  el("pj-verdict-pill").className = "pill " + v.cls;
  el("pj-verdict-tiles").innerHTML = verdictTiles(pj, v);
  el("pj-ladder").innerHTML = ladderRows(pj);
  el("pj-inputs").innerHTML = inputRows(pj, cfg);
  // When the growth rate is a small-sample artifact the ladder is arithmetic on a meaningless
  // input, so dim it — the status pill and the table then say the same thing.
  el("pj-ladder-wrap").classList.toggle("pj-void", !!pj.growth_implausible);

  // The captions below are legends: what a mark means, not what to think of it.
  setNote(
    "pj-fan-note",
    `${pj.paths} paths · ${pj.block_days}-day blocks · median line, 25–75 and 5–95 bands · ` +
      `costs, CGT and withdrawals settled by the book's own ledgers.`
  );
  setNote("pj-dd-note", "Peak-to-trough on trading P&amp;L within one year; scheduled withdrawals excluded.");
  setNote(
    "pj-pay-note",
    `${pct(cfg.withdraw_fraction)} of profit above the high-water mark · every ` +
      `${cfg.withdraw_cadence_months} mo · floor ${usd(cfg.withdraw_floor_usd, 0)} · ` +
      `CGT ${pct(cfg.cgt_rate)} above ${gbp(cfg.cgt_annual_exempt_gbp, 0)} at 6 April.`
  );
  setNote(
    "pj-ramp-note",
    "Take-home per year if reinvesting stopped that year — capital flat, after CGT and costs. " +
      "Thick line = median growth, thin = 25th/75th percentile, gold rule = the day rate."
  );
  setNote(
    "pj-ladder-note",
    pj.growth_implausible
      ? `Greyed out: the capital column divides by a median growth of ${fmtGrowth(pj.growth.p50)}, ` +
          `which ${pj.sample.trading_days} trading days can't support.`
      : `Steady state: capital held flat, after CGT and running costs. Built from ` +
          `${pj.sample.trading_days} trading days / ${pj.sample.trades} trades.`
  );
}

/* ---------- Stat tiles ---------- */

function tile(label, value, cls = "", title = "") {
  const t = title ? ` title="${esc(title)}"` : "";
  return (
    `<div class="tile"${t}><div class="tile-l">${esc(label)}</div>` +
    `<div class="tile-v ${cls}">${value}</div></div>`
  );
}

// Costs are first-order on a $500 book (research/broker-costs.md, #232) — show the drag as a share
// of starting equity rather than burying it inside net P&L.
function costTile(s, start) {
  if (s.total_costs_usd == null) return "";
  const pctOf = start ? ` <span class="muted">(${((s.total_costs_usd / start) * 100).toFixed(1)}%)</span>` : "";
  const breakdown =
    `IBKR commission ${fmtUsd(s.commission_usd)} · ` +
    `exchange/clearing/TAF/SEC ${fmtUsd(s.fees_usd)} · ` +
    `market data ${fmtUsd(s.data_fees_usd)}`;
  return tile("Costs", fmtUsd(s.total_costs_usd) + pctOf, "pf-neg", breakdown);
}

function statTiles(book, start) {
  const s = book.stats;
  const grew = s.end_equity >= start;
  return (
    tile("Balance", fmtUsd(s.end_equity), grew ? "pf-pos" : "pf-neg") +
    tile("Return", fmtPct(s.return_pct), grew ? "pf-pos" : "pf-neg") +
    tile("Win rate", s.win_rate == null ? "—" : (s.win_rate * 100).toFixed(0) + "%") +
    tile("Trades", `${fmtInt(s.n_trades)} <span class="muted">${s.wins}W/${s.losses}L</span>`) +
    tile("Avg R", fmtR(s.avg_r)) +
    tile("Expectancy", `${fmtUsd(s.expectancy_usd)}<span class="muted">/trade</span>`) +
    tile("Max DD", s.max_drawdown_pct == null ? "—" : "-" + (s.max_drawdown_pct * 100).toFixed(1) + "%", "pf-neg") +
    costTile(s, start)
  );
}

/* ---------- Next session: the knobs in force right now (#286) ---------- */

// How many more decisive days until the kill-switch moves a rung, phrased from the signed streak
// (see step_risk_rung): +n = n net-positive days in a row, -n = n net-negative. A flat day holds.
function streakNote(st) {
  const need = st.step_days - Math.abs(st.streak);
  const dayWord = (n) => `${n} ${n === 1 ? "day" : "days"}`;
  if (st.streak === 0) {
    return `No run either way — ${dayWord(st.step_days)} in a row moves risk a rung.`;
  }
  const dir = st.streak > 0 ? "net-positive" : "net-negative";
  const moving = st.streak > 0 ? "up" : "down";
  const atEnd = st.streak > 0 ? st.rung >= st.n_rungs - 1 : st.rung <= 0;
  const wall = st.streak > 0 ? "already at full risk" : "already parked at 0%";
  const tail = atEnd
    ? ` — but the book is ${wall}, so it holds.`
    : `; ${dayWord(need)} more steps risk ${moving} a rung.`;
  return `${dayWord(Math.abs(st.streak))} of ${dir} results${tail}`;
}

// Whether the target on the tile is the optimiser's pick or the fallback (#463), and the sample
// behind it. `target_fitted` post-dates these payloads, so against a portfolio.json published
// before it existed the tag drops out rather than asserting either way.
function fitTag(st, c) {
  if (st.target_fitted == null) return "";
  const need = c.adaptive_min_samples;
  const n = st.target_trailing_n;
  return st.target_fitted
    ? `<span class="muted">(fitted · ${n} trades)</span>`
    : `<span class="muted">(fallback · ${n}/${need})</span>`;
}

function fitTitle(st, c) {
  const base = "The R multiple the next setup exits at";
  if (st.target_fitted == null) return `${base} — re-fit daily from the trailing window`;
  return st.target_fitted
    ? `${base}, chosen by the daily re-fit over ${st.target_trailing_n} trades in the trailing ` +
        `${c.adaptive_window_days}-day window.`
    : `${base}. The re-fit did NOT run: the trailing ${c.adaptive_window_days}-day window holds ` +
        `${st.target_trailing_n} trades and needs ${c.adaptive_min_samples}, so this is the ` +
        `${c.target_fallback_r}R fallback, not an adaptive choice.`;
}

// Note: `n_rungs - 1` because rung 0 is the 0% floor — a 3-rung ladder has 2 steps above sitting out.
function todayTiles(st, c) {
  const parked = st.risk_fraction === 0;
  return (
    tile("Target", `${st.target_r}R ${fitTag(st, c)}`, "", fitTitle(st, c)) +
    tile(
      "Risk / trade",
      pct(st.risk_fraction) + ` <span class="muted">(rung ${st.rung}/${st.n_rungs - 1})</span>`,
      parked ? "pf-neg" : "",
      `The kill-switch rung in force. Ladder: ${(c.risk_ladder || []).map(pct).join(" / ")}`
    ) +
    tile(
      "Risk budget",
      parked ? "—" : fmtUsd(st.risk_budget_usd),
      parked ? "pf-neg" : "",
      "Dollars the next setup may risk = balance × risk/trade. A setup is sized so entry−stop × qty lands here, unless the position cap binds first."
    ) +
    tile(
      "Max position",
      fmtUsd(st.max_position_usd),
      "",
      `Notional ceiling per position = balance × ${pct(c.position_fraction)}. On a tight stop this — not the risk budget — sets the size.`
    )
  );
}

function renderToday(book) {
  const st = book.next_session;
  const wrap = el("pf-today-wrap");
  // Only the adaptive book throttles risk or re-fits a target, so only it has a "next session".
  if (!st) {
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;
  el("pf-today-tiles").innerHTML = todayTiles(st, PAYLOAD.config);
  const parked = st.risk_fraction === 0;
  const sitting = parked
    ? ` The book is <strong>sitting out</strong> — it still watches the tape and re-arms once setups work again.`
    : "";
  setNote(
    "pf-today-note",
    `Applies to the next session the book sizes (data through ${esc(prevDay(st.as_of))}). ` +
      streakNote(st) +
      sitting
  );
}

// The state is stamped with the session it governs; the day before it is the last collected one.
function prevDay(iso) {
  const d = new Date(iso + "T00:00:00Z");
  if (isNaN(d)) return iso;
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

/* ---------- Getting paid: withdrawals + UK CGT + VPS, in GBP ---------- */

const CF_LBL = { withdrawal: "Withdrawal", tax: "CGT", vps: "VPS" };

function payoutTiles(book) {
  const s = book.stats;
  return (
    tile("Take-home", fmtGbp(s.net_take_home_gbp), s.net_take_home_gbp > 0 ? "pf-pos" : "", "Sum of withdrawals paid out to you, net, in GBP") +
    tile("CGT reserved", fmtGbp(s.tax_paid_gbp), s.tax_paid_gbp > 0 ? "pf-neg" : "", "UK Capital Gains Tax reserved on realised gains above the annual allowance") +
    tile("VPS cost", fmtGbp(s.vps_costs_gbp), s.vps_costs_gbp > 0 ? "pf-neg" : "", "Running cost of the box, charged monthly")
  );
}

function cashFlowRows(book, cfg) {
  const flows = book.cash_flows || [];
  if (!flows.length) {
    const floor = cfg && cfg.withdraw_floor_usd != null ? fmtUsd(cfg.withdraw_floor_usd) : "the floor";
    return (
      `<p class="muted pf-note">No payouts yet — withdrawals stay dormant until the balance clears ` +
      `${floor} (profit above a high-water mark is paid out ` +
      `${cfg ? (cfg.withdraw_fraction * 100).toFixed(0) : "—"}% every ` +
      `${cfg ? cfg.withdraw_cadence_months : "—"} months), and CGT is only reserved on gains above the allowance.</p>`
    );
  }
  const rows = flows
    .slice()
    .reverse() // newest first
    .map((c) => {
      const cls = c.kind === "withdrawal" ? "pf-pos" : "pf-neg";
      return (
        "<tr>" +
        `<td>${esc(c.date)}</td>` +
        `<td><span class="pf-reason pf-reason-${c.kind === "withdrawal" ? "target" : "stop"}">${CF_LBL[c.kind] || c.kind}</span></td>` +
        `<td class="r ${cls}">${fmtGbp(c.gbp)}</td>` +
        `<td class="r muted">${fmtUsd(c.usd)}</td>` +
        "</tr>"
      );
    })
    .join("");
  return (
    '<div class="tbl-wrap pf-cash-scroll"><table class="tbl"><thead><tr>' +
    '<th>Date</th><th>Type</th><th class="r">GBP</th><th class="r">USD</th>' +
    `</tr></thead><tbody>${rows}</tbody></table></div>`
  );
}

/* ---------- Trade log ---------- */

const REASON_LBL = { target: "target", stop: "stop", breakeven: "b/e", close: "close" };

// The risk a trade actually took, plus a badge when the notional cap — not the risk target — set
// the size (#286). `risk_pct` is absent from books published before that; show "—" rather than
// silently falling back to the configured ceiling, which is the very overstatement this fixes.
function riskCell(t) {
  if (t.risk_pct == null) return '<td class="r muted" title="Not recorded for this trade">—</td>';
  const capped = t.sized_by === "cap";
  const tip =
    `${fmtUsd(t.risk_usd)} at risk` +
    (capped
      ? ` — the ${pct(PAYLOAD.config.position_fraction)} position cap held this under the ` +
        `${pct(t.risk_fraction)} risk target (stop is tight relative to entry)`
      : ` — sized by the ${pct(t.risk_fraction)} risk target`);
  const badge = capped ? ' <span class="pf-reason pf-reason-stop">cap</span>' : "";
  return `<td class="r" title="${esc(tip)}"><span class="${capped ? "muted" : ""}">${pct(t.risk_pct)}</span>${badge}</td>`;
}

// R cells wear the shared diverging ramp (0R anchor, stop at −1R) so the
// column reads as a distribution; Net keeps simple win/loss colouring.
const rRampCell = (v) =>
  `<td class="r ${v == null ? "muted" : rRampClass(v)}">${fmtR(v)}</td>`;

// Float at flag time — context for the name, never a filter here (the float gate ran upstream).
// Absent from books published before #390, and genuinely null when no source returned one.
const floatCell = (t) =>
  `<td class="r ${t.float_shares == null ? "muted" : ""}">${fmtShares(t.float_shares)}</td>`;

// Peak favourable excursion. Deliberately NOT on the R ramp: Max R is ≥ 0 by construction, so the
// diverging scale would paint the whole column green and drown out the realised-R column beside it.
// The tooltip carries the actual question — how much this exit left on the table.
function maxRCell(t, realized) {
  if (t.max_r == null) return '<td class="r muted" title="Not recorded for this trade">—</td>';
  const left = t.max_r - realized;
  // A 0R peak means the trade never traded above entry, so there was nothing on the table to
  // leave — phrasing that gap as "left on the table" would describe the LOSS as forgone upside.
  const offered = t.max_r > 0.005;
  const tip = !offered
    ? `never traded above entry — nothing was on the table`
    : left > 0.005
      ? `${fmtR(left)} left on the table — the exit took ${fmtR(realized)} of a ${fmtR(t.max_r)} peak`
      : `caught the whole move — the setup never went beyond ${fmtR(t.max_r)}`;
  // Half an R+ unclaimed is worth flagging — in GOLD, the Max R marker colour the review chart
  // already uses. Not the loss colour: "you didn't capture all of it" is not "you lost money", and
  // painting it red next to a green Net would read as a contradiction on every winning runner.
  const cls = offered && left > 0.5 ? "warn" : "muted";
  return `<td class="r" title="${esc(tip)}"><span class="${cls}">${fmtR(t.max_r)}</span></td>`;
}

// The same peak as a plain move off entry (payload stores a fraction, like every other _pct field).
const maxPctCell = (t) =>
  `<td class="r ${t.max_pct == null ? "muted" : ""}">${t.max_pct == null ? "—" : fmtPct(t.max_pct)}</td>`;

// The date cell, tagged when the row came from reconstructed history rather than live capture
// (#430). Absent `source` — every payload published before the harvest — reads as live.
function dateCell(t) {
  const tag =
    t.source === "recon"
      ? ` <span class="pf-src" title="Reconstructed from vendor minute bars, not captured live">recon</span>`
      : "";
  return `<td>${esc(t.date)}${tag}</td>`;
}

function tradeRows(book) {
  if (!book.trades.length) return '<tr><td colspan="15" class="muted">No qualifying pre-market trades yet.</td></tr>';
  return book.trades
    .slice()
    .reverse() // newest first
    .map((t) => {
      const nCls = t.net_pnl > 0 ? "pf-pos" : t.net_pnl < 0 ? "pf-neg" : "muted";
      const rev = `review.html?date=${encodeURIComponent(t.date)}&sym=${encodeURIComponent(t.symbol)}`;
      return (
        "<tr>" +
        dateCell(t) +
        `<td><a href="${rev}"><strong>${esc(t.symbol)}</strong></a></td>` +
        floatCell(t) +
        `<td>${etClockIso(t.trigger_at)}</td>` +
        `<td class="r">${fmtUsd(t.entry)}</td>` +
        `<td class="r">${fmtUsd(t.stop)}</td>` +
        `<td class="r">${fmtInt(t.qty)}</td>` +
        riskCell(t) +
        `<td class="r">${Number(t.target_r).toFixed(1)}R</td>` +
        `<td><span class="pf-reason pf-reason-${t.reason}">${REASON_LBL[t.reason] || t.reason}</span> ${fmtUsd(t.exit_price)}</td>` +
        rRampCell(t.realized_r) +
        maxRCell(t, t.realized_r) +
        maxPctCell(t) +
        `<td class="r ${nCls}">${fmtUsd(t.net_pnl)}</td>` +
        `<td class="r">${fmtUsd(t.equity_after)}</td>` +
        "</tr>"
      );
    })
    .join("");
}

/* ---------- Skipped setups (dropped by the daily cap) ---------- */

// Why a qualifying setup wasn't taken. Defaults to "cap" for payloads written before #251.
const SKIP_LBL = {
  cap: '<span class="muted">daily cap</span>',
  unaffordable: '<span class="pf-neg">unaffordable</span>',
};

// Setups selected but impossible to size to even one share (#251). Kept apart from the cap
// population: distinct cause, distinct fix (more capital, not a wider cap). Normally absent — it
// takes a >90% drawdown at the default book — so it stays silent rather than adding noise.
function unaffordableNote(book) {
  const n = (book.stats || {}).unaffordable_count || 0;
  if (!n) return "";
  return (
    ` ${n} setup${n === 1 ? " was" : "s were"} also selected but <strong>unaffordable</strong> — the ` +
    `book couldn't size even one share at this equity (at full risk; throttled days aren't counted).`
  );
}

function skippedNote(book) {
  const s = book.stats;
  const n = s.skipped_count || 0;
  if (!n) {
    // "No setups were dropped" would contradict the table below whenever unaffordable rows exist,
    // since skipped_count is cap-only. Speak only for the cap here.
    const capNote = `The ${PAYLOAD.config.max_trades_per_day}/day cap was never the binding constraint — it dropped nothing.`;
    return capNote + unaffordableNote(book);
  }
  const totR = s.skipped_total_r;
  const cls = totR > 0 ? "pf-pos" : totR < 0 ? "pf-neg" : "muted";
  return (
    `${n} qualifying setup${n === 1 ? "" : "s"} passed strategy but weren't taken because the ` +
    `${PAYLOAD.config.max_trades_per_day}/day cap was already full. At this book's target they'd ` +
    `have returned <span class="${cls}">${fmtR(totR)}</span> in total (unsized — R only, since a ` +
    `third concurrent position wouldn't fit the settled-cash limit).` +
    unaffordableNote(book)
  );
}

function skippedRows(book) {
  const skipped = book.skipped || [];
  if (!skipped.length) return '<tr><td colspan="12" class="muted">None — the daily cap was never binding.</td></tr>';
  return skipped
    .slice()
    .reverse() // newest first, matching the trade log
    .map((t) => {
      const rev = `review.html?date=${encodeURIComponent(t.date)}&sym=${encodeURIComponent(t.symbol)}`;
      return (
        "<tr>" +
        dateCell(t) +
        `<td><a href="${rev}"><strong>${esc(t.symbol)}</strong></a></td>` +
        floatCell(t) +
        `<td>${SKIP_LBL[t.skip_reason] || SKIP_LBL.cap}</td>` +
        `<td>${etClockIso(t.trigger_at)}</td>` +
        `<td class="r">${fmtUsd(t.entry)}</td>` +
        `<td class="r">${fmtUsd(t.stop)}</td>` +
        `<td class="r">${Number(t.target_r).toFixed(1)}R</td>` +
        `<td><span class="pf-reason pf-reason-${t.reason}">${REASON_LBL[t.reason] || t.reason}</span> ${fmtUsd(t.exit_price)}</td>` +
        rRampCell(t.realized_r) +
        maxRCell(t, t.realized_r) +
        maxPctCell(t) +
        "</tr>"
      );
    })
    .join("");
}

/* ---------- Notes + meta line ---------- */

// Each note now sits under the chart it explains rather than all of it under the equity curve.
// `target_fallback_r` post-dates these payloads, so the prose degrades instead of printing
// "undefined" against a portfolio.json published before it existed.
function targetNote(book) {
  const c = PAYLOAD.config;
  const targets = (book.daily_targets || []).filter((d) => d.target != null);
  if (!targets.length) return "";
  const last = targets[targets.length - 1];
  const uniq = [...new Set(targets.map((d) => d.target))].sort((a, b) => a - b);
  const fallback = c.target_fallback_r != null ? `the ${c.target_fallback_r}R fallback` : "the configured fallback";
  return (
    `Target re-fits daily from the trailing ${c.adaptive_window_days}-day window (needs ≥ ` +
    `${c.adaptive_min_samples} prior trades, else ${fallback}). Latest chosen target: ` +
    `<strong>${last.target}R</strong> · targets used: ${uniq.map((t) => t + "R").join(", ")}. ` +
    fitCoverage(targets, c)
  );
}

// How much of the plotted line is the optimiser and how much is the fallback (#463). A flat target
// chart is ambiguous on its face — "the fit kept picking the same rung" and "the fit never ran"
// draw an identical line — and for the live book's first 28 days it was always the second.
function fitCoverage(targets, c) {
  const known = targets.filter((d) => d.fitted != null);
  if (!known.length) return ""; // payload predates the flag — say nothing rather than guess
  const fitted = known.filter((d) => d.fitted).length;
  if (fitted === known.length) return `All ${known.length} days were re-fitted.`;
  if (fitted === 0) {
    const last = known[known.length - 1];
    return (
      `<strong>The re-fit has never run</strong> — all ${known.length} days fell back. The window ` +
      `holds ${last.n} trades and needs ${c.adaptive_min_samples}.`
    );
  }
  return `Re-fitted on ${fitted} of ${known.length} days; the rest fell back.`;
}

function riskNote(book) {
  const c = PAYLOAD.config;
  if (!(book.daily_risk || []).length) return "";
  const ladder = (c.risk_ladder || []).map(pct).join(" / ");
  const d = c.risk_step_days || 1;
  const days = d === 1 ? "day" : `${d} days`;
  // Deliberately no "Latest risk: N%" here (#286): the forward-looking number
  // lives in the Next session panel.
  return (
    `Risk throttle (kill-switch): position risk walks ${c.risk_rungs} rungs (${ladder}), starting ` +
    `at full risk. It takes ${days} in a row of net-positive results to step risk up a rung (and ` +
    `${days} of net-negative to step down); at 0% the book sits out but still watches the tape to ` +
    `re-arm. The rung caps the risk — a tight stop can still leave the ` +
    `${pct(c.position_fraction)} position cap binding first, so the risk actually taken lands below it.`
  );
}

// The header used to promise a flat "up to 5% risk / trade", which read as a description of what
// the book does. It is only a ceiling: the 50% notional cap binds on any stop tighter than
// risk/position (10%) of entry — most bull-flag setups — so trades routinely risk a fraction of it
// (#286). Lead with the ceiling, then the risk actually taken, so the gap is visible not implied.
function riskMeta(book, c) {
  const ceiling =
    `≤ ${(c.risk_fraction * 100).toFixed(0)}% risk / trade (adaptive throttles), ` +
    `max ${(c.position_fraction * 100).toFixed(0)}% size`;
  const s = (book && book.stats) || {};
  if (s.avg_risk_pct == null || !s.n_trades) return ceiling;
  const capped = s.cap_bound_count
    ? ` (${s.cap_bound_count} of ${s.n_trades} sized by the ${pct(c.position_fraction)} cap, not the risk target)`
    : "";
  return `${ceiling} · <strong>actually risked ${pct(s.avg_risk_pct)}/trade on average</strong>${capped}`;
}

// The takeable trigger window, "05:30–09:15" (floor inclusive, cutoff strict). The floor is newer
// than the cutoff, so a payload published before it existed omits the key — fall back to the
// cutoff-only form rather than rendering "undefined–09:15" until the next publish lands.
function premarketWindow(c) {
  const cutoff = c.premarket_cutoff_et.slice(0, 5);
  if (!c.premarket_earliest_et) return `< ${cutoff}`;
  return `${c.premarket_earliest_et.slice(0, 5)}–${cutoff}`;
}

// What each provenance contributes, straight from the payload's `coverage` block (#430). Stated as
// spans and counts rather than a conclusion about what the mix is worth — that belongs in a report.
function coverageLine() {
  const cov = PAYLOAD.coverage;
  if (!cov) return "";
  const span = (c) => (c && c.days ? `${isoDay(c.from)}→${isoDay(c.to)} (${c.days}d)` : "none");
  const recon = cov.recon || {};
  const reconPart = recon.days
    ? ` · reconstructed ${span(recon)}${SCOPE === "all" ? "" : " — not in this book"}`
    : "";
  return ` · Data: live ${span(cov.live)}${reconPart}`;
}

// The live/reconstructed split of the book on screen. Size-independent numbers only — see
// `_by_source_json`: an equity curve cannot be attributed to one source after a splice.
function sourceSplit(book) {
  const bs = (book.stats || {}).by_source;
  if (!bs || !bs.recon || !bs.recon.n_trades) return "";
  const one = (k, l) =>
    `${l} ${fmtInt(bs[k].n_trades)} trades / ${fmtR(bs[k].total_r)}` +
    (bs[k].win_rate == null ? "" : ` / ${(bs[k].win_rate * 100).toFixed(0)}% win`);
  return ` · <strong>Split:</strong> ${one("live", "live")} · ${one("recon", "reconstructed")}`;
}

// The per-book config/meta line, under the options bar's ··· expander.
function metaLine(book) {
  const c = PAYLOAD.config;
  return (
    `Pre-shadow paper book — the trades I'd take, over the data already collected. ` +
    `Start ${fmtUsd(PAYLOAD.start_equity)} · ${riskMeta(book, c)} · ` +
    `max ${c.max_trades_per_day}/day · pre-market fills only (${esc(premarketWindow(c))} ET) · ` +
    `entry $${c.entry_price_min}–${c.entry_price_max} · ` +
    `IBKR tiered costs + $${c.market_data_usd_per_month}/mo data (#232) · ` +
    `withdraw ${(c.withdraw_fraction * 100).toFixed(0)}% of profit &gt; ${fmtUsd(c.withdraw_floor_usd)} every ` +
    `${c.withdraw_cadence_months}mo · ${(c.cgt_rate * 100).toFixed(0)}% CGT &gt; £${c.cgt_annual_exempt_gbp} · ` +
    `£/$ ${Number(PAYLOAD.gbpusd_rate).toFixed(2)} · ` +
    `Not advice, not real orders — computed on-read from the tracker's own data. ` +
    `Small samples: a wiring/sanity view, not an edge estimate.` +
    coverageLine() +
    sourceSplit(book)
  );
}

/* ---------- render + load ---------- */

// Only the adaptive book re-fits a target or throttles risk; a fixed book holds both flat by
// construction, so the panels hide rather than charting a straight line as if it were a result.
function renderStateNotes(book) {
  const has = (book.daily_targets || []).length > 0;
  const hasRisk = (book.daily_risk || []).length > 0;
  el("pf-target-wrap").hidden = !has;
  el("pf-risk-wrap").hidden = !hasRisk;
  if (has) setNote("pf-target-note", targetNote(book));
  if (hasRisk) setNote("pf-risk-note", riskNote(book));
  // A book with neither knob has half the panels — cockpit.css narrows the left
  // region and restacks the rails so the trade log takes the reclaimed width.
  document.querySelector(".pf").classList.toggle("pf-single", !has && !hasRisk);
}

// Drawn last, after every panel is in the DOM AND its view is visible: each chart is sized from
// the box its panel ended up with, and a hidden region measures 0 — so a chart drawn before the
// swap would be built for a zero-width box and stay that way until the next resize.
function drawCharts(book) {
  if (VIEW === "projection") {
    const pj = book.projection;
    if (!pj || !pj.available) return;
    el("pj-fan").innerHTML = fanSvg(pj, chartBox(el("pj-fan"), 2.1));
    el("pj-ramp").innerHTML = rampSvg(pj.income_ramp, chartBox(el("pj-ramp"), 1.5));
    return;
  }
  el("pf-chart-wrap").innerHTML = equitySvg(
    book.equity_curve,
    PAYLOAD.start_equity,
    book.cash_flows,
    chartBox(el("pf-chart-wrap"))
  );
  if (!el("pf-target-wrap").hidden) {
    el("pf-target-chart").innerHTML = targetSvg(book, chartBox(el("pf-target-chart")));
  }
  if (!el("pf-risk-wrap").hidden) {
    el("pf-risk-chart").innerHTML = riskSvg(book, chartBox(el("pf-risk-chart")));
  }
}

// Resizing the window (or dragging it between monitors) changes every chart's box, and a
// box-sized chart drawn for the old one would be stretched by the browser. Redraw instead.
// Both views' left regions are observed: the hidden one reports 0×0 and fires on reveal, which
// is precisely the moment its charts become measurable.
let pending = 0;
const redraw = new ResizeObserver(() => {
  if (pending || !PAYLOAD) return;
  pending = requestAnimationFrame(() => {
    pending = 0;
    if (PAYLOAD) drawCharts(booksFor()[BOOK]);
  });
});
document.querySelectorAll(".pf-left").forEach((n) => redraw.observe(n));

function render() {
  const book = booksFor()[BOOK];
  el("pf-meta").innerHTML = metaLine(book);
  el("pf-view").hidden = VIEW !== "book";
  // Both projection regions belong to the projection view; `renderProjection` picks which of the
  // two to reveal once it is the active one.
  el("pj-view").hidden = true;
  el("pj-none").hidden = true;
  if (VIEW === "projection") {
    renderProjection(book);
    drawCharts(book);
    const pj = book.projection;
    setStatusPage(
      `projection · book ${esc(BOOK === "adaptive" ? "adaptive" : BOOK + "R")} · ` +
        (pj && pj.available ? `${pj.paths} paths over ${pj.sessions} sessions` : "unavailable")
    );
    return;
  }
  el("pf-tiles").innerHTML = statTiles(book, PAYLOAD.start_equity);
  renderToday(book);
  renderStateNotes(book);
  el("pf-payout-tiles").innerHTML = payoutTiles(book);
  el("pf-cashflows").innerHTML = cashFlowRows(book, PAYLOAD.config);
  const noPayouts = el("pf-cashflows").querySelector(".pf-note");
  if (noPayouts) setNote(noPayouts);
  el("pf-trades").innerHTML = tradeRows(book);
  setNote("pf-skipped-note", skippedNote(book));
  el("pf-skipped").innerHTML = skippedRows(book);
  drawCharts(book);
  const s = book.stats;
  setStatusPage(
    `book ${esc(BOOK === "adaptive" ? "adaptive" : BOOK + "R")} · ${s.n_trades ?? 0} trades · ` +
      `${(book.skipped || []).length} skipped`,
  );
}

async function load() {
  el("pf-error").hidden = true;
  const data = await fetchJson("portfolio.json");
  if (!data || !data.books) {
    el("pf-error").hidden = false;
    el("pf-error").textContent = "No portfolio data yet — it's built at the end-of-day report.";
    return;
  }
  PAYLOAD = data;
  if (!PAYLOAD.books[BOOK]) BOOK = "adaptive";
  buildOptbar(); // the book list comes from the payload
  render();
}

buildOptbar();
load().catch((e) => {
  el("pf-error").hidden = false;
  el("pf-error").textContent = `Failed to load portfolio: ${e && e.message ? e.message : e}`;
});
