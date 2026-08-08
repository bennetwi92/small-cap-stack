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

import os
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
    # `showError("rv-error", …)` / `setBanner("pf-error", …)`. These resolve their node with a
    # bare `document.getElementById` and return quietly when it is missing — deliberately, so a
    # handler can't throw while reporting an error. The cost is that a deleted banner fails
    # *silently*: the page swallows its own failures and looks merely blank. #508 shipped
    # `#rv-error` past this check because the old pattern didn't see it, so it is checked here.
    r"""|\b(?:showError|setBanner)\(\s*["']([\w-]+)["']"""
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
      showError("delta-error", "Failed to load", err);
      setBanner('epsilon-error', msg);
    """
    assert ids_referenced({"m.js": src}).keys() == {
        "alpha",
        "beta",
        "gamma",
        "delta-error",
        "epsilon-error",
    }


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


#: `catch (e) {`, and `catch {` — the optional-binding form, which this frontend uses six times.
#: Requiring the parameter list left 38% of the corpus unscanned by the first version of this.
_CATCH = re.compile(r"\}\s*catch\s*(?:\([^)]*\))?\s*\{")


def _blank_literals(src: str) -> str:
    """`src` with the *contents* of strings, template literals, comments and regexes replaced by
    spaces — same length, same line numbers, no syntax inside them.

    Scanning raw text is what makes a hand-rolled matcher lie. A `}` inside a string truncates a
    brace-matched body; stripping `//…` blindly eats the rest of a line from inside a URL. Both
    are silent *false negatives* in a guard, which is the worst kind: it reports success.
    """
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch in "\"'`":
            quote, j = ch, i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    break
                if src[j] != "\n":
                    out[j] = " "
                j += 1
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j == -1 else j
            out[i:j] = " " * (j - i)
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                if src[k] != "\n":
                    out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def _catch_bodies(src: str) -> list[tuple[int, str]]:
    """`(line, body)` for every catch block, brace-matched over literal-blanked source.

    The body returned is the *blanked* text: line numbers still line up, but nothing inside a
    string or comment can be mistaken for code by the caller either.
    """
    masked = _blank_literals(src)
    out: list[tuple[int, str]] = []
    for match in _CATCH.finditer(masked):
        depth, start = 0, match.end() - 1
        for i in range(start, len(masked)):
            if masked[i] == "{":
                depth += 1
            elif masked[i] == "}":
                depth -= 1
                if depth == 0:
                    out.append((masked.count("\n", 0, match.start()) + 1, masked[start : i + 1]))
                    break
    return out


def test_no_error_handler_can_throw_over_the_error_it_reports() -> None:
    """`el()` throws by design, so calling it inside a `catch` can re-throw over the failure being
    handled — and the failure most likely to reach a `catch` on these pages is exactly the one
    `el` raises: a stale-asset `MissingElementError` (#515).

    When that happens the user sees **nothing**: no banner, no message, a blank panel. `plan.js`
    hand-rolled `el("pl-error").textContent = …` in its catch, and `reports.js` cleared
    `el("rp-doc-body")` before reporting. `setBanner`/`showError` resolve their node with a bare
    `getElementById` precisely so a missing banner stays silent instead of shouting over the
    message it was asked to deliver.

    Strings and comments are blanked first, so the prose explaining all this cannot trip its own
    check — and a `}` inside a string can't truncate a body before the `el(` that follows it.

    ⚠️ **Limit: this sees literal `el(` only.** A handler that calls a *helper* which calls `el`
    has the same hazard and is invisible here — `review.js`'s `setStatus` was exactly that, and
    was fixed by reading rather than by this test. One level of indirection is where a text scan
    stops being honest; don't read a green run as proof no handler can throw.
    """
    offenders: list[str] = []
    for path, src in _js_sources().items():
        for line, body in _catch_bodies(src):
            if re.search(r"(?<![\w.])el\(", body):
                offenders.append(f"{path}:{line}")
    assert not offenders, (
        "these catch blocks call the throwing `el()`; use setBanner/showError (or a bare "
        "getElementById) so the handler can't outlive its own error:\n  " + "\n  ".join(offenders)
    )


def test_the_catch_scanner_survives_the_forms_that_fooled_it() -> None:
    """Each of these was a silent false negative in the first version — the guard passed while
    the hazard sat in the block it failed to read.

    Bodies come back literal-blanked, so these assert on the `el(` call form; the argument itself
    is (correctly) spaces by then.
    """
    at = lambda js: _catch_bodies(js)[0][1]  # noqa: E731
    # Optional catch binding: `catch {`, no parameter list. Six of these exist in docs/, and the
    # first version's regex required the parens — leaving 38% of the corpus unscanned.
    assert "el(" in at('try { a(); } catch { el("x"); }')
    # A `}` inside a string or template used to end the body early, hiding everything after it.
    assert "el(" in at('try { a(); } catch (e) { log("oops }"); el("x"); }')
    assert "el(" in at('try { a(); } catch (e) { log(`a } b`); el("x"); }')
    # `//` inside a string used to blank the rest of the line, including the el() after it.
    assert "el(" in at('try { a(); } catch (e) { log("https://h"); el("x"); }')
    # A nested object literal is balanced and must NOT end the body early.
    assert "el(" in at('try { a(); } catch (e) { f({ k: 1 }); el("x"); }')
    # And the converse: a clean handler still reads clean.
    assert "el(" not in at('try { a(); } catch (e) { showError("x-error", "nope", e); }')


def test_the_literal_blanker_preserves_offsets_and_hides_content() -> None:
    """Blanking must not shift a single character, or every reported line number is wrong."""
    src = 'a("}");\n// note }\nb();'
    masked = _blank_literals(src)
    assert len(masked) == len(src)
    assert masked.count("\n") == src.count("\n")
    assert "}" not in masked  # both the string's and the comment's brace are gone
    assert masked.startswith('a("') and "b();" in masked  # code itself survives


def test_the_catch_scanner_finds_a_real_block() -> None:
    """Brace-matching that silently matches nothing makes the check above vacuous."""
    src = 'try { a(); } catch (e) { el("x"); }\ntry { b(); } catch (err) { ok(); }'
    bodies = _catch_bodies(src)
    assert len(bodies) == 2
    assert "el(" in bodies[0][1] and "el(" not in bodies[1][1]
    # And the real corpus is scanned in full. A floor ("more than 5") was useless: the broken
    # version found 10 of 16 real blocks and sailed past it. Compare against the `catch` keywords
    # actually present instead — self-maintaining, and losing a whole *form* of catch block fails
    # here rather than quietly shrinking the guard's reach.
    for path, src in _js_sources().items():
        # `(?<![\w.])` already excludes the promise form `.catch(` — that is a callback, not a
        # catch *block*, and it has no braces of its own to match.
        statements = len(re.findall(r"(?<![\w.])catch\s*[({]", _blank_literals(src)))
        found = len(_catch_bodies(src))
        assert found == statements, (
            f"{path}: the scanner reads {found} catch blocks but the file has {statements}. "
            "A form it can't parse is a form it can't guard."
        )


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


# --------------------------------------------------- the options bar survives a Refresh (#512)
#
# `createOptionsBar` wipes its mount, and the `···` extras row — where the config/coverage line
# lives — reopens collapsed. Refresh re-runs `load()`, so a rebuild there closes that panel out
# from under someone mid-read.
#
# These are shape checks over source text: weak, and deliberately narrow. They pin the *mechanism*
# each page uses rather than trying to prove conditionality in general. A JS parser would be the
# honest tool; there is no browser and no JS toolchain in CI at all.

#: page -> the memo it compares before rebuilding. This is the complete set of pages with a
#: `buildOptbar` reachable from a refresh path: plan/app/reports call `createOptionsBar` once at
#: module scope, and `review.js` hand-rolls its bar rather than importing the module.
OPTBAR_MEMOS = {"portfolio.js": "optbarBuiltFrom", "results.js": "optbarOffersScope"}


def _fn_body(src: str, header: str) -> str:
    """A function's body, extracted by brace-matching from its opening `{`.

    Not `src.find("\\n}")`: a template literal containing a column-0 `}` would end the slice early
    and leave the rest of the function unscanned, silently passing whatever it contained.
    """
    open_at = src.index("{", src.index(header))
    depth = 0
    for j in range(open_at, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at : j + 1]
    raise AssertionError(f"unbalanced braces after {header!r}")


@pytest.mark.parametrize("page", sorted(OPTBAR_MEMOS))
def test_load_does_not_rebuild_the_options_bar_unconditionally(page: str) -> None:
    """No `buildOptbar()` may sit directly in `load()`'s body."""
    body = _fn_body((DOCS / page).read_text(encoding="utf-8"), "async function load(")
    depth = 0
    for line in body.splitlines():
        if line.strip() == "buildOptbar();" and depth <= 1:
            raise AssertionError(
                f"{page}: `buildOptbar();` runs directly in `load()`, which Refresh re-runs, so it "
                f"collapses the `···` extras row. Guard it on the control set changing."
            )
        for ch in line.split("//")[0]:
            depth += (ch == "{") - (ch == "}")


@pytest.mark.parametrize(("page", "memo"), sorted(OPTBAR_MEMOS.items()))
def test_the_optbar_rebuild_is_memoised(page: str, memo: str) -> None:
    """The page must record what its bar was built from, and compare it before rebuilding.

    This is the half the first version of these tests missed. #512 moved `buildOptbar()` out of
    `load()` entirely, so the check above matched **zero** lines in `portfolio.js` — a standing
    regression guard for #512 that asserted nothing about the file it was written for.
    `rebuildOptbarIfControlsChanged` could have been gutted to rebuild every time and it would
    have stayed green.
    """
    src = (DOCS / page).read_text(encoding="utf-8")
    assert memo in src, f"{page} has no record of what its options bar was last built from"
    compared = [
        ln.strip()
        for ln in src.splitlines()
        if memo in ln and ("===" in ln or "!==" in ln) and not ln.strip().startswith("//")
    ]
    assert compared, (
        f"{page}: `{memo}` is never compared, so the rebuild cannot be conditional on it — "
        f"Refresh would collapse the `···` extras row."
    )


# ------------------------------------------------ one staleness threshold, one place (#516)

#: The single module allowed to define a staleness threshold.
THRESHOLDS = "js/thresholds.js"
#: Names that must resolve to exactly one definition across the whole frontend.
SHARED_THRESHOLDS = ("STALE_PUBLISH_MS", "HARVEST_STALE_H")


def _js_sources() -> dict[str, str]:
    """Every frontend module, keyed by its path relative to docs/."""
    return {
        str(p.relative_to(DOCS)): p.read_text(encoding="utf-8") for p in sorted(DOCS.rglob("*.js"))
    }


@pytest.mark.parametrize("name", SHARED_THRESHOLDS)
def test_a_staleness_threshold_is_defined_exactly_once(name: str) -> None:
    """Both pages render on Plan, so two values meant it contradicted itself (#516).

    The status bar warned at 30 minutes while the Plan page's "Data freshness" check called the
    same `status.json.generated_utc` fresh until 60 — a FRESH row above an amber bar reporting
    the same timestamp, with no way for a reader to tell which was right.
    """
    definers = [
        path
        for path, src in _js_sources().items()
        if re.search(rf"^\s*(?:export\s+)?(?:const|let|var)\s+{name}\s*=", src, re.M)
    ]
    assert definers == [THRESHOLDS], (
        f"{name} must be defined only in {THRESHOLDS}, and imported everywhere else — "
        f"found: {definers}"
    )


def test_no_page_hardcodes_a_staleness_literal() -> None:
    """A re-inlined literal is how the two values drifted apart in the first place.

    Targeted at *staleness* comparisons rather than any numeric comparison, so an ordinary
    `if (n > 0)` isn't swept up. Both real shapes are covered: an arithmetic duration
    (`ageMs > 60 * 60_000`) and a bare count (`hrs > 36`) — my first version matched only the
    former, so `app.js` going back to `hrs > 36` passed a guard written to prevent exactly that.
    """
    offenders = [
        f"{path}:{i}: {ln.strip()}"
        for path, src in _js_sources().items()
        if path != THRESHOLDS
        for i, ln in enumerate(src.splitlines(), 1)
        # Comments excluded, so the prose explaining all this cannot trip its own check.
        if not ln.strip().startswith("//")
        and "stale" in ln.lower()
        and re.search(r"[<>]=?\s*\d[\d_]*(\s*\*\s*\d[\d_]*)*\s*[;,)&|?]", ln)
    ]
    assert not offenders, (
        "staleness compared against an inline literal; import it from "
        f"{THRESHOLDS} instead:\n" + "\n".join(offenders)
    )


# ------------------------------------------------ one ET clock, one place (#510)

#: The single module allowed to construct a date/time formatter.
FMT = "js/fmt.js"


#: Every way a module can render a date in the *viewer's* zone. `Intl.DateTimeFormat` is the
#: spelling #510 was written in; the `toLocale*` family is the shorter one, and banning only the
#: former would leave a one-line path back to the same bug. `Number.toLocaleString` (digit
#: grouping, used on three pages) is deliberately not here — it is matched on `Date` receivers
#: and on the two Date-only method names.
LOCAL_TIME_SPELLINGS = (
    re.compile(r"new\s+Intl\s*(?:\.\s*DateTimeFormat|\[\s*[\"']DateTimeFormat[\"']\s*\])"),
    re.compile(r"\.toLocale(?:Time|Date)String\b"),
    re.compile(r"\bDate\b[^;\n]*\.toLocaleString\b"),
)


def test_only_one_module_builds_a_datetime_formatter() -> None:
    """Forking the ET formatter is how two call sites lost their `timeZone` (#510).

    The four copies in `fmt.js` / `session.js` / `status-bar.js` / `app.js` were byte-identical —
    they had not drifted. The failure was that with no obvious shared helper, the "updated …" and
    "fetched …" stamps each wrote a *fresh* formatter and both omitted `timeZone`, so an
    unlabelled browser-local clock rendered between `PRE 09:14 ET` and `data 14:22 ET` on the same
    status line. From London that reads as a five-hour discrepancy in the data feed. Nothing was
    wrong with the feed.

    So this bans the *construction*, not the duplication — and bans it in every spelling, because
    `new Date().toLocaleTimeString()` restores the bug in one line and passed a first draft of
    this test that only knew about `Intl.DateTimeFormat`.
    """
    offenders = [
        f"{path}:{i}: {ln.strip()}"
        for path, src in _js_sources().items()
        if path != FMT
        for i, ln in enumerate(src.splitlines(), 1)
        if not ln.strip().startswith("//") and any(p.search(ln) for p in LOCAL_TIME_SPELLINGS)
    ]
    assert not offenders, (
        f"dates must be formatted by {FMT}'s helpers, which are pinned to ET. A locally-built "
        "formatter renders the viewer's clock, and the page gives it no label:\n"
        + "\n".join(offenders)
    )


def _formatter_options(src: str) -> list[str]:
    """The options object of each `new Intl.DateTimeFormat(…)`, by matching parens.

    Not `\\((.*?)\\);` — a constructor without a trailing semicolon (ASI is legal, and nothing
    lints JS in this repo) makes the lazy match run to the *next* `);` in the file, so a
    timeZone-less formatter borrows the following one's `timeZone` and the check passes.
    """
    out: list[str] = []
    for m in re.finditer(r"new Intl\.DateTimeFormat\(", src):
        depth, start = 0, m.end() - 1
        for i in range(start, len(src)):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    out.append(src[start + 1 : i])
                    break
    return out


def test_every_et_formatter_is_pinned_to_eastern() -> None:
    """The specific defect, stated directly: a formatter with no `timeZone` renders the viewer's
    clock. Checked against the formatters themselves rather than their call sites, so it holds
    even if `fmt.js` grows a new one."""
    ctors = _formatter_options(_js_sources()[FMT])
    assert ctors, f"no formatter found in {FMT} — has it moved?"
    missing = [c.strip()[:60] for c in ctors if "timeZone" not in c]
    assert not missing, (
        "these formatters have no timeZone and will render the viewer's local clock: "
        f"{missing}. Every time this app prints is ET."
    )


def test_the_formatter_parser_stops_at_its_own_closing_paren() -> None:
    """The bug the paren-matching replaced: with a lazy `.*?);` the first (bad) formatter would
    swallow the second and inherit its `timeZone`, and both would vanish from the result."""
    src = (
        # No trailing semicolon on the first — ASI is legal and nothing lints JS here.
        'const a = new Intl.DateTimeFormat("en-US", { hour: "2-digit" })\n'
        'const b = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York" });\n'
    )
    opts = _formatter_options(src)
    assert len(opts) == 2
    assert "timeZone" not in opts[0] and "timeZone" in opts[1]


# `import { a, b as c } from "./x.js"` — and the same clause after a default binding
# (`import D, { a } from …`). `import * as ns` binds no individual names, so there is nothing
# for this check to resolve; a default-only import is checked via the "default" pseudo-name.
_NAMED_IMPORT = re.compile(
    r"import\s+(?:[A-Za-z_$][\w$]*\s*,\s*)?\{([^}]+)\}\s*from\s*[\"'](\.[^\"']+)[\"']", re.S
)


def _exports(src: str) -> set[str] | None:
    """Names a module exports, or `None` when the module re-exports a whole namespace.

    `export * from "./x.js"` makes the export list unknowable without following the chain, so the
    honest answer is "can't tell" — returning a partial set would make the check above report a
    perfectly valid import as broken. No module does this today; it is the one form that turns
    this guard from a safety net into a nuisance, which is how guards get deleted.
    """
    if re.search(r"export\s*\*\s*from", src):
        return None
    decl = r"export\s+(?:async\s+)?(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)"
    names = set(re.findall(decl, src))
    if re.search(r"export\s+default\b", src):
        names.add("default")
    for clause in re.findall(r"export\s*\{([^}]*)\}", src):
        for part in clause.split(","):
            alias = part.strip().split(" as ")
            if alias and alias[-1].strip():
                names.add(alias[-1].strip())
    return names


def test_every_named_import_resolves_to_a_real_export() -> None:
    """There is no browser coverage in CI (CLAUDE.md), so a frontend change ships green even when
    a module imports a name that no longer exists — the page then dies at load with
    `SyntaxError: does not provide an export named …` and nothing before the user sees it.

    Cheap to check statically, and it earns its keep whenever exports move between modules — as
    #510 did, folding five forked ET formatters into `fmt.js`.
    """
    sources = _js_sources()
    broken: list[str] = []
    for path, src in sources.items():
        for names, target in _NAMED_IMPORT.findall(src):
            resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
            if resolved not in sources:
                broken.append(f"{path}: imports from {target}, which does not exist")
                continue
            available = _exports(sources[resolved])
            if available is None:  # `export *` — the list isn't knowable from one file
                continue
            for raw in names.split(","):
                name = raw.strip().split(" as ")[0].strip()
                if name and name not in available:
                    broken.append(f"{path}: `{name}` is not exported by {resolved}")
    assert not broken, "broken named imports — these pages die at load:\n" + "\n".join(broken)


def test_the_import_check_would_notice_a_missing_export() -> None:
    """A resolver that silently finds nothing makes the check above vacuous — and it has two ways
    to do that: a path that doesn't resolve, or an export form it can't parse (which shows up as
    a false *failure*, the kind of noise that gets a guard deleted rather than fixed)."""
    assert "etClockNowSec" in (_exports(_js_sources()[FMT]) or set())
    assert "etClockNowSec" not in (_exports("export const somethingElse = 1;") or set())
    assert _exports("const hidden = 1;\nexport { hidden as shown };") == {"shown"}
    assert _exports('export { a } from "./y.js";') == {"a"}  # a re-export IS an export
    assert _exports("export default function x() {}") == {"default"}
    assert _exports('export * from "./y.js";') is None  # unknowable — must not report broken
    assert len(_js_sources()) > 10  # the corpus is real, not an empty dict


def test_the_import_pattern_reads_every_clause_form_in_use() -> None:
    """`import D, { a } from` and a clause wrapped over several lines both appear in real code and
    both used to be skipped silently — a blind spot in a guard reads exactly like a pass."""
    assert _NAMED_IMPORT.findall('import { a, b } from "./x.js";') == [(" a, b ", "./x.js")]
    assert _NAMED_IMPORT.findall('import D, { a } from "./x.js";') == [(" a ", "./x.js")]
    assert _NAMED_IMPORT.findall('import {\n  a,\n  b,\n} from "./x.js";') == [
        ("\n  a,\n  b,\n", "./x.js")
    ]
    # A CDN import is not ours to resolve.
    assert _NAMED_IMPORT.findall('import { T } from "https://cdn/x.js";') == []


# ------------------------------------------------ one name, one behaviour (#527)


def test_no_page_redefines_a_name_that_fmt_js_exports() -> None:
    """`portfolio.js` declared its own `fmtR` — a **signed** one — while `js/fmt.js` exports an
    unsigned `fmtR` that `results.js` uses. So `fmtR(0.5)` rendered `0.50R` on Results and
    `+0.50R` on Portfolio: one identifier, two behaviours, two pages showing the same book.

    Shadowing is what made that invisible. A local `const fmtR` reads as "this page's helper"
    rather than as a divergence from the shared one, and the page couldn't have imported the
    shared version anyway — the declaration would have been a redeclaration.
    """
    shared = _exports(_js_sources()[FMT]) or set()
    assert "fmtR" in shared and "fmtRSigned" in shared, "fmt.js's R formatters moved"
    offenders: list[str] = []
    for path, src in _js_sources().items():
        if path == FMT:
            continue
        for match in re.finditer(r"^\s*(?:const|let|var|function)\s+([A-Za-z_$][\w$]*)", src, re.M):
            if match.group(1) in shared:
                line = src.count("\n", 0, match.start()) + 1
                offenders.append(f"{path}:{line}: local `{match.group(1)}` shadows js/fmt.js")
    assert not offenders, (
        "these shadow a shared formatter — import it, or rename the local so the difference is "
        "visible at the call site:\n  " + "\n  ".join(offenders)
    )


def test_portfolio_reads_its_svg_palette_from_the_stylesheet() -> None:
    """Five hex literals in `portfolio.js` matched `cockpit.css`'s `:root` character-for-character
    (#527), so a theme change moved every CSS-driven element and left the page's inline SVGs
    behind — drift that still renders, which is the hard kind to notice.

    The fallbacks are deliberate and must stay: a token that goes missing should leave the chart in
    today's colours, not paint strokes of `""`.
    """
    src = _js_sources()["portfolio.js"]
    assert "getComputedStyle(document.documentElement)" in src, (
        "the palette must come from the stylesheet, not from hex literals"
    )
    for token in ("--win", "--loss", "--dim", "--cyan", "--gold"):
        assert f'cssToken("{token}"' in src, f"{token} is not read from CSS"
    # Every entry resolves through the accessor: `key: cssToken("--token", "#fallback"),`. A bare
    # hex sitting directly in the object is the state this replaced.
    block = re.search(r"const PF_MK = \{(.*?)\n\};", src, re.S)
    assert block, "PF_MK is gone or reshaped"
    entries = [ln.strip() for ln in block.group(1).splitlines() if ln.strip()]
    assert entries, "PF_MK is empty"
    for entry in entries:
        assert re.fullmatch(r'\w+: cssToken\("--[\w-]+", "#[0-9a-fA-F]{6}"\),', entry), (
            f"PF_MK entry does not read from CSS with a fallback: {entry}"
        )


def test_the_two_palettes_keep_distinct_names() -> None:
    """`js/inspector.js` exports `MK` — the chart-candle palette (`up: "#1a7f37"`), which
    `review.js` imports. `portfolio.js`'s is a different palette for a different purpose, and while
    it was also called `MK` the collision hid that choice instead of stating it."""
    portfolio = _js_sources()["portfolio.js"]
    assert "PF_MK" in portfolio
    assert not re.search(r"^\s*const MK\b", portfolio, re.M), (
        "portfolio.js must not redeclare `MK` — inspector.js exports a different one"
    )
    assert re.search(r"^export const MK\b", _js_sources()["js/inspector.js"], re.M)


def test_the_fmt_palette_tokens_exist_in_the_stylesheet() -> None:
    """The accessor falls back silently by design, so a typo'd token name would go unnoticed —
    the page would keep rendering, permanently, in the fallback colours."""
    css = (DOCS / "cockpit.css").read_text()
    root = re.search(r":root\s*\{(.*?)\}", css, re.S)
    assert root, "cockpit.css has no :root block"
    declared = set(re.findall(r"(--[\w-]+)\s*:", root.group(1)))
    used = set(re.findall(r'cssToken\("(--[\w-]+)"', _js_sources()["portfolio.js"]))
    assert used, "no CSS tokens read — has the accessor been removed?"
    missing = used - declared
    assert not missing, f"portfolio.js reads tokens cockpit.css does not define: {missing}"


def test_the_provenance_chip_has_exactly_one_renderer() -> None:
    """`.pf-src` marks a row rebuilt from purchased vendor bars rather than captured live (#430),
    and it was hand-written in five places across Results and Portfolio. Four matched; the fifth —
    Portfolio's Date tile — had already lost its `title`, so the chip explained itself on four
    surfaces and stayed cryptic on the fifth. That is drift on a *provenance* marker, which is the
    one thing on these pages that must never be ambiguous.

    ⚠️ This is the whole of #524 that the evidence supported. The issue's headline ask was to
    extract a shared `createInspectorHost` from the two docks; measured against the current files
    they are **9.8% similar, with exactly one non-trivial identical line** — this chip. A shared
    host would be a config object papering over the other 90%, written without a browser to
    smoke-load it. Recorded rather than built.
    """
    offenders = [
        f"{path}:{i}"
        for path, src in _js_sources().items()
        if path != FMT
        for i, ln in enumerate(src.splitlines(), 1)
        if 'class="pf-src"' in ln and not ln.lstrip().startswith("//")
    ]
    assert not offenders, (
        "the provenance chip must come from `reconChip` in js/fmt.js — a second copy is how the "
        "Date tile lost its tooltip:\n  " + "\n  ".join(offenders)
    )
    assert "reconChip" in (_exports(_js_sources()[FMT]) or set())


def test_chrome_accent_is_constant() -> None:
    """The chrome accent (`--acc`) used to flip gold/cyan at the 09:30 ET open, driven by
    `data-session` stamped on `<html>` by `js/session.js` on a 10-second timer. It changed with
    nothing on the page explaining it, so it read as a glitch rather than a signal (#683) — the
    trader asked for a constant accent instead. Removed on request; not a decision that needs a
    `D-nn` entry."""
    css = (DOCS / "cockpit.css").read_text()
    assert re.search(r"--acc:\s*var\(--gold\);", css), (
        "the chrome accent must be a constant gold — `--acc: var(--gold);` in the base :root block"
    )
    assert "[data-session" not in css, (
        "the session-driven accent swap was removed on request (#683) — no `[data-session…]` "
        "selector should style the chrome again"
    )
