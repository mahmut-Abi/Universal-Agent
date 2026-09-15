"""Delegation state contracts and codec for the multi-agent orchestrator.

Owns the delegation ledger types (spec / batch result / task state / state),
their JSON payloads, and the typed delegation errors, so the orchestrator can
focus on fan-out/merge control flow. Pure data + codec: no runtime coupling.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated

from pydantic import Field

from universal_agent.core import JsonMapping, JsonValue
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    duplicate_values,
    enum_before_validator,
    parse_payload,
    parse_string,
)
from universal_agent.multi_agent.contracts import (
    AgentTaskId,
    AgentTaskRequest,
    AgentTaskResult,
    agent_task_request_payload,
    agent_task_result_payload,
    decode_agent_task_request,
    decode_agent_task_result,
)
from universal_agent.multi_agent.registry import AgentId

__all__ = [
    "AgentDelegationBatchResult",
    "AgentDelegationBatchStatus",
    "AgentDelegationDependencyError",
    "AgentDelegationError",
    "AgentDelegationLimitError",
    "AgentDelegationSpec",
    "AgentDelegationState",
    "AgentDelegationTaskState",
    "AgentExecutorNotRegisteredError",
    "NoEligibleAgentError",
    "agent_delegation_batch_result_payload",
    "agent_delegation_spec_payload",
    "agent_delegation_state_payload",
    "decode_agent_delegation_batch_result",
    "decode_agent_delegation_spec",
    "decode_agent_delegation_state",
]


class AgentDelegationError(RuntimeError):
    pass


class NoEligibleAgentError(AgentDelegationError):
    pass


class AgentExecutorNotRegisteredError(AgentDelegationError):
    pass


class AgentDelegationLimitError(AgentDelegationError):
    pass


class AgentDelegationDependencyError(AgentDelegationError):
    pass


class AgentDelegationBatchStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


_AgentDelegationBatchStatusPayload = Annotated[
    AgentDelegationBatchStatus,
    enum_before_validator(
        AgentDelegationBatchStatus,
        "status",
        invalid_template="unsupported agent delegation batch status: {value}",
    ),
]


class _AgentDelegationSpecPayload(ConfigPayload):
    request: dict[str, PydanticJsonValue]
    agent_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class _AgentDelegationBatchResultPayload(ConfigPayload):
    status: _AgentDelegationBatchStatusPayload
    reason: str = ""
    skipped_task_ids: list[str] = Field(default_factory=list)
    results: list[dict[str, PydanticJsonValue]] = Field(default_factory=list)


class _AgentDelegationTaskStatePayload(ConfigPayload):
    task_id: str
    child_count: int = 0
    delegation_depth: int | None = None


class _AgentDelegationStatePayload(ConfigPayload):
    tasks: list[_AgentDelegationTaskStatePayload] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AgentDelegationSpec:
    request: AgentTaskRequest
    agent_id: AgentId | None = None
    depends_on: tuple[AgentTaskId, ...] = ()

    def __post_init__(self) -> None:
        if self.request.task_id in self.depends_on:
            raise ValueError("agent delegation spec cannot depend on itself")


@dataclass(frozen=True, slots=True)
class AgentDelegationBatchResult:
    status: AgentDelegationBatchStatus
    results: tuple[AgentTaskResult, ...]
    skipped_task_ids: tuple[AgentTaskId, ...] = ()
    reason: str = ""

    @property
    def completed(self) -> bool:
        return self.status is AgentDelegationBatchStatus.COMPLETED


@dataclass(frozen=True, slots=True)
class AgentDelegationTaskState:
    task_id: AgentTaskId
    child_count: int = 0
    delegation_depth: int | None = None

    def __post_init__(self) -> None:
        if not str(self.task_id).strip():
            raise ValueError("agent delegation task state task_id must not be empty")
        if self.child_count < 0:
            raise ValueError("agent delegation task state child_count must be non-negative")
        if self.delegation_depth is not None and self.delegation_depth < 0:
            raise ValueError("agent delegation task state delegation_depth must be non-negative")


@dataclass(frozen=True, slots=True)
class AgentDelegationState:
    tasks: tuple[AgentDelegationTaskState, ...] = ()

    def __post_init__(self) -> None:
        duplicates = _duplicates(tuple(task.task_id for task in self.tasks))
        if duplicates:
            raise ValueError("duplicate agent delegation state task ids: " + ", ".join(duplicates))


def agent_delegation_spec_payload(spec: AgentDelegationSpec) -> JsonMapping:
    return MappingProxyType(
        {
            "request": dict(agent_task_request_payload(spec.request)),
            "agent_id": _optional_str(spec.agent_id),
            "depends_on": [str(task_id) for task_id in spec.depends_on],
        }
    )


def decode_agent_delegation_spec(payload: JsonMapping) -> AgentDelegationSpec:
    parsed = _parse_payload(_AgentDelegationSpecPayload, payload)
    return AgentDelegationSpec(
        request=decode_agent_task_request(parsed.request),
        agent_id=_optional_agent_id(parsed.agent_id),
        depends_on=tuple(AgentTaskId(value) for value in parsed.depends_on),
    )


def agent_delegation_batch_result_payload(result: AgentDelegationBatchResult) -> JsonMapping:
    return MappingProxyType(
        {
            "status": result.status.value,
            "completed": result.completed,
            "reason": result.reason,
            "skipped_task_ids": [str(task_id) for task_id in result.skipped_task_ids],
            "results": [dict(agent_task_result_payload(item)) for item in result.results],
        }
    )


def decode_agent_delegation_batch_result(payload: JsonMapping) -> AgentDelegationBatchResult:
    parsed = _parse_payload(_AgentDelegationBatchResultPayload, payload)
    return AgentDelegationBatchResult(
        status=parsed.status,
        results=tuple(decode_agent_task_result(item) for item in parsed.results),
        skipped_task_ids=tuple(AgentTaskId(value) for value in parsed.skipped_task_ids),
        reason=parsed.reason,
    )


def agent_delegation_state_payload(state: AgentDelegationState) -> JsonMapping:
    return MappingProxyType(
        {
            "tasks": [
                {
                    "task_id": str(task.task_id),
                    "child_count": task.child_count,
                    "delegation_depth": task.delegation_depth,
                }
                for task in state.tasks
            ],
        }
    )


def decode_agent_delegation_state(payload: JsonMapping) -> AgentDelegationState:
    parsed = _parse_payload(_AgentDelegationStatePayload, payload)
    return AgentDelegationState(
        tasks=tuple(
            AgentDelegationTaskState(
                task_id=AgentTaskId(item.task_id),
                child_count=item.child_count,
                delegation_depth=item.delegation_depth,
            )
            for item in parsed.tasks
        )
    )


def _parse_payload[T: ConfigPayload](payload_type: type[T], payload: JsonMapping) -> T:
    return parse_payload(payload_type, payload)


def _duplicates(values: tuple[AgentTaskId, ...]) -> tuple[str, ...]:
    found = duplicate_values(values)
    return tuple(str(item) for item in found)


def _optional_str(value: object | None) -> JsonValue:
    if value is None:
        return None
    return str(value)


def _optional_agent_id(value: object) -> AgentId | None:
    if value is None:
        return None
    return AgentId(parse_string(value, "agent_id"))
