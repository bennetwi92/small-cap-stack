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
export const fmtRSigned = (x) =>
  x == null || !isFinite(x) ? "—" : (x >= 0 ? "+" : "") + Number(x).toFixed(2) + "R";

const _etHM = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
});

// HH:MM ET for a UNIX-seconds instant.
export const etClockSec = (sec) => (sec == null ? "—" : _etHM.format(new Date(sec * 1000)));
// HH:MM ET for an ISO string.
export const etClockIso = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : _etHM.format(d);
};

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
