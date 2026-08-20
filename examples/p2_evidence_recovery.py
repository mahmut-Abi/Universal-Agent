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
from universal_agent.domains.kubernetes import KubernetesDomain


class FakeDiagnosticBackend:
    def __init__(self) -> None:
        self.calls = 0

    async def inspect(self, capability, arguments):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("simulated transient timeout")
        return immutable_json(
            {
                "resource": "deployment/example",
                "healthy": True,
                "capability": capability,
            }
        )


async def main() -> None:
    backend = FakeDiagnosticBackend()
    domain = DomainLoader().load(KubernetesDomain(backend))
    components = RuntimeBuilder().build(domain)
    model = ScriptedModelAdapter(
        [
            Decision(
                DecisionType.EXECUTE,
                "Inspect workload health",
                capability="inspect_workload",
                target="deployment/example",
                arguments=immutable_json({"name": "example"}),
                expected_observations=("healthy",),
            ),
            Decision(DecisionType.FINISH, "Evidence-backed health check completed"),
        ]
    )
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=model,
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=events,
    )
    result = await runtime.run(
        Goal("Verify workload", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    world = components.world_model.snapshot(result.session_id)
    print(f"status={result.status.value} iterations={result.iterations}")
    print(f"healthy={world.value_for('healthy')} backend_calls={backend.calls}")
    print("events=" + " -> ".join(event.type for event in events.events))


if __name__ == "__main__":
    asyncio.run(main())
