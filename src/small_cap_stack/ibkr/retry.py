"""Exponential backoff policy for reconnection attempts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with a cap. ``attempt`` is 1-based."""

    base: float = 1.0
    factor: float = 2.0
    max_delay: float = 60.0

    def delay(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        return min(self.base * self.factor ** (attempt - 1), self.max_delay)
