from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from universal_agent.core import AgentState, EventId, RuntimeEvent, SessionId
from universal_agent.persistence.codec import (
    decode_runtime_event,
    decode_session_snapshot,
    encode_runtime_event,
    encode_session_snapshot,
    json_mapping,
)
from universal_agent.runtime.events import filter_events
from universal_agent.state import SessionSnapshot, StateNotFoundError, session_from_state
from universal_agent.state.session import with_state


class FileSessionStore:
    """File-backed SessionStore adapter for local durable runtime tests.

    Each SessionSnapshot is stored as one JSON document. This is intentionally a
    local adapter, not a database abstraction or event-sourcing implementation.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._sessions = self._root / "sessions"

    async def create_session(self, snapshot: SessionSnapshot) -> None:
        path = self._session_path(snapshot.state.session_id)
        if path.exists():
            raise ValueError(f"session already exists: {snapshot.state.session_id}")
        self._write_snapshot(path, snapshot)

    async def list_sessions(self) -> tuple[SessionSnapshot, ...]:
        if not self._sessions.exists():
            return ()
        snapshots: list[SessionSnapshot] = []
        for path in sorted(self._sessions.glob("*.json")):
            with path.open("r", encoding="utf-8") as handle:
                snapshots.append(decode_session_snapshot(json_mapping(json.load(handle))))
        return tuple(
            sorted(
                snapshots,
                key=lambda snapshot: (
                    snapshot.state.goal.created_at,
                    str(snapshot.state.session_id),
                ),
                reverse=True,
            )
        )

    async def load_session(self, session_id: SessionId) -> SessionSnapshot:
        path = self._session_path(session_id)
        if not path.exists():
            raise StateNotFoundError(f"session not found: {session_id}")
        with path.open("r", encoding="utf-8") as handle:
            return decode_session_snapshot(json_mapping(json.load(handle)))

    async def save_session(self, snapshot: SessionSnapshot) -> None:
        path = self._session_path(snapshot.state.session_id)
        if not path.exists():
            raise StateNotFoundError(f"session not found: {snapshot.state.session_id}")
        self._write_snapshot(path, snapshot)

    async def create(self, state: AgentState) -> None:
        await self.create_session(session_from_state(state))

    async def load(self, session_id: SessionId) -> AgentState:
        return (await self.load_session(session_id)).state

    async def save(self, state: AgentState) -> None:
        snapshot = await self.load_session(state.session_id)
        await self.save_session(with_state(snapshot, state))

    def _session_path(self, session_id: SessionId) -> Path:
        return self._sessions / f"{quote(str(session_id), safe='')}.json"

    def _write_snapshot(self, path: Path, snapshot: SessionSnapshot) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(encode_session_snapshot(snapshot), handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(path)


class FileEventStore:
    """File-backed EventSink/EventReader adapter using JSON lines."""

    def __init__(self, root: str | Path) -> None:
        self._path = Path(root) / "events.jsonl"

    async def emit(self, event: RuntimeEvent) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(encode_runtime_event(event), sort_keys=True))
            handle.write("\n")

    async def list_events(
        self,
        session_id: SessionId | None = None,
        *,
        after_event_id: EventId | None = None,
        limit: int | None = None,
    ) -> tuple[RuntimeEvent, ...]:
        if not self._path.exists():
            return ()
        events: list[RuntimeEvent] = []
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = decode_runtime_event(json_mapping(json.loads(line)))
                events.append(event)
        return filter_events(
            events,
            session_id=session_id,
            after_event_id=after_event_id,
            limit=limit,
        )
