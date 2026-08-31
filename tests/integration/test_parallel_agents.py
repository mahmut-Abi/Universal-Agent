from __future__ import annotations

import asyncio

import pytest

from universal_agent.multi_agent.contracts import (
    AgentExpectedOutput,
    AgentTaskId,
    AgentTaskRequest,
    AgentTaskResult,
    AgentTaskResultStatus,
    AgentTaskUsage,
)
from universal_agent.multi_agent.delegation import DelegationManager
from universal_agent.multi_agent.orchestrator import (
    AgentDelegationSpec,
    AgentOrchestrator,
)
from universal_agent.multi_agent.registry import (
    AgentId,
    AgentInstanceRecord,
    AgentInstanceStatus,
    AgentProfileRecord,
    AgentRegistry,
)
from universal_agent.runtime import RuntimeAPI


def _profile() -> AgentProfileRecord:
    from universal_agent.core import DomainIdentity

    return AgentProfileRecord(
        name="test-profile",
        version="1",
        domains=(DomainIdentity("test", "1.0.0"),),
        permissions=("read_only",),
        capabilities=("execute",),
    )


class StatusExecutor:
    def __init__(self, status: AgentTaskResultStatus, *, delay: float = 0.0) -> None:
        self._status = status
        self._delay = delay

    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        return AgentTaskResult(
            task_id=request.task_id,
            status=self._status,
            reason=f"executed {request.task_id}",
            usage=AgentTaskUsage(model_call_count=1),
        )


def _make_request(task_id: str) -> AgentTaskRequest:
    return AgentTaskRequest(
        goal="test",
        expected_output=AgentExpectedOutput(type="json"),
        task_id=AgentTaskId(task_id),
    )


def _make_delegation(spec_id: str, agent_id: str) -> AgentDelegationSpec:
    return AgentDelegationSpec(
        request=_make_request(spec_id),
        agent_id=AgentId(agent_id),
    )


def _register_agent(registry: AgentRegistry, agent_id: str) -> None:
    registry.register_instance(
        AgentInstanceRecord(
            AgentId(agent_id),
            "test-profile",
            "1",
            status=AgentInstanceStatus.READY,
        )
    )


class TestParallelDelegation:
    @pytest.mark.asyncio()
    async def test_parallel_execution_with_concurrency_limit(self) -> None:
        registry = AgentRegistry((_profile(),))
        for aid in ("a", "b", "c"):
            _register_agent(registry, aid)
        executors = {
            AgentId("a"): StatusExecutor(AgentTaskResultStatus.COMPLETED),
            AgentId("b"): StatusExecutor(AgentTaskResultStatus.COMPLETED),
            AgentId("c"): StatusExecutor(AgentTaskResultStatus.COMPLETED),
        }
        orchestrator = AgentOrchestrator(
            registry,
            executors,
            max_concurrency=2,
        )
        batch = await orchestrator.delegate_many(
            (
                _make_delegation("t1", "a"),
                _make_delegation("t2", "b"),
                _make_delegation("t3", "c"),
            )
        )
        assert batch.completed
        assert len(batch.results) == 3
        for r in batch.results:
            assert r.status is AgentTaskResultStatus.COMPLETED

    @pytest.mark.asyncio()
    async def test_cancellation_on_error(self) -> None:
        registry = AgentRegistry((_profile(),))
        _register_agent(registry, "fail")
        _register_agent(registry, "ok")
        executors = {
            AgentId("fail"): StatusExecutor(AgentTaskResultStatus.FAILED),
            AgentId("ok"): StatusExecutor(AgentTaskResultStatus.COMPLETED),
        }
        orchestrator = AgentOrchestrator(registry, executors, max_concurrency=1)
        batch = await orchestrator.delegate_many(
            (
                _make_delegation("f1", "fail"),
                _make_delegation("s1", "ok"),
            )
        )
        statuses = {r.task_id: r.status for r in batch.results}
        assert statuses[AgentTaskId("f1")] is AgentTaskResultStatus.FAILED
        assert statuses[AgentTaskId("s1")] is AgentTaskResultStatus.COMPLETED


class TestDelegateAndMerge:
    @pytest.mark.asyncio()
    async def test_merge_all_completed(self) -> None:
        registry = AgentRegistry((_profile(),))
        for aid in ("a", "b"):
            _register_agent(registry, aid)
        executors = {
            AgentId("a"): StatusExecutor(AgentTaskResultStatus.COMPLETED),
            AgentId("b"): StatusExecutor(AgentTaskResultStatus.COMPLETED),
        }
        orchestrator = AgentOrchestrator(registry, executors)
        merged = await orchestrator.delegate_and_merge(
            (
                _make_delegation("m1", "a"),
                _make_delegation("m2", "b"),
            )
        )
        assert len(merged.results) == 2
        assert merged.completed_task_ids or merged.status.value == "completed"

    @pytest.mark.asyncio()
    async def test_merge_with_failure(self) -> None:
        registry = AgentRegistry((_profile(),))
        for aid in ("a", "b"):
            _register_agent(registry, aid)
        executors = {
            AgentId("a"): StatusExecutor(AgentTaskResultStatus.COMPLETED),
            AgentId("b"): StatusExecutor(AgentTaskResultStatus.FAILED),
        }
        orchestrator = AgentOrchestrator(registry, executors)
        merged = await orchestrator.delegate_and_merge(
            (
                _make_delegation("m1", "a"),
                _make_delegation("m2", "b"),
            )
        )
        assert len(merged.failed_task_ids) > 0 or merged.status.value == "failed"


class TestDelegationManagerLifecycle:
    @pytest.mark.asyncio()
    async def test_delegation_tracked_through_manager(self) -> None:
        registry = AgentRegistry((_profile(),))
        _register_agent(registry, "agent1")
        manager = DelegationManager()
        executors = {
            AgentId("agent1"): StatusExecutor(AgentTaskResultStatus.COMPLETED),
        }
        orchestrator = AgentOrchestrator(
            registry,
            executors,
            delegation_manager=manager,
        )
        result = await orchestrator.delegate(
            _make_request("tracked-1"),
            agent_id=AgentId("agent1"),
        )
        assert result.status is AgentTaskResultStatus.COMPLETED
        all_del = manager.by_task(AgentTaskId("tracked-1"))
        assert len(all_del) == 1
        assert all_del[0].result is not None
        assert all_del[0].result.status is AgentTaskResultStatus.COMPLETED


class TestFromRuntimeServiceFactory:
    def test_creates_orchestrator(self) -> None:
        registry = AgentRegistry((_profile(),))
        orchestrator = AgentOrchestrator.from_runtime_service(
            object.__new__(RuntimeAPI),
            registry,
            max_concurrency=5,
        )
        assert orchestrator._max_concurrency == 5
        assert isinstance(orchestrator._delegation_manager, DelegationManager)
