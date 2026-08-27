from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import NewType

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError

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
from universal_agent.core.config_validation import ConfigPayload, PydanticJsonValue, json_mapping
from universal_agent.evidence import EvidenceId

AGENT_TASK_API_VERSION = "agent.nantian.dev/v1"

AgentTaskId = NewType("AgentTaskId", str)


class AgentTaskResultStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    WAITING = "waiting"


class _AgentExpectedOutputPayload(ConfigPayload):
    type: str
    schema_: dict[str, PydanticJsonValue] = Field(default_factory=dict, alias="schema")


class _AgentTaskConstraintsPayload(ConfigPayload):
    read_only: bool = False
    max_depth: int = 1
    max_children: int = 1
    max_duration_seconds: float | None = None
    max_cost: float | None = None
    allowed_profiles: list[str] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)


class _AgentTaskRequestPayload(ConfigPayload):
    goal: str
    expected_output: _AgentExpectedOutputPayload
    input: dict[str, PydanticJsonValue] = Field(default_factory=dict)
    constraints: _AgentTaskConstraintsPayload = Field(default_factory=_AgentTaskConstraintsPayload)
    api_version: str = AGENT_TASK_API_VERSION
    task_id: str = ""
    parent_task_id: str | None = None
    parent_session_id: str | None = None
    parent_goal_id: str | None = None
    parent_kernel_task_id: str | None = None
    delegation_depth: int = 0


class _AgentTaskUsagePayload(ConfigPayload):
    model_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int | None = None
    estimated_cost: float = 0.0
    currency: str = "USD"


class _AgentTaskResultPayload(ConfigPayload):
    task_id: str
    status: str
    result: dict[str, PydanticJsonValue] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    reason: str = ""
    session_id: str | None = None
    error_code: str | None = None
    api_version: str = AGENT_TASK_API_VERSION
    usage: _AgentTaskUsagePayload = Field(default_factory=_AgentTaskUsagePayload)


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
class AgentTaskUsage:
    model_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    currency: str = "USD"

    def __post_init__(self) -> None:
        if self.model_call_count < 0:
            raise ValueError("agent task usage model_call_count must not be negative")
        if self.input_tokens < 0:
            raise ValueError("agent task usage input_tokens must not be negative")
        if self.output_tokens < 0:
            raise ValueError("agent task usage output_tokens must not be negative")
        if self.estimated_cost < 0:
            raise ValueError("agent task usage estimated_cost must not be negative")
        if not self.currency.strip():
            raise ValueError("agent task usage currency must not be empty")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


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
    usage: AgentTaskUsage = field(default_factory=AgentTaskUsage)

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
            "usage": {
                "model_call_count": result.usage.model_call_count,
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "total_tokens": result.usage.total_tokens,
                "estimated_cost": result.usage.estimated_cost,
                "currency": result.usage.currency,
            },
        }
    )


def decode_agent_task_request(payload: JsonMapping) -> AgentTaskRequest:
    parsed = _parse_contract_payload(_AgentTaskRequestPayload, payload)
    return AgentTaskRequest(
        goal=parsed.goal,
        input=immutable_json(json_mapping(parsed.input)),
        constraints=_agent_task_constraints(parsed.constraints),
        expected_output=AgentExpectedOutput(
            parsed.expected_output.type,
            immutable_json(json_mapping(parsed.expected_output.schema_)),
        ),
        api_version=parsed.api_version,
        task_id=AgentTaskId(parsed.task_id),
        parent_task_id=_optional_agent_task_id(parsed.parent_task_id),
        parent_session_id=_optional_session_id(parsed.parent_session_id),
        parent_goal_id=_optional_goal_id(parsed.parent_goal_id),
        parent_kernel_task_id=_optional_task_id(parsed.parent_kernel_task_id),
        delegation_depth=parsed.delegation_depth,
    )


def decode_agent_task_result(payload: JsonMapping) -> AgentTaskResult:
    parsed = _parse_contract_payload(_AgentTaskResultPayload, payload)
    return AgentTaskResult(
        task_id=AgentTaskId(parsed.task_id),
        status=_result_status(parsed.status),
        result=immutable_json(json_mapping(parsed.result)),
        evidence_ids=tuple(EvidenceId(item) for item in parsed.evidence),
        reason=parsed.reason,
        session_id=_optional_session_id(parsed.session_id),
        error_code=_optional_error_code(parsed.error_code),
        api_version=parsed.api_version,
        usage=_agent_task_usage(parsed.usage),
    )


