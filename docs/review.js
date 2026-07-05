// Review workbench (#142): a mobile-first, single-screen page for cycling back through any day's
// opportunities. Reads the same published JSON as the dashboard (#141): `index.json` for the
// date/symbol navigation and per-date `charts/<date>.json` for the full-day (04:00–16:00 ET) bars.
// No build step, no framework — plain fetch + DOM, reusing app.js's `buildChart` idiom. Write-back
// (notes / annotations) is a follow-up (#143); this page is read + navigate only.

const REPO = "bennetwi92/small-cap-stack";
const BRANCH = "dashboard-data";
const REVIEW_BRANCH = "review-data"; // write-back reviews live here (#143), off the force-pushed BRANCH
const DEFAULT_BRANCH = "main"; // base the review-data branch off this on first save
const API = "https://api.github.com";
const PAT_KEY = "rv_pat"; // localStorage key for the phone-local GitHub token

const rawUrl = (file) =>
  `https://raw.githubusercontent.com/${REPO}/${BRANCH}/${file}?t=${Date.now()}`;

const el = (id) => document.getElementById(id);
const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const _etTime = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
});
const etFromEpoch = (sec) => _etTime.format(new Date(sec * 1000)); // candlestick axis (UNIX seconds)

async function fetchJson(file) {
  const res = await fetch(rawUrl(file), { cache: "no-store" });
  if (!res.ok) return null; // e.g. index.json before the first EOD -> 404
  return res.json();
}

// --- Chart colours + state (mirrors docs/app.js) -------------------------------------------
const MK = {
  up: "#1a7f37", down: "#c0362c",
  entry: "#2f81f7", stop: "#c0362c", firstHit: "#8957e5", maxR: "#d4a72c",
  volUp: "rgba(26,127,55,0.5)", volDown: "rgba(192,54,44,0.5)",
};

let chartsData = null; // last-fetched charts/<date>.json payload for the selected date
let chartApi = null; // LightweightCharts instance (recreated per drawn opportunity)
let candleSeries = null;
let volumeSeries = null;
let currentOpp = null; // the opportunity chart object currently drawn (for the notes sheet)
const noteCache = new Map(); // opportunity_id -> loaded/saved review, so re-opening is instant

// Compact "SYMBOL #run · 2.3R" option label, mirroring the dashboard's chart picker.
function optionLabel(c) {
  const label = c.run_count > 1 ? `${c.symbol} #${c.run}` : c.symbol;
  const tag = c.triggered
    ? c.stopped_out
      ? " · stopped"
      : ` · ${c.max_r ?? "?"}R`
    : " · no trigger";
  return label + tag;
}

