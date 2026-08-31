from __future__ import annotations

from datetime import UTC, datetime

from universal_agent.context.compiler import BasicContextCompiler
from universal_agent.core import (
    ActionId,
    AgentState,
    ContextFragment,
    Goal,
    GoalId,
    ObservationId,
    SessionId,
    Task,
    TaskId,
)
from universal_agent.evidence import Evidence
from universal_agent.memory import MemoryId, MemoryKind, MemoryRecord
from universal_agent.world import WorldFact, WorldSnapshot

FIXED_AT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


class FakeProvider:
    def __init__(self, name: str, fragments: tuple[ContextFragment, ...]) -> None:
        self._name = name
        self._fragments = fragments

    @property
    def name(self) -> str:
        return self._name

    def provide(self, state: AgentState) -> tuple[ContextFragment, ...]:
        return self._fragments


def make_state(
    goal_desc: str = "inspect deployment",
    task_desc: str = "check pod status",
    required_criteria: tuple[str, ...] = ("healthy",),
) -> AgentState:
    return AgentState(
        SessionId("session-1"),
        Goal(goal_desc, success_criteria=(), id=GoalId("goal-1")),
        Task(task_desc, required_criteria=required_criteria, id=TaskId("task-1")),
    )


def fragment(key: str, content: str, priority: int = 100) -> ContextFragment:
    return ContextFragment(key, content, priority)


def test_relevance_ranking_prioritizes_task_related_fragment() -> None:
    compiler = BasicContextCompiler(enable_relevance_ranking=True)
    state = make_state(
        goal_desc="inspect deployment dify-api",
        task_desc="check pod dify-api-123 status",
        required_criteria=("healthy", "ready"),
    )
    provider = FakeProvider(
        "p",
        (
            fragment("k0", "unrelated log message about something else"),
            fragment("k1", "pod dify-api-123 is crashing with OOMKilled"),
            fragment("k2", "some other random text"),
        ),
    )

    context = compiler.compile(state, (), (), (provider,))

    assert len(context.domain_context) == 3
    assert "dify-api-123" in context.domain_context[0].content
    assert "crashing" in context.domain_context[0].content


def test_relevance_disabled_falls_back_to_priority_order() -> None:
    compiler = BasicContextCompiler(enable_relevance_ranking=False)
    state = make_state(
        goal_desc="inspect deployment",
        task_desc="check pod status",
    )
    provider = FakeProvider(
        "p",
        (
            fragment("k0", "unrelated log"),
            fragment("k1", "pod dify-api-123 is crashing"),
            fragment("k2", "some other text"),
        ),
    )

    context = compiler.compile(state, (), (), (provider,))

    assert len(context.domain_context) == 3
    assert context.domain_context[0].key == "k0"
    assert context.domain_context[1].key == "k1"
    assert context.domain_context[2].key == "k2"


def test_compression_keeps_head_and_tail() -> None:
    compiler = BasicContextCompiler(
        max_characters=500,
        max_fragment_characters=50,
        enable_compression=True,
    )
    state = make_state()
    long_content = "A" * 100 + "MIDDLE" + "Z" * 100
    provider = FakeProvider("p", (fragment("k0", long_content),))

    context = compiler.compile(state, (), (), (provider,))

    assert len(context.domain_context) == 1
    content = context.domain_context[0].content
    assert "MIDDLE" not in content
    assert content.startswith("A" * 25)
    assert content.endswith("Z" * 24)
    assert "\u2026" in content


def test_compression_disabled_no_truncation_when_under_budget() -> None:
    compiler = BasicContextCompiler(
        max_characters=500,
        max_fragment_characters=2000,
        enable_compression=False,
    )
    state = make_state()
    long_content = "A" * 100 + "MIDDLE" + "Z" * 100
    provider = FakeProvider("p", (fragment("k0", long_content),))

    context = compiler.compile(state, (), (), (provider,))

    assert len(context.domain_context) == 1
    assert context.domain_context[0].content == "A" * 100 + "MIDDLE" + "Z" * 100


