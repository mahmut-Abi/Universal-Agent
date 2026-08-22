from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

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
    PendingAction,
    RuntimeEvent,
    SessionId,
    Task,
    TaskId,
    TaskStatus,
    immutable_json,
)
from universal_agent.runtime.agent import AgentRuntime
from universal_agent.runtime.events import EventReader
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
    ) -> None:
        self._runtime = runtime
        self._session_store = session_store
        self._event_reader = event_reader

    async def run_goal(self, goal: Goal, task: Task) -> RuntimeRun:
        result = await self._runtime.run(goal, task)
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
        satisfied_criteria=immutable_json(state.satisfied_criteria),
        pending_action=pending_action_view(state.pending_action),
        latest_evaluation=evaluation_view(state.latest_evaluation),
        termination_reason=state.termination_reason,
        error_code=state.error_code,
        domain_name=snapshot.domain_name,
        domain_version=snapshot.domain_version,
    )


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
        immutable_json(pending.arguments),
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
        immutable_json(evaluation.matched_criteria),
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
        data=MappingProxyType(dict(event.data)),
        occurred_at=event.occurred_at,
    )


def _cursor_value(event_id: EventId | None) -> str | None:
    if event_id is None:
        return None
    return str(event_id)


def _session_cursor_value(session_id: SessionId | None) -> str | None:
    if session_id is None:
        return None
    return str(session_id)
