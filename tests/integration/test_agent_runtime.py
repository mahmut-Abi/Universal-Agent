from __future__ import annotations

import pytest

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainLoader,
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
    DomainManifest,
    DomainMetadata,
    ErrorCode,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    ExecutionStatus,
    GoalStatus,
    JsonMapping,
    TaskStatus,
    ToolDefinition,
)
from universal_agent.domain import DomainComposition
from universal_agent.domains.kubernetes import KubernetesDomain
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import EvidenceExtractor
from universal_agent.memory import MemoryRecord
from universal_agent.policy import Policy
from universal_agent.recovery import RecoveryRule
from universal_agent.tasks import TaskExpander
from universal_agent.tools import Tool
from universal_agent.world import WorldUpdater


class FakeKubernetesBackend:
    def __init__(self, observations: list[bool]) -> None:
        self._observations = iter(observations)
        self.calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        return immutable_json({"healthy": next(self._observations)})


class StaticTool:
    def __init__(
        self,
        name: str,
        capability: str,
        output: JsonMapping,
    ) -> None:
        self.definition = ToolDefinition(name, name, (capability,))
        self._output = output

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return self._output


class CriterionEvaluator:
    def __init__(self, name: str, criterion: str) -> None:
        self.name = name
        self._criterion = criterion

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        complete = context.satisfied_criteria.get(self._criterion) is True
        matched = {self._criterion: True} if complete else {}
        return EvaluationResult(
            EvaluationStatus.COMPLETED if complete else EvaluationStatus.INCOMPLETE,
            f"{self._criterion} satisfied" if complete else f"{self._criterion} missing",
            self.name,
            immutable_json(matched),
            complete,
            complete,
        )


class TaskOnlyEvaluator:
    name = "task-only"

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        return EvaluationResult(
            EvaluationStatus.COMPLETED,
            "task is complete but goal is not",
            self.name,
            immutable_json({"task_ready": True}),
            task_completed=True,
            goal_completed=False,
        )


class SyntheticDomain:
    def __init__(
        self,
        *,
        name: str,
        capability: str,
        tool_name: str,
        evaluator: Evaluator,
        output: JsonMapping,
    ) -> None:
        self.manifest = DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata(name, "1.0.0", name),
            ("Thing",),
            (capability,),
            (evaluator.name,),
        )
        self._capability = capability
        self._tool = StaticTool(tool_name, capability, output)
        self._evaluator = evaluator

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                self._capability,
                self._capability,
                CapabilityCategory.OBSERVATION,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (self._tool,)

    def policies(self) -> tuple[Policy, ...]:
        return ()

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (self._evaluator,)

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


def execute_probe() -> Decision:
    return Decision(
        type=DecisionType.EXECUTE,
        reason="Observe current workload health",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def finish() -> Decision:
    return Decision(type=DecisionType.FINISH, reason="Required health evidence is present")


def build_runtime(
    decisions: list[Decision],
    observations: list[bool],
    *,
    max_iterations: int = 10,
) -> tuple[
    AgentRuntime,
    ScriptedModelAdapter,
    InMemoryStateStore,
    InMemoryEventSink,
    FakeKubernetesBackend,
]:
    backend = FakeKubernetesBackend(observations)
    active = DomainLoader().load(KubernetesDomain(backend))
    components = RuntimeBuilder().build(active)
    model = ScriptedModelAdapter(decisions)
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=model,
        state_store=store,
        components=components,
        event_sink=events,
        max_iterations=max_iterations,
    )
    return runtime, model, store, events, backend


def health_goal_and_task() -> tuple[Goal, Task]:
    return (
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )


@pytest.mark.asyncio
async def test_normal_loop_requires_evaluator_before_finish() -> None:
    runtime, model, store, events, backend = build_runtime(
        [execute_probe(), execute_probe(), finish()],
        [False, True],
    )
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    state = await store.load(result.session_id)

    assert result.status is ExecutionStatus.COMPLETED
    assert result.iterations == 3
    assert backend.calls == 2
    assert state.goal.status is GoalStatus.COMPLETED
    assert state.current_task.status is TaskStatus.COMPLETED
    assert model.contexts[0].capabilities[0].name == "inspect_cluster"
    assert not hasattr(model.contexts[0], "tools")
    event_types = [event.type for event in events.events]
    assert event_types.count("EvaluationCompleted") == 2
    assert event_types[-1] == "GoalCompleted"
    assert all(event.session_id == result.session_id for event in events.events)
    resolved = next(event for event in events.events if event.type == "CapabilityResolved")
    started = next(event for event in events.events if event.type == "ActionStarted")
    assert resolved.data["domain"] == "kubernetes"
    assert resolved.data["domain_version"] == "0.1.0"
    assert resolved.data["attempt"] == 1
    assert isinstance(resolved.data["parameters_hash"], str)
    assert len(resolved.data["parameters_hash"]) == 64
    assert resolved.data["idempotency_key"] == (
        f"{result.session_id}:{result.task_id}:{resolved.data['parameters_hash'][:16]}"
    )
    assert started.data["domain"] == "kubernetes"
    assert started.data["domain_version"] == "0.1.0"
    assert started.data["attempt"] == resolved.data["attempt"]
    assert started.data["parameters_hash"] == resolved.data["parameters_hash"]
    assert started.data["idempotency_key"] == resolved.data["idempotency_key"]


