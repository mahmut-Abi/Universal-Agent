from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from universal_agent.core import (
    AgentState,
    EventId,
    JsonMapping,
    RuntimeEvent,
    SessionId,
    dumps_json,
    loads_json,
    read_json_file,
    write_json_file,
)
from universal_agent.core.config_validation import parse_json_object
from universal_agent.persistence.codec import (
    decode_runtime_event,
    decode_session_snapshot,
    encode_runtime_event,
    encode_session_snapshot,
)
from universal_agent.runtime.events import filter_events
from universal_agent.state import (
    SessionSnapshot,
    SessionVersionConflictError,
    StateNotFoundError,
    session_from_state,
)
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
        snapshot.version = 0
        self._write_snapshot(path, snapshot)

    async def list_sessions(self) -> tuple[SessionSnapshot, ...]:
        if not self._sessions.exists():
            return ()
        snapshots: list[SessionSnapshot] = []
        for path in sorted(self._sessions.glob("*.json")):
            snapshots.append(decode_session_snapshot(_load_json_object(path, "session snapshot")))
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
        return decode_session_snapshot(_load_json_object(path, "session snapshot"))

    async def save_session(self, snapshot: SessionSnapshot) -> None:
        path = self._session_path(snapshot.state.session_id)
        if not path.exists():
            raise StateNotFoundError(f"session not found: {snapshot.state.session_id}")
        stored = decode_session_snapshot(_load_json_object(path, "session snapshot"))
        if snapshot.version != stored.version:
            raise SessionVersionConflictError(
                f"session version conflict: {snapshot.state.session_id} expected "
                f"{stored.version}, got {snapshot.version}"
            )
        snapshot.version = stored.version + 1
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
        write_json_file(path, encode_session_snapshot(snapshot), indent=True)


class FileEventStore:
    """File-backed EventSink/EventReader adapter using JSON lines."""

    def __init__(self, root: str | Path) -> None:
        self._path = Path(root) / "events.jsonl"

    async def emit(self, event: RuntimeEvent) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(dumps_json(encode_runtime_event(event)))
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
                event = decode_runtime_event(_loads_json_object(line, "runtime event"))
                events.append(event)
        return filter_events(
            events,
            session_id=session_id,
            after_event_id=after_event_id,
            limit=limit,
        )


class FileRuntimeStore(FileSessionStore, FileEventStore):
    """File-backed Session/Event adapter with a local commit journal.

    The file adapter cannot provide a database transaction across independent
    JSON and JSONL files. It still exposes the same RuntimeStore seam as SQLite:
    a write-ahead commit record is persisted before the snapshot and event are
    applied, then replayed on later reads if the process stopped mid-commit.
    """

    state_event_commit_strategy = "file_journal"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._sessions = self._root / "sessions"
        self._path = self._root / "events.jsonl"
        self._commits = self._root / "commits"

    async def list_sessions(self) -> tuple[SessionSnapshot, ...]:
        self._recover_commits()
        return await super().list_sessions()

    async def load_session(self, session_id: SessionId) -> SessionSnapshot:
        self._recover_commits()
        return await super().load_session(session_id)

    async def save_session(self, snapshot: SessionSnapshot) -> None:
        self._recover_commits()
        await super().save_session(snapshot)

    async def list_events(
        self,
        session_id: SessionId | None = None,
        *,
        after_event_id: EventId | None = None,
        limit: int | None = None,
    ) -> tuple[RuntimeEvent, ...]:
        self._recover_commits()
        return await super().list_events(
            session_id=session_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    async def commit_session_event(
        self,
        snapshot: SessionSnapshot,
        event: RuntimeEvent,
    ) -> None:
        self._recover_commits()
        path = self._session_path(snapshot.state.session_id)
        if not path.exists():
            raise StateNotFoundError(f"session not found: {snapshot.state.session_id}")
        if self._event_exists(event.id):
            raise ValueError(f"runtime event already exists: {event.id}")
        stored = decode_session_snapshot(_load_json_object(path, "session snapshot"))
        if snapshot.version != stored.version:
            raise SessionVersionConflictError(
                f"session version conflict: {snapshot.state.session_id} expected "
                f"{stored.version}, got {snapshot.version}"
            )
        snapshot.version = stored.version + 1
        commit_path = self._commit_path(event.id)
        if commit_path.exists():
            raise ValueError(f"runtime commit already exists: {event.id}")
        self._write_commit(commit_path, snapshot, event)
        self._write_snapshot(path, snapshot)
        self._append_event_if_missing(event)
        commit_path.unlink(missing_ok=True)

    def _recover_commits(self) -> None:
        if not self._commits.exists():
            return
        for path in sorted(self._commits.glob("*.json")):
            payload = _load_json_object(path, "file runtime commit record")
            snapshot_payload = payload.get("session")
            event_payload = payload.get("event")
            if not isinstance(snapshot_payload, dict) or not isinstance(event_payload, dict):
                raise ValueError(f"invalid file runtime commit record: {path}")
            snapshot = decode_session_snapshot(snapshot_payload)
            event = decode_runtime_event(event_payload)
            session_path = self._session_path(snapshot.state.session_id)
            if session_path.exists():
                stored = decode_session_snapshot(
                    _load_json_object(session_path, "session snapshot")
                )
                if stored.version < snapshot.version:
                    self._write_snapshot(session_path, snapshot)
            else:
                self._write_snapshot(session_path, snapshot)
            self._append_event_if_missing(event)
            path.unlink(missing_ok=True)

    def _commit_path(self, event_id: EventId) -> Path:
        return self._commits / f"{quote(str(event_id), safe='')}.json"

    def _write_commit(
        self,
        path: Path,
        snapshot: SessionSnapshot,
        event: RuntimeEvent,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_file(
            path,
            {
                "session": encode_session_snapshot(snapshot),
                "event": encode_runtime_event(event),
            },
            indent=True,
        )

    def _append_event_if_missing(self, event: RuntimeEvent) -> None:
        if self._event_exists(event.id):
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(dumps_json(encode_runtime_event(event)))
            handle.write("\n")

    def _event_exists(self, event_id: EventId) -> bool:
        if not self._path.exists():
            return False
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = decode_runtime_event(_loads_json_object(line, "runtime event"))
                if event.id == event_id:
                    return True
        return False


def _load_json_object(path: Path, field: str) -> JsonMapping:
    return _loads_json_object(read_json_file(path), field)


def _loads_json_object(value: object, field: str) -> JsonMapping:
    decoded = loads_json(value) if isinstance(value, str | bytes | bytearray) else value
    return parse_json_object(decoded, field)
