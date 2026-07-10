"""Tests for stage 1 tokenisation (#177): highs -> H/L/E within a flatness tolerance eps."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from small_cap_stack.bullflag import tokenize
from small_cap_stack.capture import Bar

_T0 = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)


def _bars(highs: list[float]) -> list[Bar]:
    """Bars carrying only meaningful highs (open/low/close/vol are irrelevant to tokenisation)."""
    return [
        Bar(start=_T0 + timedelta(minutes=5 * i), open=h, high=h, low=h - 1, close=h, volume=1000.0)
        for i, h in enumerate(highs)
    ]


def test_length_invariant() -> None:
    assert tokenize([], eps=0.01) == []
    assert tokenize(_bars([5.0]), eps=0.01) == []  # single bar -> no token
    assert len(tokenize(_bars([1.0, 2.0, 3.0, 4.0]), eps=0.01)) == 3


def test_basic_h_l_e() -> None:
    assert tokenize(_bars([1.0, 2.0]), eps=0.01) == ["H"]
    assert tokenize(_bars([2.0, 1.0]), eps=0.01) == ["L"]
    assert tokenize(_bars([2.0, 2.0]), eps=0.01) == ["E"]


def test_mixed_string() -> None:
    # 4 -> 5 (H) -> 6 (H) -> 5.5 (L) -> 5.5 (E) -> 5.0 (L)
    assert tokenize(_bars([4.0, 5.0, 6.0, 5.5, 5.5, 5.0]), eps=0.01) == ["H", "H", "L", "E", "L"]


def test_eps_boundary() -> None:
    # A move of exactly eps is still "equal" (not strictly greater/less); just past eps flips it.
    assert tokenize(_bars([5.00, 5.01]), eps=0.01) == ["E"]  # delta == eps -> E
    assert tokenize(_bars([5.00, 5.02]), eps=0.01) == ["H"]  # delta > eps -> H
    assert tokenize(_bars([5.00, 4.99]), eps=0.01) == ["E"]  # -delta == eps -> E
    assert tokenize(_bars([5.00, 4.98]), eps=0.01) == ["L"]  # -delta > eps -> L


def test_zero_eps_is_strict() -> None:
    # eps=0 -> any non-zero move is directional; only an exact tie is E.
    assert tokenize(_bars([5.00, 5.001]), eps=0.0) == ["H"]
    assert tokenize(_bars([5.00, 5.00]), eps=0.0) == ["E"]
