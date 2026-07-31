// Reports: written analyses published straight out of the repo.
//
// Unlike every other page, this one does NOT read the box's `dashboard-data`
// branch — a report is prose committed to `docs/reports/` and served by GitHub
// Pages alongside this file, so the fetches below are same-origin and relative.
// `docs/reports/index.json` (built by `python -m small_cap_stack.reports build`)
// is the list; each row fetches its own markdown on demand.
//
// Fetching the markdown is why `docs/.nojekyll` exists: Jekyll would render each
// report to `reports/<slug>.html` and serve no `.md`, 404ing every fetch below.
//
// One page, two views, switched by the `?r=<slug>` URL parameter — so a report
// is linkable, and Back returns to the list.

import "./js/nav.js";
import { createOptionsBar } from "./js/options-bar.js";
import { setStatusPage } from "./js/status-bar.js";
import { esc } from "./js/fmt.js";

const el = (id) => document.getElementById(id);

const INDEX_URL = "reports/index.json";
const reportUrl = (file) => `reports/${encodeURIComponent(file)}`;
const bust = (url) => `${url}${url.includes("?") ? "&" : "?"}t=${Date.now()}`;

let reports = []; // the published index, newest first
let query = ""; // options-bar search box

/* ---------- markdown ---------- */

// Loaded lazily from the CDN (same jsdelivr the other pages use) so a slow/blocked
// CDN costs the list nothing. If it can't load we still show the report — as its
// raw markdown source — rather than an empty pane.
let markedPromise = null;
function loadMarked() {
  markedPromise ??= import("https://cdn.jsdelivr.net/npm/marked@12.0.2/lib/marked.esm.js")
    .then((m) => m.marked)
    .catch(() => null);
  return markedPromise;
}

// Reports are repo-reviewed content (they land through a PR like any code), so
// raw HTML inside one is ours and left as-authored.
async function renderMarkdown(text, mount) {
  const marked = await loadMarked();
  if (!marked) {
    mount.innerHTML = `<pre class="md-raw">${esc(text)}</pre>`;
    return;
  }
  mount.innerHTML = marked.parse(text, { gfm: true, breaks: false });
}

/* ---------- fetch ---------- */

async function fetchText(url) {
  const res = await fetch(bust(url), { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.text();
}

function showError(msg) {
  el("rp-error").hidden = false;
  el("rp-error").textContent = msg;
}

/* ---------- options bar ---------- */

createOptionsBar("optbar", {
  primary: [
    {
      type: "search",
      id: "rp-q",
      label: "FIND",
      value: "",
      placeholder: "title, tag or summary",
    },
    { type: "readout", id: "rp-count", value: "loading…" },
    { type: "btn", id: "rp-refresh", label: "Refresh", title: "Re-read the published index" },
  ],
  extra: [
    {
      type: "note",
      value:
        "Reports are markdown analyses committed to docs/reports/ and served by GitHub Pages — " +
        "ask Claude for one and it lands here on merge. Sorted by publish date, newest first.",
    },
  ],
  onChange: (id, value) => {
    if (id === "rp-refresh") return load();
    if (id === "rp-q") {
      query = (value || "").trim().toLowerCase();
      renderList();
    }
  },
});

// The search box fires on `change` (blur/Enter); reports are few, so filter as
// you type too — this page's list is cheap to re-render.
document.getElementById("rp-q")?.addEventListener("input", (e) => {
  query = e.target.value.trim().toLowerCase();
  renderList();
});

/* ---------- list view ---------- */

const matches = (r) =>
  !query ||
  [r.title, r.summary, r.author, ...(r.tags || [])].join(" ").toLowerCase().includes(query);

// `published` is an ISO date or full datetime; the date part is what we show.
const pubDate = (r) => String(r.published || "").slice(0, 10);

const tagsHtml = (tags) =>
  (tags || []).map((t) => `<span class="rp-tag">${esc(t)}</span>`).join(" ");

function renderList() {
  const rows = reports.filter(matches);
  el("rp-count").textContent = query
    ? `${rows.length} of ${reports.length} shown`
    : countLabel();

  if (!reports.length) {
    el("rp-list").innerHTML =
      `<p class="muted">No reports published yet. Ask Claude for an analysis — ` +
      `it gets written to <code>docs/reports/</code> and appears here.</p>`;
    return;
  }
  if (!rows.length) {
    el("rp-list").innerHTML = `<p class="muted">No report matches “${esc(query)}”.</p>`;
    return;
  }

  el("rp-list").innerHTML =
    `<table class="tbl rp-tbl"><thead><tr>` +
    `<th>Published</th><th>Report</th><th>Tags</th><th class="r">Words</th>` +
    `</tr></thead><tbody>` +
    rows
      .map(
        (r) =>
          `<tr class="rp-row" data-slug="${esc(r.slug)}" tabindex="0" role="link" ` +
          `title="Read: ${esc(r.title)}">` +
          `<td class="rp-date">${esc(pubDate(r))}</td>` +
          `<td class="rp-title-cell"><span class="rp-title">${esc(r.title)}</span>` +
          (r.summary ? `<span class="rp-summary">${esc(r.summary)}</span>` : "") +
          `</td>` +
          `<td class="rp-tags">${tagsHtml(r.tags)}</td>` +
          `<td class="r muted">${Number(r.words || 0).toLocaleString()}</td>` +
          `</tr>`,
      )
      .join("") +
    `</tbody></table>`;

  for (const tr of el("rp-list").querySelectorAll(".rp-row")) {
    tr.addEventListener("click", () => openReport(tr.dataset.slug));
    tr.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openReport(tr.dataset.slug);
      }
    });
  }
}

