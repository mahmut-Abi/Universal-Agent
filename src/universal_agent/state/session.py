from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from universal_agent.core import (
    AgentState,
    DomainIdentity,
    EvaluationResult,
    Goal,
    JsonMapping,
    JsonValue,
    Observation,
    PendingAction,
    RuntimeEvent,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
    immutable_json,
    to_json_value,
)
from universal_agent.evidence import Evidence
from universal_agent.tasks import TaskGraphSnapshot, TaskNodeSnapshot

if TYPE_CHECKING:
    from universal_agent.state.event_store import EventStore
    from universal_agent.state.store import SessionStore


@dataclass(slots=True)
class SessionSnapshot:
    state: AgentState
    task_graph: TaskGraphSnapshot
    evidence: tuple[Evidence, ...] = ()
    domain_name: str = ""
    domain_version: str = ""
    domain_identities: tuple[DomainIdentity, ...] = ()
    version: int = 0

    @property
    def domains(self) -> tuple[DomainIdentity, ...]:
        if self.domain_identities:
            return self.domain_identities
        if self.domain_name and self.domain_version:
            return (DomainIdentity(self.domain_name, self.domain_version),)
        return ()


def session_from_state(
    state: AgentState,
    *,
    domain_name: str = "",
    domain_version: str = "",
    domain_identities: tuple[DomainIdentity, ...] = (),
) -> SessionSnapshot:
    graph = TaskGraphSnapshot(
        (TaskNodeSnapshot("root", state.current_task, ()),),
        state.current_task.id,
    )
    return SessionSnapshot(
        state,
        graph,
        (),
        domain_name,
        domain_version,
        domain_identities or _primary_domain(domain_name, domain_version),
    )


def with_state(snapshot: SessionSnapshot, state: AgentState) -> SessionSnapshot:
    nodes = tuple(
        TaskNodeSnapshot(node.key, state.current_task, node.depends_on)
        if node.task.id == state.current_task.id
        else node
        for node in snapshot.task_graph.nodes
    )
    known = {node.task.id for node in nodes}
    if state.current_task.id not in known:
        nodes = (*nodes, TaskNodeSnapshot(str(state.current_task.id), state.current_task, ()))
    graph = TaskGraphSnapshot(nodes, state.current_task.id)
    return SessionSnapshot(
        state,
        graph,
        snapshot.evidence,
        snapshot.domain_name,
        snapshot.domain_version,
        snapshot.domains,
        snapshot.version,
    )


def copy_session(snapshot: SessionSnapshot) -> SessionSnapshot:
    tasks = {node.task.id: _copy_task(node.task) for node in snapshot.task_graph.nodes}
    graph = TaskGraphSnapshot(
        tuple(
            TaskNodeSnapshot(node.key, tasks[node.task.id], node.depends_on)
            for node in snapshot.task_graph.nodes
        ),
        snapshot.task_graph.current_task_id,
    )
    return SessionSnapshot(
        _copy_state(snapshot.state, tasks),
        graph,
        tuple(_copy_evidence(item) for item in snapshot.evidence),
        snapshot.domain_name,
        snapshot.domain_version,
        snapshot.domains,
        snapshot.version,
    )


def _primary_domain(domain_name: str, domain_version: str) -> tuple[DomainIdentity, ...]:
    if domain_name and domain_version:
        return (DomainIdentity(domain_name, domain_version),)
    return ()


def _copy_state(state: AgentState, tasks: dict[TaskId, Task]) -> AgentState:
    copied = AgentState(
        session_id=state.session_id,
        goal=_copy_goal(state.goal),
        current_task=_resolve_task(state.current_task, tasks),
        iteration=state.iteration,
        satisfied_criteria={
            key: _copy_json(value) for key, value in state.satisfied_criteria.items()
        },
        observations=[_copy_observation(item) for item in state.observations],
        latest_evaluation=_copy_evaluation(state.latest_evaluation),
        pending_action=_copy_pending_action(state.pending_action),
        tasks=[_resolve_task(item, tasks) for item in state.tasks],
        recovery_attempts=dict(state.recovery_attempts),
        termination_reason=state.termination_reason,
        error_code=state.error_code,
    )
    return copied


def _resolve_task(task: Task, tasks: dict[TaskId, Task]) -> Task:
    existing = tasks.get(task.id)
    if existing is not None:
        return existing
    copied = _copy_task(task)
    tasks[task.id] = copied
    return copied


