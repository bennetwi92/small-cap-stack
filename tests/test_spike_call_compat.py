"""Spikes call into the package, and nothing checks that the calls still fit (#514).

`spikes/` is deliberately outside mypy (CLAUDE.md: strict, package-only) and has no tests, so a
signature change in `src/` leaves every spike calling the old shape. Ruff won't see it — arity is
not a lint — and the failure surfaces at runtime, months later, on the box.

That is exactly how #514 nearly shipped: `report._news_recent` gained a `Settings` parameter and
`spikes/review_metaanalysis.py` kept calling it with two. Worse, that call sits inside a
`try/except Exception` that collects errors per-run, so the harness would have emitted **zero rows
and N errors** and read like a data problem rather than a broken call.

This is not type-checking by the back door. It answers one question — *can this call bind to that
signature at all?* — which is the question `except Exception` hides.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SPIKES = REPO_ROOT / "spikes"
PACKAGE = "small_cap_stack"


def _spike_files() -> list[Path]:
    return sorted(p for p in SPIKES.glob("*.py") if p.name != "__init__.py")


def _imported_callables(tree: ast.AST) -> dict[str, str]:
    """`{local name: dotted path}` for every name a spike imports from the package."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(PACKAGE):
            for alias in node.names:
                found[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return found


def _rebound_names(tree: ast.AST) -> set[str]:
    """Names the spike assigns to or defines itself — a local shadow means the imported signature
    is no longer the one being called, so those are skipped rather than guessed at."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _binds(func: object, call: ast.Call) -> str | None:
    """None if the call could bind to `func`'s signature; else why not.

    `*args` / `**kwargs` at the call site make the real arity unknowable statically, so those
    calls are skipped — a guard that guesses produces noise, and noise gets guards deleted.
    """
    if any(isinstance(a, ast.Starred) for a in call.args):
        return None
    if any(kw.arg is None for kw in call.keywords):
        return None
    try:
        signature = inspect.signature(func)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    try:
        kwargs = {kw.arg: object() for kw in call.keywords if kw.arg}
        signature.bind(*[object()] * len(call.args), **kwargs)
    except TypeError as exc:
        return str(exc)
    return None


def _resolve(dotted: str) -> object:
    module_name, _, attr = dotted.rpartition(".")
    return getattr(importlib.import_module(module_name), attr)


@pytest.mark.parametrize("path", _spike_files(), ids=lambda p: p.name)
def test_spike_imports_from_the_package_still_exist(path: Path) -> None:
    """A name that has been renamed or deleted kills the spike at *import*, before its own
    error handling gets a chance — and an unused stale import is invisible to the call check
    below, because there is no call to inspect."""
    missing: list[str] = []
    for local, dotted in _imported_callables(ast.parse(path.read_text(encoding="utf-8"))).items():
        try:
            _resolve(dotted)
        except (ImportError, AttributeError) as exc:
            missing.append(f"{local} <- {dotted} ({type(exc).__name__}: {exc})")
    assert not missing, f"{path.name} imports names the package no longer has:\n  " + "\n  ".join(
        missing
    )


@pytest.mark.parametrize("path", _spike_files(), ids=lambda p: p.name)
def test_spike_calls_into_the_package_still_bind(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = _imported_callables(tree)
    shadowed = _rebound_names(tree)
    problems: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        dotted = imported.get(node.func.id)
        if dotted is None or node.func.id in shadowed:
            continue
        try:
            target = _resolve(dotted)
        except (ImportError, AttributeError):
            continue  # reported by the import test above; don't say it twice
        if not callable(target) or isinstance(target, type):
            continue
        why = _binds(target, node)
        if why is not None:
            problems.append(f"line {node.lineno}: {node.func.id}(...) — {why}")
    assert not problems, (
        f"{path.name} calls package functions with the wrong shape; the package moved and the "
        "spike didn't:\n  " + "\n  ".join(problems)
    )


def test_the_binder_actually_rejects_a_wrong_call() -> None:
    """A binder that returns None for everything makes every test above vacuous — and this one
    has three ways to do that (a signature it can't read, a `*args` skip, a swallowed TypeError).
    """

    def two_args(a: int, b: int) -> int:
        return a + b

    assert _binds(two_args, ast.parse("f(1, 2)").body[0].value) is None  # type: ignore[attr-defined]
    assert _binds(two_args, ast.parse("f(1)").body[0].value) is not None  # type: ignore[attr-defined]
    assert _binds(two_args, ast.parse("f(1, 2, 3)").body[0].value) is not None  # type: ignore[attr-defined]
    assert _binds(two_args, ast.parse("f(a=1, b=2)").body[0].value) is None  # type: ignore[attr-defined]
    assert _binds(two_args, ast.parse("f(1, c=2)").body[0].value) is not None  # type: ignore[attr-defined]
    # Unknowable at parse time — must be skipped, not guessed.
    assert _binds(two_args, ast.parse("f(*xs)").body[0].value) is None  # type: ignore[attr-defined]
    assert _binds(two_args, ast.parse("f(**kw)").body[0].value) is None  # type: ignore[attr-defined]


def test_the_corpus_is_not_empty() -> None:
    """Parametrising over a glob that matches nothing is a green suite that checks nothing."""
    files = _spike_files()
    assert len(files) > 5
    assert any(_imported_callables(ast.parse(p.read_text(encoding="utf-8"))) for p in files)
