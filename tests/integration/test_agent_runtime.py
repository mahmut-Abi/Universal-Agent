from __future__ import annotations

import asyncio

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
    PolicyEffect,
    RuntimeEvent,
    SideEffect,
    TaskStatus,
    ToolDefinition,
)
from universal_agent.domain import DomainComposition
from universal_agent.domains.kubernetes import KubernetesDomain
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import EvidenceExtractor
from universal_agent.memory import MemoryRecord
from universal_agent.policy import Policy, PolicyRule
from universal_agent.recovery import FailureCategory, RecoveryRule, RecoveryStrategy
from universal_agent.runtime.idempotency import IdempotencyKey
from universal_agent.state import SessionSnapshot
from universal_agent.tasks import TaskExpander, TaskGraphSnapshot
from universal_agent.tools import Tool
from universal_agent.world import WorldUpdater

pytestmark = pytest.mark.asyncio


class FakeKubernetesBackend:
    def __init__(self, observations: list[bool]) -> None:
        self._observations = iter(observations)
        self.calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        return immutable_json({"healthy": next(self._observations)})


class RecordingCommitStore(InMemoryStateStore):
    def __init__(self, events: InMemoryEventSink) -> None:
        super().__init__()
        self._events = events
        self.committed_event_types: list[str] = []

    async def commit_session_event(
        self,
        snapshot: SessionSnapshot,
        event: RuntimeEvent,
    ) -> None:
        self.committed_event_types.append(event.type)
        await self.save_session(snapshot)
        await self._events.emit(event)


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


class SequenceTool:
    def __init__(
        self,
        name: str,
        capability: str,
        outputs: list[JsonMapping],
    ) -> None:
        self.definition = ToolDefinition(name, name, (capability,))
        self._outputs = iter(outputs)
        self.calls = 0

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        return next(self._outputs)


class SlowDecisionAdapter:
    def __init__(self, decision: Decision, *, delay_seconds: float = 0.02) -> None:
        self._decision = decision
        self._delay_seconds = delay_seconds
        self.contexts: list[object] = []

    async def decide(self, context: object) -> Decision:
        self.contexts.append(context)
        await asyncio.sleep(self._delay_seconds)
        return self._decision


class CancellableTool:
    def __init__(self) -> None:
        self.definition = ToolDefinition(
            "slow_mutation_tool",
            "Slow mutation",
            ("slow_mutation",),
            side_effect=SideEffect.REVERSIBLE,
            timeout_seconds=30.0,
        )
        self.started = asyncio.Event()
        self.cancelled = False

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        self.started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return immutable_json({"mutated": True})


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


