from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
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
        return await executor.execute_agent_task(request)

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
