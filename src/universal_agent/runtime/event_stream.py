from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Protocol

from universal_agent.core import EventId, RuntimeEvent, SessionId
from universal_agent.core.config_validation import parse_positive_float, parse_positive_int
from universal_agent.runtime.events import EventReader, heartbeat_event


@dataclass(frozen=True, slots=True)
class EventStreamSignal:
    """Broker-delivered wakeup signal.

    The signal is intentionally not authoritative event data. It only tells the
    stream to re-read the durable EventReader from the current cursor.
    """

    session_id: SessionId
    event_id: EventId


class EventSignalSubscription(Protocol):
    @property
    def dropped_notification_count(self) -> int: ...

    async def next(self, *, timeout_seconds: float) -> EventStreamSignal | None: ...

    def close(self) -> None: ...


class EventSignalSource(Protocol):
    """Provider-neutral broker signal seam for Runtime event streaming."""

    def subscribe(
        self,
        session_id: SessionId | None = None,
        *,
        max_buffer_size: int = 1,
    ) -> EventSignalSubscription: ...


class InMemoryEventSignalBroker:
    """Local signal broker with explicit coalescing backpressure semantics."""

    def __init__(self) -> None:
        self._subscriptions: list[_InMemoryEventSignalSubscription] = []
        self._subscriber_changed = asyncio.Event()
        self._dropped_notification_count = 0

    @property
    def subscriber_count(self) -> int:
        return len(self._subscriptions)

    @property
    def dropped_notification_count(self) -> int:
        return self._dropped_notification_count

    def subscribe(
        self,
        session_id: SessionId | None = None,
        *,
        max_buffer_size: int = 1,
    ) -> EventSignalSubscription:
        subscription = _InMemoryEventSignalSubscription(
            self,
            session_id,
            max_buffer_size=parse_positive_int(
                max_buffer_size,
                "event signal max_buffer_size",
            ),
        )
        self._subscriptions.append(subscription)
        self._subscriber_changed.set()
        return subscription

    def notify(self, event: RuntimeEvent) -> None:
        signal = EventStreamSignal(event.session_id, event.id)
        for subscription in tuple(self._subscriptions):
            if not subscription.accepts(event.session_id):
                continue
            dropped = subscription.notify(signal)
            self._dropped_notification_count += dropped

    async def wait_for_subscriber_count(
        self,
        count: int,
        *,
        timeout_seconds: float = 1.0,
    ) -> None:
        expected = parse_positive_int(count, "event signal subscriber count")
        timeout = parse_positive_float(timeout_seconds, "event signal subscriber timeout_seconds")
        while self.subscriber_count < expected:
            self._subscriber_changed.clear()
            await asyncio.wait_for(self._subscriber_changed.wait(), timeout=timeout)

    def _remove(self, subscription: _InMemoryEventSignalSubscription) -> None:
        try:
            self._subscriptions.remove(subscription)
        except ValueError:  # pragma: no cover - defensive double-close guard
            return
        self._subscriber_changed.set()


class BrokerBackedEventStream:
    """EventReader/EventWatcher adapter backed by broker wakeup signals.

    Reconnect and cursor semantics are owned by the durable EventReader. Broker
    messages are deliberately treated as lossy wakeups so provider-specific
    adapters can apply coalescing backpressure without losing committed events.
    """

    def __init__(
        self,
        reader: EventReader,
        signals: EventSignalSource,
        *,
        max_catchup_events: int = 100,
        notification_buffer_size: int = 1,
    ) -> None:
        self._reader = reader
        self._signals = signals
        self._max_catchup_events = parse_positive_int(
            max_catchup_events,
            "event stream max_catchup_events",
        )
        self._notification_buffer_size = parse_positive_int(
            notification_buffer_size,
            "event stream notification_buffer_size",
        )

    async def list_events(
        self,
        session_id: SessionId | None = None,
        *,
        after_event_id: EventId | None = None,
        limit: int | None = None,
    ) -> tuple[RuntimeEvent, ...]:
        return await self._reader.list_events(
            session_id,
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
        heartbeat_seconds = parse_positive_float(
            heartbeat_interval,
            "event stream heartbeat interval",
        )
        subscription = self._signals.subscribe(
            session_id,
            max_buffer_size=self._notification_buffer_size,
        )
        cursor = after_event_id
        try:
            while True:
                events = await self._reader.list_events(
                    session_id,
                    after_event_id=cursor,
                    limit=self._max_catchup_events,
                )
                if events:
                    for event in events:
                        yield event
                        cursor = event.id
                    continue

                signal = await subscription.next(timeout_seconds=heartbeat_seconds)
                if signal is None:
                    yield heartbeat_event(session_id)
        finally:
            subscription.close()


class _InMemoryEventSignalSubscription:
    def __init__(
        self,
        broker: InMemoryEventSignalBroker,
        session_id: SessionId | None,
        *,
        max_buffer_size: int,
    ) -> None:
        self._broker = broker
        self._session_id = session_id
        self._queue: asyncio.Queue[EventStreamSignal] = asyncio.Queue(maxsize=max_buffer_size)
        self._dropped_notification_count = 0
        self._closed = False

    @property
    def dropped_notification_count(self) -> int:
        return self._dropped_notification_count

    async def next(self, *, timeout_seconds: float) -> EventStreamSignal | None:
        try:
            return await asyncio.wait_for(
                self._queue.get(),
                timeout=parse_positive_float(
                    timeout_seconds,
                    "event signal subscription timeout_seconds",
                ),
            )
        except TimeoutError:
            return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._broker._remove(self)

    def accepts(self, session_id: SessionId) -> bool:
        return self._session_id is None or self._session_id == session_id

    def notify(self, signal: EventStreamSignal) -> int:
        if self._closed:
            return 0
        if self._queue.full():
            self._dropped_notification_count += 1
            return 1
        self._queue.put_nowait(signal)
        return 0
