// Plan page (#410): the discipline screen. Every other page answers "what
// happened"; this one answers "where are we in the plan, what does the phase
// we're in forbid, and what does the next one look like".
//
// The plan text is a constant here rather than fetched — it is the *committed*
// plan, and it should only ever change by PR. Its sources of truth are
// `research/decisions.md` (the locked decisions) and `research/phase-2-roadmap.md`
// (the gate sequence); keep those and this in step.
//
// Everything with a number in it is live: the collection countdown and heatmap
// come from `dashboard-data/index.json`, the paper-book sample from
// `portfolio.json` — the same published JSON every other page reads.

import "./js/nav.js";
import { createOptionsBar } from "./js/options-bar.js";
import { setStatusPage } from "./js/status-bar.js";
import { fetchJson } from "./js/data.js";
import { esc, fmtPct, fmtRSigned } from "./js/fmt.js";

const POLL_MS = 5 * 60_000; // the plan moves in days; poll lazily

/* ============================================================
   The plan
   ============================================================ */

// The 3-month collection window (decisions.md §11 + phase-2-roadmap Gate 3:
// "3-month collection completes (~2026-10-01)"). The XNYS closures inside it —
// the app's calendar of record is `exchange_calendars` XNYS (#137), and these
// are the only two sessions it drops in this window.
const COLLECT_START = "2026-07-01";
const COLLECT_END = "2026-09-30";
const HOLIDAYS = new Set([
  "2026-07-03", // Independence Day (observed) — the junk session of #137
  "2026-09-07", // Labor Day
]);

const PHASES = [
  {
    id: "p1",
    n: "01",
    name: "Tracker",
    tag: "data collection",
    state: "live",
    window: `${COLLECT_START} → ${COLLECT_END}`,
    objective: "Record every flagged opportunity and what it would have paid. Place no orders.",
    bullets: [
      "Scanner + capture run 04:00–11:59 ET; the day's bars land in one EOD batch.",
      "The paper book takes the trades on paper — selected, sized, costed, exited.",
      "Store raw, compute on read: every rule stays re-runnable over the whole history.",
    ],
    exit: "Ends when three months of sessions are in and the book clears the Gate-2 bar.",
  },
  {
    id: "p2",
    n: "02",
    name: "Paper",
    tag: "shadow orders",
    state: "locked",
    window: "gated · earliest Oct 2026",
    objective: "Detect live, place paper orders, prove the live engine agrees with the replay.",
    bullets: [
      "Live detection ships log-only first — prefix stability is the sleeper risk.",
      "Pre-market is limit-only: the app fires every entry and every exit itself.",
      "Every paper fill is reconciled against the simulator that predicted it.",
    ],
    exit: "Ends when live fills track the sim and the edge survives real spreads.",
  },
  {
    id: "p3",
    n: "03",
    name: "Live",
    tag: "real capital",
    state: "locked",
    window: "gated · after a full paper period",
    objective: "$500 of real money. The same rules, the same size, the same two trades a day.",
    bullets: [
      "Re-validate the tradability gate on the live account — PRIIPs blocks some runners.",
      "Withdrawals, the CGT reserve and the box bill stop being a model and become cash.",
      "The strategy does not get louder because the money is real.",
    ],
    exit: "This is the destination — reached by finishing the phases, not by skipping them.",
  },
];

