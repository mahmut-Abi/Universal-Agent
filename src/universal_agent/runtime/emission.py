from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from universal_agent.core import (
    ActionId,
    AgentState,
    ErrorCode,
    ExecutionResult,
    ExecutionStatus,
    GoalStatus,
    RuntimeEvent,
    TaskStatus,
)
from universal_agent.persistence.codec import encode_session_snapshot
from universal_agent.runtime.events import EventSink
from universal_agent.runtime.session import SessionRuntimeState
from universal_agent.runtime.transitions import build_result
from universal_agent.security import redact_sensitive_mapping
from universal_agent.state import (
    SessionSnapshot,
    SessionStore,
    StateEventCommitter,
    session_from_state,
)
from universal_agent.state.event_store import SESSION_STATE_EVENT, EventStore


class EventEmitter:
    """Owns event publishing and session/event persistence.

    The runtime loop decides what happened; this object knows how to turn that
    into a `RuntimeEvent`, how to publish it, and how to persist a session
    snapshot alongside it. Keeping the seam here keeps `AgentRuntime` free of
    store- and sink-specific branching.
    """

    def __init__(
        self,
        *,
        event_sink: EventSink,
        state_store: SessionStore,
        event_store: EventStore | None = None,
    ) -> None:
        self._event_sink = event_sink
        self._state_store = state_store
        self._event_store = event_store
        self._committer: StateEventCommitter | None = (
            cast(StateEventCommitter, state_store)
            if hasattr(state_store, "commit_session_event")
            else None
        )

    def runtime_event(
        self,
        state: AgentState,
        event_type: str,
        *,
        action_id: ActionId | None = None,
        data: dict[str, object] | None = None,
    ) -> RuntimeEvent:
        return RuntimeEvent(
            type=event_type,
            session_id=state.session_id,
            goal_id=state.goal.id,
            task_id=state.current_task.id,
            action_id=action_id,
            data=data or {},
        )

    def _record(self, event: RuntimeEvent) -> None:
        if self._event_store is not None:
            if cast(object, self._event_store) is cast(object, self._event_sink):
                return
            append = getattr(self._event_store, "append", None)
            if callable(append):
                append(event)

    def _record_state(self, snapshot: SessionSnapshot) -> None:
        if self._event_store is None:
            return
        event = RuntimeEvent(
            type=SESSION_STATE_EVENT,
            session_id=snapshot.state.session_id,
            goal_id=snapshot.state.goal.id,
            task_id=snapshot.state.current_task.id,
            data={"snapshot": encode_session_snapshot(snapshot)},
        )
        append = getattr(self._event_store, "append", None)
        if callable(append):
            append(event)

    async def emit(
        self,
        state: AgentState,
        event_type: str,
        *,
        action_id: ActionId | None = None,
        data: dict[str, object] | None = None,
    ) -> None:
        event = self.runtime_event(state, event_type, action_id=action_id, data=data)
        # When the sink also owns the journal, its async emit is authoritative;
        # recording here as well would duplicate every event. A separate sync
        # EventStore (used by replay/tests) still receives the event.
        if cast(object, self._event_store) is not cast(object, self._event_sink):
            self._record(event)
        await self._event_sink.emit(event)

    async def commit_session_event(
        self,
        session: SessionRuntimeState,
        event: RuntimeEvent,
    ) -> None:
        snapshot = session.snapshot()
        if self._committer is None:
            self._record(event)
            self._record_state(snapshot)
            await self._state_store.save_session(snapshot)
            session.version = snapshot.version
            await self._event_sink.emit(event)
            return
        if cast(object, self._event_store) is not cast(object, self._event_sink):
            self._record(event)
        await self._committer.commit_session_event(snapshot, event)
        session.version = snapshot.version
        # Atomic stores persist the state snapshot and event through their
        # committer; sync journals need the explicit state record for rebuild.
        self._record_state(session.snapshot())
        await self._event_sink.emit(event)

    async def save(self, session: SessionRuntimeState) -> None:
        snapshot = session.snapshot()
        self._record_state(snapshot)
        await self._state_store.save_session(snapshot)
        session.version = snapshot.version

    def emitter_for(
        self,
        session: SessionRuntimeState,
    ) -> Callable[[str, ActionId | None, dict[str, object]], Awaitable[None]]:
        """Narrow the event sink down to what action execution is allowed to do."""

        async def emit(
            event_type: str,
            action_id: ActionId | None,
            data: dict[str, object],
        ) -> None:
            await self.emit(session.state, event_type, action_id=action_id, data=data)

        return emit

    def decision_event_data(self, decision: object) -> dict[str, object]:
        from universal_agent.core import Decision

        d = cast(Decision, decision)
        data: dict[str, object] = {
            "decision_type": d.type.value,
            "reason": d.reason,
            "argument_names": tuple(sorted(d.arguments)),
            "arguments": redact_sensitive_mapping(d.arguments),
            "expected_observations": d.expected_observations,
        }
        if d.capability is not None:
            data["capability"] = d.capability
        if d.target is not None:
            data["target"] = d.target
        if d.message is not None:
            data["message"] = d.message
        return data

    async def emit_decision_rejected(
        self,
        state: AgentState,
        decision: object,
        error_code: ErrorCode,
        reason: str,
        *,
        validation_stage: str,
    ) -> None:
        await self.emit(
            state,
            "DecisionRejected",
            data={
                **self.decision_event_data(decision),
                "error_code": error_code.value,
                "validation_stage": validation_stage,
                "rejection_reason": reason,
            },
        )

    async def reject_session(
        self,
        snapshot: SessionSnapshot,
        reason: str,
    ) -> ExecutionResult:
        """Fail a session that could not be hydrated into a runtime state."""
        state = snapshot.state
        state.goal.status = GoalStatus.FAILED
        state.current_task.status = TaskStatus.FAILED
        state.tasks = [state.current_task]
        state.termination_reason = reason
        state.error_code = ErrorCode.INVALID_STATE
        failed_snapshot = session_from_state(
            state,
            domain_name=snapshot.domain_name,
            domain_version=snapshot.domain_version,
            domain_identities=snapshot.domains,
        )
        failed_snapshot.version = snapshot.version
        event = self.runtime_event(
            state,
            "GoalFailed",
            data={"error_code": ErrorCode.INVALID_STATE.value, "reason": reason},
        )
        if self._committer is None:
            await self._state_store.save_session(failed_snapshot)
            await self._event_sink.emit(event)
        else:
            await self._committer.commit_session_event(failed_snapshot, event)
            self._record_state(failed_snapshot)
        return build_result(
            state,
            ExecutionStatus.FAILED,
            reason,
            error_code=ErrorCode.INVALID_STATE,
        )
