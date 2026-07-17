// Session-window state (#288): the 04:00–11:59 ET scan window is the spine of
// this app, and the 09:30 open is its most important line. This module computes
// the current ET session phase and stamps it on <html data-session=…> so the
// chrome's accent colour follows it (gold pre-market, cyan from the open).

const _etHM = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
});

export const MARKET_OPEN_MIN = 9 * 60 + 30; // 09:30 ET
const PREMARKET_START_MIN = 4 * 60;          // 04:00 ET — scan window opens
const MARKET_CLOSE_MIN = 16 * 60;            // 16:00 ET

// Minutes past ET-midnight for a Date (some locales emit "24" for midnight).
export function etMinutesOf(date) {
  const parts = _etHM.formatToParts(date);
  const h = +parts.find((p) => p.type === "hour").value % 24;
  const m = +parts.find((p) => p.type === "minute").value;
  return h * 60 + m;
}

export const etClockNow = () => _etHM.format(new Date());

// "pre" | "open" | "closed" for the current wall-clock moment.
export function sessionNow() {
  const m = etMinutesOf(new Date());
  if (m >= PREMARKET_START_MIN && m < MARKET_OPEN_MIN) return "pre";
  if (m >= MARKET_OPEN_MIN && m < MARKET_CLOSE_MIN) return "open";
  return "closed";
}

export function applySession() {
  document.documentElement.dataset.session = sessionNow();
}

applySession();
setInterval(applySession, 10_000);
