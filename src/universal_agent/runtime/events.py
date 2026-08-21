from __future__ import annotations

from typing import Protocol

from universal_agent.core import RuntimeEvent, SessionId


class EventSink(Protocol):
    async def emit(self, event: RuntimeEvent) -> None: ...


class EventReader(Protocol):
    async def list_events(
        self, session_id: SessionId | None = None
    ) -> tuple[RuntimeEvent, ...]: ...


class InMemoryEventSink:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    async def emit(self, event: RuntimeEvent) -> None:
        self.events.append(event)

    async def list_events(self, session_id: SessionId | None = None) -> tuple[RuntimeEvent, ...]:
        if session_id is None:
            return tuple(self.events)
        return tuple(event for event in self.events if event.session_id == session_id)
