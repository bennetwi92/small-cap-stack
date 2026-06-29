"""Smoke test so CI has something to run on the scaffolding PR."""

from small_cap_stack import __version__


def test_version_is_set() -> None:
    assert __version__ == "0.0.1"
