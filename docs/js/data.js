// Published-data access (#288): every page reads the same JSON the box pushes
// to the `dashboard-data` branch; CORS on raw.githubusercontent.com allows the
// cross-origin fetch. One copy of the URL scheme instead of one per page.

export const REPO = "bennetwi92/small-cap-stack";
export const BRANCH = "dashboard-data";

export const rawUrl = (file) =>
  `https://raw.githubusercontent.com/${REPO}/${BRANCH}/${file}?t=${Date.now()}`;

// null on any non-OK status (e.g. a file that doesn't exist before the first EOD) — and on
// unparsable content, which is not the same failure but wants the same answer. Pages fetch several
// of these together in one `Promise.all`, so a single malformed artifact throwing out of `.json()`
// took down the whole page rather than the one panel that needed it.
export async function fetchJson(file) {
  const res = await fetch(rawUrl(file), { cache: "no-store" });
  if (!res.ok) return null;
  try {
    return await res.json();
  } catch (_) {
    return null;
  }
}
