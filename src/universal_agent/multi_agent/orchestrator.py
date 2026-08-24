from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from universal_agent.core import ErrorCode, ExecutionStatus, Goal, JsonValue, SuccessCriterion, Task
from universal_agent.multi_agent.contracts import (
    AgentTaskId,
    AgentTaskRequest,
    AgentTaskResult,
    AgentTaskResultStatus,
)
from universal_agent.multi_agent.registry import (
    AgentId,
    AgentInstanceRecord,
    AgentInstanceStatus,
    AgentRegistry,
    AgentRegistryError,
)
from universal_agent.runtime import RuntimeAPI


class AgentExecutor(Protocol):
    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult: ...


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


class AgentOrchestrator:
    """Optional Multi-Agent delegation layer above independent Runtime instances."""

    def __init__(
        self,
        registry: AgentRegistry,
        executors: Mapping[AgentId, AgentExecutor] | None = None,
    ) -> None:
        self._registry = registry
        self._executors: dict[AgentId, AgentExecutor] = dict(executors or {})
        self._child_counts: defaultdict[AgentTaskId, int] = defaultdict(int)

    def register_executor(self, agent_id: AgentId, executor: AgentExecutor) -> None:
        self._registry.instance(agent_id)
        self._executors[agent_id] = executor

    async def delegate(
        self,
        request: AgentTaskRequest,
        *,
        agent_id: AgentId | None = None,
    ) -> AgentTaskResult:
        instance = self._select_instance(request, agent_id)
        executor = self._executors.get(instance.agent_id)
        if executor is None:
            raise AgentExecutorNotRegisteredError(
                f"agent executor not registered: {instance.agent_id}"
            )
        self._reserve_child_slot(request)
        return await _execute_agent_task(executor, request)

    async def delegate_many(
        self,
        specs: tuple[AgentDelegationSpec, ...],
    ) -> AgentDelegationBatchResult:
        if not specs:
            raise ValueError("agent delegation batch requires specs")
        _validate_delegation_specs(specs)
        pending = {spec.request.task_id: spec for spec in specs}
        results: dict[AgentTaskId, AgentTaskResult] = {}
        skipped: list[AgentTaskId] = []

        while pending:
            blocked = tuple(
                spec for spec in pending.values() if _has_failed_dependency(spec, results)
            )
            for spec in blocked:
                pending.pop(spec.request.task_id)
                skipped.append(spec.request.task_id)
                results[spec.request.task_id] = rejected_agent_task_result(
                    spec.request,
                    "dependency did not complete: " + _failed_dependency_reason(spec, results),
                    error_code=ErrorCode.INVALID_STATE,
                )
            if blocked:
                continue

            ready = tuple(
                spec for spec in pending.values() if _dependencies_completed(spec, results)
            )
            if not ready:
                if pending:
                    raise AgentDelegationDependencyError(
                        "agent delegation dependencies cannot be resolved"
                    )
                break

            delegated = await asyncio.gather(*(self._delegate_spec(spec) for spec in ready))
            for spec, result in zip(ready, delegated, strict=True):
                pending.pop(spec.request.task_id)
                results[spec.request.task_id] = result

        ordered = tuple(results[spec.request.task_id] for spec in specs)
        status = _batch_status(ordered)
        return AgentDelegationBatchResult(
            status=status,
            results=ordered,
            skipped_task_ids=tuple(skipped),
            reason=_batch_reason(status),
        )

    async def _delegate_spec(self, spec: AgentDelegationSpec) -> AgentTaskResult:
        try:
            return await self.delegate(spec.request, agent_id=spec.agent_id)
        except AgentDelegationError as exc:
            return rejected_agent_task_result(
                spec.request,
                str(exc),
                error_code=ErrorCode.INVALID_STATE,
            )
        except Exception as exc:
            return AgentTaskResult(
                spec.request.task_id,
                AgentTaskResultStatus.FAILED,
                reason=f"agent executor failed: {type(exc).__name__}: {exc}",
                error_code=ErrorCode.UNKNOWN_EXECUTION,
            )

    def _select_instance(
        self,
        request: AgentTaskRequest,
        agent_id: AgentId | None,
    ) -> AgentInstanceRecord:
        if agent_id is not None:
            instance = self._registry.instance(agent_id)
            if instance.status is not AgentInstanceStatus.READY:
                raise NoEligibleAgentError(f"agent instance is not ready: {agent_id}")
            profile = self._registry.profile(instance.profile_name, instance.profile_version)
            if not self._registry.profile_eligible(profile, request):
                raise NoEligibleAgentError(f"agent instance is not eligible: {agent_id}")
            return instance

        eligible = self._registry.eligible_instances(request)
        if not eligible:
            raise NoEligibleAgentError("no eligible agent instance")
        return eligible[0]

    def _reserve_child_slot(self, request: AgentTaskRequest) -> None:
        if request.parent_task_id is None:
            return
        count = self._child_counts[request.parent_task_id]
        if count >= request.constraints.max_children:
            raise AgentDelegationLimitError(
                f"agent task max_children exceeded for parent {request.parent_task_id}"
            )
        self._child_counts[request.parent_task_id] = count + 1


