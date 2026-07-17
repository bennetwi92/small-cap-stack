// Published-data access (#288): every page reads the same JSON the box pushes
// to the `dashboard-data` branch; CORS on raw.githubusercontent.com allows the
// cross-origin fetch. One copy of the URL scheme instead of one per page.

export const REPO = "bennetwi92/small-cap-stack";
export const BRANCH = "dashboard-data";

export const rawUrl = (file) =>
  `https://raw.githubusercontent.com/${REPO}/${BRANCH}/${file}?t=${Date.now()}`;

// null on any non-OK status (e.g. a file that doesn't exist before the first EOD).
export async function fetchJson(file) {
  const res = await fetch(rawUrl(file), { cache: "no-store" });
  if (!res.ok) return null;
  return res.json();
}
