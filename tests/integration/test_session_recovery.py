"""Cross-runtime recovery: a session must survive losing its runtime instance.

Every test here builds a second AgentRuntime with fresh RuntimeComponents and a
fresh scripted model, sharing only the SessionStore. That is the closest
in-process analogue of a restarted process: nothing carries over except what was
persisted in the snapshot.
"""

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
    AgentState,
    CapabilityCategory,
    CapabilityDefinition,
    ContextFragment,
    DomainManifest,
    DomainMetadata,
    ErrorCode,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    ExecutionStatus,
    JsonMapping,
    PolicyEffect,
    SideEffect,
    ToolDefinition,
)
from universal_agent.domain import DomainRuntime, RuntimeComponents
from universal_agent.domains.kubernetes import KubernetesBackend, KubernetesDomain
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import EvidenceExtractor
from universal_agent.memory import MemoryRecord
from universal_agent.policy import Policy, PolicyRule
from universal_agent.recovery import RecoveryRule
from universal_agent.tasks import TaskExpander
from universal_agent.world import WorldUpdater


class MutationTool:
    definition = ToolDefinition(
        "change_setting",
        "Change a setting",
        ("change_setting",),
        side_effect=SideEffect.REVERSIBLE,
    )

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        return immutable_json({"changed": True})


class RenamedMutationTool(MutationTool):
    """Same capability, different tool name: simulates tool resolution drift."""

    definition = ToolDefinition(
        "change_setting_v2",
        "Change a setting (renamed)",
        ("change_setting",),
        side_effect=SideEffect.REVERSIBLE,
    )


class MutationEvaluator:
    name = "mutation-evaluator"

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        complete = context.satisfied_criteria.get("changed") is True
        return EvaluationResult(
            EvaluationStatus.COMPLETED if complete else EvaluationStatus.INCOMPLETE,
            "setting changed" if complete else "setting unchanged",
            self.name,
            immutable_json({"changed": complete}),
            complete,
            complete,
        )


class MutationContext:
    name = "mutation-context"

    def provide(self, state: AgentState) -> tuple[ContextFragment, ...]:
        return (ContextFragment("test.scope", "Test mutation domain", 1),)


class MutationDomain:
    def __init__(
        self,
        tool: MutationTool,
        *,
        name: str = "mutation-test",
        version: str = "1.0.0",
    ) -> None:
        self._tool = tool
        self._name = name
        self._version = version

    @property
    def manifest(self) -> DomainManifest:
        return DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata(self._name, self._version, "Mutation test domain"),
            ("Setting",),
            ("change_setting",),
            (MutationEvaluator.name,),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                "change_setting",
                "Change setting",
                CapabilityCategory.MUTATION,
            ),
        )

    def tools(self) -> tuple[MutationTool, ...]:
        return (self._tool,)

    def policies(self) -> tuple[Policy, ...]:
        return (
            PolicyRule(
                "mutation-policy",
                PolicyEffect.REQUIRE_CONFIRMATION,
                "mutation requires confirmation",
                capabilities=("change_setting",),
            ),
        )

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (MutationEvaluator(),)

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return (MutationContext(),)

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


def mutation_decision() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Change the setting",
        capability="change_setting",
        target="setting/example",
        expected_observations=("changed",),
    )


def mutation_runtime(
    domain: DomainRuntime,
    store: InMemoryStateStore,
    decisions: list[Decision],
) -> tuple[AgentRuntime, InMemoryEventSink]:
    """Build an independent runtime over a shared store.

    RuntimeComponents are rebuilt every time, so the evidence store, world model
    and task manager all start empty; only the snapshot can bring them back.
    """
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=RuntimeBuilder().build(DomainLoader().load(domain)),
        event_sink=events,
    )
    return runtime, events


def mutation_goal_task() -> tuple[Goal, Task]:
    return Goal("Change setting", (SuccessCriterion("changed", True),)), Task(
        "Change setting",
        ("changed",),
    )


