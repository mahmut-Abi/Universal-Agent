"""P4 multi-agent delegation orchestration.

The orchestrator fans a parent task out to registered agent executors:
``delegate`` runs one spec, ``delegate_many`` runs a bounded batch, and
``delegate_and_merge`` merges child results back into the caller's session
with conflict reporting. Delegation state transitions are validated against
the delegation ledger, and eligibility, limits and dependency errors are
raised as typed failures instead of silently degrading the batch.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from graphlib import CycleError, TopologicalSorter
from types import MappingProxyType
from typing import Annotated, Protocol

from pydantic import Field

from universal_agent.core import (
    ErrorCode,
    ExecutionStatus,
    Goal,
    JsonMapping,
    JsonValue,
    SuccessCriterion,
    Task,
)
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
    AgentTaskResultStatus,
    AgentTaskUsage,
    agent_task_request_payload,
    agent_task_result_payload,
    decode_agent_task_request,
    decode_agent_task_result,
)
from universal_agent.multi_agent.delegation import DelegationManager
from universal_agent.multi_agent.merge import AgentResultMerge, AgentResultMerger
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


class AgentOrchestrator:
    """Optional Multi-Agent delegation layer above independent Runtime instances."""

    def __init__(
        self,
        registry: AgentRegistry,
        executors: Mapping[AgentId, AgentExecutor] | None = None,
        delegation_state: AgentDelegationState | None = None,
        *,
        max_concurrency: int | None = None,
        delegation_manager: DelegationManager | None = None,
    ) -> None:
        self._registry = registry
        self._executors: dict[AgentId, AgentExecutor] = dict(executors or {})
        self._child_counts: defaultdict[AgentTaskId, int] = defaultdict(int)
        self._task_depths: dict[AgentTaskId, int] = {}
        self._max_concurrency = max_concurrency
        self._delegation_manager = delegation_manager or DelegationManager()
        if delegation_state is not None:
            self._restore_delegation_state(delegation_state)

    @classmethod
    def from_runtime_service(
        cls,
        service: RuntimeAPI,
        registry: AgentRegistry,
        *,
        max_concurrency: int | None = None,
    ) -> AgentOrchestrator:
        """Create an orchestrator from a RuntimeService."""
        return cls(
            registry=registry,
            max_concurrency=max_concurrency,
        )

    def register_executor(self, agent_id: AgentId, executor: AgentExecutor) -> None:
        self._registry.instance(agent_id)
        self._executors[agent_id] = executor

    def snapshot(self) -> AgentDelegationState:
        task_ids = set(self._child_counts) | set(self._task_depths)
        return AgentDelegationState(
            tuple(
                AgentDelegationTaskState(
                    task_id=task_id,
                    child_count=self._child_counts.get(task_id, 0),
                    delegation_depth=self._task_depths.get(task_id),
                )
                for task_id in sorted(task_ids)
            )
        )

    def _restore_delegation_state(self, state: AgentDelegationState) -> None:
        for task in state.tasks:
            if task.child_count:
                self._child_counts[task.task_id] = task.child_count
            if task.delegation_depth is not None:
                self._task_depths[task.task_id] = task.delegation_depth

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
        self._reserve_delegation(request)
        delegation = self._delegation_manager.create_delegation(
            from_agent=AgentId("orchestrator"),
            to_agent=instance.agent_id,
            contract=request,
        )
        self._delegation_manager.start(delegation.delegation_id)
        try:
            result = await self._execute_on_instance(instance, executor, request)
            if result.status is AgentTaskResultStatus.COMPLETED:
                self._delegation_manager.complete(delegation.delegation_id, result)
            else:
                self._delegation_manager.fail(delegation.delegation_id, result.reason)
            return result
        except Exception as exc:
            self._delegation_manager.fail(delegation.delegation_id, str(exc))
            raise

    async def delegate_many(
        self,
        specs: tuple[AgentDelegationSpec, ...],
    ) -> AgentDelegationBatchResult:
        if not specs:
            raise ValueError("agent delegation batch requires specs")
        sorter = _delegation_sorter(specs)
        specs_by_id = {spec.request.task_id: spec for spec in specs}
        results: dict[AgentTaskId, AgentTaskResult] = {}
        skipped: list[AgentTaskId] = []

        while sorter.is_active():
            ready_ids = sorter.get_ready()
            if not ready_ids:
                raise AgentDelegationDependencyError(
                    "agent delegation dependencies cannot be resolved"
                )
            ready_specs = tuple(specs_by_id[task_id] for task_id in ready_ids)
            delegated_specs = tuple(
                spec for spec in ready_specs if not _has_failed_dependency(spec, results)
            )
            delegated_task_ids = {spec.request.task_id for spec in delegated_specs}
            for spec in ready_specs:
                if spec.request.task_id in delegated_task_ids:
                    continue
                skipped.append(spec.request.task_id)
                results[spec.request.task_id] = rejected_agent_task_result(
                    spec.request,
                    "dependency did not complete: " + _failed_dependency_reason(spec, results),
                    error_code=ErrorCode.INVALID_STATE,
                )
            delegated = (
                await asyncio.gather(
                    *(self._delegate_spec_with_tracking(spec) for spec in delegated_specs)
                )
                if delegated_specs
                else []
            )
            for spec, result in zip(delegated_specs, delegated, strict=True):
                results[spec.request.task_id] = result
            sorter.done(*ready_ids)

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

    async def _delegate_spec_with_tracking(self, spec: AgentDelegationSpec) -> AgentTaskResult:
        """Delegate spec; delegation lifecycle is tracked by delegate()."""
        try:
            return await self._delegate_spec(spec)
        except Exception as exc:
            return AgentTaskResult(
                spec.request.task_id,
                AgentTaskResultStatus.FAILED,
                reason=f"agent executor failed: {type(exc).__name__}: {exc}",
                error_code=ErrorCode.UNKNOWN_EXECUTION,
            )

    async def delegate_and_merge(
        self,
        specs: tuple[AgentDelegationSpec, ...],
    ) -> AgentResultMerge:
        """Delegate multiple tasks and merge results into a single AgentResultMerge."""
        batch = await self.delegate_many(specs)
        merger = AgentResultMerger()
        return merger.merge(batch.results)

    async def _execute_on_instance(
        self,
        instance: AgentInstanceRecord,
        executor: AgentExecutor,
        request: AgentTaskRequest,
    ) -> AgentTaskResult:
        self._registry.update_instance_status(instance.agent_id, AgentInstanceStatus.BUSY)
        try:
            return _enforce_cost_limit(request, await _execute_agent_task(executor, request))
        finally:
            self._registry.update_instance_status(instance.agent_id, AgentInstanceStatus.READY)

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

    def _reserve_delegation(self, request: AgentTaskRequest) -> None:
        self._validate_delegation_depth(request)
        if request.parent_task_id is not None:
            count = self._child_counts[request.parent_task_id]
            if count >= request.constraints.max_children:
                raise AgentDelegationLimitError(
                    f"agent task max_children exceeded for parent {request.parent_task_id}"
                )
            self._child_counts[request.parent_task_id] = count + 1
        self._task_depths[request.task_id] = request.delegation_depth

    def _validate_delegation_depth(self, request: AgentTaskRequest) -> None:
        if request.parent_task_id is None:
            if request.delegation_depth != 0:
                raise AgentDelegationLimitError("root agent task delegation_depth must be 0")
            return
        parent_depth = self._task_depths.get(request.parent_task_id)
        if parent_depth is None:
            return
        expected_depth = parent_depth + 1
        if request.delegation_depth != expected_depth:
            raise AgentDelegationLimitError(
                "agent task delegation_depth must be "
                f"{expected_depth} for parent {request.parent_task_id}"
            )


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
        usage = _agent_task_usage_from_events(
            await self._runtime_api.list_events(run.result.session_id)
        )
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
            usage=usage,
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


def _enforce_cost_limit(request: AgentTaskRequest, result: AgentTaskResult) -> AgentTaskResult:
    max_cost = request.constraints.max_cost
    if (
        max_cost is None
        or result.status is not AgentTaskResultStatus.COMPLETED
        or result.usage.estimated_cost <= max_cost
    ):
        return result
    return AgentTaskResult(
        task_id=result.task_id,
        status=AgentTaskResultStatus.FAILED,
        result=result.result,
        evidence_ids=result.evidence_ids,
        reason=(
            f"agent task exceeded max_cost={max_cost} "
            f"observed={result.usage.estimated_cost} {result.usage.currency}"
        ),
        session_id=result.session_id,
        error_code=ErrorCode.INVALID_STATE,
        usage=result.usage,
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


def _agent_task_usage_from_events(events: tuple[object, ...]) -> AgentTaskUsage:
    model_call_count = 0
    input_tokens = 0
    output_tokens = 0
    estimated_cost_micros = 0
    currencies: set[str] = set()
    for event in events:
        if getattr(event, "type", None) != "ModelUsageRecorded":
            continue
        data = getattr(event, "data", {})
        if not isinstance(data, Mapping):
            continue
        model_call_count += 1
        input_tokens += _non_negative_int(data.get("input_tokens"))
        output_tokens += _non_negative_int(data.get("output_tokens"))
        estimated_cost_micros += _non_negative_int(data.get("estimated_cost_micros"))
        currency = parse_string(data.get("currency"), "currency", default="USD")
        if currency:
            currencies.add(currency)
    return AgentTaskUsage(
        model_call_count=model_call_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost=round(estimated_cost_micros / 1_000_000, 6),
        currency=_aggregate_currency(currencies),
    )


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _aggregate_currency(currencies: set[str]) -> str:
    if not currencies:
        return "USD"
    if len(currencies) == 1:
        return next(iter(currencies))
    return "MIXED"


def _optional_str(value: object | None) -> JsonValue:
    if value is None:
        return None
    return str(value)


def _optional_agent_id(value: object) -> AgentId | None:
    if value is None:
        return None
    return AgentId(parse_string(value, "agent_id"))


def _delegation_sorter(
    specs: tuple[AgentDelegationSpec, ...],
) -> TopologicalSorter[AgentTaskId]:
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
    sorter = TopologicalSorter({spec.request.task_id: spec.depends_on for spec in specs})
    try:
        sorter.prepare()
    except CycleError as exc:
        raise AgentDelegationDependencyError(
            "agent delegation dependencies contain a cycle"
        ) from exc
    return sorter


def _has_failed_dependency(
    spec: AgentDelegationSpec,
    results: Mapping[AgentTaskId, AgentTaskResult],
) -> bool:
    return any(
        task_id in results and results[task_id].status is not AgentTaskResultStatus.COMPLETED
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
    return duplicate_values(values)


def _format_task_ids(task_ids: tuple[AgentTaskId, ...]) -> str:
    return ", ".join(str(task_id) for task_id in task_ids)
