"""Registry of desired market-data subscriptions, so they can be replayed after a reconnect.

IBKR drops market-data subscriptions on a connection loss (and on error 1101). We record what
we *want* subscribed here; on (re)connect the transport replays the registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SubscriptionRegistry:
    """Desired subscriptions keyed by a caller-chosen token (e.g. symbol or conId)."""

    _specs: dict[str, object] = field(default_factory=dict)

    def register(self, token: str, spec: object) -> None:
        self._specs[token] = spec

    def unregister(self, token: str) -> None:
        self._specs.pop(token, None)

    def all(self) -> list[tuple[str, object]]:
        return list(self._specs.items())

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, token: object) -> bool:
        return token in self._specs
