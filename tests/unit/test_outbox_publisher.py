from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from universal_agent.core import EventId, GoalId, RuntimeEvent, SessionId, TaskId
from universal_agent.persistence.outbox import publish_outbox_batch


@dataclass(frozen=True, slots=True)
class _OutboxRecord:
    event_id: EventId
    event: RuntimeEvent


class _OutboxStore:
    def __init__(self, records: tuple[_OutboxRecord, ...]) -> None:
        self.records = records
        self.lease_publisher_id: str | None = None
        self.lease_limit: int | None = None
        self.lease_ttl_seconds: float | None = None
        self.lease_now: datetime | None = None
        self.marked: tuple[EventId, ...] = ()
        self.mark_publisher_id: str | None = None
        self.released: tuple[EventId, ...] = ()
        self.release_publisher_id: str | None = None
        self.release_reason: str | None = None
        self.retry_at: datetime | None = None

    def lease_outbox_events(
        self,
        *,
        publisher_id: str,
        limit: int,
        ttl_seconds: float,
        now: datetime | None = None,
    ) -> tuple[_OutboxRecord, ...]:
        self.lease_publisher_id = publisher_id
        self.lease_limit = limit
        self.lease_ttl_seconds = ttl_seconds
        self.lease_now = now
        return self.records[:limit]

    def mark_outbox_published(
        self,
        event_ids: tuple[EventId, ...],
        *,
        publisher_id: str | None = None,
    ) -> int:
        self.marked = event_ids
        self.mark_publisher_id = publisher_id
        return len(event_ids)

    def release_outbox_events(
        self,
        event_ids: tuple[EventId, ...],
        *,
        publisher_id: str,
        reason: str,
        retry_at: datetime | None = None,
    ) -> int:
        self.released = event_ids
        self.release_publisher_id = publisher_id
        self.release_reason = reason
        self.retry_at = retry_at
        return len(event_ids)


class _BrokerPublisher:
    def __init__(self, *, fail_event_ids: tuple[EventId, ...] = ()) -> None:
        self.fail_event_ids = set(fail_event_ids)
        self.published: list[RuntimeEvent] = []

    async def publish(self, event: RuntimeEvent) -> None:
        if event.id in self.fail_event_ids:
            raise RuntimeError(f"publish failed for {event.id}")
        self.published.append(event)


@pytest.mark.unit
async def test_publish_outbox_batch_marks_successful_events_published() -> None:
    now = datetime(2026, 8, 31, tzinfo=UTC)
    event_1 = _event("event-1")
    event_2 = _event("event-2")
    store = _OutboxStore(
        (
            _OutboxRecord(EventId("event-1"), event_1),
            _OutboxRecord(EventId("event-2"), event_2),
        )
    )
    publisher = _BrokerPublisher()

    result = await publish_outbox_batch(
        store,
        publisher,
        publisher_id="publisher-a",
        limit=10,
        lease_ttl_seconds=45.0,
        now=now,
    )

    assert [event.id for event in publisher.published] == [EventId("event-1"), EventId("event-2")]
    assert store.lease_publisher_id == "publisher-a"
    assert store.lease_limit == 10
    assert store.lease_ttl_seconds == 45.0
    assert store.lease_now == now
    assert store.marked == (EventId("event-1"), EventId("event-2"))
    assert store.mark_publisher_id == "publisher-a"
    assert store.released == ()
    assert result.leased_count == 2
    assert result.published_count == 2
    assert result.released_count == 0
    assert result.failures == ()


@pytest.mark.unit
async def test_publish_outbox_batch_releases_failed_events_for_retry() -> None:
    now = datetime(2026, 8, 31, tzinfo=UTC)
    event_1 = _event("event-1")
    event_2 = _event("event-2")
    store = _OutboxStore(
        (
            _OutboxRecord(EventId("event-1"), event_1),
            _OutboxRecord(EventId("event-2"), event_2),
        )
    )
    publisher = _BrokerPublisher(fail_event_ids=(EventId("event-2"),))

    result = await publish_outbox_batch(
        store,
        publisher,
        publisher_id="publisher-a",
        limit=10,
        lease_ttl_seconds=45.0,
        retry_delay_seconds=30.0,
        now=now,
    )

    assert [event.id for event in publisher.published] == [EventId("event-1")]
    assert store.marked == (EventId("event-1"),)
    assert store.released == (EventId("event-2"),)
    assert store.release_publisher_id == "publisher-a"
    assert store.release_reason == "publish failed for event-2"
    assert store.retry_at == now + timedelta(seconds=30)
    assert result.leased_count == 2
    assert result.published_count == 1
    assert result.released_count == 1
    assert len(result.failures) == 1
    assert result.failures[0].event_id == EventId("event-2")
    assert result.failures[0].reason == "publish failed for event-2"


def _event(event_id: str) -> RuntimeEvent:
    return RuntimeEvent(
        id=EventId(event_id),
        type="StateUpdated",
        session_id=SessionId("session-1"),
        goal_id=GoalId("goal-1"),
        task_id=TaskId("task-1"),
    )