@pytest.mark.asyncio
async def test_finish_is_rejected_without_evaluation() -> None:
    runtime, _, store, events, _ = build_runtime([finish()], [])
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    state = await store.load(result.session_id)

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.INVALID_STATE
    assert state.goal.status is GoalStatus.FAILED
    assert events.events[-1].type == "GoalFailed"


@pytest.mark.asyncio
async def test_finish_requires_goal_completed_evaluation_flag() -> None:
    domain = DomainLoader().load(
        SyntheticDomain(
            name="task-only",
            capability="inspect_task",
            tool_name="inspect_task_tool",
            evaluator=TaskOnlyEvaluator(),
            output=immutable_json({"task_ready": True}),
        )
    )
    components = RuntimeBuilder().build(domain)
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                Decision(
                    DecisionType.EXECUTE,
                    "Inspect task readiness",
                    capability="inspect_task",
                    expected_observations=("task_ready",),
                ),
                finish(),
            ]
        ),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=events,
    )

    result = await runtime.run(
        Goal("Verify task and goal", (SuccessCriterion("goal_ready", True),)),
        Task("Inspect task", ("task_ready",)),
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.INVALID_STATE
    assert any(event.type == "EvaluationCompleted" for event in events.events)
    assert events.events[-1].type == "GoalFailed"


@pytest.mark.asyncio
async def test_multi_domain_evaluator_routes_by_action_domain() -> None:
    loader = DomainLoader()
    alpha = loader.load(
        SyntheticDomain(
            name="alpha",
            capability="inspect_alpha",
            tool_name="alpha_inspect",
            evaluator=CriterionEvaluator("alpha-evaluator", "alpha_ready"),
            output=immutable_json({"alpha_ready": True}),
        )
    )
    beta = loader.load(
        SyntheticDomain(
            name="beta",
            capability="inspect_beta",
            tool_name="beta_inspect",
            evaluator=CriterionEvaluator("beta-evaluator", "beta_ready"),
            output=immutable_json({"beta_ready": True}),
        )
    )
    components = RuntimeBuilder().build(DomainComposition((alpha, beta)))
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                Decision(
                    DecisionType.EXECUTE,
                    "Inspect beta readiness",
                    capability="inspect_beta",
                    expected_observations=("beta_ready",),
                ),
                finish(),
            ]
        ),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=events,
    )

    result = await runtime.run(
        Goal("Verify beta", (SuccessCriterion("beta_ready", True),)),
        Task("Inspect beta", ("beta_ready",)),
    )
    evaluation_event = next(event for event in events.events if event.type == "EvaluationCompleted")

    assert result.status is ExecutionStatus.COMPLETED
    assert evaluation_event.data["evaluator"] == "beta-evaluator"
    assert events.events[-1].type == "GoalCompleted"


@pytest.mark.asyncio
async def test_unknown_capability_fails_before_action() -> None:
    decision = Decision(
        DecisionType.EXECUTE,
        "Inspect",
        capability="missing",
        expected_observations=("healthy",),
    )
    runtime, _, _, events, backend = build_runtime([decision], [])
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)

    assert result.error_code is ErrorCode.UNKNOWN_CAPABILITY
    assert backend.calls == 0
    assert not any(event.type == "ActionStarted" for event in events.events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "message"),
    [
        (Decision(DecisionType.WAIT, "External work is pending"), None),
        (
            Decision(
                DecisionType.ASK_USER,
                "A required value is missing",
                message="Which target should be inspected?",
            ),
            "Which target should be inspected?",
        ),
    ],
)
async def test_wait_and_ask_user_pause_runtime(decision: Decision, message: str | None) -> None:
    runtime, _, store, _, _ = build_runtime([decision], [])
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    state = await store.load(result.session_id)
    assert result.status is ExecutionStatus.WAITING
    assert result.user_message == message
    assert state.goal.status is GoalStatus.WAITING
    assert state.current_task.status is TaskStatus.WAITING


@pytest.mark.asyncio
async def test_iteration_limit_is_runtime_owned() -> None:
    runtime, _, _, _, _ = build_runtime(
        [execute_probe(), execute_probe()],
        [False, False],
        max_iterations=2,
    )
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    assert result.error_code is ErrorCode.ITERATION_LIMIT
    assert result.iterations == 2


@pytest.mark.asyncio
async def test_invalid_decision_is_rejected_before_resolution() -> None:
    invalid = Decision(DecisionType.EXECUTE, "Inspect")
    runtime, _, _, events, _ = build_runtime([invalid], [])
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    assert result.error_code is ErrorCode.VALIDATION_ERROR
    assert not any(event.type == "CapabilityResolved" for event in events.events)