// The five rules of the phase we are actually in. Each carries the reason it
// exists, because a rule you can't justify is a rule you'll break.
const ORDERS = [
  {
    rule: "Place no orders. Not one.",
    why:
      "Phase 1 is a tracker by decision (decisions.md §11). There is no order code in the " +
      "repo at all — no bracket, no limit, nothing. There is deliberately nothing to click.",
  },
  {
    rule: "Never change a rule to catch a trade you're watching.",
    why:
      "Store raw, compute on read: a rule changed later re-scores the entire history on the " +
      "next publish. Nothing is lost by waiting for the sample — and a rule bent live is the " +
      "one change that can never be undone.",
  },
  {
    rule: "Judge the plan on the sample, not on the session.",
    why:
      "~2 qualifying setups a day. The 2026-07-31 time-of-day study found window spreads that " +
      "looked real were reproduced by chance 68% of the time. One green day is not evidence, " +
      "and neither is one red one.",
  },
  {
    rule: "Let the book take the trades.",
    why:
      "The virtual portfolio already takes every trade you would have taken — selected, sized " +
      "at 5% risk, costed at full IBKR tiered, exited on bars. If you would have taken it, " +
      "it is already in the trade log.",
  },
  {
    rule: "Advance by gate, not by feeling.",
    why:
      "Phase 2 opens when the gates close, not when the waiting gets boring. When the itch " +
      "comes, the productive answer is on the right: work a gate.",
  },
];

// The Phase-2 gates (research/phase-2-roadmap.md, epic #308). `state`:
//   open    — unblocked, workable today, needs nothing from the market
//   running — in flight (the collection countdown is Gate 3)
//   blocked — waiting on the gates named in `after`
const GATES = [
  {
    n: "0",
    name: "Truth debt",
    issues: "#302 · #297 · #270",
    state: "open",
    note: "Settings flip and docs are done; the unrunnable spike import is not.",
  },
  {
    n: "1",
    name: "Spread capture",
    issues: "#309",
    state: "open",
    note: "BID_ASK in the EOD batch → a quotes table. Sets the exit-limit policy from evidence.",
  },
  {
    n: "2",
    name: "Go/no-go criteria",
    issues: "#310",
    state: "open",
    note: "Write the bar for entering Phase 2 now — before the data can argue back.",
  },
  {
    n: "3",
    name: "Validation",
    issues: "#49",
    state: "running",
    note: "The collection countdown. A calendar wait, nothing else.",
  },
  {
    n: "4",
    name: "Market data",
    issues: "#311",
    state: "open",
    note: "The $10/mo L1 bundle. Unblocks everything real-time; already in the cost model.",
  },
  {
    n: "5",
    name: "Live detection",
    issues: "#312",
    state: "blocked",
    after: "0 · 4",
    note: "Shadow mode: stream bars, detect, log only. Measures live-vs-replay drift.",
  },
  {
    n: "6",
    name: "Execution",
    issues: "#313",
    state: "blocked",
    after: "5",
    note: "Limit entries, app-side stops, an OMS. The first code that can lose money.",
  },
  {
    n: "7",
    name: "Paper live",
    issues: "#314",
    state: "blocked",
    after: "3 · 6",
    note: "Reconciliation and the live-vs-sim divergence report. The Phase-2 finish line.",
  },
];

/* ============================================================
   Trading-day arithmetic
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

// Every weekday in [from, to] that XNYS actually opens. Dates are handled in UTC
// throughout so a browser west of Greenwich can't shift a day.
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

/* ============================================================
   Options bar
   ============================================================ */

createOptionsBar("optbar", {
  primary: [
    { type: "readout", id: "pl-phase", label: "PHASE", value: "1 · TRACKER" },
    { type: "readout", id: "pl-collected", label: "COLLECTED", value: "—" },
    { type: "readout", id: "pl-countdown", label: "SESSIONS LEFT", value: "—" },
    { type: "btn", id: "pl-refresh", label: "Refresh", title: "Refresh now" },
  ],
  extra: [
    {
      type: "note",
      value:
        "The plan is committed to the repo and only changes by PR — see research/decisions.md " +
        "and research/phase-2-roadmap.md. The counts are live from the published data.",
    },
  ],
  onChange: (id) => {
    if (id === "pl-refresh") refresh();
  },
});

/* ============================================================
   Static render — the plan itself
   ============================================================ */

