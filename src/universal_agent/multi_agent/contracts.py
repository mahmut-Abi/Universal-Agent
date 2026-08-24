from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import NewType

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


def _agent_task_id(goal: str) -> str:
    normalized = "-".join(word for word in goal.lower().split() if word)
    suffix = utc_now().strftime("%Y%m%d%H%M%S%f")
    return f"agent-task-{normalized[:40] or 'task'}-{suffix}"


def _optional_str(value: object | None) -> JsonValue:
    if value is None:
        return None
    return str(value)


def _reject_empty_items(values: tuple[str, ...], field_name: str) -> None:
    for index, value in enumerate(values):
        if not value.strip():
            raise ValueError(f"agent task {field_name}[{index}] must not be empty")
