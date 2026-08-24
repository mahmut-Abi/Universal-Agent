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

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        if capability == "inspect_workload":
            return immutable_json(
                {
                    "resource": "deployment/example",
                    "healthy": self.scaled,
                    "desired_replicas": 3,
                    "ready_replicas": 3 if self.scaled else 1,
                    "verification_observed": self.scaled,
                }
            )
        return immutable_json({"resource": "pod/example-123", "root_cause": "under_replicated"})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.scaled = True
        return immutable_json(
            {
                "resource": "deployment/example",
                "mutation_applied": True,
                "capability": capability,
                "resource_version": "rv-2",
            }
        )


def scale_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Scale deployment with runtime-owned resource locking",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json(
            {
                "name": "example",
                "namespace": "default",
                "replicas": 3,
                "resource_version": "rv-1",
            }
        ),
        expected_observations=("mutation_applied",),
    )


def verify_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Verify the deployment after mutation",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


async def main() -> None:
    backend = FakeRemediationBackend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    components.resource_versions.set_current("deployment/example", "rv-1")
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                scale_workload(),
                verify_workload(),
                Decision(DecisionType.FINISH, "Workload is healthy"),
            ]
        ),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "staging"}),
    )

    result = await runtime.run(
        Goal("Scale and verify workload", (SuccessCriterion("healthy", True),)),
        Task("Scale workload", ("mutation_applied",)),
    )
    lock_events = [
        event.type
        for event in events.events
        if event.type in {"ResourceLockAcquired", "ResourceLockReleased"}
    ]
    version_events = [
        event.type
        for event in events.events
        if event.type in {"ResourceVersionChecked", "ResourceVersionUpdated"}
    ]

    print(f"status={result.status.value} error={result.error_code}")
    print("locks=" + " -> ".join(lock_events))
    print("versions=" + " -> ".join(version_events))
    print(f"current_version={components.resource_versions.current('deployment/example')}")
    print(f"active_locks={len(components.resource_locks.active())}")


if __name__ == "__main__":
    asyncio.run(main())
