// Plan page (#410, rebuilt in #414): a status board, not an essay.
//
// Every value here is computed at render time — collection progress and opportunity counts
// from `index.json`, the paper book and the risk state in force from `portfolio.json`,
// publish freshness and the historical harvest's progress from `status.json`, and each Phase-2
// gate's status from whether the issues it names are closed on GitHub (`js/gh.js`). The only
// committed text is labels:
// the phase names, the gate names, and the issue numbers they map to
// (`research/phase-2-roadmap.md`). Nothing on this page states a status a human has to
// remember to update.
//
// Commentary — why the plan waits, what each rule is for, what the numbers mean — lives in
// a report (Reports tab), because a page of argument goes stale the moment anything moves.

import "./js/nav.js";
import { createOptionsBar } from "./js/options-bar.js";
import { setStatusPage } from "./js/status-bar.js";
import { fetchJson } from "./js/data.js";
import { el, setBanner, showError } from "./js/dom.js";
import { issueStates, issueUrl } from "./js/gh.js";
import { esc, etDateOf, fmtPct, fmtPctPlain, fmtRSigned } from "./js/fmt.js";
import { HARVEST_STALE_H, STALE_PUBLISH_MS } from "./js/thresholds.js";

const POLL_MS = 5 * 60_000; // the plan moves in days; poll lazily


/* ============================================================
   The committed skeleton — names, windows, issue numbers
   ============================================================ */

// The 3-month collection window (decisions.md §11). It is a **data readout**, not a gate: the
// harvest overtook it as the sample of record, so nothing waits on this window closing (#49).
// Collection keeps running because the live leg is the only thing that can validate the
// reconstructed one — and because 09:30–11:59 exists in live data and nowhere else.
// The XNYS closures inside it — the app's calendar of record is `exchange_calendars` XNYS
// (#137) — are the only two sessions it drops.
const COLLECT_START = "2026-07-01";
const COLLECT_END = "2026-09-30";
const HOLIDAYS = new Set([
  "2026-07-03", // Independence Day (observed) — the junk session of #137
  "2026-09-07", // Labor Day
]);

// The trade count at which the sample stops being anecdote. Gate 2 (#310) writes the real
// go/no-go bar; until it lands this is the placeholder the progress row measures against.
const VERDICT_TRADES = 100;

const PHASES = [
  { id: "p1", n: "01", name: "Tracker", tag: "data collection", window: `${COLLECT_START} → ${COLLECT_END}` },
  { id: "p2", n: "02", name: "Paper", tag: "shadow orders", window: "gated · on the go/no-go bar" },
  { id: "p3", n: "03", name: "Live", tag: "real capital", window: "gated · after paper" },
];

// The Phase-2 gates (research/phase-2-roadmap.md, epic #308). `issues` is what decides the
// status — a gate is done when all of them are closed; `after` is the dependency graph the
// page falls back to when GitHub can't be reached. No status is written here.
// Numbers are labels, not order: the ladder was numbered before we knew that money sits in the
// middle of it. Gate 4 buys the market-data feed, so gate 1 — which captures spreads off that
// feed — runs after it, and the account is not funded until the bar is written (2) and the
// sample clears it (3). Rows stay in numeric order and the Blocked pill names what each waits on.
const GATES = [
  { n: "0", name: "Truth debt", issues: [302, 297, 270] },
  { n: "1", name: "Spread capture", issues: [309], after: ["4"] },
  { n: "2", name: "Go/no-go criteria", issues: [310] },
  { n: "3", name: "Validation", issues: [462], after: ["2"] },
  { n: "4", name: "Market data", issues: [311], after: ["2", "3"] },
  { n: "5", name: "Live detection", issues: [312], after: ["0", "4"] },
  { n: "6", name: "Execution", issues: [313], after: ["5"] },
  { n: "7", name: "Paper live", issues: [314], after: ["3", "6"] },
];

/* ============================================================
   Small helpers
   ============================================================ */

const todayEt = () => etDateOf();

const isoDay = (d) => d.toISOString().slice(0, 10);
const addDays = (d, n) => {
  const x = new Date(d.getTime());
  x.setUTCDate(x.getUTCDate() + n);
  return x;
};

