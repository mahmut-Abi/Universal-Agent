from __future__ import annotations

import asyncio

import pytest

from universal_agent.core import EventId, GoalId, RuntimeEvent, SessionId, TaskId, utc_now
from universal_agent.runtime.event_stream import BrokerBackedEventStream, InMemoryEventSignalBroker
from universal_agent.runtime.events import InMemoryEventSink


@pytest.mark.unit
async def test_broker_event_stream_replays_from_cursor_before_waiting() -> None:
    session = SessionId("session-1")
    store = InMemoryEventSink()
    first = _event("event-1", session)
    second = _event("event-2", session)
    store.append(first)
    store.append(second)
    stream = BrokerBackedEventStream(store, InMemoryEventSignalBroker())

    received: list[RuntimeEvent] = []
    async for event in stream.watch_events(
        session,
        after_event_id=first.id,
        heartbeat_interval=0.05,
    ):
        received.append(event)
        break

    assert [event.id for event in received] == [EventId("event-2")]


@pytest.mark.unit
async def test_broker_event_stream_uses_signal_to_drain_authoritative_reader() -> None:
    session = SessionId("session-1")
    store = InMemoryEventSink()
    broker = InMemoryEventSignalBroker()
    stream = BrokerBackedEventStream(store, broker)
    received: list[RuntimeEvent] = []

    async def consume() -> None:
        async for event in stream.watch_events(session, heartbeat_interval=0.2):
            received.append(event)
            if event.id == EventId("event-1"):
                break

    async def produce() -> None:
        await broker.wait_for_subscriber_count(1)
        event = _event("event-1", session)
        store.append(event)
        broker.notify(event)

    await asyncio.gather(consume(), produce())

    assert [event.id for event in received] == [EventId("event-1")]


@pytest.mark.unit
async def test_broker_event_stream_coalesces_signals_without_losing_reader_events() -> None:
    session = SessionId("session-1")
    store = InMemoryEventSink()
    broker = InMemoryEventSignalBroker()
    stream = BrokerBackedEventStream(store, broker, notification_buffer_size=1)
    received: list[RuntimeEvent] = []

    async def consume() -> None:
        async for event in stream.watch_events(session, heartbeat_interval=0.2):
            received.append(event)
            if len(received) == 3:
                break

    async def produce() -> None:
        await broker.wait_for_subscriber_count(1)
        events = tuple(_event(f"event-{index}", session) for index in range(1, 4))
        for event in events:
            store.append(event)
            broker.notify(event)

    await asyncio.gather(consume(), produce())

    assert [event.id for event in received] == [
        EventId("event-1"),
        EventId("event-2"),
        EventId("event-3"),
    ]
    assert broker.dropped_notification_count >= 1
    assert broker.subscriber_count == 0


def _event(event_id: str, session_id: SessionId) -> RuntimeEvent:
    return RuntimeEvent(
        id=EventId(event_id),
        type="StateUpdated",
        session_id=session_id,
        goal_id=GoalId("goal-1"),
        task_id=TaskId("task-1"),
        occurred_at=utc_now(),
    )
