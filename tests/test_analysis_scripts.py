"""The operator scripts must at least import (#506).

`scripts/analysis/` is the box-side toolkit the `review-analysis` and `box-data` skills drive —
the documented way to answer "why does the review page say Max R = X". Nothing else checks it:
mypy is `packages = ["small_cap_stack"]`, ruff can't resolve cross-module imports, and no test
imported these files.

So `probe_run.py` sat broken. It imported `_first_trigger` and `_iter_setups` from `rmetrics` —
names deleted with the anchored detector in #296/#302 — and `bar_interval` from `rmetrics`, which
had moved to `capture`. Three dead names in one import block, undetected for weeks, in the tool
you reach for when a number on the dashboard looks wrong.

Importing is a low bar, but it is exactly the bar that was failing. Each script guards its work
behind `if __name__ == "__main__"`, so import touches no store and no network.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ANALYSIS = Path(__file__).resolve().parents[1] / "scripts" / "analysis"


def _scripts() -> list[Path]:
    return sorted(p for p in ANALYSIS.glob("*.py") if not p.name.startswith("_"))


def test_there_are_scripts_to_check() -> None:
    """Guard against the parametrized test passing because the glob found nothing."""
    assert len(_scripts()) >= 3, f"expected the analysis toolkit, found {_scripts()}"


@pytest.mark.parametrize("path", _scripts(), ids=lambda p: p.name)
def test_analysis_script_imports(path: Path) -> None:
    """Executes the module body — which is what surfaces a name that no longer exists."""
    spec = importlib.util.spec_from_file_location(f"_analysis_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)


@pytest.mark.parametrize("path", _scripts(), ids=lambda p: p.name)
def test_analysis_script_does_no_work_at_import(path: Path) -> None:
    """Every script's work sits behind `main()`, so importing it above is free of side effects.

    Checked structurally rather than by searching for `def main(` — a substring scan is satisfied
    by the phrase appearing in a docstring, and what actually matters is that nothing at module
    level *does* anything.
    """
    tree = ast.parse(path.read_text())
    allowed = (
        ast.Import,
        ast.ImportFrom,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Assign,
        ast.AnnAssign,
        ast.Expr,  # the module docstring
        ast.If,  # the `__main__` guard
    )
    for node in tree.body:
        assert isinstance(node, allowed), (
            f"{path.name}:{node.lineno}: {type(node).__name__} runs at import time"
        )
        if isinstance(node, ast.If):
            src = ast.unparse(node.test)
            assert "__name__" in src, f"{path.name}:{node.lineno}: unguarded top-level if ({src})"

    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "main" in names, f"{path.name} has no main()"


def test_the_probe_measures_over_the_full_day_not_the_run_window() -> None:
    """`symbol_runs` windows bars per run; the engine must still see the whole day (#506).

    Every production caller feeds `day_chart_bars` to `detect_day` / `compute_r_metrics`. The run
    window ends when the scanner stops hitting, so it truncates a live trade at a boundary the
    trade never saw and hides the exhaustion cycles counted across the day (#180). Probing on
    `run.bars` reports a different cycle standing on 6 of the 25 golden fixtures and, on a symbol
    that pops twice, a Max R up to 3.5x too low — the very number the probe exists to explain.
    """
    probe = ANALYSIS / "probe_run.py"
    tree = ast.parse(probe.read_text())
    # Both the engine calls AND the helper that forwards to them. Bar *selection* lives in `main()`
    # now, so watching only the engine call sites leaves the real mistake — `_probe_run(run.bars,
    # ...)` — invisible. Mutation-tested in both positions.
    watched = {"detect_day_with_settings", "compute_r_metrics", "detect_day", "_probe_run"}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in watched
        ):
            first = ast.unparse(node.args[0]) if node.args else ""
            assert "run.bars" not in first, (
                f"probe_run.py:{node.lineno}: {node.func.id}({first}) uses the run window; "
                "pass the full day from day_chart_bars"
            )
    assert "day_chart_bars" in probe.read_text(), "the probe must source its bars from the full day"


def test_no_unscoped_store_read() -> None:
    """`store.read("bars")` with no `dt=` pulls the whole archive into the tracker's 2 GB cgroup.

    These scripts run via `docker exec` *inside the app container*, so an unscoped read competes
    with the live tracker for memory — the shape that OOM-killed the box for 5h37m (#264).
    `probe_run.py` did exactly this before #506.
    """
    # AST, not a text scan: the docstring above discusses `store.read("bars")` in prose, and a
    # guard that fails on its own explanation is worse than none.
    offenders: list[str] = []
    for path in _scripts():
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "read":
                continue
            # Two signals, unioned. Receiver alone is not enough: the bug this test exists to catch
            # was literally `s.read("bars")` — `s`, not `store` — so a receiver-name check would
            # have waved through the exact line in #506's title. A string first argument is the
            # other tell: a dataset name is a str, a file object's `.read(1024)` takes an int and
            # a bare `.read()` takes nothing.
            recv = ast.unparse(node.func.value)
            first = node.args[0] if node.args else None
            if first is None:
                first = next((kw.value for kw in node.keywords if kw.arg == "dataset"), None)
            names_a_dataset = isinstance(first, ast.Constant) and isinstance(first.value, str)
            if not ("store" in recv.lower() or names_a_dataset):
                continue
            if not any(kw.arg == "dt" for kw in node.keywords):
                offenders.append(f"{path.name}:{node.lineno}: {recv}.read(...) has no dt=")
    assert not offenders, "dt-scope these store reads:\n" + "\n".join(offenders)


def test_store_query_callers_bound_their_range() -> None:
    """`Store.query` builds a DuckDB view over `**/*.parquet` for the whole dataset — unbounded.

    `export_query.py` runs in the app container from `data-export.yml`, so an unbounded query
    competes with the live tracker for the same 2 GB. It is not `dt=`-scopable like `read`, so the
    requirement is weaker but real: the script must expose a way to bound the range, and say so.
    """
    src = (ANALYSIS / "export_query.py").read_text()
    tree = ast.parse(src)
    queries = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "query"
        and "store" in ast.unparse(n.func.value).lower()
    ]
    if not queries:
        pytest.skip("no Store.query caller left to guard")
    # The bound must be READ, not merely mentioned. A substring check for "SCS_START" stays green
    # when the code honouring it is deleted, because the name survives in the module docstring and
    # in an error message — the same self-satisfying-guard trap as the reads above.
    read_env = {
        ast.unparse(n.args[0]).strip("'\"")
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "get"
        and "environ" in ast.unparse(n.func.value)
        and n.args
    }
    missing = {"SCS_START", "SCS_END"} - read_env
    assert not missing, (
        f"export_query.py calls Store.query (a full-dataset view) but never reads {missing}; "
        "an unbounded export shares the live tracker's 2 GB cgroup"
    )
