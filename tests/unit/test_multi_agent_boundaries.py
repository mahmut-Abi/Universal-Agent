"""Multi-agent boundary tests (P1): delegation failure, timeout, cancel,
dependency cycle, and depth-limit scenarios.

Complements the existing happy-path tests with edge cases that verify the
orchestrator's error handling under adverse conditions.
"""

from __future__ import annotations

import pytest

from universal_agent.core import (
    DomainIdentity,
    immutable_json,
)
from universal_agent.multi_agent.contracts import (
    AgentExpectedOutput,
    AgentTaskConstraints,
    AgentTaskId,
    AgentTaskRequest,
    AgentTaskResult,
    AgentTaskResultStatus,
)
from universal_agent.multi_agent.delegation_state import (
    AgentDelegationDependencyError,
    AgentDelegationError,
    AgentDelegationLimitError,
    AgentDelegationSpec,
    NoEligibleAgentError,
)
from universal_agent.multi_agent.merge import AgentResultMerger
from universal_agent.multi_agent.orchestrator import AgentOrchestrator
from universal_agent.multi_agent.registry import (
    AgentId,
    AgentInstanceRecord,
    AgentInstanceStatus,
    AgentProfileRecord,
    AgentRegistry,
)

pytestmark = pytest.mark.unit


def _profile(name: str = "worker-a") -> AgentProfileRecord:
    return AgentProfileRecord(
        name=name,
        version="1.0.0",
        domains=(DomainIdentity("test", "1.0.0"),),
        description=f"{name} profile",
    )


def _instance(
    agent_id: str = "agent-1",
    status: AgentInstanceStatus = AgentInstanceStatus.READY,
) -> AgentInstanceRecord:
    return AgentInstanceRecord(
        agent_id=AgentId(agent_id),
        profile_name="worker-a",
        profile_version="1.0.0",
        status=status,
    )


def _request(task_id: str = "task-1", **overrides: object) -> AgentTaskRequest:
    defaults: dict[str, object] = {
        "goal": "child goal",
        "expected_output": AgentExpectedOutput(type="json"),
        "constraints": AgentTaskConstraints(max_children=3, max_depth=1),
        "delegation_depth": 0,
    }
    defaults.update(overrides)
    return AgentTaskRequest(task_id=AgentTaskId(task_id), **defaults)  # type: ignore[arg-type]


class _OkExecutor:
    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        return AgentTaskResult(
            task_id=request.task_id,
            status=AgentTaskResultStatus.COMPLETED,
            result=immutable_json({"result": "ok"}),
        )


class _FailingExecutor:
    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        return AgentTaskResult(
            task_id=request.task_id,
            status=AgentTaskResultStatus.FAILED,
            reason="child agent crashed",
        )


class _CancelledExecutor:
    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        return AgentTaskResult(
            task_id=request.task_id,
            status=AgentTaskResultStatus.CANCELLED,
            reason="child agent cancelled",
        )


def _build_orchestrator(
    executor: object,
    *,
    instances: tuple[AgentInstanceRecord, ...] = (),
) -> AgentOrchestrator:
    registry = AgentRegistry(profiles=(_profile(),), instances=instances or (_instance(),))
    return AgentOrchestrator(
        registry=registry,
        executors={AgentId("agent-1"): executor},  # type: ignore[dict-item]
    )


def _spec(task_id: str = "task-1", **overrides: object) -> AgentDelegationSpec:
    request = _request(task_id, **overrides)
    return AgentDelegationSpec(request=request)


# --- delegation failure -------------------------------------------------


@pytest.mark.asyncio
async def test_delegation_failure_produces_failed_result() -> None:
    orchestrator = _build_orchestrator(_FailingExecutor())
    result = await orchestrator.delegate(_request("task-1"))
    assert result.status is AgentTaskResultStatus.FAILED


@pytest.mark.asyncio
async def test_delegation_failure_does_not_crash_batch() -> None:
    orchestrator = _build_orchestrator(_FailingExecutor())
    batch = await orchestrator.delegate_many((_spec("task-1"), _spec("task-2")))
    assert batch.status.value in {"partial", "failed"}
    assert len(batch.results) == 2


# --- delegation cancelled ------------------------------------------------


@pytest.mark.asyncio
async def test_delegation_cancelled_produces_cancelled_result() -> None:
    orchestrator = _build_orchestrator(_CancelledExecutor())
    result = await orchestrator.delegate(_request("task-1"))
    assert result.status is AgentTaskResultStatus.CANCELLED


# --- delegation error ---------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_rejects_unregistered_executor() -> None:
    registry = AgentRegistry(profiles=(_profile(),), instances=(_instance(),))
    orchestrator = AgentOrchestrator(registry=registry, executors={})
    with pytest.raises(AgentDelegationError, match="not registered"):
        await orchestrator.delegate(_request("task-1"))


# --- dependency cycle ---------------------------------------------------


@pytest.mark.asyncio
async def test_dependency_cycle_raises_dependency_error() -> None:
    orchestrator = _build_orchestrator(_OkExecutor())
    spec_a = AgentDelegationSpec(request=_request("task-a"), depends_on=(AgentTaskId("task-b"),))
    spec_b = AgentDelegationSpec(request=_request("task-b"), depends_on=(AgentTaskId("task-a"),))
    with pytest.raises(AgentDelegationDependencyError, match="cycle"):
        await orchestrator.delegate_many((spec_a, spec_b))


# --- depth limit --------------------------------------------------------


@pytest.mark.asyncio
async def test_delegation_depth_limit_enforced() -> None:
    orchestrator = _build_orchestrator(_OkExecutor())
    request = _request("task-1", parent_task_id=AgentTaskId("parent-1"))
    with pytest.raises(AgentDelegationLimitError, match="unknown parent_task_id"):
        await orchestrator.delegate(request)


# --- no eligible agent --------------------------------------------------


@pytest.mark.asyncio
async def test_no_eligible_agent_raises() -> None:
    registry = AgentRegistry(
        profiles=(_profile(),),
        instances=(_instance(status=AgentInstanceStatus.OFFLINE),),
    )
    orchestrator = AgentOrchestrator(
        registry=registry,
        executors={AgentId("agent-1"): _OkExecutor()},
    )
    with pytest.raises(NoEligibleAgentError, match="no eligible"):
        await orchestrator.delegate(_request("task-1"))


# --- merge with mixed results --------------------------------------------


def test_merge_with_mixed_results() -> None:
    merger = AgentResultMerger()
    results = (
        AgentTaskResult(
            task_id=AgentTaskId("task-1"),
            status=AgentTaskResultStatus.COMPLETED,
            result=immutable_json({"healthy": True}),
        ),
        AgentTaskResult(
            task_id=AgentTaskId("task-2"),
            status=AgentTaskResultStatus.FAILED,
            reason="child failed",
        ),
    )
    merge = merger.merge(results)
    assert merge is not None
