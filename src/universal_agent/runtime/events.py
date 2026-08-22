from __future__ import annotations

from typing import Protocol

from universal_agent.core import EventId, RuntimeEvent, SessionId


class EventCursorError(ValueError):
    pass


class EventSink(Protocol):
    async def emit(self, event: RuntimeEvent) -> None: ...


class EventReader(Protocol):
    async def list_events(
        self,
        session_id: SessionId | None = None,
        *,
        after_event_id: EventId | None = None,
        limit: int | None = None,
    ) -> tuple[RuntimeEvent, ...]: ...


class InMemoryEventSink:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    async def emit(self, event: RuntimeEvent) -> None:
        self.events.append(event)

    async def list_events(
        self,
        session_id: SessionId | None = None,
        *,
        after_event_id: EventId | None = None,
        limit: int | None = None,
    ) -> tuple[RuntimeEvent, ...]:
        return filter_events(
            self.events,
            session_id=session_id,
            after_event_id=after_event_id,
            limit=limit,
        )


def filter_events(
    events: list[RuntimeEvent] | tuple[RuntimeEvent, ...],
    *,
    session_id: SessionId | None = None,
    after_event_id: EventId | None = None,
    limit: int | None = None,
) -> tuple[RuntimeEvent, ...]:
    """Return an ordered event batch after a cursor.

    This is the local Event Stream foundation. The same cursor semantics are
    shared by memory and file adapters so future SSE delivery can sit above one
    interface instead of reimplementing filtering in every application adapter.
    """
    if limit is not None and limit < 1:
        raise ValueError("event stream limit must be positive")

    selected: list[RuntimeEvent] = []
    cursor_seen = after_event_id is None
    cursor_in_scope = False
    for event in events:
        if session_id is not None and event.session_id != session_id:
            continue
        if after_event_id is not None and event.id == after_event_id:
            cursor_in_scope = True
        if not cursor_seen:
            if event.id == after_event_id:
                cursor_seen = True
            continue
        selected.append(event)
        if limit is not None and len(selected) >= limit:
            break

    if after_event_id is not None and not cursor_in_scope:
        raise EventCursorError(f"event cursor not found: {after_event_id}")
    return tuple(selected)
