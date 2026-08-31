from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from universal_agent.core import EventId, RuntimeEvent, SessionId
from universal_agent.state import SessionSnapshot

SessionReducer = Callable[[RuntimeEvent, SessionSnapshot], SessionSnapshot]


class EventStore(Protocol):
    def append(self, event: RuntimeEvent) -> None: ...

    def events_for(self, session_id: SessionId) -> tuple[RuntimeEvent, ...]: ...

    def all(self) -> tuple[RuntimeEvent, ...]: ...


class EventReplayError(ValueError):
    pass


SESSION_STATE_EVENT = "SessionStateCommitted"


def rebuild_session_snapshot(store: EventStore, session_id: SessionId) -> SessionSnapshot:
    """Reconstruct the latest session snapshot from the event journal.

    The runtime appends a ``SessionStateCommitted`` event (carrying the full
    serialized ``SessionSnapshot``) on every committed state change, so replaying
    those events and taking the most recent one reproduces the session state
    without the snapshot store. This is the event-sourced rebuild path used by
    resume/pause/cancel when the snapshot store has no record for the session.
    """
    from universal_agent.persistence.codec import decode_session_snapshot

    candidate: SessionSnapshot | None = None
    for event in store.events_for(session_id):
        if event.type != SESSION_STATE_EVENT:
            continue
        payload = event.data.get("snapshot")
        if payload is None:
            continue
        try:
            candidate = decode_session_snapshot(payload)
        except Exception as exc:
            raise EventReplayError(
                f"failed to decode session state event {event.id}: {exc}"
            ) from exc
    if candidate is None:
        raise EventReplayError(f"no session state events for {session_id}")
    return candidate


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events: list[RuntimeEvent] = []
        self._event_ids: set[EventId] = set()
        self._by_session: dict[SessionId, list[RuntimeEvent]] = {}

    async def emit(self, event: RuntimeEvent) -> None:
        self.append(event)

    def append(self, event: RuntimeEvent) -> None:
        if event.id in self._event_ids:
            return
        self._event_ids.add(event.id)
        self._events.append(event)
        self._by_session.setdefault(event.session_id, []).append(event)

    def clear(self) -> None:
        self._events.clear()
        self._event_ids.clear()
        self._by_session.clear()

    def events_for(self, session_id: SessionId) -> tuple[RuntimeEvent, ...]:
        return tuple(self._by_session.get(session_id, []))

    def all(self) -> tuple[RuntimeEvent, ...]:
        return tuple(self._events)


class FileEventStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(exist_ok=True)
        self._event_ids: set[EventId] = set()
        self._by_session: dict[SessionId, list[RuntimeEvent]] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    event_id = EventId(data.get("id", ""))
                    self._event_ids.add(event_id)
                    session_id = SessionId(data.get("session_id", ""))
                    event = _event_from_dict(data)
                    self._by_session.setdefault(session_id, []).append(event)
                except (json.JSONDecodeError, AttributeError):
                    pass

    def append(self, event: RuntimeEvent) -> None:
        if event.id in self._event_ids:
            return
        self._event_ids.add(event.id)
        self._by_session.setdefault(event.session_id, []).append(event)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_event_to_dict(event), default=_json_default) + "\n")

    def events_for(self, session_id: SessionId) -> tuple[RuntimeEvent, ...]:
        return tuple(self._by_session.get(session_id, []))

    def all(self) -> tuple[RuntimeEvent, ...]:
        if not self._path.exists():
            return ()
        events: list[RuntimeEvent] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                events.append(_event_from_dict(json.loads(line)))
        return tuple(events)


def rebuild_session(
    store: EventStore,
    session_id: SessionId,
    *,
    initial: SessionSnapshot,
    reducer: SessionReducer,
) -> SessionSnapshot:
    snapshot = initial
    for event in store.events_for(session_id):
        try:
            snapshot = reducer(event, snapshot)
        except EventReplayError:
            raise
        except Exception as exc:
            raise EventReplayError(
                f"reducer failed on event {event.id} of type {event.type!r}: {exc}"
            ) from exc
    return snapshot


def _event_to_dict(event: RuntimeEvent) -> dict[str, Any]:
    return {
        "type": event.type,
        "session_id": event.session_id,
        "goal_id": event.goal_id,
        "task_id": event.task_id,
        "id": event.id,
        "action_id": event.action_id,
        "data": dict(event.data),
        "occurred_at": event.occurred_at.isoformat(),
    }


def _event_from_dict(raw: dict[str, Any]) -> RuntimeEvent:
    return RuntimeEvent(
        type=raw["type"],
        session_id=SessionId(raw["session_id"]),
        goal_id=raw["goal_id"],
        task_id=raw["task_id"],
        id=EventId(raw["id"]),
        action_id=raw["action_id"],
        data=raw.get("data", {}),
        occurred_at=datetime.fromisoformat(raw["occurred_at"]),
    )


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"cannot serialize object of type {type(obj).__name__}")
