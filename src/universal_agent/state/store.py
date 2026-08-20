from __future__ import annotations

from typing import Protocol

from universal_agent.core import AgentState, SessionId


class StateNotFoundError(LookupError):
    pass


class StateStore(Protocol):
    async def create(self, state: AgentState) -> None: ...

    async def load(self, session_id: SessionId) -> AgentState: ...

    async def save(self, state: AgentState) -> None: ...


class InMemoryStateStore:
    def __init__(self) -> None:
        self._states: dict[SessionId, AgentState] = {}

    async def create(self, state: AgentState) -> None:
        if state.session_id in self._states:
            raise ValueError(f"session already exists: {state.session_id}")
        self._states[state.session_id] = state

    async def load(self, session_id: SessionId) -> AgentState:
        try:
            return self._states[session_id]
        except KeyError as exc:
            raise StateNotFoundError(f"session not found: {session_id}") from exc

    async def save(self, state: AgentState) -> None:
        if state.session_id not in self._states:
            raise StateNotFoundError(f"session not found: {state.session_id}")
        self._states[state.session_id] = state
