from __future__ import annotations

import asyncio

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
from universal_agent.core import ExecutionResult, JsonMapping
from universal_agent.domains.kubernetes import KubernetesDomain
from universal_agent.memory import InMemoryMemoryStore


class HealthyBackend:
    """Returns a healthy workload on the first inspection."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.calls.append(capability)
        return immutable_json({"resource": "deployment/example", "healthy": True})


async def run_once(
    store: InMemoryMemoryStore,
    decisions: list[Decision],
) -> tuple[ExecutionResult, ScriptedModelAdapter, InMemoryEventSink]:
    backend = HealthyBackend()
    components = RuntimeBuilder(memory_store_factory=lambda: store).build(
        DomainLoader().load(KubernetesDomain(backend))
    )
    events = InMemoryEventSink()
    model = ScriptedModelAdapter(decisions)
    runtime = AgentRuntime(
        model=model,
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=events,
    )
    result = await runtime.run(
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    return result, model, events


async def main() -> None:
    # A single shared memory store survives across both sessions.
    shared_store = InMemoryMemoryStore()

    first, first_model, _ = await run_once(
        shared_store,
        [
            Decision(
                DecisionType.EXECUTE,
                "Inspect workload health",
                capability="inspect_workload",
                target="deployment/example",
                arguments=immutable_json({"name": "example"}),
                expected_observations=("healthy",),
            ),
            Decision(DecisionType.FINISH, "Health check completed"),
        ],
    )
    print(f"[session 1] status={first.status.value}")
    print(
        "[session 1] memory_context="
        + ", ".join(fragment.key for fragment in first_model.contexts[-1].memory_context)
    )
    # Domain-declared procedural knowledge appears even on the first run.
    assert first_model.contexts[-1].memory_context

    # Session 2 shares the store, so the episodic record from session 1 is
    # recalled alongside the domain's procedural knowledge. It still has to
    # produce the observation-backed evidence the evaluator needs, so it runs
    # the same inspection as session 1 rather than finishing empty-handed.
    second, second_model, _ = await run_once(
        shared_store,
        [
            Decision(
                DecisionType.EXECUTE,
                "Re-inspect workload health",
                capability="inspect_workload",
                target="deployment/example",
                arguments=immutable_json({"name": "example"}),
                expected_observations=("healthy",),
            ),
            Decision(DecisionType.FINISH, "Health check completed with prior recall"),
        ],
    )
    print(f"[session 2] status={second.status.value}")
    print(
        "[session 2] memory_context="
        + ", ".join(fragment.key for fragment in second_model.contexts[0].memory_context)
    )
    episodic = [r for r in shared_store.export() if r.kind.value == "episodic"]
    print(f"[store] episodic_records={len(episodic)} total={len(shared_store.export())}")


if __name__ == "__main__":
    asyncio.run(main())