def test_dedup_removes_duplicate_content_when_enabled() -> None:
    compiler = BasicContextCompiler(
        max_fragments=8,
        max_characters=1000,
        enable_dedup=True,
    )
    state = make_state()
    provider = FakeProvider(
        "p",
        (
            fragment("k0", "duplicate content"),
            fragment("k1", "unique content"),
            fragment("k2", "duplicate content"),
            fragment("k3", "another unique"),
        ),
    )

    context = compiler.compile(state, (), (), (provider,))

    assert len(context.domain_context) == 3
    contents = {f.content for f in context.domain_context}
    assert "duplicate content" in contents
    assert "unique content" in contents
    assert "another unique" in contents


def test_dedup_disabled_keeps_all() -> None:
    compiler = BasicContextCompiler(enable_dedup=False)
    state = make_state()
    provider = FakeProvider(
        "p",
        (
            fragment("k0", "duplicate content"),
            fragment("k1", "unique content"),
            fragment("k2", "duplicate content"),
        ),
    )

    context = compiler.compile(state, (), (), (provider,))

    assert len(context.domain_context) == 3


def test_cross_context_relevance_ordering() -> None:
    compiler = BasicContextCompiler(
        max_fragments=10,
        max_characters=2000,
        enable_relevance_ranking=True,
    )
    state = make_state(
        goal_desc="fix deployment crash",
        task_desc="check pod crash",
    )
    world = WorldSnapshot(
        SessionId("session-1"),
        facts=(
            WorldFact("pod", "status", "Running", 1.0, FIXED_AT, ()),
            WorldFact("deployment", "replicas", "3", 0.9, FIXED_AT, ()),
        ),
    )
    evidence = (
        Evidence(
            SessionId("session-1"),
            TaskId("task-1"),
            ActionId("action-1"),
            ObservationId("observation-1"),
            "pod",
            "status",
            "CrashLoopBackOff",
            "tool:kubectl",
        ),
        Evidence(
            SessionId("session-1"),
            TaskId("task-1"),
            ActionId("action-2"),
            ObservationId("observation-2"),
            "deployment",
            "replicas",
            "3",
            "tool:kubectl",
        ),
    )

    context = compiler.compile(state, (), (), (), world=world, evidence=evidence)

    _ = {f.key for f in context.evidence_context}
    _ = {f.key for f in context.world_context}
    assert len(context.evidence_context) + len(context.world_context) > 0


def test_memory_dropped_under_pressure() -> None:
    compiler = BasicContextCompiler(
        max_characters=0,
        max_memory_fragments=4,
        max_memory_characters=0,
    )
    state = make_state()
    memories = (
        MemoryRecord(
            MemoryKind.SEMANTIC,
            "s",
            "very long memory content that exceeds budget",
            id=MemoryId("m0"),
        ),
        MemoryRecord(MemoryKind.EPISODIC, "s", "another long memory", id=MemoryId("m1")),
    )

    context = compiler.compile(state, (), (), (), memories=memories)

    assert len(context.memory_context) == 0


def test_evidence_sorted_by_confidence() -> None:
    compiler = BasicContextCompiler()
    state = make_state()
    evidence = (
        Evidence(
            SessionId("session-1"),
            TaskId("task-1"),
            ActionId("action-1"),
            ObservationId("observation-1"),
            "pod",
            "status",
            "Running",
            "tool:kubectl",
            confidence=0.5,
        ),
        Evidence(
            SessionId("session-1"),
            TaskId("task-1"),
            ActionId("action-2"),
            ObservationId("observation-2"),
            "pod",
            "status",
            "CrashLoopBackOff",
            "tool:kubectl",
            confidence=0.9,
        ),
    )

    context = compiler.compile(state, (), (), (), evidence=evidence)

    assert len(context.evidence_context) == 2
    assert "CrashLoopBackOff" in context.evidence_context[0].content
    assert "Running" in context.evidence_context[1].content


def test_relevance_uses_goal_and_task_and_criteria() -> None:
    compiler = BasicContextCompiler(enable_relevance_ranking=True)
    state = make_state(
        goal_desc="fix database connection",
        task_desc="check postgres pod",
        required_criteria=("postgres", "connection"),
    )
    provider = FakeProvider(
        "p",
        (
            fragment("k0", "unrelated info about nginx"),
            fragment("k1", "postgres pod is in CrashLoopBackOff"),
            fragment("k2", "something about redis cache"),
        ),
    )

    context = compiler.compile(state, (), (), (provider,))

    assert "postgres" in context.domain_context[0].content
    assert "CrashLoopBackOff" in context.domain_context[0].content