class RecoverableEvaluationEvaluator:
    name = "recoverable-evaluation"

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        valid = context.observation.data.get("valid")
        if valid is False:
            return EvaluationResult(
                EvaluationStatus.FAILED,
                "verification failed after tool success",
                self.name,
                immutable_json(),
                task_completed=False,
                goal_completed=False,
            )
        complete = valid is True
        return EvaluationResult(
            EvaluationStatus.COMPLETED if complete else EvaluationStatus.INCOMPLETE,
            "verification recovered" if complete else "verification incomplete",
            self.name,
            immutable_json({"valid": True}) if complete else immutable_json(),
            task_completed=complete,
            goal_completed=complete,
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


class PartiallyExecutableDomain:
    def __init__(self) -> None:
        self._evaluator = CriterionEvaluator("ready-evaluator", "ready")
        self._tool = StaticTool(
            "inspect_ready_tool",
            "inspect_ready",
            immutable_json({"ready": True}),
        )
        self.manifest = DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata("partial", "1.0.0", "Partial executable surface"),
            ("Thing",),
            ("inspect_ready", "inspect_missing"),
            (self._evaluator.name,),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition("inspect_ready", "Inspect ready", CapabilityCategory.OBSERVATION),
            CapabilityDefinition(
                "inspect_missing",
                "Inspect missing",
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


class RecoverableEvaluationDomain:
    def __init__(self) -> None:
        self._evaluator = RecoverableEvaluationEvaluator()
        self.tool = SequenceTool(
            "verify_tool",
            "verify",
            [immutable_json({"valid": False}), immutable_json({"valid": True})],
        )
        self.manifest = DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata("recoverable-evaluation", "1.0.0", "Recoverable evaluation"),
            ("Thing",),
            ("verify",),
            (self._evaluator.name,),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (CapabilityDefinition("verify", "Verify", CapabilityCategory.OBSERVATION),)

    def tools(self) -> tuple[Tool, ...]:
        return (self.tool,)

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
        return (
            RecoveryRule(
                "retry-failed-evaluation",
                (FailureCategory.EVALUATION_FAILED,),
                RecoveryStrategy.RETRY_ACTION,
                max_attempts=1,
            ),
        )

    def memories(self) -> tuple[MemoryRecord, ...]:
        return ()


class CancellableMutationDomain:
    def __init__(self) -> None:
        self.tool = CancellableTool()
        self._evaluator = CriterionEvaluator("mutation-evaluator", "mutated")
        self.manifest = DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata("cancellable", "1.0.0", "Cancellable mutation"),
            ("Thing",),
            ("slow_mutation",),
            (self._evaluator.name,),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                "slow_mutation",
                "Slow mutation",
                CapabilityCategory.MUTATION,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (self.tool,)

    def policies(self) -> tuple[Policy, ...]:
        return (
            PolicyRule(
                "allow-slow-mutation",
                effect=PolicyEffect.ALLOW,
                reason="test mutation is explicitly allowed",
                capabilities=("slow_mutation",),
            ),
        )

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
@pytest.mark.behavior
async def test_runtime_commits_state_events_through_store_seam() -> None:
    backend = FakeKubernetesBackend([True])
    active = DomainLoader().load(KubernetesDomain(backend))
    components = RuntimeBuilder().build(active)
    model = ScriptedModelAdapter([execute_probe(), finish()])
    events = InMemoryEventSink()
    store = RecordingCommitStore(events)
    runtime = AgentRuntime(
        model=model,
        state_store=store,
        components=components,
        event_sink=events,
    )

    result = await runtime.run(*health_goal_and_task())

    assert result.status is ExecutionStatus.COMPLETED
    assert store.committed_event_types == [
        "StateUpdated",
        "StateUpdated",
        "GoalCompleted",
    ]
    assert [event.type for event in events.events if event.type == "StateUpdated"] == [
        "StateUpdated",
        "StateUpdated",
    ]


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_cancel_running_session_stops_active_tool_and_settles_once() -> None:
    domain = CancellableMutationDomain()
    components = RuntimeBuilder().build(DomainLoader().load(domain))
    events = InMemoryEventSink()
    store = InMemoryStateStore()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                Decision(
                    DecisionType.EXECUTE,
                    "start cancellable mutation",
                    capability="slow_mutation",
                    target="resource/example",
                    expected_observations=("mutated",),
                ),
            ]
        ),
        state_store=store,
        components=components,
        event_sink=events,
    )
    run_task = asyncio.create_task(
        runtime.run(
            Goal("Cancel a running mutation", (SuccessCriterion("mutated", True),)),
            Task("Run mutation", ("mutated",)),
        )
    )
    await asyncio.wait_for(domain.tool.started.wait(), timeout=1.0)
    snapshots = await store.list_sessions()
    session_id = snapshots[0].state.session_id

    cancelled = await runtime.cancel(session_id, reason="operator cancelled running session")
    result = await run_task
    state = await store.load(session_id)
    resolved_event = next(event for event in events.events if event.type == "CapabilityResolved")

    assert cancelled.status is ExecutionStatus.CANCELLED
    assert result.status is ExecutionStatus.CANCELLED
    assert state.goal.status is GoalStatus.CANCELLED
    assert state.current_task.status is TaskStatus.CANCELLED
    assert domain.tool.cancelled is True
    assert not runtime._actions.idempotency_store.seen(
        IdempotencyKey(str(resolved_event.data["idempotency_key"]))
    )
    assert [event.type for event in events.events].count("GoalCancelled") == 1


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_decision_context_exposes_only_executable_capabilities() -> None:
    active = DomainLoader().load(PartiallyExecutableDomain())
    components = RuntimeBuilder().build(active)
    model = ScriptedModelAdapter([Decision(DecisionType.WAIT, "inspect context only")])
    runtime = AgentRuntime(
        model=model,
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=InMemoryEventSink(),
    )

    result = await runtime.run(
        Goal("Verify ready capability", (SuccessCriterion("ready", True),)),
        Task("Inspect ready", ("ready",)),
    )

    assert result.status is ExecutionStatus.WAITING
    assert tuple(item.name for item in model.contexts[0].capabilities) == ("inspect_ready",)
    assert model.contexts[0].capabilities[0].required_arguments == ()


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_non_executable_context_capability_fails_before_action() -> None:
    active = DomainLoader().load(PartiallyExecutableDomain())
    components = RuntimeBuilder().build(active)
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                Decision(
                    DecisionType.EXECUTE,
                    "try unavailable capability",
                    capability="inspect_missing",
                    expected_observations=("ready",),
                )
            ]
        ),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=events,
    )

    result = await runtime.run(
        Goal("Verify ready capability", (SuccessCriterion("ready", True),)),
        Task("Inspect ready", ("ready",)),
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.NO_CAPABILITY_TOOL
    rejection = next(event for event in events.events if event.type == "DecisionRejected")
    assert rejection.data["error_code"] == ErrorCode.NO_CAPABILITY_TOOL.value
    assert rejection.data["validation_stage"] == "context"
    assert rejection.data["capability"] == "inspect_missing"
    assert not any(event.type == "ActionStarted" for event in events.events)


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_decision_arguments_are_validated_against_context_before_action() -> None:
    runtime, _, _, events, backend = build_runtime(
        [
            Decision(
                DecisionType.EXECUTE,
                "missing required workload name",
                capability="inspect_workload",
                arguments=immutable_json({}),
                expected_observations=("healthy",),
            )
        ],
        [True],
    )

    result = await runtime.run(*health_goal_and_task())

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.VALIDATION_ERROR
    assert "invalid decision arguments for capability inspect_workload" in result.reason
    assert "missing required arguments: name" in result.reason
    assert backend.calls == 0
    rejection = next(event for event in events.events if event.type == "DecisionRejected")
    assert rejection.data["error_code"] == ErrorCode.VALIDATION_ERROR.value
    assert rejection.data["validation_stage"] == "context"
    assert rejection.data["capability"] == "inspect_workload"
    assert rejection.data["argument_names"] == ()
    assert not any(event.type == "PolicyChecked" for event in events.events)
    assert not any(event.type == "ActionStarted" for event in events.events)


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_decision_argument_schema_is_validated_against_context_before_action() -> None:
    runtime, _, _, events, backend = build_runtime(
        [
            Decision(
                DecisionType.EXECUTE,
                "empty workload name",
                capability="inspect_workload",
                arguments=immutable_json({"name": ""}),
                expected_observations=("healthy",),
            )
        ],
        [True],
    )

    result = await runtime.run(*health_goal_and_task())

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.VALIDATION_ERROR
    assert "argument name length must be >= 1" in result.reason
    assert backend.calls == 0
    rejection = next(event for event in events.events if event.type == "DecisionRejected")
    assert rejection.data["argument_names"] == ("name",)
    assert rejection.data["expected_observations"] == ("healthy",)
    assert not any(event.type == "PolicyChecked" for event in events.events)
    assert not any(event.type == "ActionStarted" for event in events.events)


@pytest.mark.asyncio
@pytest.mark.behavior
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
    workload_capability = next(
        item for item in model.contexts[0].capabilities if item.name == "inspect_workload"
    )
    assert workload_capability.required_arguments == ("name",)
    assert workload_capability.argument_schema["required"] == ["name"]
    assert not hasattr(model.contexts[0], "tools")
    event_types = [event.type for event in events.events]
    assert event_types.count("DecisionValidated") == 3
    assert event_types.count("EvaluationCompleted") == 2
    assert "GoalCompleted" in event_types
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
@pytest.mark.behavior
async def test_wall_clock_budget_pauses_at_decision_boundary() -> None:
    backend = FakeKubernetesBackend([True])
    active = DomainLoader().load(KubernetesDomain(backend))
    components = RuntimeBuilder().build(active)
    events = InMemoryEventSink()
    store = InMemoryStateStore()
    runtime = AgentRuntime(
        model=SlowDecisionAdapter(execute_probe()),
        state_store=store,
        components=components,
        event_sink=events,
    )

    result = await runtime.run(*health_goal_and_task(), timeout_seconds=0.001)
    snapshot = await store.load_session(result.session_id)
    event_types = [event.type for event in events.events]

    assert result.status is ExecutionStatus.WAITING
    assert result.reason == "wall-clock budget expired; session paused at runtime boundary"
    assert snapshot.state.goal.status is GoalStatus.WAITING
    assert backend.calls == 0
    assert "DecisionGenerated" in event_types
    assert "SessionPaused" in event_types
    assert "ActionStarted" not in event_types


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_finish_with_action_fields_is_normalized_after_evaluator_success() -> None:
    runtime, _, store, events, backend = build_runtime(
        [
            execute_probe(),
            Decision(
                DecisionType.FINISH,
                "Health verified, but provider echoed the prior action fields",
                capability="inspect_workload",
                target="deployment/example",
                arguments=immutable_json({"name": "example"}),
                expected_observations=("healthy",),
            ),
        ],
        [True],
    )

    result = await runtime.run(*health_goal_and_task())
    state = await store.load(result.session_id)
    generated = [event for event in events.events if event.type == "DecisionGenerated"]
    validated = [event for event in events.events if event.type == "DecisionValidated"]

    assert result.status is ExecutionStatus.COMPLETED
    assert backend.calls == 1
    assert state.goal.status is GoalStatus.COMPLETED
    assert generated[-1].data["capability"] == "inspect_workload"
    assert validated[-1].data["decision_type"] == "finish"
    assert "capability" not in validated[-1].data
    assert not any(event.type == "DecisionRejected" for event in events.events)


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_malformed_finish_before_evaluation_still_fails_finish_gate() -> None:
    runtime, _, store, events, backend = build_runtime(
        [
            Decision(
                DecisionType.FINISH,
                "Trying to finish before evidence exists",
                capability="inspect_workload",
                target="deployment/example",
                arguments=immutable_json({"name": "example"}),
                expected_observations=("healthy",),
            )
        ],
        [],
    )

    result = await runtime.run(*health_goal_and_task())
    state = await store.load(result.session_id)

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.INVALID_STATE
    assert backend.calls == 0
    assert state.goal.status is GoalStatus.FAILED
    assert any(event.type == "DecisionValidated" for event in events.events)
    assert not any(event.type == "ActionStarted" for event in events.events)


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_finish_is_rejected_without_evaluation() -> None:
    runtime, _, store, events, _ = build_runtime([finish()], [])
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    state = await store.load(result.session_id)

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.INVALID_STATE
    assert state.goal.status is GoalStatus.FAILED
    assert any(e.type == "GoalFailed" for e in events.events)


@pytest.mark.asyncio
@pytest.mark.behavior
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
    assert any(e.type == "GoalFailed" for e in events.events)


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_failed_evaluation_uses_recovery_before_failing_goal() -> None:
    domain = RecoverableEvaluationDomain()
    components = RuntimeBuilder().build(DomainLoader().load(domain))
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                Decision(
                    DecisionType.EXECUTE,
                    "Verify the condition",
                    capability="verify",
                    expected_observations=("valid",),
                ),
                finish(),
            ]
        ),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=events,
    )

    result = await runtime.run(
        Goal("Recover failed verification", (SuccessCriterion("valid", True),)),
        Task("Verify", ("valid",)),
    )
    event_types = [event.type for event in events.events]

    assert result.status is ExecutionStatus.COMPLETED
    assert domain.tool.calls == 2
    assert event_types.count("EvaluationCompleted") == 2
    assert "RecoveryPlanned" in event_types
    assert "GoalFailed" not in event_types