/* ---------- report view ---------- */

// The page header already shows the title and metadata, so the markdown's own
// front matter and its opening `# Title` are dropped rather than repeated.
function stripHeader(text) {
  return text
    .replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "")
    .replace(/^\s*#\s+.*\r?\n/, "")
    .trimStart();
}

async function showReport(slug) {
  const report = reports.find((r) => r.slug === slug);
  el("rp-list-view").hidden = true;
  el("rp-doc-view").hidden = false;

  if (!report) {
    el("rp-doc-title").textContent = "Report not found";
    el("rp-doc-meta").textContent = slug;
    el("rp-doc-body").innerHTML = `<p class="muted">No published report has the slug “${esc(
      slug,
    )}”.</p>`;
    setStatusPage("report not found");
    return;
  }

  document.title = `small-cap-stack · ${report.title}`;
  el("rp-doc-title").textContent = report.title;
  el("rp-doc-meta").textContent =
    `${pubDate(report)} · ${report.author || "Claude"} · ${report.words} words` +
    (report.tags && report.tags.length ? ` · ${report.tags.join(", ")}` : "");
  el("rp-doc-body").innerHTML = `<p class="muted">Loading…</p>`;

  try {
    const text = await fetchText(reportUrl(report.file));
    await renderMarkdown(stripHeader(text), el("rp-doc-body"));
    window.scrollTo(0, 0);
    setStatusPage(`reading ${report.slug}`);
  } catch (e) {
    el("rp-doc-body").innerHTML = "";
    showError(`Failed to load ${report.file}: ${e && e.message ? e.message : e}`);
  }
}

const countLabel = () => `${reports.length} report${reports.length === 1 ? "" : "s"}`;

function showList() {
  document.title = "small-cap-stack · reports";
  el("rp-doc-view").hidden = true;
  el("rp-list-view").hidden = false;
  renderList();
  setStatusPage(countLabel());
}

/* ---------- routing ---------- */

const slugFromUrl = () => new URLSearchParams(location.search).get("r");

function openReport(slug) {
  history.pushState({ slug }, "", `reports.html?r=${encodeURIComponent(slug)}`);
  route();
}

function route() {
  el("rp-error").hidden = true;
  const slug = slugFromUrl();
  if (slug) showReport(slug);
  else showList();
}

window.addEventListener("popstate", route);

/* ---------- load ---------- */

async function load() {
  el("rp-error").hidden = true;
  el("rp-count").textContent = "loading…";
  try {
    const index = JSON.parse(await fetchText(INDEX_URL));
    reports = Array.isArray(index.reports) ? index.reports : [];
  } catch (e) {
    reports = [];
    showError(`Failed to load the reports index: ${e && e.message ? e.message : e}`);
  }
  el("rp-count").textContent = countLabel();   // stays truthful in the report view too
  route();
}

load();
