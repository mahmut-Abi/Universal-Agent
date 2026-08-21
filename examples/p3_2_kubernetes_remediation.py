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
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class FakeRemediationBackend:
    def __init__(self) -> None:
        self.scaled = False
        self.inspect_calls: list[str] = []
        self.mutation_calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls.append(capability)
        if capability == "inspect_workload":
            if not self.scaled:
                return immutable_json(
                    {
                        "resource": "deployment/example",
                        "healthy": False,
                        "desired_replicas": 3,
                        "ready_replicas": 1,
                    }
                )
            return immutable_json(
                {
                    "resource": "deployment/example",
                    "healthy": True,
                    "desired_replicas": 3,
                    "ready_replicas": 3,
                    "verification_observed": True,
                }
            )
        if capability == "inspect_pod":
            return immutable_json(
                {
                    "resource": "pod/example-123",
                    "root_cause": "under_replicated",
                }
            )
        raise AssertionError(f"unexpected capability: {capability}")

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        self.mutation_calls += 1
        self.scaled = True
        return immutable_json(
            {
                "resource": "deployment/example",
                "mutation_applied": True,
                "previous_replicas": 1,
                "replicas": 3,
                "mutation_id": "mutation-1",
            }
        )


def execute(capability: str, *observations: str) -> Decision:
    arguments: dict[str, str | int] = {"name": "example"}
    if capability == "scale_workload":
        arguments.update({"namespace": "default", "replicas": 3})
    return Decision(
        DecisionType.EXECUTE,
        f"Run {capability}",
        capability=capability,
        target="deployment/example",
        arguments=immutable_json(arguments),
        expected_observations=observations,
    )


async def main() -> None:
    backend = FakeRemediationBackend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                execute("inspect_workload", "healthy"),
                execute("inspect_pod", "root_cause"),
                execute("scale_workload", "mutation_applied"),
                execute("inspect_workload", "verification_observed", "healthy"),
                Decision(DecisionType.FINISH, "Health verified after remediation"),
            ]
        ),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "staging"}),
    )
    result = await runtime.run(
        Goal("Restore workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ()),
    )
    world = components.world_model.snapshot(result.session_id)
    print(f"status={result.status.value} iterations={result.iterations}")
    print(f"healthy={world.value_for('healthy')} mutation_calls={backend.mutation_calls}")
    print("inspections=" + ",".join(backend.inspect_calls))
    print("events=" + " -> ".join(event.type for event in events.events))


if __name__ == "__main__":
    asyncio.run(main())
