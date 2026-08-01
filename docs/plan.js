// Plan page (#410, rebuilt in #414): a status board, not an essay.
//
// Every value here is computed at render time — collection progress and opportunity counts
// from `index.json`, the paper book and the risk state in force from `portfolio.json`,
// publish freshness from `status.json`, and each Phase-2 gate's status from whether the
// issues it names are closed on GitHub (`js/gh.js`). The only committed text is labels:
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
import { issueStates, issueUrl } from "./js/gh.js";
import { esc, fmtPct, fmtPctPlain, fmtRSigned } from "./js/fmt.js";

const POLL_MS = 5 * 60_000; // the plan moves in days; poll lazily

/* ============================================================
   The committed skeleton — names, windows, issue numbers
   ============================================================ */

// The 3-month collection window (decisions.md §11 + phase-2-roadmap Gate 3:
// "3-month collection completes (~2026-10-01)"). The XNYS closures inside it — the app's
// calendar of record is `exchange_calendars` XNYS (#137), and these are the only two
// sessions it drops in this window.
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
  { id: "p2", n: "02", name: "Paper", tag: "shadow orders", window: "gated · earliest 2026-10-01" },
  { id: "p3", n: "03", name: "Live", tag: "real capital", window: "gated · after paper" },
];

// The Phase-2 gates (research/phase-2-roadmap.md, epic #308). `issues` is what decides the
// status — a gate is done when all of them are closed; `after` is the dependency graph the
// page falls back to when GitHub can't be reached. No status is written here.
const GATES = [
  { n: "0", name: "Truth debt", issues: [302, 297, 270] },
  { n: "1", name: "Spread capture", issues: [309] },
  { n: "2", name: "Go/no-go criteria", issues: [310] },
  // Gate 3 is the collection countdown: a calendar wait that runs whether or not 1 and 2
  // are closed, so it reports its own progress rather than sitting greyed out behind them.
  { n: "3", name: "Validation", issues: [49], after: ["1", "2"], progress: "collection" },
  { n: "4", name: "Market data", issues: [311] },
  { n: "5", name: "Live detection", issues: [312], after: ["0", "4"] },
  { n: "6", name: "Execution", issues: [313], after: ["5"] },
  { n: "7", name: "Paper live", issues: [314], after: ["3", "6"] },
];

/* ============================================================
   Small helpers
   ============================================================ */

const el = (id) => document.getElementById(id);
// en-CA renders YYYY-MM-DD; ET so "today" flips with the trading date, not the browser.
const _etDate = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" });
const todayEt = () => _etDate.format(new Date());

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

