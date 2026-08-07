// Shared DOM access for the cockpit pages (#406).
//
// Every page is plain HTML + a module that reaches into it by id, and the two
// ship as separate static assets. GitHub Pages serves both with `max-age=600`,
// and a browser reload revalidates the navigation HTML while still serving a
// fresh-enough script from cache — so for up to ten minutes after a deploy that
// changed the markup, a visitor can run the *previous* JS against the *current*
// HTML. The stale script then reaches for an element that no longer exists and
// the page dies on `Cannot set properties of null`, which reads like the box is
// down when nothing is wrong with it (that is exactly what #403 produced: the
// removed `#charts-card` panel).
//
// `el()` can't stop the desync — no build step, no control over Pages' cache
// headers — but it can name it. A missing element raises a MissingElementError
// that says which id vanished and that a hard reload fixes it, and `showError`
// puts that message in the page's banner instead of the data-feed wording.
//
// The other half of the guard is static: tests/test_dashboard_dom.py fails the
// build when a page's JS references an id its HTML doesn't define, so the
// permanent version of this mismatch can't be merged in the first place.

export class MissingElementError extends Error {
  constructor(id) {
    super(
      `missing #${id} — this page's HTML and JavaScript are out of step. ` +
        "That is usually a script cached from an earlier deploy: hard-reload " +
        "(Cmd/Ctrl-Shift-R) to pick up the current assets.",
    );
    this.name = "MissingElementError";
    this.id = id;
  }
}

// The lookup every page uses. Throws rather than handing back null, so the
// failure names the element instead of surfacing three frames later as a
// null-property error on whatever the caller did next.
export function el(id) {
  const node = document.getElementById(id);
  if (node === null) throw new MissingElementError(id);
  return node;
}

// Write (or clear, with a falsy message) a page's error banner. Resolved with a
// bare lookup: a banner that has itself gone missing must not throw over the top
// of the error it was asked to report.
export function setBanner(id, msg) {
  const banner = document.getElementById(id);
  if (!banner) return;
  banner.hidden = !msg;
  banner.textContent = msg || "";
}

// Report a caught failure. A stale-asset mismatch keeps its own wording — the
// page's "failed to load X" prefix would blame the data feed for a front-end
// problem — and everything lands in the console either way.
export function showError(bannerId, prefix, err) {
  const msg =
    err instanceof MissingElementError
      ? `Dashboard page out of date: ${err.message}`
      : `${prefix}: ${(err && err.message) || err}`;
  setBanner(bannerId, msg);
  console.error(msg, err);
  return msg;
}
