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
    ActionId,
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
    SessionId,
    SideEffect,
    TaskId,
    ToolDefinition,
)
from universal_agent.domain import RuntimeComponents
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import EvidenceExtractor
from universal_agent.memory import MemoryRecord
from universal_agent.policy import Policy, PolicyRule
from universal_agent.recovery import RecoveryRule
from universal_agent.tasks import TaskExpander
from universal_agent.world import WorldUpdater


class LockedMutationTool:
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
        return immutable_json({"changed": True, "resource_version": "rv-2"})


class LockedMutationEvaluator:
    name = "locked-mutation-evaluator"

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


class LockedMutationContext:
    name = "locked-mutation-context"

    def provide(self, state: AgentState) -> tuple[ContextFragment, ...]:
        return (ContextFragment("test.scope", "Locked mutation domain", 1),)


class LockedMutationDomain:
    def __init__(self, tool: LockedMutationTool, effect: PolicyEffect) -> None:
        self._tool = tool
        self._effect = effect

    @property
    def manifest(self) -> DomainManifest:
        return DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata("locked-mutation-test", "1.0.0", "Locked mutation test domain"),
            ("Setting",),
            ("change_setting",),
            (LockedMutationEvaluator.name,),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                "change_setting",
                "Change setting",
                CapabilityCategory.MUTATION,
            ),
        )

    def tools(self) -> tuple[LockedMutationTool, ...]:
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
        return (LockedMutationEvaluator(),)

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return (LockedMutationContext(),)

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
        arguments=immutable_json({"resource_version": "rv-1"}),
        expected_observations=("changed",),
    )


def build_runtime(
    effect: PolicyEffect,
) -> tuple[
    AgentRuntime, InMemoryStateStore, InMemoryEventSink, RuntimeComponents, LockedMutationTool
]:
    tool = LockedMutationTool()
    components = RuntimeBuilder().build(DomainLoader().load(LockedMutationDomain(tool, effect)))
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [mutation_decision(), Decision(DecisionType.FINISH, "Change verified")]
        ),
        state_store=store,
        components=components,
        event_sink=events,
    )
    return runtime, store, events, components, tool


def goal_task() -> tuple[Goal, Task]:
    return Goal("Change setting", (SuccessCriterion("changed", True),)), Task(
        "Change setting",
        ("changed",),
    )


@pytest.mark.asyncio
async def test_mutation_conflict_prevents_tool_execution() -> None:
    runtime, _, events, components, tool = build_runtime(PolicyEffect.ALLOW)
    components.resource_locks.acquire(
        resource_key="setting/example",
        action_id=ActionId("action-existing"),
        session_id=SessionId("session-existing"),
        task_id=TaskId("task-existing"),
    )

    result = await runtime.run(*goal_task())

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.RESOURCE_CONFLICT
    assert tool.calls == 0
    assert len(components.resource_locks.active()) == 1
    assert "ResourceConflictDetected" in [event.type for event in events.events]
    assert "ActionStarted" not in [event.type for event in events.events]


@pytest.mark.asyncio
async def test_stale_resource_version_prevents_tool_execution() -> None:
    runtime, _, events, components, tool = build_runtime(PolicyEffect.ALLOW)
    components.resource_versions.set_current("setting/example", "rv-2")

    result = await runtime.run(*goal_task())
    event_types = [event.type for event in events.events]
    version_checked = next(
        event for event in events.events if event.type == "ResourceVersionChecked"
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.RESOURCE_CONFLICT
    assert tool.calls == 0
    assert components.resource_locks.active() == ()
    assert "ResourceConflictDetected" in event_types
    assert "ResourceLockAcquired" not in event_types
    assert "ActionStarted" not in event_types
    assert version_checked.data["resource_version"] == "rv-1"
    assert version_checked.data["current_resource_version"] == "rv-2"
    assert version_checked.data["matched"] is False


@pytest.mark.asyncio
async def test_matching_resource_version_allows_mutation_and_updates_current_version() -> None:
    runtime, _, events, components, tool = build_runtime(PolicyEffect.ALLOW)
    components.resource_versions.set_current("setting/example", "rv-1")

    result = await runtime.run(*goal_task())
    event_types = [event.type for event in events.events]
    version_checked = next(
        event for event in events.events if event.type == "ResourceVersionChecked"
    )
    version_updated = next(
        event for event in events.events if event.type == "ResourceVersionUpdated"
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert tool.calls == 1
    assert version_checked.data["matched"] is True
    assert version_checked.data["current_resource_version"] == "rv-1"
    assert version_updated.data["resource_version"] == "rv-2"
    assert components.resource_versions.current("setting/example") == "rv-2"
    assert "ActionStarted" in event_types


@pytest.mark.asyncio
async def test_confirmation_holds_resource_lock_until_rejection() -> None:
    runtime, store, events, components, tool = build_runtime(PolicyEffect.REQUIRE_CONFIRMATION)

    waiting = await runtime.run(*goal_task())
    snapshot = await store.load_session(waiting.session_id)
    state = snapshot.state

    assert waiting.status is ExecutionStatus.WAITING
    assert state.pending_action is not None
    assert state.pending_action.resource_key == "setting/example"
    assert state.pending_action.resource_version == "rv-1"
    assert len(components.resource_locks.active()) == 1
    assert tool.calls == 0

    rejected = await runtime.resume(waiting.session_id, confirmed=False)

    assert rejected.status is ExecutionStatus.FAILED
    assert rejected.error_code is ErrorCode.CONFIRMATION_REJECTED
    assert components.resource_locks.active() == ()
    assert tool.calls == 0
    assert "ResourceLockReleased" in [event.type for event in events.events]


@pytest.mark.asyncio
async def test_confirmed_mutation_reuses_and_releases_resource_lock() -> None:
    runtime, _, events, components, tool = build_runtime(PolicyEffect.REQUIRE_CONFIRMATION)
    waiting = await runtime.run(*goal_task())

    completed = await runtime.resume(waiting.session_id, confirmed=True)
    action_started = next(event for event in events.events if event.type == "ActionStarted")
    event_types = [event.type for event in events.events]

    assert completed.status is ExecutionStatus.COMPLETED
    assert tool.calls == 1
    assert components.resource_locks.active() == ()
    assert event_types.count("ResourceLockAcquired") == 1
    assert event_types.count("ResourceLockReleased") == 1
    assert action_started.data["resource_key"] == "setting/example"
    assert action_started.data["resource_version"] == "rv-1"
    assert components.resource_versions.current("setting/example") == "rv-2"