def _copy_task(task: Task) -> Task:
    return Task(
        task.description,
        task.required_criteria,
        task.id,
        task.status,
        task.created_at,
    )


def _copy_goal(goal: Goal) -> Goal:
    criteria = tuple(
        SuccessCriterion(item.key, _copy_json(item.expected)) for item in goal.success_criteria
    )
    return Goal(goal.description, criteria, goal.id, goal.status, goal.created_at)


def _copy_observation(observation: Observation) -> Observation:
    return Observation(
        observation.id,
        observation.action_id,
        observation.task_id,
        observation.source,
        observation.status,
        _copy_mapping(observation.data),
        observation.observed_at,
        observation.error,
        observation.error_code,
    )


def _copy_evaluation(evaluation: EvaluationResult | None) -> EvaluationResult | None:
    if evaluation is None:
        return None
    return EvaluationResult(
        evaluation.status,
        evaluation.reason,
        evaluation.evaluator_name,
        _copy_mapping(evaluation.matched_criteria),
        evaluation.task_completed,
        evaluation.goal_completed,
    )


def _copy_pending_action(action: PendingAction | None) -> PendingAction | None:
    if action is None:
        return None
    return PendingAction(
        action.action_id,
        action.capability,
        action.tool_name,
        action.target,
        _copy_mapping(action.arguments),
        action.domain_name,
        action.domain_version,
        action.idempotency_key,
        action.parameters_hash,
        action.attempt,
        action.resource_key,
        action.resource_version,
    )


def _copy_evidence(evidence: Evidence) -> Evidence:
    return Evidence(
        evidence.session_id,
        evidence.task_id,
        evidence.action_id,
        evidence.observation_id,
        evidence.subject,
        evidence.claim,
        _copy_json(evidence.value),
        evidence.source,
        evidence.confidence,
        evidence.id,
        evidence.observed_at,
        evidence.domain_name,
        evidence.domain_version,
    )


def _copy_mapping(values: JsonMapping) -> JsonMapping:
    copied = to_json_value(values)
    if not isinstance(copied, dict):  # pragma: no cover - JsonMapping contract guard
        raise TypeError("JSON mapping did not copy to an object")
    return immutable_json(copied)


def _copy_json(value: JsonValue) -> JsonValue:
    return to_json_value(value)


class EventSourcedSessionStore:
    """Session store that rebuilds snapshots from the event journal on a miss.

    The runtime already records a ``SessionStateCommitted`` event (carrying the
    full serialized snapshot) on every committed state change. When the
    snapshot store has no record for a session, this store reconstructs the
    snapshot by replaying those events instead of failing. This is what makes
    resume/pause/cancel event-sourced: the snapshot store is authoritative, but
    the event journal is the recovery source of truth.
    """

    def __init__(self, store: SessionStore, event_store: EventStore) -> None:
        self._store = store
        self._event_store = event_store

    async def create_session(self, snapshot: SessionSnapshot) -> None:
        await self._store.create_session(snapshot)

    async def list_sessions(self) -> tuple[SessionSnapshot, ...]:
        return await self._store.list_sessions()

    async def load_session(self, session_id: SessionId) -> SessionSnapshot:
        from universal_agent.state.store import StateNotFoundError

        try:
            return await self._store.load_session(session_id)
        except StateNotFoundError:
            from universal_agent.state.event_store import rebuild_session_snapshot

            snapshot = rebuild_session_snapshot(self._event_store, session_id)
            await self._store.create_session(snapshot)
            return snapshot

    async def save_session(self, snapshot: SessionSnapshot) -> None:
        await self._store.save_session(snapshot)

    async def create(self, state: AgentState) -> None:
        await self._store.create(state)

    async def load(self, session_id: SessionId) -> AgentState:
        return await self._store.load(session_id)

    async def save(self, state: AgentState) -> None:
        await self._store.save(state)

    async def commit_session_event(self, snapshot: SessionSnapshot, event: RuntimeEvent) -> None:
        from typing import cast

        from universal_agent.state.store import StateEventCommitter

        committer = self._store
        if hasattr(committer, "commit_session_event"):
            await cast(StateEventCommitter, committer).commit_session_event(snapshot, event)
            return
        await self._store.save_session(snapshot)
