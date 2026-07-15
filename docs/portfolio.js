// Virtual-portfolio tracker (#230): a *pre-shadow* paper book over the tracker's own data. Plain
// fetch + DOM, mirroring results.js / review.js idioms — no framework, no build step.
//
// Reads a single published `portfolio.json` (dashboard-data branch) built server-side by the tested
// `small_cap_stack.portfolio` module: the adaptive (daily re-fit) book plus one fixed-target book
// per selectable R target. The page just picks a book and renders its equity curve / stats / trade
// log — all the trading logic (select → size → simulate-exit) lives in the Python package.

const REPO = "bennetwi92/small-cap-stack";
const BRANCH = "dashboard-data";
const rawUrl = (file) => `https://raw.githubusercontent.com/${REPO}/${BRANCH}/${file}?t=${Date.now()}`;

const el = (id) => document.getElementById(id);
const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const MK = { up: "#3fb950", down: "#f85149", flat: "#8b949e", line: "#2f81f7" };

const fmtUsd = (x) => (x == null || !isFinite(x) ? "—" : "$" + Number(x).toFixed(2));
const fmtR = (x) => (x == null || !isFinite(x) ? "—" : (x >= 0 ? "+" : "") + Number(x).toFixed(2) + "R");
const fmtPct = (x) => (x == null || !isFinite(x) ? "—" : (x >= 0 ? "+" : "") + (x * 100).toFixed(1) + "%");
const fmtInt = (x) => (x == null || !isFinite(x) ? "—" : String(x));

const _etHM = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
});
// The exported trigger_at is already an ET ISO string; show HH:MM.
const etClock = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : _etHM.format(d);
};

let PAYLOAD = null; // the whole portfolio.json
let BOOK = "adaptive"; // selected book key

async function fetchJson(file) {
  const res = await fetch(rawUrl(file), { cache: "no-store" });
  if (!res.ok) return null;
  return res.json();
}

// --- Equity curve (inline SVG, theme-agnostic via currentColor + explicit marks) ----------------

function equitySvg(curve, start) {
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
  return (
    `<svg viewBox="0 0 ${W} ${H}" class="pf-chart" role="img" aria-label="Equity curve">` +
    `<line x1="${PAD}" x2="${W - PAD}" y1="${baseY}" y2="${baseY}" stroke="${MK.flat}" stroke-dasharray="3 3" stroke-width="1"/>` +
    `<text x="${W - PAD}" y="${(+baseY - 4).toFixed(1)}" text-anchor="end" class="pf-axis">start ${fmtUsd(start)}</text>` +
    `<path d="${area}" fill="${stroke}" opacity="0.10"/>` +
    `<path d="${line}" fill="none" stroke="${stroke}" stroke-width="2"/>` +
    `<circle cx="${x(pts.length - 1).toFixed(1)}" cy="${y(end).toFixed(1)}" r="3.5" fill="${stroke}"/>` +
    `<text x="${(x(pts.length - 1) - 6).toFixed(1)}" y="${(y(end) - 8).toFixed(1)}" text-anchor="end" class="pf-axis">${fmtUsd(end)}</text>` +
    `</svg>`
  );
}

// --- Stat tiles ---------------------------------------------------------------------------------

function tile(label, value, cls = "", title = "") {
  const t = title ? ` title="${esc(title)}"` : "";
  return `<div class="pf-tile"${t}><div class="pf-tile-val ${cls}">${value}</div><div class="pf-tile-lbl">${esc(label)}</div></div>`;
}

// Costs are first-order on a $500 book (research/broker-costs.md, #232) — show the drag as a share
// of starting equity rather than burying it inside net P&L.
function costTile(s, start) {
  if (s.total_costs_usd == null) return "";
  const pct = start ? ` <span class="muted">(${((s.total_costs_usd / start) * 100).toFixed(1)}%)</span>` : "";
  const breakdown =
    `IBKR commission ${fmtUsd(s.commission_usd)} · ` +
    `exchange/clearing/TAF/SEC ${fmtUsd(s.fees_usd)} · ` +
    `market data ${fmtUsd(s.data_fees_usd)}`;
  return tile("Costs", fmtUsd(s.total_costs_usd) + pct, "pf-neg", breakdown);
}

function statTiles(book, start) {
  const s = book.stats;
  const grew = s.end_equity >= start;
  return (
    tile("Balance", fmtUsd(s.end_equity), grew ? "pf-pos" : "pf-neg") +
    tile("Return", fmtPct(s.return_pct), grew ? "pf-pos" : "pf-neg") +
    tile("Win rate", s.win_rate == null ? "—" : (s.win_rate * 100).toFixed(0) + "%") +
    tile("Trades", `${fmtInt(s.n_trades)} <span class="muted">(${s.wins}W/${s.losses}L)</span>`) +
    tile("Avg R", fmtR(s.avg_r)) +
    tile("Expectancy", fmtUsd(s.expectancy_usd) + "/trade") +
    tile("Max DD", s.max_drawdown_pct == null ? "—" : "-" + (s.max_drawdown_pct * 100).toFixed(1) + "%", "pf-neg") +
    costTile(s, start)
  );
}