function renderSpine() {
  el("pl-spine").innerHTML = PHASES.map((p) => {
    const bullets = p.bullets.map((b) => `<li>${esc(b)}</li>`).join("");
    const badge = p.state === "live" ? "Now" : "Locked";
    return (
      `<article class="plan-phase plan-phase-${esc(p.state)}" id="pl-phase-${esc(p.id)}">` +
      `<header class="plan-phase-h">` +
      `<span class="plan-phase-n">${esc(p.n)}</span>` +
      `<span class="plan-phase-name">${esc(p.name)}</span>` +
      `<span class="plan-phase-tag">${esc(p.tag)}</span>` +
      `<span class="pill plan-phase-badge">${esc(badge)}</span>` +
      `</header>` +
      `<p class="plan-phase-win muted">${esc(p.window)}</p>` +
      `<p class="plan-phase-obj">${esc(p.objective)}</p>` +
      `<ul class="plan-phase-list">${bullets}</ul>` +
      // A progress bar only means anything for the phase actually running.
      (p.state === "live"
        ? `<div class="plan-bar"><span id="pl-phase-bar" class="plan-bar-fill" style="width:0%"></span></div>`
        : "") +
      `<p class="plan-phase-exit">${esc(p.exit)}</p>` +
      `</article>`
    );
  }).join("");
}

function renderOrders() {
  el("pl-orders").innerHTML = ORDERS.map(
    (o) =>
      `<li class="plan-order"><p class="plan-order-rule">${esc(o.rule)}</p>` +
      `<p class="plan-order-why muted">${esc(o.why)}</p></li>`,
  ).join("");
}

const GATE_LABEL = { open: "Open now", running: "Running", blocked: "Blocked" };

function renderGates() {
  const open = GATES.filter((g) => g.state === "open").length;
  el("pl-gates-cap").innerHTML =
    `${GATES.length} gates stand between here and paper orders. ` +
    `<strong class="plan-hot">${open} are open today</strong> — none of them need the market ` +
    `to do anything.`;
  el("pl-gates").innerHTML = GATES.map((g) => {
    const label = g.state === "blocked" ? `After ${esc(g.after)}` : GATE_LABEL[g.state];
    return (
      `<li class="plan-gate plan-gate-${esc(g.state)}">` +
      `<span class="plan-gate-n">${esc(g.n)}</span>` +
      `<div class="plan-gate-body">` +
      `<p class="plan-gate-h"><span class="plan-gate-name">${esc(g.name)}</span>` +
      `<span class="plan-gate-iss muted">${esc(g.issues)}</span>` +
      `<span class="pill plan-gate-pill">${label}</span></p>` +
      `<p class="plan-gate-note muted">${esc(g.note)}</p>` +
      `</div></li>`
    );
  }).join("");
}

