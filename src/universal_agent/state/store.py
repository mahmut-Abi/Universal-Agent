from __future__ import annotations

from typing import Protocol

from universal_agent.core import AgentState, SessionId
from universal_agent.state.session import (
    SessionSnapshot,
    copy_session,
    session_from_state,
    with_state,
)


class StateNotFoundError(LookupError):
    pass


class StateStore(Protocol):
    async def create(self, state: AgentState) -> None: ...

    async def load(self, session_id: SessionId) -> AgentState: ...

    async def save(self, state: AgentState) -> None: ...


class SessionStore(StateStore, Protocol):
    async def create_session(self, snapshot: SessionSnapshot) -> None: ...

    async def load_session(self, session_id: SessionId) -> SessionSnapshot: ...

    async def save_session(self, snapshot: SessionSnapshot) -> None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[SessionId, SessionSnapshot] = {}

    async def create_session(self, snapshot: SessionSnapshot) -> None:
        session_id = snapshot.state.session_id
        if session_id in self._sessions:
            raise ValueError(f"session already exists: {session_id}")
        self._sessions[session_id] = copy_session(snapshot)

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
        self._sessions[session_id] = copy_session(snapshot)

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
