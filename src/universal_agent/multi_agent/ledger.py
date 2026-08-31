from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NewType, Protocol
from uuid import uuid4

import jsonlines

from universal_agent.core import (
    JsonMapping,
    JsonValue,
    dumps_json,
    immutable_json,
    loads_json,
    utc_now,
)
from universal_agent.core.config_validation import parse_json_object, parse_non_empty_string
from universal_agent.multi_agent.contracts import AgentTaskId
from universal_agent.multi_agent.registry import AgentId

DelegationEventId = NewType("DelegationEventId", str)


def new_delegation_event_id() -> DelegationEventId:
    return DelegationEventId(f"delegation-event-{uuid4()}")


@dataclass(frozen=True, slots=True)
class DelegationEvent:
    event_type: str
    delegation_id: str
    task_id: AgentTaskId
    from_agent: AgentId
    to_agent: AgentId
    status: str
    data: JsonMapping = field(default_factory=immutable_json)
    event_id: DelegationEventId = field(default_factory=new_delegation_event_id)
    occurred_at: datetime | str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        parse_non_empty_string(self.event_type, "delegation event type")
        parse_non_empty_string(self.delegation_id, "delegation event delegation_id")
        parse_non_empty_string(str(self.task_id), "delegation event task_id")
        parse_non_empty_string(str(self.from_agent), "delegation event from_agent")
        parse_non_empty_string(str(self.to_agent), "delegation event to_agent")
        parse_non_empty_string(self.status, "delegation event status")
        object.__setattr__(self, "data", immutable_json(self.data))


class DelegationLedger(Protocol):
    def append(self, event: DelegationEvent) -> None: ...

    def list_events(
        self,
        *,
        delegation_id: str | None = None,
        task_id: AgentTaskId | None = None,
    ) -> tuple[DelegationEvent, ...]: ...


class InMemoryDelegationLedger:
    def __init__(self) -> None:
        self._events: list[DelegationEvent] = []
        self._ids: set[DelegationEventId] = set()

    def append(self, event: DelegationEvent) -> None:
        if event.event_id in self._ids:
            return
        self._ids.add(event.event_id)
        self._events.append(event)

    def list_events(
        self,
        *,
        delegation_id: str | None = None,
        task_id: AgentTaskId | None = None,
    ) -> tuple[DelegationEvent, ...]:
        return tuple(
            _filter_events(
                tuple(self._events),
                delegation_id=delegation_id,
                task_id=task_id,
            )
        )


class FileDelegationLedger:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def append(self, event: DelegationEvent) -> None:
        if any(existing.event_id == event.event_id for existing in self.list_events()):
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with jsonlines.open(self._path, mode="a", dumps=dumps_json) as writer:
            writer.write(delegation_event_payload(event))

    def list_events(
        self,
        *,
        delegation_id: str | None = None,
        task_id: AgentTaskId | None = None,
    ) -> tuple[DelegationEvent, ...]:
        if not self._path.exists():
            return ()
        return tuple(_filter_events(tuple(_iter_events(self._path)), delegation_id, task_id))


def delegation_event_payload(event: DelegationEvent) -> dict[str, JsonValue]:
    occurred_at = event.occurred_at
    occurred_at_text = occurred_at.isoformat() if isinstance(occurred_at, datetime) else occurred_at
    return {
        "event_id": str(event.event_id),
        "event_type": event.event_type,
        "delegation_id": event.delegation_id,
        "task_id": str(event.task_id),
        "from_agent": str(event.from_agent),
        "to_agent": str(event.to_agent),
        "status": event.status,
        "data": dict(event.data),
        "occurred_at": occurred_at_text,
    }


def decode_delegation_event(payload: JsonMapping) -> DelegationEvent:
    body = parse_json_object(payload, "delegation event")
    return DelegationEvent(
        event_type=_string(body, "event_type"),
        delegation_id=_string(body, "delegation_id"),
        task_id=AgentTaskId(_string(body, "task_id")),
        from_agent=AgentId(_string(body, "from_agent")),
        to_agent=AgentId(_string(body, "to_agent")),
        status=_string(body, "status"),
        data=immutable_json(parse_json_object(body.get("data"), "delegation event data")),
        event_id=DelegationEventId(_string(body, "event_id")),
        occurred_at=_string(body, "occurred_at"),
    )


def _iter_events(path: Path) -> Iterator[DelegationEvent]:
    with jsonlines.open(path, loads=loads_json) as reader:
        for payload in reader.iter(allow_none=True, skip_empty=True):
            yield decode_delegation_event(parse_json_object(payload, "delegation event"))


def _filter_events(
    events: tuple[DelegationEvent, ...],
    delegation_id: str | None = None,
    task_id: AgentTaskId | None = None,
) -> tuple[DelegationEvent, ...]:
    return tuple(
        event
        for event in events
        if (delegation_id is None or event.delegation_id == delegation_id)
        and (task_id is None or event.task_id == task_id)
    )


def _string(payload: JsonMapping, key: str) -> str:
    value = payload.get(key)
    return parse_non_empty_string(value, f"delegation event {key}")