@pytest.mark.asyncio
async def test_confirmation_resumes_on_a_rebuilt_runtime() -> None:
    store = InMemoryStateStore()
    tool = MutationTool()
    first, first_events = mutation_runtime(
        MutationDomain(tool),
        store,
        [mutation_decision()],
    )
    waiting = await first.run(*mutation_goal_task())
    assert waiting.status is ExecutionStatus.WAITING
    assert tool.calls == 0

    # Runtime B knows nothing about runtime A beyond the shared store.
    second, second_events = mutation_runtime(
        MutationDomain(tool),
        store,
        [Decision(DecisionType.FINISH, "Change verified")],
    )
    completed = await second.resume(waiting.session_id, confirmed=True)

    assert completed.status is ExecutionStatus.COMPLETED
    assert tool.calls == 1
    first_types = [event.type for event in first_events.events]
    second_types = [event.type for event in second_events.events]
    assert first_types.count("PolicyChecked") == 1
    assert second_types.count("PolicyChecked") == 1
    assert second_types[-1] == "GoalCompleted"


@pytest.mark.asyncio
async def test_resume_rejects_a_different_domain_name() -> None:
    store = InMemoryStateStore()
    tool = MutationTool()
    first, _ = mutation_runtime(MutationDomain(tool), store, [mutation_decision()])
    waiting = await first.run(*mutation_goal_task())
    assert waiting.status is ExecutionStatus.WAITING

    second, _ = mutation_runtime(
        MutationDomain(MutationTool(), name="other-domain"),
        store,
        [Decision(DecisionType.FINISH, "Change verified")],
    )
    rejected = await second.resume(waiting.session_id, confirmed=True)

    assert rejected.status is ExecutionStatus.FAILED
    assert rejected.error_code is ErrorCode.INVALID_STATE
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_resume_rejects_a_different_domain_version() -> None:
    store = InMemoryStateStore()
    tool = MutationTool()
    first, _ = mutation_runtime(MutationDomain(tool), store, [mutation_decision()])
    waiting = await first.run(*mutation_goal_task())

    second, _ = mutation_runtime(
        MutationDomain(MutationTool(), version="2.0.0"),
        store,
        [Decision(DecisionType.FINISH, "Change verified")],
    )
    rejected = await second.resume(waiting.session_id, confirmed=True)

    assert rejected.status is ExecutionStatus.FAILED
    assert rejected.error_code is ErrorCode.INVALID_STATE
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_resume_rejects_drifted_tool_resolution() -> None:
    """Same domain identity, but the capability now resolves to another tool."""
    store = InMemoryStateStore()
    tool = MutationTool()
    first, _ = mutation_runtime(MutationDomain(tool), store, [mutation_decision()])
    waiting = await first.run(*mutation_goal_task())

    renamed = RenamedMutationTool()
    second, _ = mutation_runtime(
        MutationDomain(renamed),
        store,
        [Decision(DecisionType.FINISH, "Change verified")],
    )
    rejected = await second.resume(waiting.session_id, confirmed=True)

    assert rejected.status is ExecutionStatus.FAILED
    assert rejected.error_code is ErrorCode.INVALID_STATE
    assert tool.calls == 0
    assert renamed.calls == 0


class DiagnosticBackend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.calls.append(capability)
        if capability == "inspect_workload":
            return immutable_json({"resource": "deployment/example", "healthy": False})
        return immutable_json({"resource": "pod/example-123", "root_cause": "crash_loop"})


class TimeoutTwiceBackend:
    """Times out until the third call, which exceeds a single recovery budget."""

    def __init__(self) -> None:
        self.calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        if self.calls <= 2:
            raise TimeoutError("simulated timeout")
        return immutable_json({"resource": "deployment/example", "healthy": True})


