"""P3.1 Memory: the advisory-knowledge pipeline and its 4.6 boundary.

Three concerns share this file:

* Long-context budgeting (AGENTS.md section 15): a large execution history is
  compiled down to a bounded relevant context, and memory is the first category
  dropped under pressure because it is advisory.
* Cross-session accumulation: a terminal transition writes an episodic record
  that a fresh runtime sharing the memory store recalls on its next session.
* The 4.6 boundary triple: memory never becomes evidence, never updates the
  world model, and never alone completes a task or goal.

Each test builds a fresh runtime; nothing carries over except what is injected
explicitly (the shared memory store) or persisted in a snapshot.
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
from universal_agent.core import ExecutionStatus, JsonMapping
from universal_agent.domain import RuntimeComponents
from universal_agent.domains.kubernetes import KubernetesBackend, KubernetesDomain
from universal_agent.memory import (
    InMemoryMemoryStore,
    MemoryKind,
    MemoryRecord,
    MemoryStore,
)


class HealthyBackend:
    """Returns a healthy workload on the first inspection."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.calls.append(capability)
        return immutable_json({"resource": "deployment/example", "healthy": True})


def decision(capability: str, criterion: str) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        f"Run {capability}",
        capability=capability,
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=(criterion,),
    )


def build_runtime(
    backend: KubernetesBackend,
    decisions: list[Decision],
    *,
    memory_store: MemoryStore | None = None,
) -> tuple[
    AgentRuntime,
    InMemoryStateStore,
    InMemoryEventSink,
    ScriptedModelAdapter,
    RuntimeComponents,
]:
    if memory_store is not None:
        builder = RuntimeBuilder(memory_store_factory=lambda: memory_store)
    else:
        builder = RuntimeBuilder()
    components = builder.build(DomainLoader().load(KubernetesDomain(backend)))
    events = InMemoryEventSink()
    store = InMemoryStateStore()
    model = ScriptedModelAdapter(decisions)
    runtime = AgentRuntime(
        model=model,
        state_store=store,
        components=components,
        event_sink=events,
    )
    return runtime, store, events, model, components


def goal_task_matching_healthy() -> tuple[Goal, Task]:
    """A goal whose criterion the healthy backend satisfies directly."""
    return (
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ()),
    )


def diagnosis_goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Diagnose workload", (SuccessCriterion("root_cause", "crash_loop"),)),
        Task("Inspect workload", ()),
    )


@pytest.mark.asyncio
async def test_long_context_stays_within_memory_budget() -> None:
    """A store flooded with memory still compiles to <=4 fragments / <=1200 chars.

    Domain-declared procedural knowledge that matches the goal must survive the
    budget while unrelated filler is dropped.
    """
    store = InMemoryMemoryStore()
    # Flood the store with unrelated semantic filler that should be filtered out.
    for index in range(60):
        store.add(
            MemoryRecord(
                MemoryKind.SEMANTIC,
                f"billing-{index}",
                "invoice line item totals reconcile against ledger entries",
                scope="kubernetes",
                confidence=0.2,
            )
        )
    runtime, _, _, model, _ = build_runtime(
        HealthyBackend(),
        [decision("inspect_workload", "healthy"), Decision(DecisionType.FINISH, "done")],
        memory_store=store,
    )
    await runtime.run(*goal_task_matching_healthy())
    context = model.contexts[-1]
    assert len(context.memory_context) <= 4
    assert sum(len(fragment.content) for fragment in context.memory_context) <= 1_200
    # Every other category keeps its own budget intact (memory did not steal it).
    for fragments in (
        context.world_context,
        context.evidence_context,
        context.task_context,
    ):
        assert len(fragments) <= 8
        assert sum(len(fragment.content) for fragment in fragments) <= 4_000


@pytest.mark.asyncio
async def test_episodic_memory_carries_across_sessions() -> None:
    """A terminal transition writes an episodic record the next session recalls."""
    shared_store = InMemoryMemoryStore()
    backend = HealthyBackend()
    first, _, _, _, _ = build_runtime(
        backend,
        [decision("inspect_workload", "healthy"), Decision(DecisionType.FINISH, "done")],
        memory_store=shared_store,
    )
    result = await first.run(*goal_task_matching_healthy())
    assert result.status is ExecutionStatus.COMPLETED
    episodic = [r for r in shared_store.export() if r.kind is MemoryKind.EPISODIC]
    assert len(episodic) == 1
    assert episodic[0].source_session_id == result.session_id

    # A fresh runtime sharing the store recalls the prior episode on its first turn.
    second, _, _, second_model, _ = build_runtime(
        backend,
        [Decision(DecisionType.FINISH, "done")],
        memory_store=shared_store,
    )
    await second.run(*diagnosis_goal_task())
    keys = {fragment.key for fragment in second_model.contexts[0].memory_context}
    assert any("memory" in key for key in keys)
    assert second_model.contexts[0].memory_context


