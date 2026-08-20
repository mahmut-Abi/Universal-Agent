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


class FakeKubernetesBackend:
    async def inspect(self, capability, arguments):  # type: ignore[no-untyped-def]
        return immutable_json(
            {
                "healthy": True,
                "resource": arguments["name"],
                "capability": capability,
            }
        )


async def main() -> None:
    domain = DomainLoader().load(KubernetesDomain(FakeKubernetesBackend()))
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
            Decision(DecisionType.FINISH, "Health evaluation completed"),
        ]
    )
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=model,
        state_store=InMemoryStateStore(),
        components=RuntimeBuilder().build(domain),
        event_sink=events,
    )
    result = await runtime.run(
        Goal("Verify workload", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    print(f"status={result.status.value} iterations={result.iterations}")
    print("events=" + " -> ".join(event.type for event in events.events))


if __name__ == "__main__":
    asyncio.run(main())