// --- Trade log ----------------------------------------------------------------------------------

const REASON_LBL = { target: "target", stop: "stop", breakeven: "b/e", close: "close" };

function tradeRows(book) {
  if (!book.trades.length) return '<tr><td colspan="11" class="muted">No qualifying pre-market trades yet.</td></tr>';
  return book.trades
    .slice()
    .reverse() // newest first
    .map((t) => {
      const rCls = t.realized_r > 0 ? "pf-pos" : t.realized_r < 0 ? "pf-neg" : "muted";
      const nCls = t.net_pnl > 0 ? "pf-pos" : t.net_pnl < 0 ? "pf-neg" : "muted";
      const rev = `review.html?date=${encodeURIComponent(t.date)}&sym=${encodeURIComponent(t.symbol)}`;
      return (
        "<tr>" +
        `<td>${esc(t.date)}</td>` +
        `<td><a href="${rev}">${esc(t.symbol)}</a></td>` +
        `<td>${etClock(t.trigger_at)}</td>` +
        `<td>${fmtUsd(t.entry)}</td>` +
        `<td>${fmtUsd(t.stop)}</td>` +
        `<td>${fmtInt(t.qty)}</td>` +
        `<td>${Number(t.target_r).toFixed(1)}R</td>` +
        `<td><span class="pf-reason pf-reason-${t.reason}">${REASON_LBL[t.reason] || t.reason}</span> ${fmtUsd(t.exit_price)}</td>` +
        `<td class="${rCls}">${fmtR(t.realized_r)}</td>` +
        `<td class="${nCls}">${fmtUsd(t.net_pnl)}</td>` +
        `<td>${fmtUsd(t.equity_after)}</td>` +
        "</tr>"
      );
    })
    .join("");
}

// --- Book selector + render ---------------------------------------------------------------------

function bookSelector() {
  const opts = ["adaptive", ...PAYLOAD.targets];
  return opts
    .map((k) => {
      const lbl = k === "adaptive" ? "Adaptive" : `${k}R`;
      const on = k === BOOK ? ' aria-current="true"' : "";
      return `<button class="pf-book" data-book="${esc(k)}"${on}>${esc(lbl)}</button>`;
    })
    .join("");
}

function adaptiveTargetNote(book) {
  if (!book.daily_targets || !book.daily_targets.length) return "";
  const active = book.daily_targets.filter((d) => d.target != null);
  if (!active.length) return "";
  const last = active[active.length - 1];
  const uniq = [...new Set(active.map((d) => d.target))].sort((a, b) => a - b);
  const c = PAYLOAD.config;
  return (
    `<p class="muted pf-note">Target re-fits daily from the trailing ${c.adaptive_window_days}-day ` +
    `window (needs ≥ ${c.adaptive_min_samples} prior trades, else the configured fallback). ` +
    `Latest chosen target: <strong>${last.target}R</strong> · targets used: ${uniq.map((t) => t + "R").join(", ")}.</p>`
  );
}

function render() {
  const book = PAYLOAD.books[BOOK];
  el("pf-books").innerHTML = bookSelector();
  el("pf-tiles").innerHTML = statTiles(book, PAYLOAD.start_equity);
  el("pf-chart-wrap").innerHTML = equitySvg(book.equity_curve, PAYLOAD.start_equity);
  el("pf-note").innerHTML = BOOK === "adaptive" ? adaptiveTargetNote(book) : "";
  el("pf-trades").innerHTML = tradeRows(book);
  document.querySelectorAll(".pf-book").forEach((b) =>
    b.addEventListener("click", () => {
      BOOK = b.dataset.book;
      render();
    })
  );
}

async function load() {
  el("pf-error").hidden = true;
  const data = await fetchJson("portfolio.json");
  if (!data || !data.books) {
    el("pf-error").hidden = false;
    el("pf-error").textContent = "No portfolio data yet — it's built at the end-of-day report.";
    el("pf-meta").textContent = "";
    return;
  }
  PAYLOAD = data;
  if (!PAYLOAD.books[BOOK]) BOOK = "adaptive";
  const c = PAYLOAD.config;
  el("pf-meta").innerHTML =
    `Start ${fmtUsd(PAYLOAD.start_equity)} · ${(c.position_fraction * 100).toFixed(0)}% per trade · ` +
    `max ${c.max_trades_per_day}/day · pre-market fills only (&lt; ${esc(c.premarket_cutoff_et.slice(0, 5))} ET) · ` +
    `entry $${c.entry_price_min}–${c.entry_price_max} · ` +
    `IBKR tiered costs + $${c.market_data_usd_per_month}/mo data (#232)`;
  render();
}

el("pf-refresh").addEventListener("click", load);
load().catch((e) => {
  el("pf-error").hidden = false;
  el("pf-error").textContent = `Failed to load portfolio: ${e && e.message ? e.message : e}`;
});
