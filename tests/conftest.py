"""Suite-wide test isolation (#507).

Only pytest hooks and fixtures live here. Shared *helpers* are in ``tests/support.py``:
``tests/`` has no ``__init__.py``, so pytest imports this file as the top-level module
``conftest``, and a test doing ``from tests.conftest import ...`` would build a **second** module
object with its own copy of everything in it. Keeping helpers out of conftest makes that mistake
impossible to make.
"""

from __future__ import annotations

import os

import pytest

from small_cap_stack.config import Settings

#: Env vars that map onto a `Settings` field. Field names map case-insensitively to env vars and
#: there is no `env_prefix`, so `BULL_FLAG_MAX_CONS` in the ambient environment is read by every
#: `Settings()` in the suite. Derived from the model, so a new knob is covered the day it is added.
_SETTINGS_ENV = tuple(name.upper() for name in Settings.model_fields)

#: Names this must never unset, however `Settings` evolves. The list above is derived, so a future
#: field called `path` or `tz` would silently clear that variable process-wide for the whole run —
#: breaking `shutil.which`, subprocesses, and the developer's own shell semantics. Every field today
#: is snake_case with an underscore, so a collision cannot happen by accident from the existing
#: naming convention; this fails loudly if that ever stops being true.
_NEVER_STRIP = frozenset(
    {"PATH", "HOME", "USER", "SHELL", "LANG", "TZ", "TMPDIR", "PWD", "TERM", "PYTHONPATH", "CI"}
)
assert not (set(_SETTINGS_ENV) & _NEVER_STRIP), (
    f"a Settings field shadows a shell variable: {set(_SETTINGS_ENV) & _NEVER_STRIP}"
)


def pytest_configure(config: pytest.Config) -> None:
    """Strip Settings-shaped env vars before **collection** (#507).

    This has to happen here, not in a fixture. `tests/test_review_fixtures.py` — the 25 signed-off
    golden engine cases — builds its `Settings` at *module* level, which executes while pytest is
    importing test modules, long before any fixture runs. An autouse fixture looks like it closes
    the hole and doesn't: with `BULL_FLAG_MIN_POLE_PCT=0.10` exported, 15 of the golden cases still
    failed. `pytest_configure` runs before collection, so it catches module-level construction too.

    Why this matters at all: `Settings(_env_file=None)` blocks the *file* and nothing else —
    pydantic-settings still reads `os.environ`. So an engine knob left over from a live experiment
    silently rewrites the rules the suite asserts, while CI — which has neither a `.env` nor those
    vars — stays green. The test that exists to stop engine regressions is the one most easily
    disarmed by the machine it runs on.
    """
    for name in _SETTINGS_ENV:
        os.environ.pop(name, None)


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-test belt-and-braces for anything that sets one mid-session and doesn't clean up.

    Function-scoped, so a test that deliberately sets an env var (`test_config.py` asserts the
    mapping works) still does: this clears first, the test body sets after.
    """
    for name in _SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)
