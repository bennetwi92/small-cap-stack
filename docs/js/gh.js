// Live issue state (#414). The Plan page's gate ladder used to carry a hand-written
// status per gate, so the page started lying the moment an issue closed. Status is now
// derived from the repo itself: a gate is done when every issue it names is closed.
//
// Unauthenticated GitHub REST is 60 requests/hour per IP, so states are cached in
// sessionStorage for CACHE_MS and every failure degrades to "unknown" — the caller falls
// back to its dependency graph rather than the page breaking.

import { REPO } from "./data.js";

const CACHE_MS = 30 * 60_000;
const cacheKey = (n) => `scs.issue.${n}`;

function cached(n) {
  try {
    const raw = sessionStorage.getItem(cacheKey(n));
    if (!raw) return null;
    const v = JSON.parse(raw);
    return Date.now() - v.t < CACHE_MS ? v.state : null;
  } catch {
    return null; // private mode / quota — just re-fetch
  }
}

function remember(n, state) {
  try {
    sessionStorage.setItem(cacheKey(n), JSON.stringify({ state, t: Date.now() }));
  } catch {
    /* nothing to do — the cache is an optimisation, not a requirement */
  }
}

async function fetchState(n) {
  const hit = cached(n);
  if (hit) return hit;
  try {
    const res = await fetch(`https://api.github.com/repos/${REPO}/issues/${n}`, {
      headers: { Accept: "application/vnd.github+json" },
    });
    if (!res.ok) return null; // 403 = rate-limited, 404 = renumbered issue
    const body = await res.json();
    const state = body.state === "closed" ? "closed" : "open";
    remember(n, state);
    return state;
  } catch {
    return null;
  }
}

// issue number -> "open" | "closed". Numbers whose state couldn't be read are ABSENT from
// the map, which is what lets a caller tell "open" from "we don't know".
export async function issueStates(numbers) {
  const uniq = [...new Set(numbers)];
  const states = await Promise.all(uniq.map(fetchState));
  const out = new Map();
  uniq.forEach((n, i) => {
    if (states[i]) out.set(n, states[i]);
  });
  return out;
}

export const issueUrl = (n) => `https://github.com/${REPO}/issues/${n}`;