// How long ago an ISO instant was, in one unit.
function ago(iso) {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (!isFinite(s)) return "—";
  if (s < 90) return `${Math.round(s)}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 172800) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

// Every weekday in [from, to] that XNYS actually opens. Dates are handled in UTC throughout
// so a browser west of Greenwich can't shift a day.
function sessionsBetween(from, to) {
  const out = [];
  const end = new Date(to + "T00:00:00Z");
  for (let d = new Date(from + "T00:00:00Z"); d <= end; d = addDays(d, 1)) {
    const dow = d.getUTCDay();
    const iso = isoDay(d);
    if (dow >= 1 && dow <= 5 && !HOLIDAYS.has(iso)) out.push(iso);
  }
  return out;
}

const SESSIONS = sessionsBetween(COLLECT_START, COLLECT_END);
const SESSION_SET = new Set(SESSIONS);

// Same contract as `checkRow` below: the middle slot is raw HTML — callers pass
// `<span class="muted"> / total</span>` to grey out a denominator — while the label and sub-line
// are escaped here. Named `valueHtml` so the asymmetry reads as deliberate rather than missed
// (#515).
const tile = (label, valueHtml, sub) =>
  `<div class="tile"><div class="tile-l">${esc(label)}</div>` +
  `<div class="tile-v">${valueHtml}</div>` +
  (sub ? `<div class="tile-s">${esc(sub)}</div>` : "") +
  `</div>`;

/* ============================================================
   Options bar
   ============================================================ */

createOptionsBar("optbar", {
  primary: [
    { type: "readout", id: "pl-phase", label: "PHASE", value: "—" },
    { type: "readout", id: "pl-collected", label: "COLLECTED", value: "—" },
    { type: "readout", id: "pl-countdown", label: "SESSIONS LEFT", value: "—" },
    { type: "readout", id: "pl-harvestread", label: "HARVESTED", value: "—" },
    { type: "readout", id: "pl-gatesread", label: "GATES DONE", value: "—" },
    { type: "btn", id: "pl-refresh", label: "Refresh", title: "Refresh now" },
  ],
  extra: [
    {
      type: "note",
      value:
        "Counts come from the published dataset; gate status from GitHub issue state. " +
        "The reasoning behind the plan is on the Reports tab.",
    },
  ],
  onChange: (id) => {
    if (id === "pl-refresh") refresh();
  },
});

/* ============================================================
   Deriving state from the data
   ============================================================ */

// A day counts as collected only if it is a real session — the 2026-07-04/05 weekend rows
// the tracker captured before the calendar gate (#137) landed are data, but not progress.
function collectionState(index) {
  const byDate = new Map();
  let opps = 0;
  let triggered = 0;
  for (const e of (index && index.dates) || []) {
    if (!SESSION_SET.has(e.date)) continue;
    const list = e.opportunities || [];
    byDate.set(e.date, list.length);
    opps += list.length;
    triggered += list.filter((o) => o.triggered).length;
  }
  const today = todayEt();
  // A session only counts as *due* once it's over: today's rows land in the EOD batch.
  const due = SESSIONS.filter((d) => d < today);
  const missing = due.filter((d) => !byDate.has(d));
  const dates = [...byDate.keys()].sort();
  return {
    byDate,
    today,
    done: byDate.size,
    total: SESSIONS.length,
    left: SESSIONS.length - byDate.size,
    due: due.length,
    missing,
    opps,
    triggered,
    lastDate: dates.length ? dates[dates.length - 1] : null,
    frac: SESSIONS.length ? byDate.size / SESSIONS.length : 0,
  };
}

function bookState(pf) {
  const book = pf && pf.books && pf.books.adaptive;
  return {
    stats: (book && book.stats) || null,
    next: (book && book.next_session) || null,
    config: (pf && pf.config) || null,
    startEquity: pf ? pf.start_equity : null,
  };
}

// Gate status, derived. A gate is done when every issue it names is closed; blocked when a
// gate it depends on isn't done; open otherwise. `states` empty (GitHub unreachable) means
// nothing reads as done and the ladder degrades to the dependency graph.
function gateState(states) {
  // Two passes: a gate can depend on one listed after it (1 waits on 4), so every `done` has to
  // be known before any `waiting` is resolved.
  const doneById = new Map(
    GATES.map((g) => {
      const known = g.issues.filter((n) => states.has(n));
      return [g.n, known.length === g.issues.length && g.issues.every((n) => states.get(n) === "closed")];
    }),
  );
  const rows = GATES.map((g) => {
    const done = doneById.get(g.n);
    const waiting = (g.after || []).filter((d) => !doneById.get(d));
    let status = "open";
    if (done) status = "done";
    else if (waiting.length) status = "blocked";
    return { ...g, done, status, waiting, states };
  });
  return {
    rows,
    done: rows.filter((r) => r.done).length,
    open: rows.filter((r) => r.status === "open").length,
    total: rows.length,
    live: states.size > 0,
  };
}

/* ============================================================
   The spine — three phases, each with its own counters
   ============================================================ */

const PHASE_BADGE = { live: "Now", locked: "Locked", done: "Done" };

function phaseMetrics(id, collection, book, gates) {
  const cfg = book.config || {};
  const s = book.stats;
  if (id === "p1") {
    return [
      ["Sessions", `${collection.done} / ${collection.total}`],
      ["Opportunities", String(collection.opps)],
      ["Paper trades", String(s ? s.n_trades || 0 : 0)],
    ];
  }
  if (id === "p2") {
    return [
      ["Gates done", `${gates.done} / ${gates.total}`],
      ["Open now", String(gates.open)],
      ["Earliest", "2026-10-01"],
    ];
  }
  return [
    ["Capital", book.startEquity != null ? `$${Number(book.startEquity).toFixed(0)}` : "—"],
    ["Risk / trade", cfg.risk_fraction != null ? fmtPctPlain(cfg.risk_fraction, 1) : "—"],
    ["Trades / day", cfg.max_trades_per_day != null ? `≤ ${cfg.max_trades_per_day}` : "—"],
  ];
}

function renderSpine(collection, book, gates) {
  const allGatesDone = gates.total > 0 && gates.done === gates.total;
  const state = { p1: allGatesDone ? "done" : "live", p2: allGatesDone ? "live" : "locked", p3: "locked" };
  const bar = { p1: collection.frac, p2: gates.total ? gates.done / gates.total : 0, p3: 0 };

  el("pl-spine").innerHTML = PHASES.map((p) => {
    const st = state[p.id];
    const metrics = phaseMetrics(p.id, collection, book, gates)
      .map(
        ([k, v]) =>
          `<div class="plan-metric"><span class="plan-metric-l">${esc(k)}</span>` +
          `<span class="plan-metric-v">${esc(v)}</span></div>`,
      )
      .join("");
    return (
      `<article class="plan-phase plan-phase-${esc(st)}" id="pl-phase-${esc(p.id)}">` +
      `<header class="plan-phase-h">` +
      `<span class="plan-phase-n">${esc(p.n)}</span>` +
      `<span class="plan-phase-name">${esc(p.name)}</span>` +
      `<span class="plan-phase-tag">${esc(p.tag)}</span>` +
      `<span class="pill plan-phase-badge">${esc(PHASE_BADGE[st])}</span>` +
      `</header>` +
      `<p class="plan-phase-win muted">${esc(p.window)}</p>` +
      `<div class="plan-metrics">${metrics}</div>` +
      `<div class="plan-bar"><span class="plan-bar-fill" style="width:${(bar[p.id] * 100).toFixed(1)}%"></span></div>` +
      `</article>`
    );
  }).join("");

  el("pl-phase").textContent = allGatesDone ? "2 · PAPER" : "1 · TRACKER";
}

/* ============================================================
   Column 1 — the countdown ring, the session grid, the book
   ============================================================ */

const RING_R = 34;
const RING_C = 2 * Math.PI * RING_R;

function ringSvg(frac) {
  const on = Math.max(0, Math.min(1, frac)) * RING_C;
  return (
    `<svg viewBox="0 0 88 88" class="plan-ring-svg" role="img" ` +
    `aria-label="${(frac * 100).toFixed(0)}% of the collection window complete">` +
    `<circle class="plan-ring-track" cx="44" cy="44" r="${RING_R}"></circle>` +
    `<circle class="plan-ring-arc" cx="44" cy="44" r="${RING_R}" ` +
    `stroke-dasharray="${on.toFixed(2)} ${(RING_C - on).toFixed(2)}" ` +
    `transform="rotate(-90 44 44)"></circle>` +
    `<text class="plan-ring-pct" x="44" y="44">${(frac * 100).toFixed(0)}%</text>` +
    `<text class="plan-ring-sub" x="44" y="56">COLLECTED</text>` +
    `</svg>`
  );
}

function renderProgress(c) {
  el("pl-ring").innerHTML = ringSvg(c.frac);
  el("pl-ring-tiles").innerHTML =
    tile("Sessions", `${c.done}<span class="muted"> / ${c.total}</span>`, "trading days in") +
    tile("Left to run", String(c.left), c.left === 1 ? "session" : "sessions") +
    tile("Window ends", COLLECT_END.slice(5), COLLECT_END.slice(0, 4)) +
    tile("Opportunities", String(c.opps), "flagged so far");

  el("pl-collected").textContent = `${c.done} / ${c.total}`;
  el("pl-countdown").textContent = String(c.left);
  renderHeat(c);
}

// GitHub-contribution-style grid: one column per week, Mon–Fri down each column, from the
// Monday of the start week to the Friday of the end week so every column holds exactly five
// cells and the grid can flow by column.
function renderHeat(c) {
  const first = new Date(COLLECT_START + "T00:00:00Z");
  const gridStart = addDays(first, -((first.getUTCDay() + 6) % 7)); // back to Monday
  const last = new Date(COLLECT_END + "T00:00:00Z");
  const gridEnd = addDays(last, (5 - last.getUTCDay() + 7) % 7); // forward to Friday

  const cells = [];
  for (let d = gridStart; d <= gridEnd; d = addDays(d, 1)) {
    const dow = d.getUTCDay();
    if (dow === 0 || dow === 6) continue;
    const iso = isoDay(d);
    let cls = "plan-cell";
    let title = iso;
    if (!SESSION_SET.has(iso)) {
      // Outside the window, or a day XNYS doesn't open.
      cls += HOLIDAYS.has(iso) ? " plan-cell-holiday" : " plan-cell-off";
      title += HOLIDAYS.has(iso) ? " · market closed" : " · outside the window";
    } else if (c.byDate.has(iso)) {
      const n = c.byDate.get(iso);
      cls += ` plan-cell-l${n === 0 ? 1 : n < 4 ? 2 : n < 9 ? 3 : 4}`;
      title += ` · ${n} ${n === 1 ? "opportunity" : "opportunities"}`;
    } else {
      cls += iso < c.today ? " plan-cell-gap" : " plan-cell-todo";
      title += iso < c.today ? " · missing" : " · to come";
    }
    if (iso === c.today) cls += " plan-cell-today";
    cells.push(`<span class="${cls}" title="${esc(title)}"></span>`);
  }
  el("pl-heat").innerHTML = cells.join("");
  el("pl-heat-cap").innerHTML =
    `${esc(COLLECT_START)} → ${esc(COLLECT_END)} · one square per session &nbsp; ` +
    `<span class="plan-key"><i class="plan-cell plan-cell-todo"></i>to&nbsp;come</span> &nbsp; ` +
    `<span class="plan-key"><i class="plan-cell plan-cell-gap"></i>missing</span> &nbsp; ` +
    `<span class="plan-key">fewer<i class="plan-cell plan-cell-l1"></i>` +
    `<i class="plan-cell plan-cell-l2"></i><i class="plan-cell plan-cell-l3"></i>` +
    `<i class="plan-cell plan-cell-l4"></i>more opportunities</span>`;
}

function renderBook(book) {
  const s = book.stats;
  if (!s) {
    el("pl-book").innerHTML = tile("Paper book", "—", "no data yet");
    return;
  }
  el("pl-book").innerHTML =
    tile("Trades", String(s.n_trades || 0), `${s.wins || 0}W / ${s.losses || 0}L`) +
    tile("Avg", fmtRSigned(s.avg_r), "per trade") +
    tile("Win rate", fmtPctPlain(s.win_rate, 0), "of closed trades") +
    tile("Balance", `$${(s.end_equity || 0).toFixed(0)}`, `${fmtPct(s.return_pct, 0)} · DD ${fmtPctPlain(s.max_drawdown_pct, 0)}`);
}

/* ============================================================
   Column 1 — the historical harvest
   ============================================================ */

// The 2-year pre-market rebuild (#430/#431), read straight off `status.json.harvest` (#450).
// It is a second collection front running beside the live one: ~500 vendor sessions walked
// newest-first, a whole session at a time, each landing in the recon store the night it finishes —
// so "done" and "still to go" are both real numbers at any moment, not a wait for a batch to end.
//
// Two things the numbers do NOT say, and the reason the denominator never quite fills:
// `sessions_in_window` is the calendar count of the harvest's lookback, not reduced by the days
// the tracker already collected live (that would cost an unscoped store read on every status
// tick), and not reduced by the vendor's entitlement floor — the dates before it can never be
// bought at all.
function harvestState(status) {
  const h = (status && status.harvest) || null;
  if (!h) return { live: false };
  const done = h.sessions_done || 0;
  const daily = h.daily_done || 0;
  const total = h.sessions_in_window || 0;
  const hrs = h.hours_since_progress;
  return {
    live: true,
    done,
    daily,
    total,
    left: Math.max(0, total - done),
    frac: total ? Math.min(1, done / total) : 0,
    dailyFrac: total ? Math.min(1, daily / total) : 0,
    dailyDone: total > 0 && daily >= total,
    finished: total > 0 && done >= total,
    calls: h.calls_spent || 0,
    oldest: h.oldest || null,
    newest: h.newest || null,
    floor: h.entitlement_floor || null,
    // The box's own reading, not a client-side diff against `updated_utc`: the staleness call and
    // the number shown for it then agree even when the two clocks don't.
    hours: hrs == null ? null : hrs,
    stale: hrs != null && hrs > HARVEST_STALE_H,
  };
}

function renderHarvest(hv) {
  el("pl-hv-count").textContent = hv.live ? `${hv.done} / ${hv.total} sessions` : "";
  el("pl-harvestread").textContent = hv.live ? `${hv.done} / ${hv.total}` : "—";

  if (!hv.live) {
    el("pl-harvest").innerHTML = checkRow({
      label: "Sessions rebuilt",
      valueHtml: "—",
      sub: "no checkpoint published yet",
      status: "NOT STARTED",
      title:
        "The harvest publishes its progress from its checkpoint, which appears once the nightly " +
        "job has completed its first session.",
    });
    el("pl-hv-cap").textContent = "";
    return;
  }

  const span =
    hv.oldest && hv.newest
      ? `${esc(hv.oldest)} <span class="muted">→</span> ${esc(hv.newest)}`
      : "—";

  el("pl-harvest").innerHTML = [
    checkRow({
      label: "Sessions rebuilt",
      valueHtml: `${hv.done}<span class="muted"> / ${hv.total}</span>`,
      sub: hv.newest
        ? `${hv.left} still to go · newest-first from ${hv.newest}`
        : `${hv.left} still to go · minute bars not started`,
      status: hv.finished ? "COMPLETE" : hv.stale ? "STALLED" : "RUNNING",
      tone: hv.finished ? "ok" : hv.stale ? "warn" : "run",
      bar: hv.frac,
      title:
        "Pre-market sessions rebuilt from vendor minute bars, against the calendar sessions in " +
        "the harvest's lookback window. That window is not reduced by the days already collected " +
        "live, so the count finishes a little short of the total.",
    }),
    checkRow({
      label: "Universe pass",
      valueHtml: `${hv.daily}<span class="muted"> / ${hv.total}</span>`,
      sub: "grouped-daily bars + previous closes",
      status: hv.dailyDone ? "DONE" : "RUNNING",
      tone: hv.dailyDone ? "ok" : "run",
      bar: hv.dailyFrac,
      title:
        "Phase 1 of the harvest, and a prerequisite of the minute-bar pass: without the previous " +
        "day's close the reconstruction fires a median 18 minutes early (#428).",
    }),
    checkRow({
      label: "History covered",
      valueHtml: `<span class="plan-hv-span">${span}</span>`,
      sub: hv.floor
        ? `${hv.done} sessions deep · vendor history starts ${hv.floor}`
        : `${hv.done} sessions deep`,
      title: "The contiguous block of rebuilt sessions, and the oldest date the vendor will sell.",
    }),
  ].join("");

  // Calls spent and freshness are one line, not two more rows: the count is a budget reading and
  // the hours are the failure signal, neither of which is a step of the job.
  const cap = el("pl-hv-cap");
  cap.className = `plan-cap ${hv.stale ? "warn" : "muted"}`;
  cap.textContent =
    `${hv.calls.toLocaleString("en-US")} vendor calls · last progress ` +
    (hv.hours == null ? "unknown" : `${hv.hours}h ago`) +
    (hv.stale ? " — a night was missed" : "");
  cap.title =
    "The harvest runs nightly and is deliberately off the tracker's dead-man's switch, so a " +
    `checkpoint that has not moved in ${HARVEST_STALE_H}h is the only signal that a night was lost.`;
}

/* ============================================================
   Column 2 — the checks
   ============================================================ */

// One row: a label, the number, a one-line sub-value, and a status pill. `bar` draws a
// progress line under the row. Everything passed in is computed; nothing is hard-coded.
//
// `valueHtml` is the one field interpolated raw, and the name says so (#515): five callers pass
// `<span class="muted"> / total</span>` to grey out a denominator, so escaping it would render
// the markup as text. Every caller therefore escapes its own text — the audit flagged the bare
// `value` as an injection risk, and it isn't (all 14 sites pass literals, numeric conversions,
// `fmtPctPlain`/`fmtRSigned` output or pre-escaped content, and the source is our own
// `status.json`) — but a raw slot indistinguishable from five escaped ones is a trap.
function checkRow({ label, valueHtml, sub, status, tone, bar, title }) {
  const t = title ? ` title="${esc(title)}"` : "";
  return (
    `<li class="plan-check plan-check-${esc(tone || "none")}"${t}>` +
    `<div class="plan-check-h">` +
    `<span class="plan-check-l">${esc(label)}</span>` +
    (status ? `<span class="pill plan-check-pill">${esc(status)}</span>` : "") +
    `</div>` +
    `<div class="plan-check-v">${valueHtml}</div>` +
    (sub ? `<div class="plan-check-s muted">${esc(sub)}</div>` : "") +
    (bar != null
      ? `<div class="plan-bar plan-check-bar"><span class="plan-bar-fill" ` +
        `style="width:${(Math.max(0, Math.min(1, bar)) * 100).toFixed(1)}%"></span></div>`
      : "") +
    `</li>`
  );
}

// Is the adaptive target actually adapting? (#463) The book carries an R target either way, so
// nothing on the page used to distinguish the optimiser's pick from the fallback standing in for
// it — and the live book spent its whole first 28 days on the fallback while the risk ladder beside
// it moved normally. `target_fitted` post-dates these payloads: absent, the row drops out entirely
// rather than reporting a state it cannot know.
function targetFitRow(next, cfg) {
  if (!next || next.target_fitted == null) return "";
  const fitted = next.target_fitted;
  const n = next.target_trailing_n;
  const need = cfg.adaptive_min_samples;
  // null window = the fit uses every trade there is (#476), which must not print as "null days".
  const scope = cfg.adaptive_window_days == null ? "all history" : `the trailing ${cfg.adaptive_window_days} days`;
  // Three outcomes, three rows (#476). "margin" is not a degraded "fallback": the optimiser ran and
  // produced an answer, and the gate judged the evidence too thin to change the exit rule on. That
  // is the system working, so it reads "ok", not "warn".
  const held = next.target_status === "margin";
  const z = next.target_edge_z == null ? null : Number(next.target_edge_z);
  return checkRow({
    label: "Adaptive target",
    valueHtml: `${Number(next.target_r).toFixed(1)}R`,
    sub: held
      ? `held — ${next.target_considered_r}R preferred but only ${z == null ? "—" : z.toFixed(2)}σ of edge over ${n} trades (${cfg.target_switch_z}σ needed)`
      : fitted
        ? `re-fit over ${n} trades from ${scope}`
        : `fallback — ${scope} holds ${n} of the ${need} trades the re-fit needs`,
    status: held ? "HELD" : fitted ? "FITTED" : "FALLBACK",
    tone: held ? "ok" : fitted ? "ok" : "warn",
    bar: held || fitted || !need ? null : Math.min(1, n / need),
    title:
      `The exit target the next setup uses. It is re-fit daily to the highest-expectancy value on the ` +
      `${(cfg.target_grid || []).map((t) => t + "R").join(" / ")} grid over ${scope}, falls back to ` +
      `${cfg.target_fallback_r}R until that sample holds ${need} trades, and must beat the fallback by ` +
      `${cfg.target_switch_z}σ (paired — the same trades scored under both rules) before it switches.`,
  });
}

function renderChecks(c, book, status) {
  const s = book.stats;
  const next = book.next;
  const cfg = book.config || {};
  const n = s ? s.n_trades || 0 : 0;
  const perSession = c.done ? c.opps / c.done : 0;
  const trigRate = c.opps ? c.triggered / c.opps : 0;
  const gap = c.missing.length;
  const published = status && status.generated_utc;
  const ageMs = published ? Date.now() - new Date(published).getTime() : null;
  const stale = ageMs == null || ageMs > STALE_PUBLISH_MS;
  const atTopRung = next ? next.rung >= next.n_rungs - 1 : false;
  const throttleOff = next ? next.n_rungs <= 1 : false; // one rung = no ladder to walk (#474)

  const rows = [
    checkRow({
      label: "Sessions collected",
      valueHtml: `${c.done}<span class="muted"> / ${c.total}</span>`,
      sub: `${c.done} of ${c.due} sessions due so far`,
      status: gap ? `${gap} MISSING` : "NO GAPS",
      tone: gap ? "bad" : "ok",
      bar: c.frac,
      title: gap ? `Missing: ${c.missing.join(", ")}` : "Every session due in this window is in the store.",
    }),
    checkRow({
      label: "Sessions left",
      valueHtml: String(c.left),
      sub: `window ends ${COLLECT_END}`,
      status: c.left > 0 ? "RUNNING" : "WINDOW CLOSED",
      tone: c.left > 0 ? "run" : "ok",
    }),
    checkRow({
      label: "Opportunities flagged",
      valueHtml: String(c.opps),
      sub: `${perSession.toFixed(1)} per collected session`,
    }),
    checkRow({
      label: "Trigger rate",
      valueHtml: fmtPctPlain(trigRate, 0),
      sub: `${c.triggered} of ${c.opps} setups fired their entry`,
      title: "Share of flagged opportunities where price crossed the 1-tick trigger above the consolidation high.",
    }),
    checkRow({
      label: "Sample for a verdict",
      valueHtml: `${n}<span class="muted"> / ${VERDICT_TRADES}</span>`,
      sub: n >= VERDICT_TRADES ? "sample target met" : `${VERDICT_TRADES - n} trades short`,
      status: n >= VERDICT_TRADES ? "READY" : "THIN",
      tone: n >= VERDICT_TRADES ? "ok" : "warn",
      bar: Math.min(1, n / VERDICT_TRADES),
      title: `Placeholder bar of ${VERDICT_TRADES} paper trades. Gate 2 (#310) replaces it with the written go/no-go criteria.`,
    }),
    // `n_rungs - 1` steps sit above rung 0, which is the 0% floor (the book sitting out). A
    // one-rung ladder is the throttle switched off (#474) — there is no rung to report, and
    // "FULL" would imply a ladder that could be somewhere else.
    checkRow({
      label: "Risk in force",
      valueHtml: next ? fmtPctPlain(next.risk_fraction, 1) : "—",
      sub: !next
        ? "no next-session state"
        : (throttleOff
            ? `flat — kill-switch off · `
            : `rung ${next.rung}/${next.n_rungs - 1} · `) +
          `target ${Number(next.target_r).toFixed(1)}R · $${Number(next.risk_budget_usd).toFixed(2)} budget`,
      status: !next
        ? null
        : throttleOff
          ? "FLAT"
          : next.risk_fraction === 0
            ? "SITTING OUT"
            : atTopRung
              ? "FULL"
              : "THROTTLED",
      tone: !next
        ? "none"
        : throttleOff
          ? "ok"
          : next.risk_fraction === 0
            ? "bad"
            : atTopRung
              ? "ok"
              : "warn",
      title: throttleOff
        ? "What the adaptive book will risk on its next setup. The kill-switch ladder is switched off, so this does not vary with recent results."
        : "What the adaptive book will risk on its next setup — the kill-switch rung and the target now in force.",
    }),
    targetFitRow(next, cfg),
    checkRow({
      label: "Cost drag",
      valueHtml: s ? `$${(s.total_costs_usd || 0).toFixed(2)}` : "—",
      sub:
        s && s.end_equity
          ? `${fmtPctPlain((s.total_costs_usd || 0) / s.end_equity, 1)} of balance · ` +
            `$${(s.commission_usd || 0).toFixed(2)} commission`
          : "—",
      title: "Commission, exchange and clearing fees plus the market-data subscription, charged to the book.",
    }),
    checkRow({
      label: "Data freshness",
      valueHtml: c.lastDate ? esc(c.lastDate) : "—",
      sub: `last session collected · published ${ago(published)}`,
      status: stale ? "STALE" : "FRESH",
      tone: stale ? "warn" : "ok",
      title:
        `Publish runs every 15 minutes; anything over ${STALE_PUBLISH_MS / 60_000} minutes ` +
        "old means the box or the workflow is behind.",
    }),
  ];
  el("pl-checks").innerHTML = rows.join("");
}

