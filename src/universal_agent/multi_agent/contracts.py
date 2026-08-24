from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import NewType, cast

from universal_agent.core import (
    ErrorCode,
    GoalId,
    JsonMapping,
    JsonValue,
    SessionId,
    TaskId,
    immutable_json,
    utc_now,
)
from universal_agent.evidence import EvidenceId

AGENT_TASK_API_VERSION = "agent.nantian.dev/v1"

AgentTaskId = NewType("AgentTaskId", str)


class AgentTaskResultStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    WAITING = "waiting"


@dataclass(frozen=True, slots=True)
class AgentTaskConstraints:
    read_only: bool = False
    max_depth: int = 1
    max_children: int = 1
    max_duration_seconds: float | None = None
    max_cost: float | None = None
    allowed_profiles: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_depth < 0:
            raise ValueError("agent task max_depth must be non-negative")
        if self.max_children < 1:
            raise ValueError("agent task max_children must be positive")
        if self.max_duration_seconds is not None and self.max_duration_seconds <= 0:
            raise ValueError("agent task max_duration_seconds must be positive")
        if self.max_cost is not None and self.max_cost < 0:
            raise ValueError("agent task max_cost must be non-negative")
        _reject_empty_items(self.allowed_profiles, "allowed_profiles")
        _reject_empty_items(self.required_permissions, "required_permissions")


@dataclass(frozen=True, slots=True)
class AgentExpectedOutput:
    type: str
    schema: JsonMapping = field(default_factory=immutable_json)

    def __post_init__(self) -> None:
        if not self.type.strip():
            raise ValueError("agent expected output type must not be empty")
        object.__setattr__(self, "schema", immutable_json(self.schema))


@dataclass(frozen=True, slots=True)
class AgentTaskRequest:
    goal: str
    expected_output: AgentExpectedOutput
    input: JsonMapping = field(default_factory=immutable_json)
    constraints: AgentTaskConstraints = field(default_factory=AgentTaskConstraints)
    api_version: str = AGENT_TASK_API_VERSION
    task_id: AgentTaskId = AgentTaskId("")
    parent_task_id: AgentTaskId | None = None
    parent_session_id: SessionId | None = None
    parent_goal_id: GoalId | None = None
    parent_kernel_task_id: TaskId | None = None
    delegation_depth: int = 0

    def __post_init__(self) -> None:
        if self.api_version != AGENT_TASK_API_VERSION:
            raise ValueError(f"unsupported agent task api version: {self.api_version}")
        if not self.goal.strip():
            raise ValueError("agent task goal must not be empty")
        if self.delegation_depth < 0:
            raise ValueError("agent task delegation_depth must be non-negative")
        if self.delegation_depth > self.constraints.max_depth:
            raise ValueError("agent task delegation_depth exceeds max_depth")
        object.__setattr__(self, "input", immutable_json(self.input))
        if not str(self.task_id).strip():
            object.__setattr__(self, "task_id", AgentTaskId(_agent_task_id(self.goal)))


@dataclass(frozen=True, slots=True)
class AgentTaskResult:
    task_id: AgentTaskId
    status: AgentTaskResultStatus
    result: JsonMapping = field(default_factory=immutable_json)
    evidence_ids: tuple[EvidenceId, ...] = ()
    reason: str = ""
    session_id: SessionId | None = None
    error_code: ErrorCode | None = None
    api_version: str = AGENT_TASK_API_VERSION

    def __post_init__(self) -> None:
        if self.api_version != AGENT_TASK_API_VERSION:
            raise ValueError(f"unsupported agent task result api version: {self.api_version}")
        if not str(self.task_id).strip():
            raise ValueError("agent task result task_id must not be empty")
        if self.status is AgentTaskResultStatus.COMPLETED and self.error_code is not None:
            raise ValueError("completed agent task result cannot include error_code")
        if self.status is not AgentTaskResultStatus.COMPLETED and not self.reason.strip():
            raise ValueError("non-completed agent task result requires reason")
        object.__setattr__(self, "result", immutable_json(self.result))


def agent_task_request_payload(request: AgentTaskRequest) -> JsonMapping:
    return MappingProxyType(
        {
            "api_version": request.api_version,
            "task_id": str(request.task_id),
            "parent_task_id": _optional_str(request.parent_task_id),
            "parent_session_id": _optional_str(request.parent_session_id),
            "parent_goal_id": _optional_str(request.parent_goal_id),
            "parent_kernel_task_id": _optional_str(request.parent_kernel_task_id),
            "goal": request.goal,
            "input": dict(request.input),
            "constraints": {
                "read_only": request.constraints.read_only,
                "max_depth": request.constraints.max_depth,
                "max_children": request.constraints.max_children,
                "max_duration_seconds": request.constraints.max_duration_seconds,
                "max_cost": request.constraints.max_cost,
                "allowed_profiles": list(request.constraints.allowed_profiles),
                "required_permissions": list(request.constraints.required_permissions),
            },
            "expected_output": {
                "type": request.expected_output.type,
                "schema": dict(request.expected_output.schema),
            },
            "delegation_depth": request.delegation_depth,
        }
    )


def agent_task_result_payload(result: AgentTaskResult) -> JsonMapping:
    return MappingProxyType(
        {
            "api_version": result.api_version,
            "task_id": str(result.task_id),
            "status": result.status.value,
            "result": dict(result.result),
            "evidence": [str(evidence_id) for evidence_id in result.evidence_ids],
            "reason": result.reason,
            "session_id": _optional_str(result.session_id),
            "error_code": None if result.error_code is None else result.error_code.value,
        }
    )


