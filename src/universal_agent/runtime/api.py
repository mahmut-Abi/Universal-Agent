from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, cast

from universal_agent.core import (
    ActionId,
    ErrorCode,
    EvaluationResult,
    EvaluationStatus,
    EventId,
    ExecutionResult,
    Goal,
    GoalId,
    GoalStatus,
    JsonMapping,
    JsonValue,
    ObservationId,
    PendingAction,
    RuntimeEvent,
    SessionId,
    Task,
    TaskId,
    TaskStatus,
    immutable_json,
    to_json_value,
)
from universal_agent.evidence import Evidence, EvidenceId
from universal_agent.runtime.agent import AgentRuntime
from universal_agent.runtime.events import EventReader, EventSink, EventWatcher, poll_event_reader
from universal_agent.state import SessionSnapshot, SessionStore


@dataclass(frozen=True, slots=True)
class PendingActionView:
    action_id: ActionId
    capability: str
    tool_name: str
    target: str | None
    arguments: JsonMapping
    domain_name: str
    domain_version: str
    idempotency_key: str
    parameters_hash: str
    attempt: int
    resource_key: str
    resource_version: str | None


@dataclass(frozen=True, slots=True)
class EvaluationView:
    status: EvaluationStatus
    reason: str
    evaluator_name: str
    matched_criteria: JsonMapping
    task_completed: bool
    goal_completed: bool


@dataclass(frozen=True, slots=True)
class TaskView:
    task_id: TaskId
    description: str
    status: TaskStatus
    required_criteria: tuple[str, ...]
    depends_on: tuple[TaskId, ...]


@dataclass(frozen=True, slots=True)
class SessionView:
    session_id: SessionId
    goal_id: GoalId
    goal_description: str
    goal_status: GoalStatus
    current_task_id: TaskId
    current_task_description: str
    current_task_status: TaskStatus
    iteration: int
    tasks: tuple[TaskView, ...]
    satisfied_criteria: JsonMapping
    pending_action: PendingActionView | None
    latest_evaluation: EvaluationView | None
    termination_reason: str | None
    error_code: ErrorCode | None
    domain_name: str
    domain_version: str


@dataclass(frozen=True, slots=True)
class SessionSummaryView:
    session_id: SessionId
    goal_id: GoalId
    goal_description: str
    goal_status: GoalStatus
    current_task_id: TaskId
    current_task_description: str
    current_task_status: TaskStatus
    iteration: int
    task_count: int
    pending_action: bool
    termination_reason: str | None
    error_code: ErrorCode | None
    domain_name: str
    domain_version: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RuntimeEventView:
    event_id: str
    type: str
    session_id: SessionId
    goal_id: GoalId
    task_id: TaskId
    action_id: ActionId | None
    data: MappingProxyType[str, Any]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceView:
    evidence_id: EvidenceId
    session_id: SessionId
    task_id: TaskId
    action_id: ActionId
    observation_id: ObservationId
    subject: str
    claim: str
    value: JsonValue
    source: str
    confidence: float
    observed_at: datetime
    domain_name: str = ""
    domain_version: str = ""


@dataclass(frozen=True, slots=True)
class SessionDiagnosticsView:
    session: SessionView
    evidence: tuple[EvidenceView, ...]


@dataclass(frozen=True, slots=True)
class RuntimeRun:
    result: ExecutionResult
    session: SessionView


