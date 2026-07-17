// Virtual-portfolio tracker (#230, cockpit #290): a *pre-shadow* paper book
// over the tracker's own data, on the shared cockpit chrome. All the trading
// logic (select → size → simulate-exit) lives in the tested Python package;
// this page just picks a book from the published portfolio.json and renders
// its equity curve / stats / trade log.

import "./js/nav.js";
import { createOptionsBar } from "./js/options-bar.js";
import { setStatusPage } from "./js/status-bar.js";
import { fetchJson } from "./js/data.js";
import { esc, etClockIso, rRampClass } from "./js/fmt.js";

const el = (id) => document.getElementById(id);
const MK = { up: "#3ec07e", down: "#f06673", flat: "#9aa0b5", line: "#4fe3ef" };

const fmtUsd = (x) => (x == null || !isFinite(x) ? "—" : "$" + Number(x).toFixed(2));
const fmtGbp = (x) => (x == null || !isFinite(x) ? "—" : "£" + Number(x).toFixed(2));
const fmtR = (x) => (x == null || !isFinite(x) ? "—" : (x >= 0 ? "+" : "") + Number(x).toFixed(2) + "R");
const fmtPct = (x) => (x == null || !isFinite(x) ? "—" : (x >= 0 ? "+" : "") + (x * 100).toFixed(1) + "%");
const fmtInt = (x) => (x == null || !isFinite(x) ? "—" : String(x));
const pct = (r) => (r * 100).toFixed(2).replace(/\.?0+$/, "") + "%"; // 0.025 -> "2.5%"

let PAYLOAD = null; // the whole portfolio.json
let BOOK = "adaptive"; // selected book key

/* ---------- options bar: book selector + refresh; meta line under ··· ---------- */

function buildOptbar() {
  const books = PAYLOAD
    ? ["adaptive", ...PAYLOAD.targets].map((k) => ({
        value: k,
        label: k === "adaptive" ? "Adaptive" : `${k}R`,
      }))
    : [{ value: "adaptive", label: "Adaptive" }];
  createOptionsBar("optbar", {
    primary: [
      { type: "seg", id: "pf-book", label: "BOOK", value: BOOK, options: books },
      { type: "btn", id: "pf-refresh", label: "Refresh", title: "Refresh now" },
    ],
    extra: [{ type: "note", id: "pf-meta", value: "loading…" }],
    onChange: (id, value) => {
      if (id === "pf-refresh") return load();
      if (id === "pf-book") {
        BOOK = value;
        render();
      }
    },
  });
}

/* ---------- Equity curve (inline SVG) ---------- */

