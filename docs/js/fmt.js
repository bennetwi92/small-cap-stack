// Shared formatters (#288) — the one copy of the helpers app.js / results.js /
// review.js each carried privately (#163). Converted pages import from here.

export const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// Compact share/volume formatter — tiers, casing and boundary promotion match
// the legacy pages so the same float renders identically everywhere.
export function fmtShares(n) {
  if (n == null || !isFinite(n)) return "—";
  const a = Math.abs(n);
  if (a >= 999.95e6) return (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
  if (a >= 999.5e3) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (a >= 1e3) return Math.round(n / 1e3) + "k";
  return String(Math.round(n));
}

export const fmtPrice = (x) => (x == null || !isFinite(x) ? "—" : "$" + Number(x).toFixed(2));
export const fmtR = (x) => (x == null || !isFinite(x) ? "—" : Number(x).toFixed(2) + "R");

// A fraction as a SIGNED percent move (0.0345 -> "+3.5%"). The size-of-the-move
// view that sits next to R — the portfolio's max % column and the results grid
// read the same helper so one number can't render two ways (#288).
export const fmtPct = (x, dp = 1) =>
  x == null || !isFinite(x) ? "—" : (x >= 0 ? "+" : "") + (x * 100).toFixed(dp) + "%";
// A fraction as an unsigned percent (0.32 -> "32%") — for ratios that aren't moves
// (retracement, wick fraction, …), where a leading "+" would read as a gain.
export const fmtPctPlain = (x, dp = 0) =>
  x == null || !isFinite(x) ? "—" : (x * 100).toFixed(dp) + "%";
// Plain fixed-dp number; `signed` prefixes "+" so a slope's direction reads at a glance.
export const fmtNum = (x, dp = 2, signed = false) =>
  x == null || !isFinite(x) ? "—" : (signed && x >= 0 ? "+" : "") + Number(x).toFixed(dp);
export const fmtRSigned = (x) =>
  x == null || !isFinite(x) ? "—" : (x >= 0 ? "+" : "") + Number(x).toFixed(2) + "R";

// ─── ET clocks ──────────────────────────────────────────────────────────────
// Every time this app prints is ET, because the trading day is ET. There is exactly one place
// each formatter is constructed (#510): four byte-identical copies of `_etHM` had drifted apart
// across fmt/session/status-bar/app, and two more call sites built one WITHOUT a `timeZone` at
// all — printing the viewer's local clock, unlabelled, between two "… ET" fields in the status
// bar. From London that read as a 5-hour discrepancy on a single line.
export const ET_TZ = "America/New_York";

const _etHM = new Intl.DateTimeFormat("en-US", {
  timeZone: ET_TZ, hour: "2-digit", minute: "2-digit", hour12: false,
});
// With seconds — for the "updated/fetched HH:MM:SS" stamps, which tick every poll.
const _etHMS = new Intl.DateTimeFormat("en-US", {
  timeZone: ET_TZ, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
// en-CA renders YYYY-MM-DD; in ET so "today" flips with the trading date, not the browser's.
const _etDate = new Intl.DateTimeFormat("en-CA", { timeZone: ET_TZ });

// The raw formatters, for callers that need `formatToParts` (session.js) rather than a string.
export const etHM = () => _etHM;

// HH:MM ET for a UNIX-seconds instant.
export const etClockSec = (sec) => (sec == null ? "—" : _etHM.format(new Date(sec * 1000)));
// HH:MM ET for an ISO string.
export const etClockIso = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : _etHM.format(d);
};
// HH:MM ET *with* the suffix — the status bar's own convention, so a field can't be added
// without it. `iso` absent renders the em-dash the bar uses for "no data".
export const etClockIsoSuffixed = (iso) => (iso ? `${etClockIso(iso)} ET` : "—");
// HH:MM:SS ET for right now, suffixed. The "updated …" / "fetched …" stamps.
export const etClockNowSec = () => `${_etHMS.format(new Date())} ET`;
// YYYY-MM-DD, the ET trading date.
export const etDateOf = (date = new Date()) => _etDate.format(date);

// "Aug 07, 14:32 ET" — a date *and* time, for stamps that can be days old.
const _etDateTime = new Intl.DateTimeFormat("en-US", {
  timeZone: ET_TZ, month: "short", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hour12: false,
});
export const etDateTimeIsoSuffixed = (iso) =>
  iso ? `${_etDateTime.format(new Date(iso))} ET` : "—";

// Minutes-past-ET-midnight for a UNIX-seconds instant; null when absent.
export function etMinutesSec(sec) {
  if (sec == null) return null;
  const parts = _etHM.formatToParts(new Date(sec * 1000));
  const h = +parts.find((p) => p.type === "hour").value % 24;
  const m = +parts.find((p) => p.type === "minute").value;
  return h * 60 + m;
}

/* ---------- R ramp (#288) ----------
   The meaningful colour scale here is R, not percent: anchored at 0R with the
   stop at −1R. One class per bucket (defined in cockpit.css); use paintR on
   recycled cells (Tabulator) so a stale class never survives. */
export const R_CLASSES = ["r-l2", "r-l1", "r-flat", "r-w1", "r-w2", "r-w3"];

export function rRampClass(v) {
  if (v == null || !isFinite(v)) return "";
  if (v <= -1) return "r-l2";      // at/below the stop
  if (v < 0) return "r-l1";
  if (v < 0.25) return "r-flat";   // ~0R: noise
  if (v < 1) return "r-w1";
  if (v < 2) return "r-w2";
  return "r-w3";                   // ≥ 2R
}

// Toggle the WHOLE set so a recycled cell can't keep a stale colour.
export function paintR(el, v) {
  const want = rRampClass(v);
  for (const cls of R_CLASSES) el.classList.toggle(cls, cls === want);
}
