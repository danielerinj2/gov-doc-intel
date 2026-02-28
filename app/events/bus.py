from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable


EventHandler = Callable[[dict[str, Any]], None]


class InMemoryEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._subscribers[event_type].append(handler)

    def publish(self, event_type: str, envelope: dict[str, Any]) -> None:
        # Exact subscribers
        for handler in self._subscribers.get(event_type, []):
            handler(envelope)

        # Wildcard subscribers
        for handler in self._subscribers.get("*", []):
            handler(envelope)
