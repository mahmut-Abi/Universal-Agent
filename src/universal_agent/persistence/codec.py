from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum

from universal_agent.core import (
    ActionId,
    AgentState,
    DomainIdentity,
    ErrorCode,
    EvaluationResult,
    EvaluationStatus,
    EventId,
    Goal,
    GoalId,
    GoalStatus,
    JsonValue,
    Observation,
    ObservationId,
    ObservationStatus,
    PendingAction,
    RuntimeEvent,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
    TaskStatus,
    immutable_json,
    parse_iso_datetime,
)
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    json_mapping,
    parse_payload,
)
from universal_agent.evidence import Evidence, EvidenceId
from universal_agent.state import SessionSnapshot
from universal_agent.tasks import TaskGraphSnapshot, TaskNodeSnapshot

SCHEMA_VERSION = 1
JsonObject = dict[str, JsonValue]


class _DomainIdentityPayload(ConfigPayload):
    name: str
    version: str


class _SuccessCriterionPayload(ConfigPayload):
    key: str
    expected: PydanticJsonValue


class _GoalPayload(ConfigPayload):
    description: str
    success_criteria: list[_SuccessCriterionPayload]
    id: str
    status: str
    created_at: str


class _TaskPayload(ConfigPayload):
    description: str
    required_criteria: list[str]
    id: str
    status: str
    created_at: str


class _TaskNodePayload(ConfigPayload):
    key: str
    task: _TaskPayload
    depends_on: list[str]


class _TaskGraphPayload(ConfigPayload):
    nodes: list[_TaskNodePayload]
    current_task_id: str


class _ObservationPayload(ConfigPayload):
    id: str
    action_id: str
    task_id: str
    source: str
    status: str
    data: dict[str, PydanticJsonValue]
    observed_at: str
    error: str | None
    error_code: str | None


class _EvaluationPayload(ConfigPayload):
    status: str
    reason: str
    evaluator_name: str
    matched_criteria: dict[str, PydanticJsonValue]
    task_completed: bool
    goal_completed: bool


class _PendingActionPayload(ConfigPayload):
    action_id: str
    capability: str
    tool_name: str
    target: str | None
    arguments: dict[str, PydanticJsonValue]
    domain_name: str
    domain_version: str
    idempotency_key: str = ""
    parameters_hash: str = ""
    attempt: int = 1
    resource_key: str = ""
    resource_version: str | None = None


class _AgentStatePayload(ConfigPayload):
    session_id: str
    goal: _GoalPayload
    current_task_id: str
    iteration: int
    satisfied_criteria: dict[str, PydanticJsonValue]
    observations: list[_ObservationPayload]
    latest_evaluation: _EvaluationPayload | None
    pending_action: _PendingActionPayload | None
    task_ids: list[str]
    recovery_attempts: dict[str, int]
    termination_reason: str | None
    error_code: str | None


class _EvidencePayload(ConfigPayload):
    session_id: str
    task_id: str
    action_id: str
    observation_id: str
    subject: str
    claim: str
    value: PydanticJsonValue
    source: str
    confidence: float
    id: str
    observed_at: str
    domain_name: str = ""
    domain_version: str = ""


class _SessionSnapshotPayload(ConfigPayload):
    schema_version: int
    domain: _DomainIdentityPayload
    domains: list[_DomainIdentityPayload] | None = None
    version: int = 0
    task_graph: _TaskGraphPayload
    state: _AgentStatePayload
    evidence: list[_EvidencePayload]


class _RuntimeEventPayload(ConfigPayload):
    schema_version: int
    event_id: str
    type: str
    session_id: str
    goal_id: str
    task_id: str
    action_id: str | None
    data: dict[str, PydanticJsonValue]
    occurred_at: str


def encode_session_snapshot(snapshot: SessionSnapshot) -> JsonObject:
    return {
        "schema_version": SCHEMA_VERSION,
        "domain": {
            "name": snapshot.domain_name,
            "version": snapshot.domain_version,
        },
        "domains": [
            {"name": identity.name, "version": identity.version} for identity in snapshot.domains
        ],
        "version": snapshot.version,
        "task_graph": _encode_task_graph(snapshot.task_graph),
        "state": _encode_agent_state(snapshot.state),
        "evidence": [_encode_evidence(item) for item in snapshot.evidence],
    }


def decode_session_snapshot(payload: Mapping[str, JsonValue]) -> SessionSnapshot:
    snapshot = _parse_codec_payload(_SessionSnapshotPayload, payload)
    if snapshot.schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported session snapshot schema version: {snapshot.schema_version}")
    graph, tasks = _decode_task_graph(snapshot.task_graph)
    state = _decode_agent_state(snapshot.state, tasks)
    evidence = tuple(_decode_evidence(item) for item in snapshot.evidence)
    return SessionSnapshot(
        state,
        graph,
        evidence,
        snapshot.domain.name,
        snapshot.domain.version,
        _decode_domain_identities(
            snapshot.domains,
            snapshot.domain.name,
            snapshot.domain.version,
        ),
        snapshot.version,
    )