/* ============================================================
   Column 3 — the gate ladder
   ============================================================ */

const GATE_LABEL = { done: "Done", open: "Open now", blocked: "Blocked" };

function renderGates(gates) {
  el("pl-gates-count").textContent = `${gates.done} / ${gates.total} done · ${gates.open} open now`;
  el("pl-gatesread").textContent = `${gates.done} / ${gates.total}`;
  el("pl-gates").innerHTML = gates.rows
    .map((g) => {
      const pill =
        g.status === "blocked" && g.waiting.length
          ? `Blocked · ${esc(g.waiting.join(" · "))}`
          : GATE_LABEL[g.status];
      const issues = g.issues
        .map((num) => {
          const st = g.states.get(num);
          const cls = st === "closed" ? "plan-iss-done" : st === "open" ? "plan-iss-open" : "plan-iss-unknown";
          const mark = st === "closed" ? "✓" : st === "open" ? "○" : "·";
          return (
            `<a class="plan-iss ${cls}" href="${issueUrl(num)}" target="_blank" rel="noopener" ` +
            `title="#${num} — ${st || "state unavailable"}">#${num}<span class="plan-iss-m">${mark}</span></a>`
          );
        })
        .join("");
      return (
        `<li class="plan-gate plan-gate-${esc(g.status)}">` +
        `<span class="plan-gate-n">${esc(g.n)}</span>` +
        `<div class="plan-gate-body">` +
        `<p class="plan-gate-h"><span class="plan-gate-name">${esc(g.name)}</span>` +
        `<span class="pill plan-gate-pill">${pill}</span></p>` +
        `<p class="plan-gate-iss">${issues}</p>` +
        `</div></li>`
      );
    })
    .join("");
  el("pl-gates-src").textContent = gates.live
    ? "Status read from GitHub issue state — a gate closes here when its issues close."
    : "GitHub issue state unavailable; showing the dependency graph only.";
}