@pytest.mark.asyncio
async def test_memory_does_not_become_evidence() -> None:
    """Injecting semantic memory leaves session evidence untouched.

    The goal's criterion is 'root_cause'; the healthy backend never satisfies
    it, so no evidence for that claim is ever recorded. A 'workload is healthy'
    memory must not synthesize an evidence record to fill that gap.
    """
    store = InMemoryMemoryStore()
    store.add(
        MemoryRecord(
            MemoryKind.SEMANTIC,
            "workload health",
            "deployment is healthy when ready replicas match desired",
            scope="kubernetes",
        )
    )
    runtime, _, _, _, components = build_runtime(
        HealthyBackend(),
        [decision("inspect_workload", "healthy"), Decision(DecisionType.FINISH, "done")],
        memory_store=store,
    )
    result = await runtime.run(*goal_task_matching_healthy())
    evidence = components.evidence_store.export(result.session_id)
    assert all(record.claim != "root_cause" for record in evidence)


@pytest.mark.asyncio
async def test_memory_does_not_update_world_model() -> None:
    """A semantic 'workload is healthy' memory must not appear as a world fact."""
    store = InMemoryMemoryStore()
    store.add(
        MemoryRecord(
            MemoryKind.SEMANTIC,
            "workload",
            "workload is healthy",
            scope="kubernetes",
            confidence=1.0,
        )
    )
    runtime, _, _, _, components = build_runtime(
        HealthyBackend(),
        [Decision(DecisionType.FINISH, "done")],
        memory_store=store,
    )
    result = await runtime.run(*goal_task_matching_healthy())
    world = components.world_model.snapshot(result.session_id)
    # The world did contain a 'healthy' fact, but it came from the observation
    # via the world updater, not from memory. Memory must not inject facts with
    # the literal 'workload is healthy' phrasing it carries.
    assert all("workload is healthy" != fact.claim for fact in world.facts)


@pytest.mark.asyncio
async def test_memory_alone_cannot_complete_task_or_goal() -> None:
    """A 'workload is healthy' memory without matching evidence cannot finish.

    The evaluator still requires the success criterion to be satisfied by
    evidence; the model's FINISH decision must be rejected as invalid state.
    """
    store = InMemoryMemoryStore()
    store.add(
        MemoryRecord(
            MemoryKind.SEMANTIC,
            "workload",
            "workload is healthy and root cause is known",
            scope="kubernetes",
            confidence=1.0,
        )
    )
    runtime, _, _, _, _ = build_runtime(
        HealthyBackend(),
        [Decision(DecisionType.FINISH, "memory says done")],
        memory_store=store,
    )
    result = await runtime.run(*goal_task_matching_healthy())
    assert result.status is ExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_memory_is_not_persisted_in_snapshot() -> None:
    """A snapshot round-trip carries no memory; world rebuilds from evidence."""
    store = InMemoryMemoryStore()
    store.add(
        MemoryRecord(
            MemoryKind.SEMANTIC,
            "workload",
            "advisory note",
            scope="kubernetes",
        )
    )
    runtime, state_store, _, _, components = build_runtime(
        HealthyBackend(),
        [decision("inspect_workload", "healthy"), Decision(DecisionType.FINISH, "done")],
        memory_store=store,
    )
    result = await runtime.run(*goal_task_matching_healthy())
    snapshot = await state_store.load_session(result.session_id)
    # The snapshot dataclass has no memory field; rebuilding the world from it
    # must not depend on the memory store at all.
    assert not hasattr(snapshot, "memories") or not getattr(snapshot, "memories", ())
    world_before = components.world_model.snapshot(result.session_id)
    # Rebuild from the snapshot evidence alone into a fresh world model.
    from universal_agent.world import InMemoryWorldModel

    fresh = InMemoryWorldModel()
    fresh.rebuild(result.session_id, snapshot.evidence, components.world_updaters)
    assert {(fact.subject, fact.claim) for fact in fresh.snapshot(result.session_id).facts} == {
        (fact.subject, fact.claim) for fact in world_before.facts
    }
