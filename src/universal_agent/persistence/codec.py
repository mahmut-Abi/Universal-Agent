from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum
from typing import cast

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
    JsonMapping,
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
)
from universal_agent.evidence import Evidence, EvidenceId
from universal_agent.state import SessionSnapshot
from universal_agent.tasks import TaskGraphSnapshot, TaskNodeSnapshot

SCHEMA_VERSION = 1
JsonObject = dict[str, JsonValue]


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
        "task_graph": _encode_task_graph(snapshot.task_graph),
        "state": _encode_agent_state(snapshot.state),
        "evidence": [_encode_evidence(item) for item in snapshot.evidence],
    }


def decode_session_snapshot(payload: Mapping[str, JsonValue]) -> SessionSnapshot:
    version = _int(_required(payload, "schema_version"), "schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported session snapshot schema version: {version}")
    graph, tasks = _decode_task_graph(_object(_required(payload, "task_graph"), "task_graph"))
    state = _decode_agent_state(_object(_required(payload, "state"), "state"), tasks)
    evidence = tuple(
        _decode_evidence(_object(item, "evidence[]"))
        for item in _list(_required(payload, "evidence"), "evidence")
    )
    domain = _object(_required(payload, "domain"), "domain")
    domain_name = _string(_required(domain, "name"), "domain.name")
    domain_version = _string(_required(domain, "version"), "domain.version")
    return SessionSnapshot(
        state,
        graph,
        evidence,
        domain_name,
        domain_version,
        _decode_domain_identities(payload.get("domains"), domain_name, domain_version),
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
    version = _int(_required(payload, "schema_version"), "schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported runtime event schema version: {version}")
    action_id = _required(payload, "action_id")
    return RuntimeEvent(
        type=_string(_required(payload, "type"), "type"),
        session_id=SessionId(_string(_required(payload, "session_id"), "session_id")),
        goal_id=GoalId(_string(_required(payload, "goal_id"), "goal_id")),
        task_id=TaskId(_string(_required(payload, "task_id"), "task_id")),
        id=EventId(_string(_required(payload, "event_id"), "event_id")),
        action_id=None if action_id is None else ActionId(_string(action_id, "action_id")),
        data=immutable_json(_object(_required(payload, "data"), "data")),
        occurred_at=_datetime(_required(payload, "occurred_at"), "occurred_at"),
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


def _decode_agent_state(payload: JsonObject, tasks: Mapping[TaskId, Task]) -> AgentState:
    current_task = _task_by_id(
        tasks,
        TaskId(_string(_required(payload, "current_task_id"), "current_task_id")),
    )
    state = AgentState(
        session_id=SessionId(_string(_required(payload, "session_id"), "session_id")),
        goal=_decode_goal(_object(_required(payload, "goal"), "goal")),
        current_task=current_task,
        iteration=_int(_required(payload, "iteration"), "iteration"),
        satisfied_criteria=dict(
            _object(_required(payload, "satisfied_criteria"), "satisfied_criteria")
        ),
        observations=[
            _decode_observation(_object(item, "observations[]"))
            for item in _list(_required(payload, "observations"), "observations")
        ],
        latest_evaluation=_decode_optional_evaluation(_required(payload, "latest_evaluation")),
        pending_action=_decode_optional_pending_action(_required(payload, "pending_action")),
        tasks=[
            _task_by_id(tasks, TaskId(_string(item, "task_ids[]")))
            for item in _list(_required(payload, "task_ids"), "task_ids")
        ],
        recovery_attempts={
            key: _int(value, f"recovery_attempts.{key}")
            for key, value in _object(
                _required(payload, "recovery_attempts"),
                "recovery_attempts",
            ).items()
        },
        termination_reason=_optional_string(_required(payload, "termination_reason")),
        error_code=_decode_optional_error(_required(payload, "error_code")),
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


def _decode_goal(payload: JsonObject) -> Goal:
    return Goal(
        _string(_required(payload, "description"), "goal.description"),
        tuple(
            SuccessCriterion(
                _string(_required(_object(item, "success_criteria[]"), "key"), "criterion.key"),
                _required(_object(item, "success_criteria[]"), "expected"),
            )
            for item in _list(_required(payload, "success_criteria"), "success_criteria")
        ),
        GoalId(_string(_required(payload, "id"), "goal.id")),
        GoalStatus(_string(_required(payload, "status"), "goal.status")),
        _datetime(_required(payload, "created_at"), "goal.created_at"),
    )


def _encode_task(task: Task) -> JsonObject:
    return {
        "description": task.description,
        "required_criteria": list(task.required_criteria),
        "id": str(task.id),
        "status": task.status.value,
        "created_at": task.created_at.isoformat(),
    }


def _decode_task(payload: JsonObject) -> Task:
    return Task(
        _string(_required(payload, "description"), "task.description"),
        tuple(
            _string(item, "required_criteria[]")
            for item in _list(
                _required(payload, "required_criteria"),
                "required_criteria",
            )
        ),
        TaskId(_string(_required(payload, "id"), "task.id")),
        TaskStatus(_string(_required(payload, "status"), "task.status")),
        _datetime(_required(payload, "created_at"), "task.created_at"),
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


def _decode_task_graph(payload: JsonObject) -> tuple[TaskGraphSnapshot, dict[TaskId, Task]]:
    nodes: list[TaskNodeSnapshot] = []
    tasks: dict[TaskId, Task] = {}
    for item in _list(_required(payload, "nodes"), "nodes"):
        node = _object(item, "nodes[]")
        task = _decode_task(_object(_required(node, "task"), "node.task"))
        if task.id in tasks:
            raise ValueError(f"duplicate task id: {task.id}")
        tasks[task.id] = task
        nodes.append(
            TaskNodeSnapshot(
                _string(_required(node, "key"), "node.key"),
                task,
                tuple(
                    TaskId(_string(task_id, "depends_on[]"))
                    for task_id in _list(_required(node, "depends_on"), "depends_on")
                ),
            )
        )
    graph = TaskGraphSnapshot(
        tuple(nodes),
        TaskId(_string(_required(payload, "current_task_id"), "current_task_id")),
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


def _decode_observation(payload: JsonObject) -> Observation:
    return Observation(
        ObservationId(_string(_required(payload, "id"), "observation.id")),
        ActionId(_string(_required(payload, "action_id"), "observation.action_id")),
        TaskId(_string(_required(payload, "task_id"), "observation.task_id")),
        _string(_required(payload, "source"), "observation.source"),
        ObservationStatus(_string(_required(payload, "status"), "observation.status")),
        immutable_json(_object(_required(payload, "data"), "observation.data")),
        _datetime(_required(payload, "observed_at"), "observation.observed_at"),
        _optional_string(_required(payload, "error")),
        _decode_optional_error(_required(payload, "error_code")),
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


def _decode_optional_evaluation(value: JsonValue) -> EvaluationResult | None:
    if value is None:
        return None
    payload = _object(value, "latest_evaluation")
    return EvaluationResult(
        EvaluationStatus(_string(_required(payload, "status"), "evaluation.status")),
        _string(_required(payload, "reason"), "evaluation.reason"),
        _string(_required(payload, "evaluator_name"), "evaluation.evaluator_name"),
        immutable_json(_object(_required(payload, "matched_criteria"), "matched_criteria")),
        _bool(_required(payload, "task_completed"), "evaluation.task_completed"),
        _bool(_required(payload, "goal_completed"), "evaluation.goal_completed"),
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
    }


def _decode_optional_pending_action(value: JsonValue) -> PendingAction | None:
    if value is None:
        return None
    payload = _object(value, "pending_action")
    return PendingAction(
        ActionId(_string(_required(payload, "action_id"), "pending_action.action_id")),
        _string(_required(payload, "capability"), "pending_action.capability"),
        _string(_required(payload, "tool_name"), "pending_action.tool_name"),
        _optional_string(_required(payload, "target")),
        immutable_json(_object(_required(payload, "arguments"), "pending_action.arguments")),
        _string(_required(payload, "domain_name"), "pending_action.domain_name"),
        _string(_required(payload, "domain_version"), "pending_action.domain_version"),
        _optional_string(payload.get("idempotency_key")) or "",
        _optional_string(payload.get("parameters_hash")) or "",
        _int(payload.get("attempt", 1), "pending_action.attempt"),
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
    }


def _decode_evidence(payload: JsonObject) -> Evidence:
    return Evidence(
        SessionId(_string(_required(payload, "session_id"), "evidence.session_id")),
        TaskId(_string(_required(payload, "task_id"), "evidence.task_id")),
        ActionId(_string(_required(payload, "action_id"), "evidence.action_id")),
        ObservationId(_string(_required(payload, "observation_id"), "evidence.observation_id")),
        _string(_required(payload, "subject"), "evidence.subject"),
        _string(_required(payload, "claim"), "evidence.claim"),
        _required(payload, "value"),
        _string(_required(payload, "source"), "evidence.source"),
        _float(_required(payload, "confidence"), "evidence.confidence"),
        EvidenceId(_string(_required(payload, "id"), "evidence.id")),
        _datetime(_required(payload, "observed_at"), "evidence.observed_at"),
    )


def _decode_domain_identities(
    value: JsonValue,
    fallback_name: str,
    fallback_version: str,
) -> tuple[DomainIdentity, ...]:
    if value is None:
        if fallback_name and fallback_version:
            return (DomainIdentity(fallback_name, fallback_version),)
        return ()
    identities = tuple(
        _decode_domain_identity(_object(item, "domains[]")) for item in _list(value, "domains")
    )
    if not identities and fallback_name and fallback_version:
        return (DomainIdentity(fallback_name, fallback_version),)
    return identities


def _decode_domain_identity(payload: JsonObject) -> DomainIdentity:
    return DomainIdentity(
        _string(_required(payload, "name"), "domain_identity.name"),
        _string(_required(payload, "version"), "domain_identity.version"),
    )


def _decode_optional_error(value: JsonValue) -> ErrorCode | None:
    if value is None:
        return None
    return ErrorCode(_string(value, "error_code"))


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


def _required(payload: Mapping[str, JsonValue], key: str) -> JsonValue:
    try:
        return payload[key]
    except KeyError as exc:
        raise ValueError(f"missing required field: {key}") from exc


def _object(value: JsonValue, field: str) -> JsonObject:
    if isinstance(value, dict):
        return value
    raise ValueError(f"{field} must be an object")


def _list(value: JsonValue, field: str) -> list[JsonValue]:
    if isinstance(value, list):
        return value
    raise ValueError(f"{field} must be a list")


def _string(value: JsonValue, field: str) -> str:
    if isinstance(value, str):
        return value
    raise ValueError(f"{field} must be a string")


def _optional_string(value: JsonValue) -> str | None:
    if value is None:
        return None
    return _string(value, "optional string")


def _int(value: JsonValue, field: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be an integer")


def _float(value: JsonValue, field: str) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    raise ValueError(f"{field} must be a number")


def _bool(value: JsonValue, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be a boolean")


def _datetime(value: JsonValue, field: str) -> datetime:
    return datetime.fromisoformat(_string(value, field))


def json_mapping(value: object) -> JsonMapping:
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return cast(JsonMapping, value)
    raise ValueError("expected a JSON object")
