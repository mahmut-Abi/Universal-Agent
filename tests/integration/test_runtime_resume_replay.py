from __future__ import annotations

import pytest

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainLoader,
    ExecutionStatus,
    Goal,
    InMemoryStateStore,
    RuntimeBuilder,
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.context import DomainContextProvider
from universal_agent.core import (
    CapabilityCategory,
    CapabilityDefinition,
    DomainManifest,
    DomainMetadata,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    JsonMapping,
    ToolDefinition,
)
from universal_agent.domain import DomainRuntime, RuntimeComponents
from universal_agent.evidence import EvidenceExtractor
from universal_agent.memory import MemoryRecord
from universal_agent.policy import Policy
from universal_agent.recovery import RecoveryRule
from universal_agent.state import EventSourcedSessionStore
from universal_agent.state.event_store import InMemoryEventStore
from universal_agent.tasks import TaskExpander
from universal_agent.tools import Tool
from universal_agent.world import WorldUpdater


class FinishEvaluator:
    name = "finish-evaluator"

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        return EvaluationResult(
            EvaluationStatus.COMPLETED,
            "goal finished",
            self.name,
            {},
            True,
            True,
        )


class SafeTool:
    def __init__(self, name: str, capability: str, output: JsonMapping) -> None:
        self.definition = ToolDefinition(name, name, (capability,))
        self._output = output
        self.calls = 0

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        return self._output


class HighRiskDomain:
    def __init__(self) -> None:
        self._safe_tool = SafeTool("inspect_safe_tool", "inspect_safe", {"ok": True})
        self.manifest = DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata("highrisk", "1.0.0", "High risk domain"),
            ("Thing",),
            ("inspect_safe",),
            (FinishEvaluator.name,),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition("inspect_safe", "Inspect safely", CapabilityCategory.OBSERVATION),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (self._safe_tool,)

    def policies(self) -> tuple[Policy, ...]:
        return ()

    def evaluators(self) -> tuple[FinishEvaluator, ...]:
        return (FinishEvaluator(),)

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return ()

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return ()

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return ()

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return ()

    def recovery_rules(self) -> tuple[RecoveryRule, ...]:
        return ()

    def memories(self) -> tuple[MemoryRecord, ...]:
        return ()


def build_components(domain: DomainRuntime) -> RuntimeComponents:
    active = DomainLoader().load(domain)
    return RuntimeBuilder().build(active)


def _decisions() -> list[Decision]:
    inspect = Decision(
        DecisionType.EXECUTE,
        "inspect safely",
        capability="inspect_safe",
        target="thing/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("ok",),
    )
    return [inspect, Decision(DecisionType.FINISH, "goal complete")]


@pytest.mark.asyncio
async def test_session_snapshot_is_rebuilt_from_event_store_after_loss() -> None:
    components = build_components(HighRiskDomain())
    event_store = InMemoryEventStore()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(_decisions()),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=event_store,
    )

    result = await runtime.run(
        Goal("Finish the task", (SuccessCriterion("done", True),)),
        Task("Initial task", ("done",)),
    )
    assert result.status is ExecutionStatus.COMPLETED
    session_id = result.session_id

    original = await runtime._state_store.load_session(session_id)
    assert len(event_store.events_for(session_id)) > 0

    # Simulate total loss of the snapshot store while the event journal survives.
    runtime._state_store = EventSourcedSessionStore(InMemoryStateStore(), event_store)

    rebuilt = await runtime._state_store.load_session(session_id)
    assert rebuilt.state.session_id == session_id
    assert rebuilt.state.goal.description == original.state.goal.description
    assert len(rebuilt.task_graph.nodes) == len(original.task_graph.nodes)


@pytest.mark.asyncio
async def test_pause_resume_cancel_operate_on_rebuilt_session() -> None:
    components = build_components(HighRiskDomain())
    event_store = InMemoryEventStore()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(_decisions()),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=event_store,
    )

    result = await runtime.run(
        Goal("Finish the task", (SuccessCriterion("done", True),)),
        Task("Initial task", ("done",)),
    )
    session_id = result.session_id
    assert result.status is ExecutionStatus.COMPLETED
    assert len(event_store.events_for(session_id)) > 0

    # Simulate a new runtime process whose snapshot store was lost but the
    # durable event journal (event_store) survived. The session must be rebuilt
    # from the journal for Resume/Pause/Cancel instead of raising StateNotFound.
    recovered = AgentRuntime(
        model=ScriptedModelAdapter(_decisions()),
        state_store=EventSourcedSessionStore(InMemoryStateStore(), event_store),
        components=components,
        event_sink=event_store,
    )

    # A completed session cannot pause/resume/cancel, but crucially the runtime
    # must load the session from the event journal instead of raising
    # StateNotFound -- proving Resume/Pause/Cancel are now event-source backed.
    paused = await recovered.pause(session_id)
    assert paused.status is ExecutionStatus.FAILED
    resumed = await recovered.resume(session_id)
    assert resumed.status is ExecutionStatus.FAILED
    cancelled = await recovered.cancel(session_id)
    assert cancelled.status is ExecutionStatus.FAILED
