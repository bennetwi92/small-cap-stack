"""The dashboard's HTML↔JS contract (#406).

`docs/` is the Pages frontend: each page is static HTML plus an ES module that reaches into
it by id. Nothing in the build ties the two together, so a PR can delete an element and leave
the script that reads it (or add a script that reads an element nobody wrote) and CI stays
green — the page only breaks in the browser, as a bare `Cannot set properties of null`.

#403 removed the dashboard's chart panel and its renderer together, so HEAD was consistent,
but a browser holding the previous `app.js` from cache ran it against the new `index.html`
and blew up on the deleted `#charts-card`. These tests close the permanent half of that gap:
every id a page's JS reaches for must exist somewhere that page can actually produce it.

Scope note: this checks *literal* id lookups — `el("x")`, `getElementById("x")`,
`querySelector("#x")`. Computed ids are invisible to it, by design; a regex that chased them
would be guessing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"

# `id="x"` in real markup and in the template strings the modules inject.
ID_ATTR = re.compile(r"""\bid=["']([\w-]+)["']""")
# `{ type: "btn", id: "refresh", … }` — options-bar fields become real elements at runtime.
OPTBAR_FIELD_ID = re.compile(r"""\bid:\s*["']([\w-]+)["']""")
# The literal lookups a page performs.
ID_REF = re.compile(
    r"""\bel\(\s*["']([\w-]+)["']\s*\)"""
    r"""|\bgetElementById\(\s*["']([\w-]+)["']\s*\)"""
    r"""|\bquerySelector\(\s*["']#([\w-]+)["']\s*\)"""
)
# Relative ES-module imports — `import … from "./js/dom.js"` / `import "./js/nav.js"`.
REL_IMPORT = re.compile(r"""\bfrom\s+["'](\.[^"']+)["']|\bimport\s+["'](\.[^"']+)["']""")
# `<script type="module" src="app.js">`
PAGE_SCRIPT = re.compile(r"""<script[^>]*\btype=["']module["'][^>]*\bsrc=["']([^"']+)["']""")


def module_graph(entry: Path) -> list[Path]:
    """Every local module reachable from `entry`, including itself (CDN imports skipped)."""
    seen: list[Path] = []
    pending = [entry]
    while pending:
        mod = pending.pop()
        if mod in seen or not mod.exists():
            continue
        seen.append(mod)
        for match in REL_IMPORT.finditer(mod.read_text(encoding="utf-8")):
            spec = next(g for g in match.groups() if g)
            pending.append((mod.parent / spec).resolve())
    return seen


def ids_defined(html: str, sources: list[str]) -> set[str]:
    """Ids the page can produce: static markup, injected markup, options-bar fields."""
    defined = set(ID_ATTR.findall(html))
    for src in sources:
        defined |= set(ID_ATTR.findall(src))
        defined |= set(OPTBAR_FIELD_ID.findall(src))
    return defined


def ids_referenced(sources: dict[str, str]) -> dict[str, set[str]]:
    """Map each looked-up id to the module names that look it up."""
    refs: dict[str, set[str]] = {}
    for name, src in sources.items():
        for match in ID_REF.finditer(src):
            refs.setdefault(next(g for g in match.groups() if g), set()).add(name)
    return refs


def pages() -> list[tuple[str, str]]:
    """(page.html, its entry module) for every page that loads one."""
    found = []
    for html in sorted(DOCS.glob("*.html")):
        for src in PAGE_SCRIPT.findall(html.read_text(encoding="utf-8")):
            found.append((html.name, src))
    return found


# ------------------------------------------------------------------ the extractors themselves
# A checker that cannot fail protects nothing, so pin the behaviour on synthetic input first.


def test_ids_referenced_finds_each_lookup_form() -> None:
    src = """
      const a = el("alpha");
      document.getElementById('beta').textContent = "";
      bar.querySelector("#gamma");
    """
    assert ids_referenced({"m.js": src}).keys() == {"alpha", "beta", "gamma"}


def test_ids_referenced_ignores_computed_lookups() -> None:
    assert ids_referenced({"m.js": 'el(name); getElementById("x" + i);'}) == {}


def test_ids_defined_spans_markup_injection_and_options_bar() -> None:
    defined = ids_defined(
        '<p id="static"></p>',
        ['mount.innerHTML = `<span id="injected"></span>`;', '{ type: "btn", id: "field" }'],
    )
    assert {"static", "injected", "field"} <= defined


def test_the_check_catches_a_removed_element() -> None:
    """The #403 shape: markup drops an element, the script keeps reaching for it."""
    html, js = '<p id="error"></p>', 'el("charts-card").hidden = true;'
    missing = set(ids_referenced({"app.js": js})) - ids_defined(html, [js])
    assert missing == {"charts-card"}


def test_module_graph_follows_relative_imports_only(tmp_path: Path) -> None:
    (tmp_path / "js").mkdir()
    (tmp_path / "entry.js").write_text(
        'import { el } from "./js/dom.js";\n'
        'import "./js/nav.js";\n'
        'import { X } from "https://cdn.example/x.js";\n'
    )
    (tmp_path / "js" / "dom.js").write_text("export const el = 1;")
    (tmp_path / "js" / "nav.js").write_text("")
    graph = {p.name for p in module_graph(tmp_path / "entry.js")}
    assert graph == {"entry.js", "dom.js", "nav.js"}


# -------------------------------------------------------------------------- the real frontend


def test_every_page_declares_an_entry_module() -> None:
    assert {name for name, _ in pages()} == {
        "index.html",
        "review.html",
        "results.html",
        "portfolio.html",
        "reports.html",
        "plan.html",
    }


@pytest.mark.parametrize(("page", "entry"), pages())
def test_page_entry_module_exists(page: str, entry: str) -> None:
    assert (DOCS / entry).is_file(), f"{page} loads {entry}, which is not in docs/"


@pytest.mark.parametrize(("page", "entry"), pages())
def test_page_js_only_reaches_for_ids_the_page_defines(page: str, entry: str) -> None:
    """Every literal id lookup in a page's module graph resolves on that page."""
    html = (DOCS / page).read_text(encoding="utf-8")
    modules = module_graph(DOCS / entry)
    sources = {m.name: m.read_text(encoding="utf-8") for m in modules}
    defined = ids_defined(html, list(sources.values()))
    dangling = {i: sorted(w) for i, w in ids_referenced(sources).items() if i not in defined}
    assert not dangling, (
        f"{page} → {entry}: JS reaches for ids the page never defines: {dangling}. "
        "Either the markup lost an element the script still reads, or the lookup is a typo."
    )


def test_pages_share_one_dom_helper() -> None:
    """No page may re-fork `el` — the shared one is what reports a stale-asset mismatch."""
    forked = [
        entry
        for _, entry in pages()
        if "document.getElementById(id)" in (DOCS / entry).read_text(encoding="utf-8")
    ]
    assert not forked, f"{forked} define a local `el`; import it from js/dom.js instead"


# ------------------------------------------------------------- memoised fetches (#509)
# There is no browser coverage in CI, so a promise-cache bug has no runtime test to catch it.
# These are structural, which is weak — but the failure they guard is permanent-until-reload
# and invisible in review, which is a bad combination to leave entirely unchecked.

#: Module-level promise memos in `inspector.js`, and the function that populates each.
PROMISE_CACHES = (("_payloads", "chartsFor"), ("_reviews", "reviewFor"))


@pytest.mark.parametrize(("cache", "populator"), PROMISE_CACHES)
def test_a_memoised_fetch_evicts_itself_on_rejection(cache: str, populator: str) -> None:
    """Caching a rejected promise makes one dropped request permanent (#509).

    `chartsFor` stored the raw `fetchJson` promise. `fetchJson` answers `null` for a missing or
    unparsable file, so the only way it rejects is a transport failure — offline, DNS, CORS — the
    one case that is certainly transient. Cached, it stopped being transient: both hosts paint
    "loading…" then await the memo inside an unguarded `async`, so the dock sat on that word for
    that date until a reload.

    The populator must therefore both catch AND delete its key, not merely catch.
    """
    src = (DOCS / "js" / "inspector.js").read_text(encoding="utf-8")
    start = src.index(f"export function {populator}(")
    end = src.find("\nexport ", start + 1)
    body = src[start : end if end != -1 else len(src)]

    catch_at = body.find(".catch(")
    assert catch_at != -1, f"{populator} memoises a promise without catching its rejection"
    # The delete must live INSIDE the catch. A `delete` earlier in the function satisfies a
    # whole-body search while defeating the memo entirely — every row click would re-download a
    # 1.5–3 MB payload. That is a worse bug than the one this guard prevents, so it must not pass.
    handler = body[catch_at:]
    evictions = [ln.strip() for ln in handler.splitlines() if f"{cache}.delete(" in ln]
    assert evictions, (
        f"{populator} must evict its key from within the rejection handler — either it caches the "
        f"failure (a dropped request stays dropped until a reload), or it deletes unconditionally "
        f"and there is no memo left at all."
    )
    # Identity-guarded ON THE EVICTION LINE ITSELF. Checking the handler region for a `.get(`
    # anywhere is satisfied by the function's own `return <cache>.get(key)`, so it would pass a
    # version with no guard at all.
    for line in evictions:
        assert f"{cache}.get(" in line and "===" in line, (
            f"{populator} evicts without checking it still owns the entry ({line!r}). A slow "
            f"rejection would then throw away a NEWER value — a Refresh's in-flight refetch, or "
            f"cacheReview's post-save seed, which would make a just-saved note read back as absent."
        )