def encode_runtime_event(event: RuntimeEvent) -> JsonObject:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": str(event.id),
        "type": event.type,
        "session_id": str(event.session_id),
        "goal_id": str(event.goal_id),
        "task_id": str(event.task_id),
        "action_id": str(event.action_id) if event.action_id is not None else None,
        "data": _to_json(event.data),
        "occurred_at": event.occurred_at.isoformat(),
    }


def decode_runtime_event(payload: Mapping[str, JsonValue]) -> RuntimeEvent:
    event = _parse_codec_payload(_RuntimeEventPayload, payload)
    if event.schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported runtime event schema version: {event.schema_version}")
    return RuntimeEvent(
        type=event.type,
        session_id=SessionId(event.session_id),
        goal_id=GoalId(event.goal_id),
        task_id=TaskId(event.task_id),
        id=EventId(event.event_id),
        action_id=None if event.action_id is None else ActionId(event.action_id),
        data=immutable_json(json_mapping(event.data)),
        occurred_at=parse_iso_datetime(event.occurred_at, field="occurred_at"),
    )


def _encode_agent_state(state: AgentState) -> JsonObject:
    return {
        "session_id": str(state.session_id),
        "goal": _encode_goal(state.goal),
        "current_task_id": str(state.current_task.id),
        "iteration": state.iteration,
        "satisfied_criteria": _to_json(state.satisfied_criteria),
        "observations": [_encode_observation(item) for item in state.observations],
        "latest_evaluation": (
            None if state.latest_evaluation is None else _encode_evaluation(state.latest_evaluation)
        ),
        "pending_action": (
            None if state.pending_action is None else _encode_pending_action(state.pending_action)
        ),
        "task_ids": [str(task.id) for task in state.tasks],
        "recovery_attempts": dict(state.recovery_attempts),
        "termination_reason": state.termination_reason,
        "error_code": state.error_code.value if state.error_code is not None else None,
    }


def _decode_agent_state(payload: _AgentStatePayload, tasks: Mapping[TaskId, Task]) -> AgentState:
    current_task = _task_by_id(tasks, TaskId(payload.current_task_id))
    state = AgentState(
        session_id=SessionId(payload.session_id),
        goal=_decode_goal(payload.goal),
        current_task=current_task,
        iteration=payload.iteration,
        satisfied_criteria=dict(json_mapping(payload.satisfied_criteria)),
        observations=[_decode_observation(item) for item in payload.observations],
        latest_evaluation=_decode_optional_evaluation(payload.latest_evaluation),
        pending_action=_decode_optional_pending_action(payload.pending_action),
        tasks=[
            _task_by_id(tasks, TaskId(task_id)) for task_id in payload.task_ids
        ],
        recovery_attempts=dict(payload.recovery_attempts),
        termination_reason=payload.termination_reason,
        error_code=_decode_optional_error(payload.error_code),
    )
    return state


def _encode_goal(goal: Goal) -> JsonObject:
    return {
        "description": goal.description,
        "success_criteria": [
            {"key": item.key, "expected": _to_json(item.expected)} for item in goal.success_criteria
        ],
        "id": str(goal.id),
        "status": goal.status.value,
        "created_at": goal.created_at.isoformat(),
    }


def _decode_goal(payload: _GoalPayload) -> Goal:
    return Goal(
        payload.description,
        tuple(
            SuccessCriterion(item.key, item.expected) for item in payload.success_criteria
        ),
        GoalId(payload.id),
        GoalStatus(payload.status),
        parse_iso_datetime(payload.created_at, field="goal.created_at"),
    )


def _encode_task(task: Task) -> JsonObject:
    return {
        "description": task.description,
        "required_criteria": list(task.required_criteria),
        "id": str(task.id),
        "status": task.status.value,
        "created_at": task.created_at.isoformat(),
    }


def _decode_task(payload: _TaskPayload) -> Task:
    return Task(
        payload.description,
        tuple(payload.required_criteria),
        TaskId(payload.id),
        TaskStatus(payload.status),
        parse_iso_datetime(payload.created_at, field="task.created_at"),
    )


def _encode_task_graph(graph: TaskGraphSnapshot) -> JsonObject:
    return {
        "nodes": [
            {
                "key": node.key,
                "task": _encode_task(node.task),
                "depends_on": [str(item) for item in node.depends_on],
            }
            for node in graph.nodes
        ],
        "current_task_id": str(graph.current_task_id),
    }


def _decode_task_graph(
    payload: _TaskGraphPayload,
) -> tuple[TaskGraphSnapshot, dict[TaskId, Task]]:
    nodes: list[TaskNodeSnapshot] = []
    tasks: dict[TaskId, Task] = {}
    for node in payload.nodes:
        task = _decode_task(node.task)
        if task.id in tasks:
            raise ValueError(f"duplicate task id: {task.id}")
        tasks[task.id] = task
        nodes.append(
            TaskNodeSnapshot(
                node.key,
                task,
                tuple(TaskId(task_id) for task_id in node.depends_on),
            )
        )
    graph = TaskGraphSnapshot(
        tuple(nodes),
        TaskId(payload.current_task_id),
    )
    return graph, tasks


