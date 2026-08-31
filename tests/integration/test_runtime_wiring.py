from __future__ import annotations

import pytest

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainLoader,
    ExecutionStatus,
    Goal,
    InMemoryEventSink,
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
    DecisionContext,
    DomainManifest,
    DomainMetadata,
    ErrorCode,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    JsonMapping,
    RiskLevel,
    SideEffect,
    ToolDefinition,
)
from universal_agent.domain import DomainRuntime, RuntimeComponents
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import EvidenceExtractor
from universal_agent.goals.compiler import DefaultGoalCompiler
from universal_agent.memory import MemoryRecord
from universal_agent.model.router import ModelCandidate, RiskAwareModelRouter
from universal_agent.policy import Policy
from universal_agent.recovery import RecoveryRule
from universal_agent.security.sandbox import LocalRestrictedSandbox, Sandbox
from universal_agent.security.trust import TrustBoundary
from universal_agent.state.event_store import InMemoryEventStore
from universal_agent.tasks import TaskExpander
from universal_agent.tools import Tool
from universal_agent.world import WorldUpdater


class RecordingTool:
    def __init__(self, name: str, capability: str, output: JsonMapping) -> None:
        self.definition = ToolDefinition(
            name,
            name,
            (capability,),
            side_effect=SideEffect.DESTRUCTIVE,
            risk=RiskLevel.HIGH,
        )
        self._output = immutable_json(output)
        self.calls = 0

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        return self._output


class SafeTool:
    def __init__(self, name: str, capability: str, output: JsonMapping) -> None:
        self.definition = ToolDefinition(name, name, (capability,))
        self._output = immutable_json(output)
        self.calls = 0

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        return self._output


class FinishEvaluator:
    name = "finish-evaluator"

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        return EvaluationResult(
            EvaluationStatus.COMPLETED,
            "goal finished",
            self.name,
            immutable_json({}),
            True,
            True,
        )


class HighRiskDomain:
    def __init__(self) -> None:
        self._safe_tool = SafeTool("inspect_safe_tool", "inspect_safe", {"ok": True})
        self._risky_tool = RecordingTool("mutate_risky_tool", "mutate_risky", {"applied": True})
        self.manifest = DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata("highrisk", "1.0.0", "High risk domain"),
            ("Thing",),
            ("inspect_safe", "mutate_risky"),
            (FinishEvaluator.name,),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition("inspect_safe", "Inspect safely", CapabilityCategory.OBSERVATION),
            CapabilityDefinition(
                "mutate_risky",
                "Mutate riskily",
                CapabilityCategory.MUTATION,
                RiskLevel.HIGH,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (self._safe_tool, self._risky_tool)

    def policies(self) -> tuple[Policy, ...]:
        return ()

    def evaluators(self) -> tuple[Evaluator, ...]:
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


class RecordingDecisionEngine:
    def __init__(self, decisions: list[Decision]) -> None:
        self._decisions = list(decisions)
        self.calls = 0

    async def decide(self, context: DecisionContext) -> Decision:
        self.calls += 1
        return self._decisions.pop(0)


class RecordingModelAdapter:
    def __init__(self, decisions: list[Decision], name: str) -> None:
        self._decisions = list(decisions)
        self.name = name
        self.calls = 0

    async def decide(self, context: DecisionContext) -> Decision:
        self.calls += 1
        return self._decisions.pop(0)


def build_components(domain: DomainRuntime) -> RuntimeComponents:
    active = DomainLoader().load(domain)
    return RuntimeBuilder().build(active)


def finish_goal_and_task() -> tuple[Goal, Task]:
    return (
        Goal("Finish the task", (SuccessCriterion("done", True),)),
        Task("Initial task", ("done",)),
    )


def inspect_decision() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "inspect safely",
        capability="inspect_safe",
        target="thing/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("ok",),
    )


def finish_decision() -> Decision:
    return Decision(DecisionType.FINISH, "goal complete")


@pytest.mark.asyncio
async def test_goal_compiler_populates_multiple_initial_tasks() -> None:
    components = build_components(HighRiskDomain())
    event_store = InMemoryEventStore()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter([Decision(DecisionType.FINISH, "done")]),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=event_store,
        goal_compiler=DefaultGoalCompiler(),
    )

    goal = Goal("first line\nsecond line\nthird line", (SuccessCriterion("done", True),))
    result = await runtime.run(goal, Task("Initial task", ("done",)))

    task_created = [
        event for event in event_store.events_for(result.session_id) if event.type == "TaskCreated"
    ]
    assert len(task_created) > 1


@pytest.mark.asyncio
async def test_decision_engine_is_used_instead_of_inline_model() -> None:
    components = build_components(HighRiskDomain())
    engine = RecordingDecisionEngine([inspect_decision(), finish_decision()])
    runtime = AgentRuntime(
        model=ScriptedModelAdapter([]),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=InMemoryEventSink(),
        decision_engine=engine,
    )

    result = await runtime.run(*finish_goal_and_task())

    assert result.status is ExecutionStatus.COMPLETED
    assert engine.calls >= 1


@pytest.mark.asyncio
async def test_model_router_routes_high_risk_to_high_weight_adapter() -> None:
    components = build_components(HighRiskDomain())
    low_adapter = RecordingModelAdapter([inspect_decision(), finish_decision()], "low")
    high_adapter = RecordingModelAdapter([inspect_decision(), finish_decision()], "high")
    router = RiskAwareModelRouter(
        [
            ModelCandidate("low", low_adapter, cost=0.0, risk_tolerance=RiskLevel.LOW),
            ModelCandidate(
                "high",
                high_adapter,
                cost=1.0,
                risk_tolerance=RiskLevel.HIGH,
                weight=10.0,
            ),
        ]
    )
    runtime = AgentRuntime(
        model=low_adapter,
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=InMemoryEventSink(),
        model_router=router,
    )

    result = await runtime.run(*finish_goal_and_task())

    assert result.status is ExecutionStatus.COMPLETED
    assert high_adapter.calls >= 1
    assert low_adapter.calls == 0


@pytest.mark.asyncio
async def test_sandbox_denies_out_of_bounds_action() -> None:
    domain = HighRiskDomain()
    components = build_components(domain)
    sandbox = Sandbox(
        boundary=TrustBoundary(deny_risky=True),
        executor=LocalRestrictedSandbox(),
    )
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                Decision(
                    DecisionType.EXECUTE,
                    "apply risky change",
                    capability="mutate_risky",
                    target="thing/example",
                    arguments=immutable_json({"name": "example"}),
                    expected_observations=("applied",),
                )
            ]
        ),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=InMemoryEventSink(),
        sandbox=sandbox,
    )

    result = await runtime.run(*finish_goal_and_task())

    assert result.error_code is ErrorCode.POLICY_DENIED
    assert domain._risky_tool.calls == 0


@pytest.mark.asyncio
async def test_event_store_receives_runtime_events() -> None:
    components = build_components(HighRiskDomain())
    event_store = InMemoryEventStore()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter([inspect_decision(), finish_decision()]),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=event_store,
    )

    result = await runtime.run(*finish_goal_and_task())

    assert result.status is ExecutionStatus.COMPLETED
    assert len(event_store.events_for(result.session_id)) > 0
