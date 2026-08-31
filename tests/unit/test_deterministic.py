from __future__ import annotations

import pytest

from universal_agent.core import (
    ActionId,
    ErrorCode,
    ObservationId,
    ObservationStatus,
    SessionId,
    TaskId,
    ToolCall,
)
from universal_agent.evaluation.deterministic import (
    DeterministicClock,
    DeterministicIdFactory,
    DeterministicRuntimeMode,
    MockToolRuntime,
    MockWorldModel,
    ToolResultScript,
)
from universal_agent.world.models import EntityId


class TestDeterministicClock:
    def test_default_start(self) -> None:
        clock = DeterministicClock()
        t1 = clock.now()
        t2 = clock.now()
        assert t1.tzinfo is not None
        assert t2 > t1

    def test_custom_step(self) -> None:
        from datetime import timedelta

        clock = DeterministicClock(step=timedelta(seconds=5))
        t1 = clock.now()
        t2 = clock.now()
        assert (t2 - t1).total_seconds() == 5


class TestDeterministicIdFactory:
    def test_sequential_ids(self) -> None:
        factory = DeterministicIdFactory()
        id1 = factory.new_id("task")
        id2 = factory.new_id("task")
        assert id1 == "task-0001"
        assert id2 == "task-0002"

    def test_different_prefixes(self) -> None:
        factory = DeterministicIdFactory()
        id1 = factory.new_id("task")
        id2 = factory.new_id("action")
        assert id1 == "task-0001"
        assert id2 == "action-0001"


class TestMockToolRuntime:
    @pytest.mark.asyncio
    async def test_scripted_success(self) -> None:
        mock = MockToolRuntime(
            [
                ToolResultScript("kubectl", "get_pod", output={"status": "Running"}),
            ]
        )
        call = ToolCall(
            action_id=ActionId("a1"),
            tool_name="kubectl",
            capability="get_pod",
            arguments={},
        )
        result = await mock.execute(call)
        assert result.status == ObservationStatus.SUCCEEDED
        assert result.output["status"] == "Running"

    @pytest.mark.asyncio
    async def test_scripted_failure(self) -> None:
        mock = MockToolRuntime(
            [
                ToolResultScript(
                    "kubectl",
                    "get_pod",
                    status=ObservationStatus.FAILED,
                    error="not found",
                    error_code=ErrorCode.UNKNOWN_TOOL,
                ),
            ]
        )
        call = ToolCall(
            action_id=ActionId("a1"),
            tool_name="kubectl",
            capability="get_pod",
            arguments={},
        )
        result = await mock.execute(call)
        assert result.status == ObservationStatus.FAILED
        assert result.error == "not found"

    @pytest.mark.asyncio
    async def test_default_response(self) -> None:
        mock = MockToolRuntime()
        call = ToolCall(
            action_id=ActionId("a1"),
            tool_name="kubectl",
            capability="get_pod",
            arguments={},
        )
        result = await mock.execute(call)
        assert result.status == ObservationStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_call_tracking(self) -> None:
        mock = MockToolRuntime(
            [
                ToolResultScript("kubectl", "get_pod", output={}),
            ]
        )
        call = ToolCall(
            action_id=ActionId("a1"),
            tool_name="kubectl",
            capability="get_pod",
            arguments={},
        )
        await mock.execute(call)
        assert mock.call_count == 1
        assert len(mock.calls) == 1


class TestMockWorldModel:
    def test_seed_fact(self) -> None:
        mock = MockWorldModel()
        session_id = SessionId("s1")
        evidence = mock.seed_fact(session_id, "pod-1", "status", "Running")
        assert evidence.subject == "pod-1"
        assert evidence.claim == "status"
        assert evidence.value == "Running"

    def test_seed_entity(self) -> None:
        mock = MockWorldModel()
        session_id = SessionId("s1")
        entity = mock.seed_entity(session_id, EntityId("pod-1"), "Pod")
        assert entity.kind == "Pod"

    def test_seed_relation(self) -> None:
        mock = MockWorldModel()
        session_id = SessionId("s1")
        rel = mock.seed_relation(session_id, EntityId("pod-1"), "belongs_to", EntityId("ns-1"))
        assert rel.relation == "belongs_to"

    def test_snapshot(self) -> None:
        mock = MockWorldModel()
        session_id = SessionId("s1")
        mock.seed_fact(session_id, "pod-1", "status", "Running")
        mock.seed_entity(session_id, EntityId("pod-1"), "Pod")
        snapshot = mock.snapshot(session_id)
        assert len(snapshot.facts) == 1
        assert len(snapshot.entities) == 1

    def test_snapshot_cache(self) -> None:
        mock = MockWorldModel()
        session_id = SessionId("s1")
        mock.seed_fact(session_id, "pod-1", "status", "Running")
        s1 = mock.snapshot(session_id)
        s2 = mock.snapshot(session_id)
        assert s1 is s2

    def test_cache_invalidation(self) -> None:
        mock = MockWorldModel()
        session_id = SessionId("s1")
        mock.seed_fact(session_id, "pod-1", "status", "Running")
        s1 = mock.snapshot(session_id)
        mock.seed_fact(session_id, "pod-2", "status", "Failed")
        s2 = mock.snapshot(session_id)
        assert s1 is not s2
        assert len(s2.facts) == 2

    def test_forget(self) -> None:
        mock = MockWorldModel()
        session_id = SessionId("s1")
        mock.seed_fact(session_id, "pod-1", "status", "Running")
        mock.forget(session_id)
        snapshot = mock.snapshot(session_id)
        assert len(snapshot.facts) == 0

    def test_apply_fact(self) -> None:
        mock = MockWorldModel()
        from universal_agent.evidence import Evidence, EvidenceId

        session_id = SessionId("s1")
        evidence = Evidence(
            id=EvidenceId("ev-1"),
            session_id=session_id,
            task_id=TaskId(""),
            action_id=ActionId(""),
            observation_id=ObservationId(""),
            subject="pod-1",
            claim="status",
            value="Running",
            source="test",
        )
        result = mock.apply_fact(evidence)
        assert result is True
        result2 = mock.apply_fact(evidence)
        assert result2 is False


class TestDeterministicRuntimeMode:
    def test_context_manager(self) -> None:
        with DeterministicRuntimeMode() as mode:
            assert isinstance(mode.tool_runtime, MockToolRuntime)
            assert isinstance(mode.world_model, MockWorldModel)

    def test_cannot_enter_twice(self) -> None:
        mode = DeterministicRuntimeMode()
        with mode:
            with pytest.raises(RuntimeError, match="already active"):
                mode.__enter__()
