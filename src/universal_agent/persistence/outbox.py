from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from universal_agent.core import EventId, RuntimeEvent, utc_now
from universal_agent.core.config_validation import (
    parse_non_empty_string,
    parse_non_negative_float,
    parse_positive_float,
    parse_positive_int,
)


class OutboxEvent(Protocol):
    @property
    def event_id(self) -> EventId: ...

    @property
    def event(self) -> RuntimeEvent: ...


class OutboxStore[TOutboxEvent: OutboxEvent](Protocol):
    """Leased transactional outbox seam used by production event publishers."""

    def lease_outbox_events(
        self,
        *,
        publisher_id: str,
        limit: int,
        ttl_seconds: float,
        now: datetime | None = None,
    ) -> tuple[TOutboxEvent, ...]: ...

    def mark_outbox_published(
        self,
        event_ids: tuple[EventId, ...],
        *,
        publisher_id: str | None = None,
    ) -> int: ...

    def release_outbox_events(
        self,
        event_ids: tuple[EventId, ...],
        *,
        publisher_id: str,
        reason: str,
        retry_at: datetime | None = None,
    ) -> int: ...


class EventPublisher(Protocol):
    """Concrete broker adapter seam for publishing runtime events."""

    async def publish(self, event: RuntimeEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class OutboxPublishFailure:
    event_id: EventId
    reason: str


@dataclass(frozen=True, slots=True)
class OutboxPublishResult:
    publisher_id: str
    leased_count: int
    published_count: int
    released_count: int
    published_event_ids: tuple[EventId, ...] = ()
    released_event_ids: tuple[EventId, ...] = ()
    failures: tuple[OutboxPublishFailure, ...] = ()


async def publish_outbox_batch[TOutboxEvent: OutboxEvent](
    store: OutboxStore[TOutboxEvent],
    publisher: EventPublisher,
    *,
    publisher_id: str,
    limit: int,
    lease_ttl_seconds: float = 30.0,
    retry_delay_seconds: float = 5.0,
    now: datetime | None = None,
) -> OutboxPublishResult:
    """Lease, publish and acknowledge one transactional outbox batch.

    The store remains authoritative for leasing and idempotent publish state.
    The broker adapter only receives already-committed RuntimeEvents. Failed
    broker publishes are released back to the outbox with a retry timestamp,
    rather than being treated as task/runtime failures.
    """

    parsed_publisher_id = parse_non_empty_string(publisher_id, "outbox publisher_id")
    parsed_limit = parse_positive_int(limit, "outbox publish limit")
    parsed_lease_ttl = parse_positive_float(
        lease_ttl_seconds,
        "outbox publish lease_ttl_seconds",
    )
    parsed_retry_delay = parse_non_negative_float(
        retry_delay_seconds,
        "outbox publish retry_delay_seconds",
    )
    timestamp = now or utc_now()
    leased = store.lease_outbox_events(
        publisher_id=parsed_publisher_id,
        limit=parsed_limit,
        ttl_seconds=parsed_lease_ttl,
        now=timestamp,
    )
    if not leased:
        return OutboxPublishResult(
            publisher_id=parsed_publisher_id,
            leased_count=0,
            published_count=0,
            released_count=0,
        )

    published_event_ids: list[EventId] = []
    failures: list[OutboxPublishFailure] = []
    for item in leased:
        try:
            await publisher.publish(item.event)
        except Exception as exc:
            failures.append(
                OutboxPublishFailure(
                    event_id=item.event_id,
                    reason=_exception_reason(exc),
                )
            )
            continue
        published_event_ids.append(item.event_id)

    published = 0
    if published_event_ids:
        published = store.mark_outbox_published(
            tuple(published_event_ids),
            publisher_id=parsed_publisher_id,
        )

    released = 0
    released_event_ids = tuple(failure.event_id for failure in failures)
    if released_event_ids:
        released = store.release_outbox_events(
            released_event_ids,
            publisher_id=parsed_publisher_id,
            reason=_release_reason(tuple(failures)),
            retry_at=timestamp + timedelta(seconds=parsed_retry_delay),
        )

    return OutboxPublishResult(
        publisher_id=parsed_publisher_id,
        leased_count=len(leased),
        published_count=published,
        released_count=released,
        published_event_ids=tuple(published_event_ids),
        released_event_ids=released_event_ids,
        failures=tuple(failures),
    )


def _exception_reason(error: Exception) -> str:
    reason = str(error).strip()
    if reason:
        return reason
    return type(error).__name__


def _release_reason(failures: tuple[OutboxPublishFailure, ...]) -> str:
    if len(failures) == 1:
        return failures[0].reason
    return f"{len(failures)} outbox publish failures; first: {failures[0].reason}"
