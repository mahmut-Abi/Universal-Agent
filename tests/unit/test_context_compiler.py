from __future__ import annotations

from datetime import UTC, datetime

from universal_agent.context.compiler import BasicContextCompiler
from universal_agent.core import (
    ActionId,
    AgentState,
    CapabilityCategory,
    CapabilityDefinition,
    CapabilityInputContract,
    CapabilitySummary,
    ContextFragment,
    DecisionContext,
    Goal,
    GoalId,
    ObservationId,
    SessionId,
    Task,
    TaskId,
    immutable_json,
)
from universal_agent.evidence import Evidence
from universal_agent.memory import MemoryId, MemoryKind, MemoryRecord
from universal_agent.tasks import TaskManager
from universal_agent.world import WorldFact, WorldSnapshot

FIXED_AT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
LATER_AT = datetime(2026, 1, 3, 0, 0, 0, tzinfo=UTC)


class FakeProvider:
    def __init__(self, name: str, fragments: tuple[ContextFragment, ...]) -> None:
        self._name = name
        self._fragments = fragments

    @property
    def name(self) -> str:
        return self._name

    def provide(self, state: AgentState) -> tuple[ContextFragment, ...]:
        return self._fragments


def make_state() -> AgentState:
    return AgentState(
        SessionId("session-1"),
        Goal("goal", success_criteria=(), id=GoalId("goal-1")),
        Task("task", required_criteria=(), id=TaskId("task-1")),
    )


def fragment(key: str, content: str, priority: int = 100) -> ContextFragment:
    return ContextFragment(key, content, priority)


def test_compile_returns_context_with_identifiers_and_empty_sections() -> None:
    compiler = BasicContextCompiler()
    state = make_state()

    context = compiler.compile(state, (), (), ())

    assert isinstance(context, DecisionContext)
    assert context.session_id == SessionId("session-1")
    assert context.goal_id == GoalId("goal-1")
    assert context.task_id == TaskId("task-1")
    assert context.goal_description == "goal"
    assert context.task_description == "task"
    assert context.domain_context == ()
    assert context.capabilities == ()
    assert context.world_context == ()
    assert context.evidence_context == ()
    assert context.task_context == ()
    assert context.memory_context == ()
    assert context.policy_summary == ()


def test_domain_fragment_count_budget_is_enforced() -> None:
    compiler = BasicContextCompiler()
    state = make_state()
    provider = FakeProvider(
        "p",
        tuple(fragment(f"k{i}", "x") for i in range(12)),
    )

    context = compiler.compile(state, (), (), (provider,))

    assert len(context.domain_context) == 8


def test_domain_character_budget_truncates_and_stops() -> None:
    compiler = BasicContextCompiler(max_characters=10)
    state = make_state()
    provider = FakeProvider(
        "p",
        (fragment("k0", "abcdef"), fragment("k1", "abcdef"), fragment("k2", "abcdef")),
    )

    context = compiler.compile(state, (), (), (provider,))

    assert len(context.domain_context) == 2
    assert context.domain_context[0].content == "abcdef"
    assert context.domain_context[1].content == "abcd"


def test_provider_priority_overrides_duplicate_keys() -> None:
    compiler = BasicContextCompiler()
    state = make_state()
    low = FakeProvider("low", (fragment("dup", "low-priority", priority=5),))
    high = FakeProvider("high", (fragment("dup", "high-priority", priority=10),))

    context = compiler.compile(state, (), (), (low, high))

    assert len(context.domain_context) == 1
    assert context.domain_context[0].content == "low-priority"
    assert context.domain_context[0].key == "dup"


def test_capability_input_contract_is_applied_to_summary() -> None:
    compiler = BasicContextCompiler()
    state = make_state()
    capabilities = (CapabilityDefinition("inspect", "desc", CapabilityCategory.OBSERVATION),)
    contracts = (
        CapabilityInputContract(
            capability="inspect",
            required_arguments=("name",),
            argument_schema=immutable_json({"name": "str"}),
        ),
    )

    context = compiler.compile(state, capabilities, (), (), capability_input_contracts=contracts)

    assert len(context.capabilities) == 1
    summary = context.capabilities[0]
    assert isinstance(summary, CapabilitySummary)
    assert summary.name == "inspect"
    assert summary.required_arguments == ("name",)
    assert summary.argument_schema == immutable_json({"name": "str"})


def test_capability_without_contract_has_empty_contract_fields() -> None:
    compiler = BasicContextCompiler()
    state = make_state()
    capabilities = (CapabilityDefinition("inspect", "desc", CapabilityCategory.OBSERVATION),)

    context = compiler.compile(state, capabilities, (), ())

    summary = context.capabilities[0]
    assert summary.required_arguments == ()
    assert summary.argument_schema == immutable_json()