def _encode_observation(observation: Observation) -> JsonObject:
    return {
        "id": str(observation.id),
        "action_id": str(observation.action_id),
        "task_id": str(observation.task_id),
        "source": observation.source,
        "status": observation.status.value,
        "data": _to_json(observation.data),
        "observed_at": observation.observed_at.isoformat(),
        "error": observation.error,
        "error_code": observation.error_code.value if observation.error_code is not None else None,
    }


def _decode_observation(payload: _ObservationPayload) -> Observation:
    return Observation(
        ObservationId(payload.id),
        ActionId(payload.action_id),
        TaskId(payload.task_id),
        payload.source,
        ObservationStatus(payload.status),
        immutable_json(json_mapping(payload.data)),
        parse_iso_datetime(payload.observed_at, field="observation.observed_at"),
        payload.error,
        _decode_optional_error(payload.error_code),
    )


def _encode_evaluation(evaluation: EvaluationResult) -> JsonObject:
    return {
        "status": evaluation.status.value,
        "reason": evaluation.reason,
        "evaluator_name": evaluation.evaluator_name,
        "matched_criteria": _to_json(evaluation.matched_criteria),
        "task_completed": evaluation.task_completed,
        "goal_completed": evaluation.goal_completed,
    }


def _decode_optional_evaluation(payload: _EvaluationPayload | None) -> EvaluationResult | None:
    if payload is None:
        return None
    return EvaluationResult(
        EvaluationStatus(payload.status),
        payload.reason,
        payload.evaluator_name,
        immutable_json(json_mapping(payload.matched_criteria)),
        payload.task_completed,
        payload.goal_completed,
    )


def _encode_pending_action(action: PendingAction) -> JsonObject:
    return {
        "action_id": str(action.action_id),
        "capability": action.capability,
        "tool_name": action.tool_name,
        "target": action.target,
        "arguments": _to_json(action.arguments),
        "domain_name": action.domain_name,
        "domain_version": action.domain_version,
        "idempotency_key": action.idempotency_key,
        "parameters_hash": action.parameters_hash,
        "attempt": action.attempt,
        "resource_key": action.resource_key,
        "resource_version": action.resource_version,
    }


def _decode_optional_pending_action(payload: _PendingActionPayload | None) -> PendingAction | None:
    if payload is None:
        return None
    return PendingAction(
        ActionId(payload.action_id),
        payload.capability,
        payload.tool_name,
        payload.target,
        immutable_json(json_mapping(payload.arguments)),
        payload.domain_name,
        payload.domain_version,
        payload.idempotency_key,
        payload.parameters_hash,
        payload.attempt,
        payload.resource_key,
        payload.resource_version,
    )


def _encode_evidence(evidence: Evidence) -> JsonObject:
    return {
        "session_id": str(evidence.session_id),
        "task_id": str(evidence.task_id),
        "action_id": str(evidence.action_id),
        "observation_id": str(evidence.observation_id),
        "subject": evidence.subject,
        "claim": evidence.claim,
        "value": _to_json(evidence.value),
        "source": evidence.source,
        "confidence": evidence.confidence,
        "id": str(evidence.id),
        "observed_at": evidence.observed_at.isoformat(),
        "domain_name": evidence.domain_name,
        "domain_version": evidence.domain_version,
    }


def _decode_evidence(payload: _EvidencePayload) -> Evidence:
    return Evidence(
        SessionId(payload.session_id),
        TaskId(payload.task_id),
        ActionId(payload.action_id),
        ObservationId(payload.observation_id),
        payload.subject,
        payload.claim,
        payload.value,
        payload.source,
        payload.confidence,
        EvidenceId(payload.id),
        parse_iso_datetime(payload.observed_at, field="evidence.observed_at"),
        payload.domain_name,
        payload.domain_version,
    )


def _decode_domain_identities(
    value: Sequence[_DomainIdentityPayload] | None,
    fallback_name: str,
    fallback_version: str,
) -> tuple[DomainIdentity, ...]:
    if value is None:
        if fallback_name and fallback_version:
            return (DomainIdentity(fallback_name, fallback_version),)
        return ()
    identities = tuple(_decode_domain_identity(item) for item in value)
    if not identities and fallback_name and fallback_version:
        return (DomainIdentity(fallback_name, fallback_version),)
    return identities


def _decode_domain_identity(payload: _DomainIdentityPayload) -> DomainIdentity:
    return DomainIdentity(payload.name, payload.version)


def _decode_optional_error(value: str | None) -> ErrorCode | None:
    if value is None:
        return None
    return ErrorCode(value)


def _task_by_id(tasks: Mapping[TaskId, Task], task_id: TaskId) -> Task:
    try:
        return tasks[task_id]
    except KeyError as exc:
        raise ValueError(f"snapshot references unknown task id: {task_id}") from exc


def _to_json(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _to_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_to_json(item) for item in value]
    return str(value)


def _parse_codec_payload[T: ConfigPayload](
    model_type: type[T],
    payload: Mapping[str, JsonValue],
) -> T:
    try:
        return parse_payload(model_type, payload)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
