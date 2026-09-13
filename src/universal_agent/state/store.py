from __future__ import annotations

from typing import Protocol

from universal_agent.core import AgentState, RuntimeEvent, SessionId
from universal_agent.state.session import (
    SessionSnapshot,
    copy_session,
    session_from_state,
    with_state,
)


class StateNotFoundError(LookupError):
    pass


class SessionVersionConflictError(RuntimeError):
    pass


class StateStore(Protocol):
    async def create(self, state: AgentState) -> None: ...

    async def load(self, session_id: SessionId) -> AgentState: ...

    async def save(self, state: AgentState) -> None: ...


class SessionStore(StateStore, Protocol):
    async def create_session(self, snapshot: SessionSnapshot) -> None: ...

    async def list_sessions(
        self,
        *,
        after_session_id: SessionId | None = None,
        limit: int | None = None,
    ) -> tuple[SessionSnapshot, ...]: ...

    async def load_session(self, session_id: SessionId) -> SessionSnapshot: ...

    async def save_session(self, snapshot: SessionSnapshot) -> None: ...


def paginate_session_snapshots(
    snapshots: tuple[SessionSnapshot, ...],
    *,
    after_session_id: SessionId | None,
    limit: int | None,
) -> tuple[SessionSnapshot, ...]:
    """Shared newest-first cursor/limit pagination over ordered snapshots.

    Stores that already paginate in SQL bypass this helper; in-memory stores
    (and file stores, which must read every document anyway) share it so the
    cursor semantics stay identical to the SQL pushdown: sessions strictly
    after the cursor follow it in newest-first order, and an unknown cursor is
    a ``ValueError``.
    """
    if limit is not None and limit < 1:
        raise ValueError("session list limit must be positive")
    selected: list[SessionSnapshot] = []
    cursor_seen = after_session_id is None
    cursor_in_scope = False
    for snapshot in snapshots:
        session_id = snapshot.state.session_id
        if after_session_id is not None and session_id == after_session_id:
            cursor_in_scope = True
        if not cursor_seen:
            if session_id == after_session_id:
                cursor_seen = True
            continue
        selected.append(snapshot)
        if limit is not None and len(selected) >= limit:
            break
    if after_session_id is not None and not cursor_in_scope:
        raise ValueError(f"session cursor not found: {after_session_id}")
    return tuple(selected)


class StateEventCommitter(Protocol):
    async def commit_session_event(
        self,
        snapshot: SessionSnapshot,
        event: RuntimeEvent,
    ) -> None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[SessionId, SessionSnapshot] = {}

    async def create_session(self, snapshot: SessionSnapshot) -> None:
        session_id = snapshot.state.session_id
        if session_id in self._sessions:
            raise ValueError(f"session already exists: {session_id}")
        created = copy_session(snapshot)
        created.version = 0
        snapshot.version = created.version
        self._sessions[session_id] = created

    async def list_sessions(
        self,
        *,
        after_session_id: SessionId | None = None,
        limit: int | None = None,
    ) -> tuple[SessionSnapshot, ...]:
        snapshots = tuple(copy_session(snapshot) for snapshot in self._sessions.values())
        ordered = tuple(
            sorted(
                snapshots,
                key=lambda snapshot: (
                    snapshot.state.goal.created_at,
                    str(snapshot.state.session_id),
                ),
                reverse=True,
            )
        )
        return paginate_session_snapshots(
            ordered,
            after_session_id=after_session_id,
            limit=limit,
        )

    async def load_session(self, session_id: SessionId) -> SessionSnapshot:
        try:
            stored = self._sessions[session_id]
        except KeyError as exc:
            raise StateNotFoundError(f"session not found: {session_id}") from exc
        return copy_session(stored)

    async def save_session(self, snapshot: SessionSnapshot) -> None:
        session_id = snapshot.state.session_id
        if session_id not in self._sessions:
            raise StateNotFoundError(f"session not found: {session_id}")
        stored = self._sessions[session_id]
        if snapshot.version != stored.version:
            raise SessionVersionConflictError(
                f"session version conflict: {session_id} expected {stored.version}, "
                f"got {snapshot.version}"
            )
        saved = copy_session(snapshot)
        saved.version = stored.version + 1
        snapshot.version = saved.version
        self._sessions[session_id] = saved

    async def create(self, state: AgentState) -> None:
        await self.create_session(session_from_state(state))

    async def load(self, session_id: SessionId) -> AgentState:
        return (await self.load_session(session_id)).state

    async def save(self, state: AgentState) -> None:
        session_id = state.session_id
        if session_id not in self._sessions:
            raise StateNotFoundError(f"session not found: {session_id}")
        await self.save_session(with_state(self._sessions[session_id], state))


InMemoryStateStore = InMemorySessionStore
