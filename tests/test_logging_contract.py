"""The swallow-and-continue logging contract (#511).

The package is full of ``except Exception`` handlers that deliberately keep going — a tick must
not die over a dashboard write, a night's harvest must not die over one symbol. Every one of them
is a place where the only evidence a human will ever see is the log line.

``logging.py`` installs ``structlog.processors.format_exc_info``, which renders **nothing** unless
``exc_info`` is in the event dict. So ``log.warning("dashboard.charts_write_failed")`` inside an
``except`` block produces the event name and no type, message or traceback — on a box reached only
by SSH. These tests pin the rule that the exception has to come along.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "small_cap_stack"

# ``log.exception`` is excluded deliberately: structlog's ``FilteringBoundLogger.exception`` does
# ``kw.setdefault("exc_info", True)`` itself, so requiring it there would force a redundant kwarg.
LOG_LEVELS = {"debug", "info", "warning", "error", "critical"}
BROAD = {"Exception", "BaseException"}


def _modules() -> list[pathlib.Path]:
    return sorted(SRC.rglob("*.py"))


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """``except Exception`` / ``except BaseException`` / a tuple containing either."""
    caught = handler.type
    if isinstance(caught, ast.Name):
        return caught.id in BROAD
    if isinstance(caught, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in BROAD for e in caught.elts)
    return False


def _names_a_logger(expr: ast.expr) -> bool:
    """True if any identifier in the receiver expression is a logger name.

    Walking the whole chain rather than testing a bare ``Name`` covers ``self.log.warning`` and
    ``log.bind(run=1).warning`` as well as plain ``log.warning`` — otherwise a module that binds
    or renames its logger opts out of this contract for free. Leading underscores are stripped so
    ``_log`` counts; ``catalog`` does not, and ``parser.error(...)`` (argparse) does not match.
    """
    for node in ast.walk(expr):
        if isinstance(node, ast.Name | ast.Attribute):
            ident = node.id if isinstance(node, ast.Name) else node.attr
            if ident.lstrip("_") in {"log", "logger"}:
                return True
    return False


def _is_logger_call(node: ast.AST) -> bool:
    """A ``<logger>.<level>(...)`` call."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in LOG_LEVELS
        and _names_a_logger(node.func.value)
    )


def _carries_exc_info(call: ast.Call) -> bool:
    """``exc_info`` present and not an explicit falsy constant.

    The ``is False`` check matters: ``exc_info=False`` is exactly what a "quieten this noisy
    handler" edit produces, and a presence-only check would let it through with a green test.
    """
    for kw in call.keywords:
        if kw.arg != "exc_info":
            continue
        return not (isinstance(kw.value, ast.Constant) and kw.value.value is False)
    return False


def _body_calls(body: list[ast.stmt]) -> list[ast.Call]:
    """Logger calls lexically in ``body``, not descending into nested scopes or narrow handlers.

    A nested ``def``/``lambda`` runs later, when ``sys.exc_info()`` is empty — ``exc_info=True``
    there would be wrong, not required. A nested narrow ``except`` has already named its type.
    """
    found: list[ast.Call] = []
    opaque = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)

    def walk(node: ast.AST) -> None:
        if isinstance(node, opaque):
            return
        if isinstance(node, ast.ExceptHandler) and not _is_broad(node):
            return
        if _is_logger_call(node):
            found.append(node)  # type: ignore[arg-type]
        for child in ast.iter_child_nodes(node):
            walk(child)

    for stmt in body:
        walk(stmt)
    return found


def _bare_log_calls(tree: ast.AST) -> list[tuple[str, int]]:
    """Logger calls inside a broad ``except`` handler that drop the exception.

    Only broad handlers are checked. A handler that names a specific type has already said what
    went wrong, and a handler that logs nothing at all is making a different choice (see
    ``portfolio/projection.py``, which uses one for control flow) — neither is this contract.
    """
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ExceptHandler) and _is_broad(node)):
            continue
        for call in _body_calls(node.body):
            if _carries_exc_info(call):
                continue
            event = "?"
            if call.args and isinstance(call.args[0], ast.Constant):
                event = str(call.args[0].value)
            out.append((event, call.lineno))
    return sorted(set(out), key=lambda p: p[1])


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_except_handlers_log_the_exception(path: pathlib.Path) -> None:
    """A log call inside ``except Exception`` must pass ``exc_info=True``."""
    offenders = _bare_log_calls(ast.parse(path.read_text()))
    assert not offenders, (
        f"{path.relative_to(SRC.parent.parent)}: log call(s) inside `except Exception` with no "
        f"exc_info=True — the traceback is dropped: "
        + ", ".join(f"{event!r} at line {line}" for event, line in offenders)
    )