function equitySvg(curve, start, cashFlows = []) {
  const pts = [{ date: null, equity: start }, ...curve]; // anchor at the opening balance
  if (pts.length < 2) return '<p class="muted">Not enough data to chart yet.</p>';
  const W = 720, H = 240, PAD = 34;
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
    `<text x="${W - PAD}" y="${(+baseY - 4).toFixed(1)}" text-anchor="end" class="pf-axis">start ${fmtUsd(start)}</text>` +
    marks +
    `<path d="${area}" fill="${stroke}" opacity="0.10"/>` +
    `<path d="${line}" fill="none" stroke="${stroke}" stroke-width="2"/>` +
    `<circle cx="${x(pts.length - 1).toFixed(1)}" cy="${y(end).toFixed(1)}" r="3.5" fill="${stroke}"/>` +
    `<text x="${(x(pts.length - 1) - 6).toFixed(1)}" y="${(y(end) - 8).toFixed(1)}" text-anchor="end" class="pf-axis">${fmtUsd(end)}</text>` +
    `</svg>`
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

// Note: `n_rungs - 1` because rung 0 is the 0% floor — a 3-rung ladder has 2 steps above sitting out.
function todayTiles(st, c) {
  const parked = st.risk_fraction === 0;
  return (
    tile("Target", `${st.target_r}R`, "", "The R multiple the next setup exits at — re-fit daily from the trailing window") +
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
  el("pf-today-note").innerHTML =
    `Applies to the next session the book sizes (data through ${esc(prevDay(st.as_of))}). ` +
    streakNote(st) +
    sitting;
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
    '<div class="tbl-wrap"><table class="tbl"><thead><tr>' +
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

function tradeRows(book) {
  if (!book.trades.length) return '<tr><td colspan="12" class="muted">No qualifying pre-market trades yet.</td></tr>';
  return book.trades
    .slice()
    .reverse() // newest first
    .map((t) => {
      const nCls = t.net_pnl > 0 ? "pf-pos" : t.net_pnl < 0 ? "pf-neg" : "muted";
      const rev = `review.html?date=${encodeURIComponent(t.date)}&sym=${encodeURIComponent(t.symbol)}`;
      return (
        "<tr>" +
        `<td>${esc(t.date)}</td>` +
        `<td><a href="${rev}"><strong>${esc(t.symbol)}</strong></a></td>` +
        `<td>${etClockIso(t.trigger_at)}</td>` +
        `<td class="r">${fmtUsd(t.entry)}</td>` +
        `<td class="r">${fmtUsd(t.stop)}</td>` +
        `<td class="r">${fmtInt(t.qty)}</td>` +
        riskCell(t) +
        `<td class="r">${Number(t.target_r).toFixed(1)}R</td>` +
        `<td><span class="pf-reason pf-reason-${t.reason}">${REASON_LBL[t.reason] || t.reason}</span> ${fmtUsd(t.exit_price)}</td>` +
        rRampCell(t.realized_r) +
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
  if (!skipped.length) return '<tr><td colspan="9" class="muted">None — the daily cap was never binding.</td></tr>';
  return skipped
    .slice()
    .reverse() // newest first, matching the trade log
    .map((t) => {
      const rev = `review.html?date=${encodeURIComponent(t.date)}&sym=${encodeURIComponent(t.symbol)}`;
      return (
        "<tr>" +
        `<td>${esc(t.date)}</td>` +
        `<td><a href="${rev}"><strong>${esc(t.symbol)}</strong></a></td>` +
        `<td>${SKIP_LBL[t.skip_reason] || SKIP_LBL.cap}</td>` +
        `<td>${etClockIso(t.trigger_at)}</td>` +
        `<td class="r">${fmtUsd(t.entry)}</td>` +
        `<td class="r">${fmtUsd(t.stop)}</td>` +
        `<td class="r">${Number(t.target_r).toFixed(1)}R</td>` +
        `<td><span class="pf-reason pf-reason-${t.reason}">${REASON_LBL[t.reason] || t.reason}</span> ${fmtUsd(t.exit_price)}</td>` +
        rRampCell(t.realized_r) +
        "</tr>"
      );
    })
    .join("");
}

/* ---------- Notes + meta line ---------- */

function adaptiveTargetNote(book) {
  const c = PAYLOAD.config;
  let out = "";
  const targets = (book.daily_targets || []).filter((d) => d.target != null);
  if (targets.length) {
    const last = targets[targets.length - 1];
    const uniq = [...new Set(targets.map((d) => d.target))].sort((a, b) => a - b);
    out +=
      `<p class="muted pf-note">Target re-fits daily from the trailing ${c.adaptive_window_days}-day ` +
      `window (needs ≥ ${c.adaptive_min_samples} prior trades, else the configured fallback). ` +
      `Latest chosen target: <strong>${last.target}R</strong> · targets used: ${uniq.map((t) => t + "R").join(", ")}.</p>`;
  }
  const risk = book.daily_risk || [];
  if (risk.length) {
    const ladder = (c.risk_ladder || []).map(pct).join(" / ");
    const d = c.risk_step_days || 1;
    const days = d === 1 ? "day" : `${d} days`;
    // Deliberately no "Latest risk: N%" here (#286): the forward-looking number
    // lives in the Next session panel.
    out +=
      `<p class="muted pf-note">Risk throttle (kill-switch): position risk walks ${c.risk_rungs} rungs ` +
      `(${ladder}), starting at full risk. It takes ${days} in a row of net-positive results to step ` +
      `risk up a rung (and ${days} of net-negative to step down); at 0% the book sits out but still ` +
      `watches the tape to re-arm.</p>`;
  }
  return out;
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

// The per-book config/meta line, under the options bar's ··· expander.
function metaLine(book) {
  const c = PAYLOAD.config;
  return (
    `Pre-shadow paper book — the trades I'd take, over the data already collected. ` +
    `Start ${fmtUsd(PAYLOAD.start_equity)} · ${riskMeta(book, c)} · ` +
    `max ${c.max_trades_per_day}/day · pre-market fills only (&lt; ${esc(c.premarket_cutoff_et.slice(0, 5))} ET) · ` +
    `entry $${c.entry_price_min}–${c.entry_price_max} · ` +
    `IBKR tiered costs + $${c.market_data_usd_per_month}/mo data (#232) · ` +
    `withdraw ${(c.withdraw_fraction * 100).toFixed(0)}% of profit &gt; ${fmtUsd(c.withdraw_floor_usd)} every ` +
    `${c.withdraw_cadence_months}mo · ${(c.cgt_rate * 100).toFixed(0)}% CGT &gt; £${c.cgt_annual_exempt_gbp} · ` +
    `£/$ ${Number(PAYLOAD.gbpusd_rate).toFixed(2)} · ` +
    `Not advice, not real orders — computed on-read from the tracker's own data. ` +
    `Small samples: a wiring/sanity view, not an edge estimate.`
  );
}

/* ---------- render + load ---------- */

function render() {
  const book = PAYLOAD.books[BOOK];
  el("pf-meta").innerHTML = metaLine(book);
  el("pf-tiles").innerHTML = statTiles(book, PAYLOAD.start_equity);
  renderToday(book);
  el("pf-chart-wrap").innerHTML = equitySvg(book.equity_curve, PAYLOAD.start_equity, book.cash_flows);
  el("pf-note").innerHTML = BOOK === "adaptive" ? adaptiveTargetNote(book) : "";
  el("pf-payout-tiles").innerHTML = payoutTiles(book);
  el("pf-cashflows").innerHTML = cashFlowRows(book, PAYLOAD.config);
  el("pf-trades").innerHTML = tradeRows(book);
  el("pf-skipped-note").innerHTML = skippedNote(book);
  el("pf-skipped").innerHTML = skippedRows(book);
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