/* ============================================================
   Poll loop
   ============================================================ */

async function refresh() {
  setStatusPage("updating…");
  try {
    const [index, pf, status] = await Promise.all([
      fetchJson("index.json"),
      fetchJson("portfolio.json"),
      fetchJson("status.json"),
    ]);
    const collection = collectionState(index);
    const book = bookState(pf);
    const harvest = harvestState(status);
    // Best-effort: a rate-limited or offline GitHub yields an empty map and the ladder
    // falls back to its dependency graph rather than the page failing.
    const states = await issueStates(GATES.flatMap((g) => g.issues)).catch(() => new Map());
    const gates = gateState(states);

    renderSpine(collection, book, gates);
    renderProgress(collection);
    renderBook(book);
    renderHarvest(harvest);
    renderChecks(collection, book, status);
    renderGates(gates);

    setBanner("pl-error", "");
    setStatusPage(
      `plan · ${collection.done}/${collection.total} sessions · ` +
        (harvest.live ? `harvest ${harvest.done}/${harvest.total} · ` : "") +
        `${gates.done}/${gates.total} gates`,
    );
  } catch (e) {
    // Not `el("pl-error")`: `el` THROWS, and the failure being reported here may itself be a
    // MissingElementError — in which case the banner lookup would re-throw over the top of the
    // original error and the user would see nothing at all. `showError` resolves the banner with
    // a bare lookup and keeps the stale-asset wording, which this page is the likeliest to need:
    // it renders more markup from `status.json` than any other (#515).
    showError("pl-error", "Failed to load plan data", e);
    setStatusPage("update failed");
  }
}

refresh();
setInterval(refresh, POLL_MS);