def test_the_contract_finds_a_real_offender() -> None:
    """The detector isn't vacuously passing — it flags the shape it exists to catch."""
    bad = ast.parse(
        "try:\n    f()\nexcept Exception:\n    log.warning('thing_failed', id=1)\n",
    )
    assert _bare_log_calls(bad) == [("thing_failed", 4)]

    good = ast.parse(
        "try:\n    f()\nexcept Exception:\n    log.warning('thing_failed', exc_info=True)\n",
    )
    assert _bare_log_calls(good) == []


def test_a_narrow_except_is_not_covered() -> None:
    """Naming the exception type is its own explanation; the rule is only for broad handlers."""
    narrow = ast.parse(
        "try:\n    f()\nexcept ValueError:\n    log.warning('thing_failed')\n",
    )
    assert _bare_log_calls(narrow) == []


def test_exc_info_false_does_not_satisfy_the_contract() -> None:
    """`exc_info=False` is what a "quieten this one" edit produces — it must not pass."""
    src = "try:\n    f()\nexcept Exception:\n    log.warning('thing_failed', exc_info=False)\n"
    assert _bare_log_calls(ast.parse(src)) == [("thing_failed", 4)]


@pytest.mark.parametrize(
    "receiver",
    ["log", "logger", "self.log", "self._log", "_log", "log.bind(a=1)"],
    ids=["log", "logger", "self.log", "self._log", "_log", "bound"],
)
def test_the_rule_is_not_escapable_by_renaming_the_logger(receiver: str) -> None:
    """A module that calls its logger something else must not opt out of the contract."""
    src = f"try:\n    f()\nexcept Exception:\n    {receiver}.warning('thing_failed')\n"
    assert _bare_log_calls(ast.parse(src)) == [("thing_failed", 4)]


@pytest.mark.parametrize(
    "clause", ["Exception", "BaseException", "(Exception, OSError)", "(OSError, Exception)"]
)
def test_broad_handlers_of_every_shape_are_covered(clause: str) -> None:
    src = f"try:\n    f()\nexcept {clause}:\n    log.warning('thing_failed')\n"
    assert _bare_log_calls(ast.parse(src)) == [("thing_failed", 4)]


def test_log_exception_is_exempt() -> None:
    """structlog's `.exception()` sets exc_info itself; demanding it again would be noise."""
    src = "try:\n    f()\nexcept Exception:\n    log.exception('thing_failed')\n"
    assert _bare_log_calls(ast.parse(src)) == []


def test_a_nested_function_is_not_covered() -> None:
    """A closure defined in a handler runs later, when sys.exc_info() is empty."""
    src = (
        "try:\n"
        "    f()\n"
        "except Exception:\n"
        "    def later():\n"
        "        log.warning('thing_failed')\n"
        "    schedule(later)\n"
    )
    assert _bare_log_calls(ast.parse(src)) == []


def test_a_narrow_handler_nested_in_a_broad_one_is_not_covered() -> None:
    src = (
        "try:\n"
        "    f()\n"
        "except Exception:\n"
        "    try:\n"
        "        g()\n"
        "    except ValueError:\n"
        "        log.warning('inner_failed')\n"
    )
    assert _bare_log_calls(ast.parse(src)) == []


def test_the_package_actually_has_handlers_to_check() -> None:
    """Guards against the parametrized test passing because the walk found nothing."""
    total = 0
    for path in _modules():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ExceptHandler):
                caught = node.type
                if isinstance(caught, ast.Name) and caught.id == "Exception":
                    total += 1
    assert total >= 20, f"expected the package's ~26 bare-Exception handlers, found {total}"