class GatedKubernetesDomain(KubernetesDomain):
    """Kubernetes with a confirmation gate on one capability.

    The gate is only a way to stop the runtime at a chosen point: every action
    on that capability pauses, so the snapshot can be handed to a fresh runtime
    mid-flight instead of only at the end.
    """

    def __init__(self, backend: KubernetesBackend, gated: str) -> None:
        super().__init__(backend)
        self._gated = gated

    def policies(self) -> tuple[Policy, ...]:
        return (
            *super().policies(),
            PolicyRule(
                "kubernetes-gate",
                PolicyEffect.REQUIRE_CONFIRMATION,
                "gated capability requires confirmation",
                capabilities=(self._gated,),
            ),
        )


def kubernetes_decision(capability: str, criterion: str) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        f"Run {capability}",
        capability=capability,
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=(criterion,),
    )


def kubernetes_runtime(
    domain: DomainRuntime,
    store: InMemoryStateStore,
    decisions: list[Decision],
) -> tuple[AgentRuntime, InMemoryEventSink, RuntimeComponents, ScriptedModelAdapter]:
    components = RuntimeBuilder().build(DomainLoader().load(domain))
    events = InMemoryEventSink()
    model = ScriptedModelAdapter(decisions)
    runtime = AgentRuntime(
        model=model,
        state_store=store,
        components=components,
        event_sink=events,
    )
    return runtime, events, components, model


def diagnosis_goal_task() -> tuple[Goal, Task]:
    return Goal("Diagnose workload", (SuccessCriterion("root_cause", "crash_loop"),)), Task(
        "Inspect workload",
        (),
    )


@pytest.mark.asyncio
async def test_dynamic_task_graph_survives_a_rebuilt_runtime() -> None:
    """The expander must not re-create a task it already created before the break."""
    store = InMemoryStateStore()
    backend = DiagnosticBackend()
    first, _, _, _ = kubernetes_runtime(
        GatedKubernetesDomain(backend, "inspect_pod"),
        store,
        [
            kubernetes_decision("inspect_workload", "healthy"),
            kubernetes_decision("inspect_pod", "root_cause"),
        ],
    )
    waiting = await first.run(*diagnosis_goal_task())
    assert waiting.status is ExecutionStatus.WAITING

    paused = await store.load_session(waiting.session_id)
    paused_keys = [node.key for node in paused.task_graph.nodes]
    assert paused_keys == ["root", "diagnose-unhealthy-workload"]
    assert backend.calls == ["inspect_workload"]

    second, second_events, components, _ = kubernetes_runtime(
        GatedKubernetesDomain(backend, "inspect_pod"),
        store,
        [Decision(DecisionType.FINISH, "Root cause identified")],
    )
    completed = await second.resume(waiting.session_id, confirmed=True)

    assert completed.status is ExecutionStatus.COMPLETED
    assert backend.calls == ["inspect_workload", "inspect_pod"]

    resumed = await store.load_session(waiting.session_id)
    nodes = resumed.task_graph.nodes
    assert [node.key for node in nodes] == paused_keys
    assert nodes[1].depends_on == (nodes[0].task.id,)
    assert nodes[1].task.id == paused.task_graph.nodes[1].task.id

    world = components.world_model.snapshot(waiting.session_id)
    assert world.value_for("healthy") is False
    assert world.value_for("root_cause") == "crash_loop"
    assert [event.type for event in second_events.events][-1] == "GoalCompleted"