class RuntimeAgentExecutor:
    """Adapter that delegates an AgentTaskRequest to a RuntimeAPI-owned Agent."""

    def __init__(self, runtime_api: RuntimeAPI) -> None:
        self._runtime_api = runtime_api

    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        run = await self._runtime_api.run_goal(
            Goal(
                request.goal,
                _success_criteria(request.expected_output.schema),
            ),
            Task(request.goal, ()),
        )
        diagnostics = await self._runtime_api.get_session_diagnostics(run.result.session_id)
        return AgentTaskResult(
            task_id=request.task_id,
            status=_agent_task_status(run.result.status),
            result={
                "session_id": str(run.result.session_id),
                "goal_id": str(run.result.goal_id),
                "task_id": str(run.result.task_id),
                "iterations": run.result.iterations,
                "reason": run.result.reason,
            },
            evidence_ids=tuple(item.evidence_id for item in diagnostics.evidence),
            reason=run.result.reason,
            session_id=run.result.session_id,
            error_code=run.result.error_code,
        )


def _agent_task_status(status: ExecutionStatus) -> AgentTaskResultStatus:
    if status is ExecutionStatus.COMPLETED:
        return AgentTaskResultStatus.COMPLETED
    if status is ExecutionStatus.CANCELLED:
        return AgentTaskResultStatus.CANCELLED
    if status is ExecutionStatus.WAITING:
        return AgentTaskResultStatus.WAITING
    if status is ExecutionStatus.FAILED:
        return AgentTaskResultStatus.FAILED
    raise AgentRegistryError(f"unsupported runtime execution status: {status}")


def _success_criteria(schema: Mapping[str, JsonValue]) -> tuple[SuccessCriterion, ...]:
    raw = schema.get("success_criteria")
    if not isinstance(raw, list):
        return ()
    criteria: list[SuccessCriterion] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        expected = item.get("expected")
        if isinstance(key, str) and key.strip():
            criteria.append(SuccessCriterion(key, expected))
    return tuple(criteria)


def rejected_agent_task_result(
    request: AgentTaskRequest,
    reason: str,
    *,
    error_code: ErrorCode = ErrorCode.POLICY_DENIED,
) -> AgentTaskResult:
    return AgentTaskResult(
        task_id=request.task_id,
        status=AgentTaskResultStatus.REJECTED,
        reason=reason,
        error_code=error_code,
    )


async def _execute_agent_task(
    executor: AgentExecutor,
    request: AgentTaskRequest,
) -> AgentTaskResult:
    try:
        if request.constraints.max_duration_seconds is None:
            return await executor.execute_agent_task(request)
        return await asyncio.wait_for(
            executor.execute_agent_task(request),
            timeout=request.constraints.max_duration_seconds,
        )
    except TimeoutError:
        return AgentTaskResult(
            request.task_id,
            AgentTaskResultStatus.FAILED,
            reason=(
                "agent task exceeded max_duration_seconds="
                f"{request.constraints.max_duration_seconds}"
            ),
            error_code=ErrorCode.TIMEOUT,
        )


def _validate_delegation_specs(specs: tuple[AgentDelegationSpec, ...]) -> None:
    task_ids = tuple(spec.request.task_id for spec in specs)
    duplicates = _duplicates(task_ids)
    if duplicates:
        raise ValueError("duplicate agent delegation task ids: " + ", ".join(duplicates))
    known = set(task_ids)
    missing = tuple(
        dependency for spec in specs for dependency in spec.depends_on if dependency not in known
    )
    if missing:
        raise ValueError("unknown agent delegation dependencies: " + _format_task_ids(missing))


def _has_failed_dependency(
    spec: AgentDelegationSpec,
    results: Mapping[AgentTaskId, AgentTaskResult],
) -> bool:
    return any(
        task_id in results and results[task_id].status is not AgentTaskResultStatus.COMPLETED
        for task_id in spec.depends_on
    )


def _dependencies_completed(
    spec: AgentDelegationSpec,
    results: Mapping[AgentTaskId, AgentTaskResult],
) -> bool:
    return all(
        task_id in results and results[task_id].status is AgentTaskResultStatus.COMPLETED
        for task_id in spec.depends_on
    )


def _failed_dependency_reason(
    spec: AgentDelegationSpec,
    results: Mapping[AgentTaskId, AgentTaskResult],
) -> str:
    failed = tuple(
        task_id
        for task_id in spec.depends_on
        if task_id in results and results[task_id].status is not AgentTaskResultStatus.COMPLETED
    )
    return _format_task_ids(failed)


def _batch_status(results: tuple[AgentTaskResult, ...]) -> AgentDelegationBatchStatus:
    completed = sum(1 for result in results if result.status is AgentTaskResultStatus.COMPLETED)
    if completed == len(results):
        return AgentDelegationBatchStatus.COMPLETED
    if completed > 0:
        return AgentDelegationBatchStatus.PARTIAL
    return AgentDelegationBatchStatus.FAILED


def _batch_reason(status: AgentDelegationBatchStatus) -> str:
    if status is AgentDelegationBatchStatus.COMPLETED:
        return "all delegated agent tasks completed"
    if status is AgentDelegationBatchStatus.PARTIAL:
        return "some delegated agent tasks did not complete"
    return "no delegated agent tasks completed"


def _duplicates(values: tuple[AgentTaskId, ...]) -> tuple[str, ...]:
    seen: set[AgentTaskId] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(str(value))
        seen.add(value)
    return tuple(sorted(duplicates))


def _format_task_ids(task_ids: tuple[AgentTaskId, ...]) -> str:
    return ", ".join(str(task_id) for task_id in task_ids)
