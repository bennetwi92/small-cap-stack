"""Shared test helpers (#523). Import as ``from tests.support import settings``.

A normal module rather than ``conftest.py`` — see the note at the top of ``tests/conftest.py``.
Fixtures go there; plain callables go here.
"""

from __future__ import annotations

from typing import Any

from small_cap_stack.config import Settings


def settings(**overrides: Any) -> Settings:
    """A `Settings` that cannot see the developer's `.env` (#507).

    The one place the isolation argument is spelled. It was previously copy-pasted as
    `Settings(_env_file=None)  # type: ignore[call-arg]` at 33 sites and simply forgotten at six
    more — including `test_review_fixtures.py`, which holds the 25 signed-off golden engine cases.

    Pairs with `conftest.pytest_configure`, which strips the matching environment variables before
    collection: this blocks the file, that blocks `os.environ`, and only both together make a
    `Settings()` in a test mean the same thing on a laptop and in CI.
    """
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]
