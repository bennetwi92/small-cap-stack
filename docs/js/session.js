// Session-window state (#288): the 04:00–11:59 ET scan window is the spine of
// this app, and the 09:30 open is its most important line. This module computes
// the current ET session phase for consumers like the status-bar session chip.

import { etMinutesOf } from "./fmt.js";

export const MARKET_OPEN_MIN = 9 * 60 + 30; // 09:30 ET
const PREMARKET_START_MIN = 4 * 60;          // 04:00 ET — scan window opens
const MARKET_CLOSE_MIN = 16 * 60;            // 16:00 ET

// "pre" | "open" | "closed" for the current wall-clock moment.
export function sessionNow() {
  const m = etMinutesOf(new Date());
  if (m >= PREMARKET_START_MIN && m < MARKET_OPEN_MIN) return "pre";
  if (m >= MARKET_OPEN_MIN && m < MARKET_CLOSE_MIN) return "open";
  return "closed";
}