def decode_agent_task_request(payload: JsonMapping) -> AgentTaskRequest:
    expected_output = _mapping(payload.get("expected_output"), "expected_output")
    return AgentTaskRequest(
        goal=_string(payload.get("goal"), "goal"),
        input=_mapping(payload.get("input"), "input", default_empty=True),
        constraints=_decode_constraints(
            _mapping(payload.get("constraints"), "constraints", default_empty=True)
        ),
        expected_output=AgentExpectedOutput(
            _string(expected_output.get("type"), "expected_output.type"),
            _mapping(expected_output.get("schema"), "expected_output.schema", default_empty=True),
        ),
        api_version=_string(payload.get("api_version"), "api_version", AGENT_TASK_API_VERSION),
        task_id=AgentTaskId(_string(payload.get("task_id"), "task_id", "")),
        parent_task_id=_optional_agent_task_id(payload.get("parent_task_id"), "parent_task_id"),
        parent_session_id=_optional_session_id(payload.get("parent_session_id")),
        parent_goal_id=_optional_goal_id(payload.get("parent_goal_id")),
        parent_kernel_task_id=_optional_task_id(payload.get("parent_kernel_task_id")),
        delegation_depth=_int(payload.get("delegation_depth"), "delegation_depth", 0),
    )


def decode_agent_task_result(payload: JsonMapping) -> AgentTaskResult:
    return AgentTaskResult(
        task_id=AgentTaskId(_string(payload.get("task_id"), "task_id")),
        status=_result_status(payload.get("status")),
        result=_mapping(payload.get("result"), "result", default_empty=True),
        evidence_ids=_evidence_ids(payload.get("evidence")),
        reason=_string(payload.get("reason"), "reason", ""),
        session_id=_optional_session_id(payload.get("session_id")),
        error_code=_optional_error_code(payload.get("error_code")),
        api_version=_string(payload.get("api_version"), "api_version", AGENT_TASK_API_VERSION),
    )


def _decode_constraints(payload: JsonMapping) -> AgentTaskConstraints:
    return AgentTaskConstraints(
        read_only=_bool(payload.get("read_only"), "constraints.read_only", False),
        max_depth=_int(payload.get("max_depth"), "constraints.max_depth", 1),
        max_children=_int(payload.get("max_children"), "constraints.max_children", 1),
        max_duration_seconds=_optional_float(
            payload.get("max_duration_seconds"), "constraints.max_duration_seconds"
        ),
        max_cost=_optional_float(payload.get("max_cost"), "constraints.max_cost"),
        allowed_profiles=_string_tuple(
            payload.get("allowed_profiles"), "constraints.allowed_profiles"
        ),
        required_permissions=_string_tuple(
            payload.get("required_permissions"), "constraints.required_permissions"
        ),
    )


def _agent_task_id(goal: str) -> str:
    normalized = "-".join(word for word in goal.lower().split() if word)
    suffix = utc_now().strftime("%Y%m%d%H%M%S%f")
    return f"agent-task-{normalized[:40] or 'task'}-{suffix}"


def _optional_str(value: object | None) -> JsonValue:
    if value is None:
        return None
    return str(value)


def _mapping(value: object, field_name: str, default_empty: bool = False) -> JsonMapping:
    if value is None and default_empty:
        return immutable_json()
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} keys must be strings")
    return immutable_json(cast(Mapping[str, JsonValue], value))


def _string(value: object, field_name: str, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name)


def _bool(value: object, field_name: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _int(value: object, field_name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _optional_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    return float(value)


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    strings: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{field_name}[{index}] must be a string")
        strings.append(item)
    return tuple(strings)


def _optional_agent_task_id(value: object, field_name: str) -> AgentTaskId | None:
    raw = _optional_string(value, field_name)
    if raw is None:
        return None
    return AgentTaskId(raw)


def _optional_session_id(value: object) -> SessionId | None:
    raw = _optional_string(value, "session_id")
    if raw is None:
        return None
    return SessionId(raw)


def _optional_goal_id(value: object) -> GoalId | None:
    raw = _optional_string(value, "goal_id")
    if raw is None:
        return None
    return GoalId(raw)


def _optional_task_id(value: object) -> TaskId | None:
    raw = _optional_string(value, "task_id")
    if raw is None:
        return None
    return TaskId(raw)


def _evidence_ids(value: object) -> tuple[EvidenceId, ...]:
    return tuple(EvidenceId(item) for item in _string_tuple(value, "evidence"))


def _result_status(value: object) -> AgentTaskResultStatus:
    raw = _string(value, "status")
    try:
        return AgentTaskResultStatus(raw)
    except ValueError as exc:
        raise ValueError(f"unsupported agent task result status: {raw}") from exc


def _optional_error_code(value: object) -> ErrorCode | None:
    raw = _optional_string(value, "error_code")
    if raw is None:
        return None
    try:
        return ErrorCode(raw)
    except ValueError as exc:
        raise ValueError(f"unsupported agent task error_code: {raw}") from exc


def _reject_empty_items(values: tuple[str, ...], field_name: str) -> None:
    for index, value in enumerate(values):
        if not value.strip():
            raise ValueError(f"agent task {field_name}[{index}] must not be empty")
