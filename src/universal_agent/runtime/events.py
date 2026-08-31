from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Protocol, runtime_checkable

from universal_agent.core import EventId, GoalId, RuntimeEvent, SessionId, TaskId
from universal_agent.core.config_validation import parse_positive_float, parse_positive_int


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


@runtime_checkable
class EventWatcher(Protocol):
    def watch_events(
        self,
        session_id: SessionId | None = None,
        *,
        after_event_id: EventId | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncGenerator[RuntimeEvent, None]: ...


class InMemoryEventSink:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []
        self._event_ids: set[EventId] = set()
        self._subscribers: list[asyncio.Event] = []

    async def emit(self, event: RuntimeEvent) -> None:
        self.append(event)
        for subscriber in self._subscribers:
            subscriber.set()

    def append(self, event: RuntimeEvent) -> None:
        if event.id in self._event_ids:
            return
        self._event_ids.add(event.id)
        self.events.append(event)

    def events_for(self, session_id: SessionId) -> tuple[RuntimeEvent, ...]:
        return tuple(event for event in self.events if event.session_id == session_id)

    def all(self) -> tuple[RuntimeEvent, ...]:
        return tuple(self.events)

    async def list_events(
        self,
        session_id: SessionId | None = None,
        *,
        after_event_id: EventId | None = None,
        limit: int | None = None,
    ) -> tuple[RuntimeEvent, ...]:
        return filter_events(
            tuple(event for event in self.events if event.type != "SessionStateCommitted"),
            session_id=session_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    async def watch_events(
        self,
        session_id: SessionId | None = None,
        *,
        after_event_id: EventId | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncGenerator[RuntimeEvent, None]:
        """Yield events as they arrive, yielding heartbeats on idle."""
        cursor = after_event_id
        subscriber = asyncio.Event()
        self._subscribers.append(subscriber)
        try:
            # Drain existing events first
            existing = self._filter_existing(session_id, cursor)
            for event in existing:
                yield event
                cursor = event.id

            # Then wait for new events
            while True:
                subscriber.clear()
                try:
                    await asyncio.wait_for(
                        subscriber.wait(),
                        timeout=heartbeat_interval,
                    )
                except TimeoutError:
                    yield heartbeat_event(session_id)
                    continue

                new_events = self._filter_existing(session_id, cursor)
                for event in new_events:
                    yield event
                    cursor = event.id
        finally:
            self._subscribers.remove(subscriber)

    def _filter_existing(
        self,
        session_id: SessionId | None,
        cursor: EventId | None,
    ) -> tuple[RuntimeEvent, ...]:
        """Return events matching session/cursor filters."""
        return filter_events(
            tuple(e for e in self.events if e.type != "SessionStateCommitted"),
            session_id=session_id,
            after_event_id=cursor,
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
    if limit is not None:
        parse_positive_int(limit, "event stream limit")

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


async def poll_event_reader(
    reader: EventReader,
    session_id: SessionId | None = None,
    *,
    after_event_id: EventId | None = None,
    heartbeat_interval: float = 15.0,
) -> AsyncGenerator[RuntimeEvent, None]:
    """Watch any EventReader by polling with the same cursor semantics.

    This is the durable-store event stream fallback. Stores that cannot notify
    subscribers directly can still provide long-lived SSE delivery without
    weakening the Runtime event contract.
    """
    parse_positive_float(heartbeat_interval, "event stream heartbeat interval")
    cursor = after_event_id
    while True:
        events = await reader.list_events(session_id, after_event_id=cursor)
        if events:
            for event in events:
                yield event
                cursor = event.id
            continue
        await asyncio.sleep(heartbeat_interval)
        events = await reader.list_events(session_id, after_event_id=cursor)
        if events:
            for event in events:
                yield event
                cursor = event.id
            continue
        yield heartbeat_event(session_id)


def heartbeat_event(session_id: SessionId | None = None) -> RuntimeEvent:
    return RuntimeEvent(
        type="Heartbeat",
        session_id=session_id or SessionId(""),
        goal_id=GoalId(""),
        task_id=TaskId(""),
        data={"kind": "heartbeat"},
    )