// Reuse the dashboard's buildChart idiom: candles + volume histogram + entry/stop price lines +
// timestamp-placed markers + fitContent(). Markers carry epoch timestamps (#141) so they land on
// the right bars of the full-day series even though its indices differ from the run window's.
function buildChart(c) {
  const LC = window.LightweightCharts;
  const container = el("rv-chart");
  if (chartApi) chartApi.remove();
  chartApi = LC.createChart(container, {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: "#9aa4b2", fontSize: 11 },
    grid: {
      vertLines: { color: "rgba(255,255,255,0.05)" },
      horzLines: { color: "rgba(255,255,255,0.05)" },
    },
    rightPriceScale: { borderColor: "rgba(255,255,255,0.15)" },
    timeScale: {
      borderColor: "rgba(255,255,255,0.15)",
      timeVisible: true,
      secondsVisible: false,
      tickMarkFormatter: (t) => etFromEpoch(t),
    },
    localization: { timeFormatter: (t) => etFromEpoch(t) + " ET" },
  });
  candleSeries = chartApi.addCandlestickSeries({
    upColor: MK.up, downColor: MK.down,
    borderUpColor: MK.up, borderDownColor: MK.down,
    wickUpColor: MK.up, wickDownColor: MK.down,
  });
  candleSeries.setData(
    c.bars.map((b) => ({ time: b.t, open: b.o, high: b.h, low: b.l, close: b.c })),
  );

  // Volume histogram overlaid on its own scale in the bottom ~20%, coloured by candle direction.
  const hasVolume = c.bars.some((b) => b.v != null);
  if (hasVolume) {
    volumeSeries = chartApi.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chartApi.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    volumeSeries.setData(
      c.bars.map((b) => ({ time: b.t, value: b.v ?? 0, color: b.c >= b.o ? MK.volUp : MK.volDown })),
    );
  } else {
    volumeSeries = null;
  }

  // Entry-trigger + stop levels (shown even when the setup never triggered — where a fill'd be).
  if (c.levels.entry != null)
    candleSeries.createPriceLine({
      price: c.levels.entry, color: MK.entry, lineStyle: 2, lineWidth: 1,
      axisLabelVisible: true, title: "entry",
    });
  if (c.levels.stop != null)
    candleSeries.createPriceLine({
      price: c.levels.stop, color: MK.stop, lineStyle: 2, lineWidth: 1,
      axisLabelVisible: true, title: "stop",
    });

  const m = c.markers;
  const markers = [];
  if (m.first_hit != null)
    markers.push({ time: m.first_hit, position: "belowBar", color: MK.firstHit, shape: "circle", text: "scan" });
  if (m.entry != null)
    markers.push({ time: m.entry, position: "belowBar", color: MK.entry, shape: "arrowUp", text: "entry" });
  if (m.max_r != null && c.max_r != null && c.max_r > 0)
    markers.push({ time: m.max_r, position: "aboveBar", color: MK.maxR, shape: "circle", text: `${c.max_r}R` });
  if (m.stop != null)
    markers.push({ time: m.stop, position: "aboveBar", color: MK.stop, shape: "arrowDown", text: "stop" });
  markers.sort((a, b) => a.time - b.time); // lightweight-charts needs ascending marker times
  candleSeries.setMarkers(markers);
  chartApi.timeScale().fitContent();

  el("rv-readout").innerHTML =
    `<span class="mk" style="color:${MK.entry}">entry ${c.levels.entry ?? "—"}</span>` +
    `<span class="mk" style="color:${MK.stop}">stop ${c.levels.stop ?? "—"}</span>` +
    `<span class="mk" style="color:${MK.maxR}">Max R ${c.max_r != null ? c.max_r + "R" : "—"}</span>` +
    (c.triggered ? (c.stopped_out ? '<span class="muted">stopped out</span>' : "") : '<span class="muted">no trigger</span>');
}

function clearChart(message) {
  if (chartApi) {
    chartApi.remove();
    chartApi = null;
    candleSeries = null;
    volumeSeries = null;
  }
  el("rv-readout").innerHTML = `<span class="muted">${esc(message)}</span>`;
}

// Draw whichever opportunity the symbol dropdown currently points at.
function drawSelected() {
  const list = (chartsData && chartsData.charts) || [];
  const c = list.find((x) => x.opportunity_id === el("rv-symbol").value) || list[0];
  if (!window.LightweightCharts) {
    clearChart("Chart library failed to load.");
    return;
  }
  if (!c) {
    currentOpp = null;
    clearChart("No opportunities for this date.");
    loadNote(null);
    return;
  }
  buildChart(c);
  currentOpp = c;
  loadNote(c); // pull this opportunity's saved note (if any) into the sheet
}

// Load a trading date's chart file, repopulate the symbol dropdown, and draw the first opportunity.
async function loadDate(date) {
  clearChart("loading…");
  chartsData = await fetchJson(`charts/${date}.json`);
  const list = (chartsData && chartsData.charts) || [];
  el("rv-symbol").innerHTML = list
    .map((c) => `<option value="${esc(c.opportunity_id)}">${esc(optionLabel(c))}</option>`)
    .join("");
  drawSelected();
}