const tile = (label, value, sub) =>
  `<div class="tile"><div class="tile-l">${esc(label)}</div>` +
  `<div class="tile-v">${value}</div>` +
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
function gateState(states, collection) {
  const doneById = new Map();
  const rows = GATES.map((g) => {
    const known = g.issues.filter((n) => states.has(n));
    const done = known.length === g.issues.length && g.issues.every((n) => states.get(n) === "closed");
    doneById.set(g.n, done);
    const waiting = (g.after || []).filter((d) => !doneById.get(d));
    let status = "open";
    if (done) status = "done";
    else if (g.progress === "collection") status = "running";
    else if (waiting.length) status = "blocked";
    const frac = g.progress === "collection" ? collection.frac : null;
    return { ...g, done, status, waiting, frac, states };
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
   Column 2 — the checks
   ============================================================ */

// One row: a label, the number, a one-line sub-value, and a status pill. `bar` draws a
// progress line under the row. Everything passed in is computed; nothing is hard-coded.
function checkRow({ label, value, sub, status, tone, bar, title }) {
  const t = title ? ` title="${esc(title)}"` : "";
  return (
    `<li class="plan-check plan-check-${esc(tone || "none")}"${t}>` +
    `<div class="plan-check-h">` +
    `<span class="plan-check-l">${esc(label)}</span>` +
    (status ? `<span class="pill plan-check-pill">${esc(status)}</span>` : "") +
    `</div>` +
    `<div class="plan-check-v">${value}</div>` +
    (sub ? `<div class="plan-check-s muted">${esc(sub)}</div>` : "") +
    (bar != null
      ? `<div class="plan-bar plan-check-bar"><span class="plan-bar-fill" ` +
        `style="width:${(Math.max(0, Math.min(1, bar)) * 100).toFixed(1)}%"></span></div>`
      : "") +
    `</li>`
  );
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
  const stale = ageMs == null || ageMs > 60 * 60_000;
  const atTopRung = next ? next.rung >= next.n_rungs - 1 : false;

  const rows = [
    checkRow({
      label: "Sessions collected",
      value: `${c.done}<span class="muted"> / ${c.total}</span>`,
      sub: `${c.done} of ${c.due} sessions due so far`,
      status: gap ? `${gap} MISSING` : "NO GAPS",
      tone: gap ? "bad" : "ok",
      bar: c.frac,
      title: gap ? `Missing: ${c.missing.join(", ")}` : "Every session due in this window is in the store.",
    }),
    checkRow({
      label: "Sessions left",
      value: String(c.left),
      sub: `window ends ${COLLECT_END}`,
      status: c.left > 0 ? "RUNNING" : "WINDOW CLOSED",
      tone: c.left > 0 ? "run" : "ok",
    }),
    checkRow({
      label: "Opportunities flagged",
      value: String(c.opps),
      sub: `${perSession.toFixed(1)} per collected session`,
    }),
    checkRow({
      label: "Trigger rate",
      value: fmtPctPlain(trigRate, 0),
      sub: `${c.triggered} of ${c.opps} setups fired their entry`,
      title: "Share of flagged opportunities where price crossed the 1-tick trigger above the consolidation high.",
    }),
    checkRow({
      label: "Sample for a verdict",
      value: `${n}<span class="muted"> / ${VERDICT_TRADES}</span>`,
      sub: n >= VERDICT_TRADES ? "sample target met" : `${VERDICT_TRADES - n} trades short`,
      status: n >= VERDICT_TRADES ? "READY" : "THIN",
      tone: n >= VERDICT_TRADES ? "ok" : "warn",
      bar: Math.min(1, n / VERDICT_TRADES),
      title: `Placeholder bar of ${VERDICT_TRADES} paper trades. Gate 2 (#310) replaces it with the written go/no-go criteria.`,
    }),
    // `n_rungs - 1` steps sit above rung 0, which is the 0% floor (the book sitting out).
    checkRow({
      label: "Risk in force",
      value: next ? fmtPctPlain(next.risk_fraction, 1) : "—",
      sub: next
        ? `rung ${next.rung}/${next.n_rungs - 1} · target ${Number(next.target_r).toFixed(1)}R · ` +
          `$${Number(next.risk_budget_usd).toFixed(2)} budget`
        : "no next-session state",
      status: next ? (next.risk_fraction === 0 ? "SITTING OUT" : atTopRung ? "FULL" : "THROTTLED") : null,
      tone: next ? (next.risk_fraction === 0 ? "bad" : atTopRung ? "ok" : "warn") : "none",
      title: "What the adaptive book will risk on its next setup — the kill-switch rung and re-fitted target now in force.",
    }),
    checkRow({
      label: "Cost drag",
      value: s ? `$${(s.total_costs_usd || 0).toFixed(2)}` : "—",
      sub:
        s && s.end_equity
          ? `${fmtPctPlain((s.total_costs_usd || 0) / s.end_equity, 1)} of balance · ` +
            `$${(s.commission_usd || 0).toFixed(2)} commission`
          : "—",
      title: "Commission, exchange and clearing fees plus the market-data subscription, charged to the book.",
    }),
    checkRow({
      label: "Data freshness",
      value: c.lastDate ? esc(c.lastDate) : "—",
      sub: `last session collected · published ${ago(published)}`,
      status: stale ? "STALE" : "FRESH",
      tone: stale ? "warn" : "ok",
      title: "Publish runs every 15 minutes; anything over an hour old means the box or the workflow is behind.",
    }),
  ];
  el("pl-checks").innerHTML = rows.join("");
}

/* ============================================================
   Column 3 — the gate ladder
   ============================================================ */

const GATE_LABEL = { done: "Done", open: "Open now", running: "Running", blocked: "Blocked" };

function renderGates(gates) {
  el("pl-gates-count").textContent = `${gates.done} / ${gates.total} done · ${gates.open} open now`;
  el("pl-gatesread").textContent = `${gates.done} / ${gates.total}`;
  el("pl-gates").innerHTML = gates.rows
    .map((g) => {
      const pill =
        g.status === "blocked" && g.waiting.length
          ? `Blocked · ${esc(g.waiting.join(" · "))}`
          : g.status === "running" && g.frac != null
            ? `Running · ${(g.frac * 100).toFixed(0)}%`
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
        (g.frac != null
          ? `<div class="plan-bar plan-gate-bar"><span class="plan-bar-fill" ` +
            `style="width:${(g.frac * 100).toFixed(1)}%"></span></div>`
          : "") +
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
    // Best-effort: a rate-limited or offline GitHub yields an empty map and the ladder
    // falls back to its dependency graph rather than the page failing.
    const states = await issueStates(GATES.flatMap((g) => g.issues)).catch(() => new Map());
    const gates = gateState(states, collection);

    renderSpine(collection, book, gates);
    renderProgress(collection);
    renderBook(book);
    renderChecks(collection, book, status);
    renderGates(gates);

    el("pl-error").hidden = true;
    setStatusPage(
      `plan · ${collection.done}/${collection.total} sessions · ${gates.done}/${gates.total} gates`,
    );
  } catch (e) {
    el("pl-error").hidden = false;
    el("pl-error").textContent = "Failed to load plan data: " + e.message;
    setStatusPage("update failed");
  }
}

refresh();
setInterval(refresh, POLL_MS);