@pytest.mark.asyncio
async def test_recovery_budget_is_not_reset_by_a_rebuilt_runtime() -> None:
    """A restart must not hand the session a fresh set of retry attempts.

    The confirmation gate fires before every tool call, including the ones a
    retry issues, so each resume advances the budget by exactly one attempt and
    the runtime can be rebuilt between them.
    """
    store = InMemoryStateStore()
    backend = TimeoutTwiceBackend()
    goal_task = (
        Goal("Verify workload", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    domain = GatedKubernetesDomain(backend, "inspect_workload")

    first, first_events, _, _ = kubernetes_runtime(
        domain,
        store,
        [kubernetes_decision("inspect_workload", "healthy")],
    )
    waiting = await first.run(*goal_task)
    assert waiting.status is ExecutionStatus.WAITING
    assert backend.calls == 0  # gated before the first call
    assert not (await store.load_session(waiting.session_id)).state.recovery_attempts

    # First timeout: runtime A spends one attempt, then the gate stops the retry.
    second, second_events, _, _ = kubernetes_runtime(domain, store, [])
    retrying = await second.resume(waiting.session_id, confirmed=True)
    assert retrying.status is ExecutionStatus.WAITING
    assert backend.calls == 1
    assert [event.type for event in second_events.events].count("RecoveryPlanned") == 1

    spent = await store.load_session(waiting.session_id)
    assert list(spent.state.recovery_attempts.values()) == [1]
    assert "RecoveryPlanned" not in [event.type for event in first_events.events]

    # Runtime C inherits that attempt instead of starting the budget over.
    third, third_events, _, _ = kubernetes_runtime(domain, store, [])
    again = await third.resume(waiting.session_id, confirmed=True)
    assert again.status is ExecutionStatus.WAITING
    assert backend.calls == 2
    assert [event.type for event in third_events.events].count("RecoveryPlanned") == 1

    resumed = await store.load_session(waiting.session_id)
    assert list(resumed.state.recovery_attempts.values()) == [2]
    assert list(resumed.state.recovery_attempts) == list(spent.state.recovery_attempts)

    fourth, fourth_events, _, _ = kubernetes_runtime(
        domain,
        store,
        [Decision(DecisionType.FINISH, "Health verified")],
    )
    completed = await fourth.resume(waiting.session_id, confirmed=True)

    assert completed.status is ExecutionStatus.COMPLETED
    assert backend.calls == 3
    assert "RecoveryPlanned" not in [event.type for event in fourth_events.events]


@pytest.mark.asyncio
async def test_context_fragments_are_equivalent_across_a_rebuilt_runtime() -> None:
    """What the model sees must be reconstructed from evidence, not carried over."""
    store = InMemoryStateStore()
    backend = DiagnosticBackend()
    # Evidence context is task-scoped, so the gate sits on a third capability:
    # by then the diagnostic task has produced its own observation and both
    # runtimes compile a context that actually carries evidence.
    first, _, _, first_model = kubernetes_runtime(
        GatedKubernetesDomain(backend, "inspect_logs"),
        store,
        [
            kubernetes_decision("inspect_workload", "healthy"),
            kubernetes_decision("inspect_pod", "root_cause"),
            kubernetes_decision("inspect_logs", "root_cause"),
        ],
    )
    waiting = await first.run(*diagnosis_goal_task())
    assert waiting.status is ExecutionStatus.WAITING
    before = first_model.contexts[-1]
    assert before.world_context
    assert before.evidence_context

    second, _, _, second_model = kubernetes_runtime(
        GatedKubernetesDomain(backend, "inspect_logs"),
        store,
        [Decision(DecisionType.FINISH, "Root cause identified")],
    )
    completed = await second.resume(waiting.session_id, confirmed=True)
    assert completed.status is ExecutionStatus.COMPLETED

    # Runtime B rebuilt world and evidence from the snapshot alone, so its first
    # context must cover everything runtime A saw, plus the gated observation.
    after = second_model.contexts[0]
    assert {fragment.key for fragment in before.world_context}.issubset(
        {fragment.key for fragment in after.world_context}
    )
    assert {fragment.key for fragment in before.task_context} == {
        fragment.key for fragment in after.task_context
    }
    assert after.evidence_context
    assert before.domain_context == after.domain_context
    assert before.policy_summary == after.policy_summary
    for fragments in (after.world_context, after.evidence_context, after.task_context):
        assert len(fragments) <= 8
        assert sum(len(fragment.content) for fragment in fragments) <= 4_000