// Step the symbol selection by ±1 with wrap-around (mirrors the dashboard's prev/next).
function stepSymbol(delta) {
  const sel = el("rv-symbol");
  const n = sel.options.length;
  if (!n) return;
  sel.selectedIndex = (sel.selectedIndex + delta + n) % n;
  drawSelected();
}

// --- Notes write-back (#143) ---------------------------------------------------------------
// Save/load a per-opportunity review by committing JSON to the `review-data` branch via the
// GitHub REST API, using a fine-grained PAT kept only in this phone's localStorage. No backend.

const getPat = () => (localStorage.getItem(PAT_KEY) || "").trim();

// `:` and `#` are illegal-ish in paths and ids; map both to `_` (e.g. 2026-07-01:AHMA#2 -> ..._AHMA_2).
const sanitizeOid = (oid) => String(oid).replace(/[:#]/g, "_");
const reviewPath = (oid) => `reviews/${sanitizeOid(oid)}.json`;

// UTF-8-safe base64 for the file body (btoa alone mangles non-ASCII notes).
const b64 = (s) => btoa(unescape(encodeURIComponent(s)));

const ghHeaders = () => ({
  Authorization: `Bearer ${getPat()}`,
  Accept: "application/vnd.github+json",
  "X-GitHub-Api-Version": "2022-11-28",
});

function setStatus(msg, kind) {
  const s = el("rv-save-status");
  s.textContent = msg;
  s.className = "rv-save-status" + (kind ? " " + kind : " muted");
}

// Load an opportunity's saved note into the sheet. Public branch -> raw fetch, no auth needed;
// 404 (or missing branch) simply means "no review yet" -> empty field. In-session cache first.
async function loadNote(c) {
  const note = el("rv-note");
  if (!c) {
    note.value = "";
    el("rv-sheet-title").textContent = "Notes";
    setStatus("", null);
    return;
  }
  el("rv-sheet-title").textContent = optionLabel(c);
  if (noteCache.has(c.opportunity_id)) {
    note.value = noteCache.get(c.opportunity_id).note || "";
    setStatus("", null);
    return;
  }
  note.value = "";
  setStatus("", null);
  const url =
    `https://raw.githubusercontent.com/${REPO}/${REVIEW_BRANCH}/` +
    `${reviewPath(c.opportunity_id)}?t=${Date.now()}`;
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (res.status === 404) {
      noteCache.set(c.opportunity_id, { note: "" }); // known-empty; don't refetch
      return;
    }
    if (!res.ok) throw new Error(`load failed (${res.status})`);
    const review = await res.json();
    noteCache.set(c.opportunity_id, review);
    if (currentOpp && currentOpp.opportunity_id === c.opportunity_id)
      note.value = review.note || ""; // ignore if the user has since navigated away
  } catch (err) {
    setStatus(`Couldn't load saved note: ${err.message}`, "bad");
  }
}

// Ensure the review-data branch exists, creating it off DEFAULT_BRANCH's HEAD on first ever save.
async function ensureReviewBranch() {
  const ref = await fetch(`${API}/repos/${REPO}/git/ref/heads/${REVIEW_BRANCH}`, {
    headers: ghHeaders(),
  });
  if (ref.ok) return;
  if (ref.status !== 404) throw new Error(`branch check failed (${ref.status})`);
  const base = await fetch(`${API}/repos/${REPO}/git/ref/heads/${DEFAULT_BRANCH}`, {
    headers: ghHeaders(),
  });
  if (!base.ok) throw new Error(`can't read ${DEFAULT_BRANCH} (${base.status})`);
  const baseSha = (await base.json()).object.sha;
  const created = await fetch(`${API}/repos/${REPO}/git/refs`, {
    method: "POST",
    headers: ghHeaders(),
    body: JSON.stringify({ ref: `refs/heads/${REVIEW_BRANCH}`, sha: baseSha }),
  });
  // 422 = ref already exists (someone raced us) — fine.
  if (!created.ok && created.status !== 422)
    throw new Error(`can't create ${REVIEW_BRANCH} (${created.status})`);
}

// Save the current opportunity's note: GET current SHA on review-data -> PUT the file back.
async function saveNote() {
  const c = currentOpp;
  if (!c) {
    setStatus("No opportunity selected.", "bad");
    return;
  }
  if (!getPat()) {
    setStatus("Enter a GitHub token first.", "bad");
    el("rv-pat-details").open = true;
    el("rv-pat").focus();
    return;
  }
  const btn = el("rv-save");
  btn.setAttribute("aria-busy", "true");
  btn.disabled = true;
  setStatus("Saving…", null);
  try {
    await ensureReviewBranch();
    const path = reviewPath(c.opportunity_id);

    // Current SHA (required to overwrite an existing file); 404 -> first write, no sha.
    let sha;
    const cur = await fetch(`${API}/repos/${REPO}/contents/${path}?ref=${REVIEW_BRANCH}`, {
      headers: ghHeaders(),
    });
    if (cur.ok) sha = (await cur.json()).sha;
    else if (cur.status !== 404) throw new Error(`SHA check failed (${cur.status})`);

    const review = {
      schema_version: 1,
      opportunity_id: c.opportunity_id,
      symbol: c.symbol,
      trading_date: el("rv-date").value || String(c.opportunity_id).split(":")[0],
      note: el("rv-note").value,
      annotations: {}, // filled in Phase 2 (#144)
      updated_utc: new Date().toISOString(),
    };
    const body = {
      message: `review: ${c.opportunity_id}`,
      content: b64(JSON.stringify(review, null, 2)),
      branch: REVIEW_BRANCH,
    };
    if (sha) body.sha = sha;

    const put = await fetch(`${API}/repos/${REPO}/contents/${path}`, {
      method: "PUT",
      headers: ghHeaders(),
      body: JSON.stringify(body),
    });
    if (!put.ok) {
      let detail = `${put.status}`;
      try {
        detail = (await put.json()).message || detail;
      } catch (_) {
        /* non-JSON error body */
      }
      throw new Error(detail);
    }
    noteCache.set(c.opportunity_id, review);
    setStatus("Saved ✓", "ok");
  } catch (err) {
    setStatus(`Save failed: ${err.message}`, "bad");
  } finally {
    btn.removeAttribute("aria-busy");
    btn.disabled = false;
  }
}

function openSheet() {
  el("rv-scrim").hidden = false;
  el("rv-sheet").classList.add("open");
  el("rv-sheet").setAttribute("aria-hidden", "false");
  el("rv-pat-details").open = !getPat(); // nudge the token field only when it's not set yet
}
function closeSheet() {
  el("rv-scrim").hidden = true;
  el("rv-sheet").classList.remove("open");
  el("rv-sheet").setAttribute("aria-hidden", "true");
}

async function init() {
  const index = await fetchJson("index.json");
  const dates = (index && index.dates) || [];
  const dateSel = el("rv-date");
  if (!dates.length) {
    dateSel.innerHTML = '<option>—</option>';
    clearChart("No review data published yet.");
    return;
  }
  // index.json dates are already sorted newest-first (#141).
  dateSel.innerHTML = dates
    .map((d) => `<option value="${esc(d.date)}">${esc(d.date)}</option>`)
    .join("");
  await loadDate(dateSel.value);
}

el("rv-date").addEventListener("change", (e) => loadDate(e.target.value));
el("rv-symbol").addEventListener("change", drawSelected);
el("rv-prev").addEventListener("click", () => stepSymbol(-1));
el("rv-next").addEventListener("click", () => stepSymbol(1));

// Notes sheet + write-back (#143).
el("rv-pat").value = getPat(); // restore the phone-local token across reloads
el("rv-pat").addEventListener("input", (e) => localStorage.setItem(PAT_KEY, e.target.value.trim()));
el("rv-notes-toggle").addEventListener("click", openSheet);
el("rv-sheet-close").addEventListener("click", closeSheet);
el("rv-scrim").addEventListener("click", closeSheet);
el("rv-save").addEventListener("click", saveNote);

init();