@pytest.mark.asyncio
@pytest.mark.behavior
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
    assert any(e.type == "GoalCompleted" for e in events.events)


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_resume_rejects_damaged_session_snapshot() -> None:
    runtime, _, store, events, _ = build_runtime(
        [Decision(DecisionType.WAIT, "External pause")],
        [],
    )
    goal, task = health_goal_and_task()
    waiting = await runtime.run(goal, task)
    snapshot = await store.load_session(waiting.session_id)
    damaged = SessionSnapshot(
        snapshot.state,
        TaskGraphSnapshot((), snapshot.state.current_task.id),
        snapshot.evidence,
        snapshot.domain_name,
        snapshot.domain_version,
        snapshot.domains,
        snapshot.version,
    )

    await store.save_session(damaged)
    result = await runtime.resume(waiting.session_id)
    reloaded = await store.load_session(waiting.session_id)

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.INVALID_STATE
    assert "invalid session snapshot task graph" in result.reason
    assert reloaded.state.goal.status is GoalStatus.FAILED
    assert reloaded.task_graph.nodes
    assert any(e.type == "GoalFailed" for e in events.events)


@pytest.mark.asyncio
@pytest.mark.behavior
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
@pytest.mark.behavior
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
@pytest.mark.behavior
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
@pytest.mark.behavior
async def test_invalid_decision_is_rejected_before_resolution() -> None:
    invalid = Decision(DecisionType.EXECUTE, "Inspect")
    runtime, _, _, events, _ = build_runtime([invalid], [])
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    assert result.error_code is ErrorCode.VALIDATION_ERROR
    assert not any(event.type == "CapabilityResolved" for event in events.events)
