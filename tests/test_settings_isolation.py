"""The suite must not be able to read the developer's configuration (#507).

`Settings` maps field names case-insensitively to env vars with no prefix and reads a cwd-relative
`.env`. So a bare `Settings()` in a test is not a statement about the code — it is a statement
about the machine the test happens to run on.

That was live, not theoretical. `tests/test_review_fixtures.py` holds the 25 signed-off golden
engine cases and built its settings with a bare `Settings()`. With `BULL_FLAG_MIN_POLE_PCT=0.10`
reachable, **17 of those 25 flip** — the test that exists to stop engine regressions, disarmed by
the environment, while CI (which has neither a `.env` nor the var) stayed green.

Two mechanisms close it, and both are pinned here: `tests.support.settings` blocks the file,
and `conftest.pytest_configure` strips the matching env vars before collection. The timing is
the whole trick — the golden fixtures build their `Settings` at module level, so a fixture is
already too late.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from small_cap_stack.config import Settings
from tests.support import settings

ROOT = Path(__file__).resolve().parents[1]

#: Knobs whose value changes what the engine detects. Leaking any of these silently rewrites the
#: rules the golden fixtures assert.
ENGINE_KNOBS = (
    "BULL_FLAG_MAX_POLE",
    "BULL_FLAG_MAX_CONS",
    "BULL_FLAG_MIN_POLE_PCT",
    "BULL_FLAG_MAX_RETRACEMENT",
    "BULL_FLAG_MAX_PEAK_WICK",
    "TICK_SIZE",
)


def test_the_factory_ignores_a_dot_env_in_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.env` is cwd-relative, so running pytest from a directory with one used to poison it."""
    (tmp_path / ".env").write_text("BULL_FLAG_MIN_POLE_PCT=0.10\nIBKR_PORT=9999\n")
    monkeypatch.chdir(tmp_path)

    s = settings()

    assert s.bull_flag_min_pole_pct == 0.02, "the .env reached Settings"
    assert s.ibkr_port == 4002


def test_a_dot_env_would_otherwise_be_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The counter-case: without the factory's isolation the file IS read.

    Without this, the test above could pass because pydantic-settings had stopped reading `.env`
    at all, and the guard would be measuring nothing.
    """
    (tmp_path / ".env").write_text("IBKR_PORT=9999\n")
    monkeypatch.chdir(tmp_path)

    assert Settings().ibkr_port == 9999


def test_no_settings_env_var_survives_into_a_test() -> None:
    """`pytest_configure` cleared them; `_env_file=None` alone would not have."""
    leaked = [name for name in ENGINE_KNOBS if name in os.environ]
    assert not leaked, f"engine knobs reached the suite from the environment: {leaked}"


def test_the_isolation_list_covers_every_settings_field() -> None:
    """Derived from `Settings.model_fields`, so a new knob is covered the day it is added rather
    than the day someone remembers to extend a hand-written list."""
    # `conftest`, not `tests.conftest`: importing the latter builds a SECOND module
    # object, and this test would then assert against a copy rather than the plugin
    # that actually ran. The hazard this module's own header warns about.
    from conftest import _SETTINGS_ENV

    assert set(_SETTINGS_ENV) == {name.upper() for name in Settings.model_fields}
    assert set(ENGINE_KNOBS) <= set(_SETTINGS_ENV)


@pytest.mark.parametrize("knob", ["BULL_FLAG_MIN_POLE_PCT=0.10", "BULL_FLAG_MAX_CONS=1"])
def test_the_golden_cases_survive_a_poisoned_environment(knob: str, tmp_path: Path) -> None:
    """End-to-end: the failure #507 describes, reproduced and shown fixed.

    Runs the golden-fixture module in a subprocess whose environment carries an engine knob. Before
    #507 this reproduced 17 failures out of 25; it must now be green. A subprocess is the only
    honest way to test this — the leak happens at interpreter start, before any fixture can run.
    """
    name, _, value = knob.partition("=")
    env = {**os.environ, name: value}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_review_fixtures.py",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"{knob} reached the golden fixtures:\n{proc.stdout[-3000:]}"
