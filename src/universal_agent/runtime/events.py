from __future__ import annotations

from typing import Protocol

from universal_agent.core import RuntimeEvent


class EventSink(Protocol):
    async def emit(self, event: RuntimeEvent) -> None: ...


class InMemoryEventSink:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    async def emit(self, event: RuntimeEvent) -> None:
        self.events.append(event)