def _agent_task_constraints(payload: _AgentTaskConstraintsPayload) -> AgentTaskConstraints:
    return AgentTaskConstraints(
        read_only=payload.read_only,
        max_depth=payload.max_depth,
        max_children=payload.max_children,
        max_duration_seconds=payload.max_duration_seconds,
        max_cost=payload.max_cost,
        allowed_profiles=tuple(payload.allowed_profiles),
        required_permissions=tuple(payload.required_permissions),
    )


def _agent_task_usage(payload: _AgentTaskUsagePayload) -> AgentTaskUsage:
    usage = AgentTaskUsage(
        model_call_count=payload.model_call_count,
        input_tokens=payload.input_tokens,
        output_tokens=payload.output_tokens,
        estimated_cost=payload.estimated_cost,
        currency=payload.currency,
    )
    if payload.total_tokens is not None and payload.total_tokens != usage.total_tokens:
        raise ValueError("usage.total_tokens does not match input_tokens + output_tokens")
    return usage


def _agent_task_id(goal: str) -> str:
    normalized = "-".join(word for word in goal.lower().split() if word)
    suffix = utc_now().strftime("%Y%m%d%H%M%S%f")
    return f"agent-task-{normalized[:40] or 'task'}-{suffix}"


def _optional_str(value: object | None) -> JsonValue:
    if value is None:
        return None
    return str(value)


def _optional_agent_task_id(value: str | None) -> AgentTaskId | None:
    if value is None:
        return None
    return AgentTaskId(value)


def _optional_session_id(value: str | None) -> SessionId | None:
    if value is None:
        return None
    return SessionId(value)


def _optional_goal_id(value: str | None) -> GoalId | None:
    if value is None:
        return None
    return GoalId(value)


def _optional_task_id(value: str | None) -> TaskId | None:
    if value is None:
        return None
    return TaskId(value)


def _result_status(raw: str) -> AgentTaskResultStatus:
    try:
        return AgentTaskResultStatus(raw)
    except ValueError as exc:
        raise ValueError(f"unsupported agent task result status: {raw}") from exc


def _optional_error_code(value: str | None) -> ErrorCode | None:
    if value is None:
        return None
    try:
        return ErrorCode(value)
    except ValueError as exc:
        raise ValueError(f"unsupported agent task error_code: {value}") from exc


def _parse_contract_payload[T: ConfigPayload](
    payload_type: type[T],
    payload: Mapping[str, JsonValue],
) -> T:
    try:
        return payload_type.model_validate(dict(payload))
    except PydanticValidationError as exc:
        raise ValueError(_contract_payload_error_message(exc)) from exc


def _contract_payload_error_message(error: PydanticValidationError) -> str:
    errors = error.errors(include_url=False)
    if not errors:
        return str(error)
    first = errors[0]
    path = _pydantic_error_path(first.get("loc", ()))
    error_type = str(first.get("type", ""))
    expected = _expected_contract_type(error_type, path)
    if expected is not None:
        return f"{path} must be {expected}"
    message = str(first.get("msg", ""))
    if message:
        return message.removeprefix("Value error, ")
    return str(error)


def _expected_contract_type(error_type: str, path: str) -> str | None:
    if error_type == "missing":
        return _missing_contract_field_type(path)
    return {
        "bool_type": "a boolean",
        "dict_type": "an object",
        "float_type": "a number",
        "int_type": "an integer",
        "invalid-json-value": "JSON-compatible",
        "list_type": "a list",
        "model_attributes_type": "an object",
        "model_type": "an object",
        "string_type": "a string",
    }.get(error_type)


def _missing_contract_field_type(path: str) -> str:
    if path in {"constraints", "expected_output", "expected_output.schema", "input", "usage"}:
        return "an object"
    if path in {
        "constraints.allowed_profiles",
        "constraints.required_permissions",
        "evidence",
    }:
        return "a list"
    return "a string"


def _pydantic_error_path(location: object) -> str:
    parts: list[str] = []
    if isinstance(location, tuple):
        for item in location:
            if isinstance(item, int):
                if parts:
                    parts[-1] = f"{parts[-1]}[{item}]"
                else:
                    parts.append(f"[{item}]")
            else:
                parts.append(str(item))
    return ".".join(parts)


def _reject_empty_items(values: tuple[str, ...], field_name: str) -> None:
    for index, value in enumerate(values):
        if not value.strip():
            raise ValueError(f"agent task {field_name}[{index}] must not be empty")
