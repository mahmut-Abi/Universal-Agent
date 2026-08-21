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
    def __init__(self, tool: MutationTool, effect: PolicyEffect) -> None:
        self._tool = tool
        self._effect = effect

    @property
    def manifest(self) -> DomainManifest:
        return DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata("mutation-test", "1.0.0", "Mutation test domain"),
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
                self._effect,
                "mutation requires policy decision",
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


def build(
    effect: PolicyEffect,
) -> tuple[AgentRuntime, InMemoryStateStore, InMemoryEventSink, MutationTool]:
    tool = MutationTool()
    active = DomainLoader().load(MutationDomain(tool, effect))
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [mutation_decision(), Decision(DecisionType.FINISH, "Change verified")]
        ),
        state_store=store,
        components=RuntimeBuilder().build(active),
        event_sink=events,
    )
    return runtime, store, events, tool


def goal_task() -> tuple[Goal, Task]:
    return Goal("Change setting", (SuccessCriterion("changed", True),)), Task(
        "Change setting",
        ("changed",),
    )


@pytest.mark.asyncio
async def test_policy_denial_prevents_tool_execution() -> None:
    runtime, _, events, tool = build(PolicyEffect.DENY)
    result = await runtime.run(*goal_task())
    assert result.error_code is ErrorCode.POLICY_DENIED
    assert tool.calls == 0
    assert not any(event.type == "ActionStarted" for event in events.events)


@pytest.mark.asyncio
async def test_confirmation_pauses_then_executes_after_recheck() -> None:
    runtime, store, events, tool = build(PolicyEffect.REQUIRE_CONFIRMATION)
    waiting = await runtime.run(*goal_task())
    state = await store.load(waiting.session_id)
    assert waiting.status is ExecutionStatus.WAITING
    assert state.pending_action is not None
    assert tool.calls == 0

    completed = await runtime.resume(waiting.session_id, confirmed=True)
    assert completed.status is ExecutionStatus.COMPLETED
    assert tool.calls == 1
    assert [event.type for event in events.events].count("PolicyChecked") == 2


@pytest.mark.asyncio
async def test_confirmation_rejection_never_executes_tool() -> None:
    runtime, _, _, tool = build(PolicyEffect.REQUIRE_CONFIRMATION)
    waiting = await runtime.run(*goal_task())
    rejected = await runtime.resume(waiting.session_id, confirmed=False)
    assert rejected.error_code is ErrorCode.CONFIRMATION_REJECTED
    assert tool.calls == 0