@dataclass(frozen=True, slots=True)
class RuntimeEventBatch:
    events: tuple[RuntimeEventView, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class RuntimeSessionBatch:
    sessions: tuple[SessionSummaryView, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class StateEventCommitView:
    supported: bool
    strategy: str
    shared_store: bool
    session_store_type: str
    event_reader_type: str
    event_sink_type: str | None


class RuntimeEventRepairUnavailableError(RuntimeError):
    pass


class RuntimeAPI:
    """Stable in-process interface for applications and future service adapters.

    The runtime remains authoritative for execution. This interface only runs
    goals, resumes waiting sessions, cancels non-terminal sessions, and returns
    immutable read models.
    """

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        session_store: SessionStore,
        event_reader: EventReader,
        event_sink: EventSink | None = None,
    ) -> None:
        self._runtime = runtime
        self._session_store = session_store
        self._event_reader = event_reader
        self._event_sink = event_sink
        if self._event_sink is None and hasattr(event_reader, "emit"):
            self._event_sink = cast(EventSink, event_reader)

    async def run_goal(
        self,
        goal: Goal,
        task: Task,
        *,
        initial_state: JsonMapping | None = None,
    ) -> RuntimeRun:
        result = await self._runtime.run(goal, task, initial_state=initial_state)
        return RuntimeRun(result, await self.get_session(result.session_id))

    async def run_compiled_goal(
        self,
        goal: Goal,
        *,
        initial_state: JsonMapping | None = None,
    ) -> RuntimeRun:
        result = await self._runtime.run_compiled(goal, initial_state=initial_state)
        return RuntimeRun(result, await self.get_session(result.session_id))

    async def resume_session(
        self,
        session_id: SessionId,
        *,
        confirmed: bool | None = None,
    ) -> RuntimeRun:
        result = await self._runtime.resume(session_id, confirmed=confirmed)
        return RuntimeRun(result, await self.get_session(result.session_id))

    async def pause_session(
        self,
        session_id: SessionId,
        *,
        reason: str = "session paused",
    ) -> RuntimeRun:
        result = await self._runtime.pause(session_id, reason=reason)
        return RuntimeRun(result, await self.get_session(result.session_id))

    async def cancel_session(
        self,
        session_id: SessionId,
        *,
        reason: str = "session cancelled",
    ) -> RuntimeRun:
        result = await self._runtime.cancel(session_id, reason=reason)
        return RuntimeRun(result, await self.get_session(result.session_id))

    async def get_session(self, session_id: SessionId) -> SessionView:
        return session_view(await self._session_store.load_session(session_id))

    async def get_session_diagnostics(self, session_id: SessionId) -> SessionDiagnosticsView:
        return session_diagnostics_view(await self._session_store.load_session(session_id))

    async def list_sessions(
        self,
        *,
        after_session_id: SessionId | None = None,
        limit: int | None = None,
    ) -> tuple[SessionSummaryView, ...]:
        return (await self.stream_sessions(after_session_id=after_session_id, limit=limit)).sessions

    async def stream_sessions(
        self,
        *,
        after_session_id: SessionId | None = None,
        limit: int | None = None,
    ) -> RuntimeSessionBatch:
        summaries = tuple(
            session_summary_view(snapshot) for snapshot in await self._session_store.list_sessions()
        )
        views = filter_session_summaries(
            summaries,
            after_session_id=after_session_id,
            limit=limit,
        )
        return RuntimeSessionBatch(
            views,
            str(views[-1].session_id) if views else _session_cursor_value(after_session_id),
        )

    async def list_events(self, session_id: SessionId) -> tuple[RuntimeEventView, ...]:
        return (await self.stream_events(session_id)).events

    async def list_all_events(self) -> tuple[RuntimeEventView, ...]:
        events = await self._event_reader.list_events()
        return tuple(event_view(event) for event in events)

    async def stream_events(
        self,
        session_id: SessionId,
        *,
        after_event_id: EventId | None = None,
        limit: int | None = None,
    ) -> RuntimeEventBatch:
        await self._session_store.load_session(session_id)
        events = await self._event_reader.list_events(
            session_id,
            after_event_id=after_event_id,
            limit=limit,
        )
        views = tuple(event_view(event) for event in events)
        return RuntimeEventBatch(
            views,
            views[-1].event_id if views else _cursor_value(after_event_id),
        )

    async def watch_events(
        self,
        session_id: SessionId,
        *,
        after_event_id: EventId | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[RuntimeEventView]:
        """Yield events as they arrive for real-time SSE streaming."""
        reader = self._event_reader
        if isinstance(reader, EventWatcher):
            stream = reader.watch_events(
                session_id,
                after_event_id=after_event_id,
                heartbeat_interval=heartbeat_interval,
            )
        else:
            stream = poll_event_reader(
                reader,
                session_id,
                after_event_id=after_event_id,
                heartbeat_interval=heartbeat_interval,
            )
        async for event in stream:
            yield event_view(event)

    async def record_repair_events(
        self,
        events: tuple[RuntimeEvent, ...],
    ) -> tuple[RuntimeEventView, ...]:
        if self._event_sink is None:
            raise RuntimeEventRepairUnavailableError("runtime event repair requires an event sink")
        for event in events:
            await self._event_sink.emit(event)
        return tuple(event_view(event) for event in events)

    def state_event_commit(self) -> StateEventCommitView:
        supported = hasattr(self._session_store, "commit_session_event")
        session_store = cast(object, self._session_store)
        event_reader = cast(object, self._event_reader)
        event_sink = None if self._event_sink is None else cast(object, self._event_sink)
        return StateEventCommitView(
            supported=supported,
            strategy=_state_event_commit_strategy(self._session_store)
            if supported
            else "split_store",
            shared_store=session_store is event_reader
            and (event_sink is None or session_store is event_sink),
            session_store_type=type(self._session_store).__name__,
            event_reader_type=type(self._event_reader).__name__,
            event_sink_type=None if self._event_sink is None else type(self._event_sink).__name__,
        )


def session_view(snapshot: SessionSnapshot) -> SessionView:
    state = snapshot.state
    current = state.current_task
    return SessionView(
        session_id=state.session_id,
        goal_id=state.goal.id,
        goal_description=state.goal.description,
        goal_status=state.goal.status,
        current_task_id=current.id,
        current_task_description=current.description,
        current_task_status=current.status,
        iteration=state.iteration,
        tasks=tuple(
            TaskView(
                task_id=node.task.id,
                description=node.task.description,
                status=node.task.status,
                required_criteria=node.task.required_criteria,
                depends_on=node.depends_on,
            )
            for node in snapshot.task_graph.nodes
        ),
        satisfied_criteria=_copy_json_mapping(state.satisfied_criteria),
        pending_action=pending_action_view(state.pending_action),
        latest_evaluation=evaluation_view(state.latest_evaluation),
        termination_reason=state.termination_reason,
        error_code=state.error_code,
        domain_name=snapshot.domain_name,
        domain_version=snapshot.domain_version,
    )


def session_diagnostics_view(snapshot: SessionSnapshot) -> SessionDiagnosticsView:
    evidence = tuple(
        evidence_view(item)
        for item in sorted(snapshot.evidence, key=lambda item: (item.observed_at, str(item.id)))
    )
    return SessionDiagnosticsView(session_view(snapshot), evidence)


def session_summary_view(snapshot: SessionSnapshot) -> SessionSummaryView:
    state = snapshot.state
    current = state.current_task
    return SessionSummaryView(
        session_id=state.session_id,
        goal_id=state.goal.id,
        goal_description=state.goal.description,
        goal_status=state.goal.status,
        current_task_id=current.id,
        current_task_description=current.description,
        current_task_status=current.status,
        iteration=state.iteration,
        task_count=len(snapshot.task_graph.nodes),
        pending_action=state.pending_action is not None,
        termination_reason=state.termination_reason,
        error_code=state.error_code,
        domain_name=snapshot.domain_name,
        domain_version=snapshot.domain_version,
        created_at=state.goal.created_at,
    )


def filter_session_summaries(
    sessions: tuple[SessionSummaryView, ...],
    *,
    after_session_id: SessionId | None = None,
    limit: int | None = None,
) -> tuple[SessionSummaryView, ...]:
    if limit is not None and limit < 1:
        raise ValueError("session list limit must be positive")

    selected: list[SessionSummaryView] = []
    cursor_seen = after_session_id is None
    cursor_in_scope = False
    for session in sessions:
        if after_session_id is not None and session.session_id == after_session_id:
            cursor_in_scope = True
        if not cursor_seen:
            if session.session_id == after_session_id:
                cursor_seen = True
            continue
        selected.append(session)
        if limit is not None and len(selected) >= limit:
            break

    if after_session_id is not None and not cursor_in_scope:
        raise ValueError(f"session cursor not found: {after_session_id}")
    return tuple(selected)


def pending_action_view(pending: PendingAction | None) -> PendingActionView | None:
    if pending is None:
        return None
    return PendingActionView(
        pending.action_id,
        pending.capability,
        pending.tool_name,
        pending.target,
        _copy_json_mapping(pending.arguments),
        pending.domain_name,
        pending.domain_version,
        pending.idempotency_key,
        pending.parameters_hash,
        pending.attempt,
        pending.resource_key,
        pending.resource_version,
    )


def evaluation_view(evaluation: EvaluationResult | None) -> EvaluationView | None:
    if evaluation is None:
        return None
    return EvaluationView(
        evaluation.status,
        evaluation.reason,
        evaluation.evaluator_name,
        _copy_json_mapping(evaluation.matched_criteria),
        evaluation.task_completed,
        evaluation.goal_completed,
    )


def event_view(event: RuntimeEvent) -> RuntimeEventView:
    return RuntimeEventView(
        event_id=str(event.id),
        type=event.type,
        session_id=event.session_id,
        goal_id=event.goal_id,
        task_id=event.task_id,
        action_id=event.action_id,
        data=MappingProxyType(_copy_json_mapping(event.data)),
        occurred_at=event.occurred_at,
    )


def _state_event_commit_strategy(store: object) -> str:
    strategy = getattr(store, "state_event_commit_strategy", None)
    return strategy if isinstance(strategy, str) and strategy else "custom_committer"


def evidence_view(evidence: Evidence) -> EvidenceView:
    return EvidenceView(
        evidence.id,
        evidence.session_id,
        evidence.task_id,
        evidence.action_id,
        evidence.observation_id,
        evidence.subject,
        evidence.claim,
        _copy_json_value(evidence.value),
        evidence.source,
        evidence.confidence,
        evidence.observed_at,
        evidence.domain_name,
        evidence.domain_version,
    )


def _copy_json_value(value: JsonValue) -> JsonValue:
    return to_json_value(value)


def _copy_json_mapping(value: JsonMapping) -> JsonMapping:
    copied = to_json_value(value)
    if not isinstance(copied, dict):  # pragma: no cover - JsonMapping contract guard
        raise TypeError("JSON mapping did not copy to an object")
    return immutable_json(copied)


def _cursor_value(event_id: EventId | None) -> str | None:
    if event_id is None:
        return None
    return str(event_id)


def _session_cursor_value(session_id: SessionId | None) -> str | None:
    if session_id is None:
        return None
    return str(session_id)