def test_world_context_is_populated_from_snapshot() -> None:
    compiler = BasicContextCompiler()
    state = make_state()
    world = WorldSnapshot(
        SessionId("session-1"),
        facts=(WorldFact("dep", "status", "unhealthy", 1.0, FIXED_AT, ()),),
    )

    context = compiler.compile(state, (), (), (), world=world)

    assert len(context.world_context) == 1
    assert context.world_context[0].key == "world.dep.status"
    assert "unhealthy" in context.world_context[0].content


def test_evidence_context_is_populated() -> None:
    compiler = BasicContextCompiler()
    state = make_state()
    evidence = (
        Evidence(
            SessionId("session-1"),
            TaskId("task-1"),
            ActionId("action-1"),
            ObservationId("observation-1"),
            "dep",
            "status",
            "unhealthy",
            "cap:tool",
        ),
    )

    context = compiler.compile(state, (), (), (), evidence=evidence)

    assert len(context.evidence_context) == 1
    assert context.evidence_context[0].key.startswith("evidence.")


def test_evidence_fragment_count_budget_is_enforced() -> None:
    compiler = BasicContextCompiler()
    state = make_state()
    evidence = tuple(
        Evidence(
            SessionId("session-1"),
            TaskId("task-1"),
            ActionId("action-1"),
            ObservationId("observation-1"),
            "dep",
            f"claim-{i}",
            i,
            "cap:tool",
        )
        for i in range(12)
    )

    context = compiler.compile(state, (), (), (), evidence=evidence)

    assert len(context.evidence_context) == 8


def test_task_context_is_populated_from_task_manager() -> None:
    compiler = BasicContextCompiler()
    state = make_state()
    manager = TaskManager(Task("root", required_criteria=(), id=TaskId("task-1")))

    context = compiler.compile(state, (), (), (), tasks=manager)

    assert len(context.task_context) == 1
    assert context.task_context[0].key == "task.task-1"
    assert "root" in context.task_context[0].content


def test_memory_fragment_count_budget_is_enforced() -> None:
    compiler = BasicContextCompiler(max_memory_fragments=4)
    state = make_state()
    memories = tuple(
        MemoryRecord(MemoryKind.SEMANTIC, "s", "x", created_at=FIXED_AT, id=MemoryId(f"m{i}"))
        for i in range(6)
    )

    context = compiler.compile(state, (), (), (), memories=memories)

    assert len(context.memory_context) == 4


def test_memory_character_budget_truncates_and_stops() -> None:
    compiler = BasicContextCompiler(max_memory_characters=10)
    state = make_state()
    memories = (
        MemoryRecord(MemoryKind.SEMANTIC, "s", "abcdef", created_at=FIXED_AT, id=MemoryId("m0")),
        MemoryRecord(MemoryKind.SEMANTIC, "s", "abcdef", created_at=LATER_AT, id=MemoryId("m1")),
        MemoryRecord(MemoryKind.SEMANTIC, "s", "abcdef", created_at=LATER_AT, id=MemoryId("m2")),
    )

    context = compiler.compile(state, (), (), (), memories=memories)

    assert len(context.memory_context) == 1
    assert len(context.memory_context[0].content) == 10


def test_memory_is_dropped_before_higher_priority_context() -> None:
    compiler = BasicContextCompiler(max_memory_fragments=0)
    state = make_state()
    memories = (MemoryRecord(MemoryKind.SEMANTIC, "s", "x", id=MemoryId("m0")),)

    context = compiler.compile(state, (), (), (), memories=memories)

    assert context.memory_context == ()


def test_policy_summary_is_passed_through() -> None:
    compiler = BasicContextCompiler()
    state = make_state()

    context = compiler.compile(state, (), ("no destructive actions",), ())

    assert context.policy_summary == ("no destructive actions",)


def test_satisfied_criteria_is_copied_into_context() -> None:
    compiler = BasicContextCompiler()
    state = make_state()
    state.satisfied_criteria["health"] = "ok"

    context = compiler.compile(state, (), (), ())

    assert context.satisfied_criteria == immutable_json({"health": "ok"})


def test_latest_observation_is_forwarded() -> None:
    from universal_agent.core import ActionId, Observation, ObservationId, ObservationStatus

    compiler = BasicContextCompiler()
    state = make_state()
    observation = Observation(
        id=ObservationId("observation-1"),
        action_id=ActionId("action-1"),
        task_id=TaskId("task-1"),
        source="cap:tool",
        status=ObservationStatus.SUCCEEDED,
        data=immutable_json({}),
        observed_at=FIXED_AT,
    )
    state.observations.append(observation)

    context = compiler.compile(state, (), (), ())

    assert context.latest_observation is observation