/* ============================================================
   Live render — the countdown, the heatmap, the sample
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

const tile = (label, value, sub) =>
  `<div class="tile"><div class="tile-l">${esc(label)}</div>` +
  `<div class="tile-v">${value}</div>` +
  (sub ? `<div class="tile-s">${esc(sub)}</div>` : "") +
  `</div>`;

// A day counts as collected only if it is a real session — the 2026-07-04/05
// weekend rows the tracker captured before the calendar gate (#137) landed are
// data, but they are not progress.
function renderProgress(index) {
  const byDate = new Map();
  for (const e of (index && index.dates) || []) {
    if (SESSION_SET.has(e.date)) byDate.set(e.date, (e.opportunities || []).length);
  }
  const today = todayEt();
  const done = byDate.size;
  const left = SESSIONS.length - done;
  const opps = [...byDate.values()].reduce((a, b) => a + b, 0);
  const frac = SESSIONS.length ? done / SESSIONS.length : 0;

  el("pl-ring").innerHTML = ringSvg(frac);
  el("pl-ring-tiles").innerHTML =
    tile("Sessions", `${done}<span class="muted"> / ${SESSIONS.length}</span>`, "trading days in") +
    tile("Left to run", String(left), left === 1 ? "session" : "sessions") +
    tile("Window ends", COLLECT_END.slice(5), COLLECT_END.slice(0, 4)) +
    tile("Opportunities", String(opps), "flagged so far");

  el("pl-collected").textContent = `${done} / ${SESSIONS.length}`;
  el("pl-countdown").textContent = String(left);
  const bar = el("pl-phase-bar");
  if (bar) bar.style.width = (frac * 100).toFixed(1) + "%";

  renderHeat(byDate, today);
  return { done, left, total: SESSIONS.length };
}

// GitHub-contribution-style grid: one column per week, Mon–Fri down each column,
// from the Monday of the start week to the Friday of the end week so every
// column holds exactly five cells and the grid can flow by column.
function renderHeat(byDate, today) {
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
    } else if (byDate.has(iso)) {
      const n = byDate.get(iso);
      cls += ` plan-cell-l${n === 0 ? 1 : n < 4 ? 2 : n < 9 ? 3 : 4}`;
      title += ` · ${n} ${n === 1 ? "opportunity" : "opportunities"}`;
    } else {
      cls += " plan-cell-todo";
      title += iso <= today ? " · not collected" : " · to come";
    }
    if (iso === today) cls += " plan-cell-today";
    cells.push(`<span class="${cls}" title="${esc(title)}"></span>`);
  }
  el("pl-heat").innerHTML = cells.join("");
  el("pl-heat-cap").innerHTML =
    `${esc(COLLECT_START)} → ${esc(COLLECT_END)} · one square per session &nbsp; ` +
    `<span class="plan-key"><i class="plan-cell plan-cell-todo"></i>to&nbsp;come</span> &nbsp; ` +
    `<span class="plan-key">fewer<i class="plan-cell plan-cell-l1"></i>` +
    `<i class="plan-cell plan-cell-l2"></i><i class="plan-cell plan-cell-l3"></i>` +
    `<i class="plan-cell plan-cell-l4"></i>more opportunities</span>`;
}

// What the patience is buying, and — just as important — how far it still has to
// go. A page that only showed the equity curve would be an argument for
// impatience; the sample size is the point.
function renderEvidence(pf, progress) {
  const book = pf && pf.books && pf.books.adaptive;
  if (!book || !book.stats) {
    el("pl-evidence").innerHTML = tile("Paper book", "—", "no data yet");
    el("pl-evidence-note").textContent = "";
    return;
  }
  const s = book.stats;
  const n = s.n_trades || 0;
  el("pl-evidence").innerHTML =
    tile("Trades taken", String(n), "on paper") +
    tile("Avg", fmtRSigned(s.avg_r), "per trade") +
    tile("Win rate", `${Math.round((s.win_rate || 0) * 100)}%`, `${s.wins || 0}W / ${s.losses || 0}L`) +
    tile(
      "Book",
      `$${(s.end_equity || 0).toFixed(0)}`,
      `${fmtPct(s.return_pct)} from $${(pf.start_equity || 0).toFixed(0)}`,
    );
  el("pl-evidence-note").textContent =
    `${n} trades is not a verdict — a sample this size can't separate a real edge from a warm ` +
    `streak. The ${progress.left} sessions left are what buys the answer, and only if the ` +
    `rules stay fixed while they run.`;
}

/* ============================================================
   Poll loop
   ============================================================ */

async function refresh() {
  setStatusPage("updating…");
  try {
    const [index, pf] = await Promise.all([fetchJson("index.json"), fetchJson("portfolio.json")]);
    const progress = renderProgress(index);
    renderEvidence(pf, progress);
    el("pl-error").hidden = true;
    setStatusPage(`plan · ${progress.done}/${progress.total} sessions collected`);
  } catch (e) {
    el("pl-error").hidden = false;
    el("pl-error").textContent = "Failed to load plan data: " + e.message;
    setStatusPage("update failed");
  }
}

renderSpine();
renderOrders();
renderGates();
refresh();
setInterval(refresh, POLL_MS);
