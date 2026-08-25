from __future__ import annotations

import asyncio
from collections.abc import Mapping

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
from universal_agent.core import JsonMapping, JsonValue
from universal_agent.domains.kubernetes import (
    KubernetesApiBackend,
    KubernetesApiResponse,
    KubernetesRemediationDomain,
)


class DemoKubernetesApiTransport:
    """Fixture transport that shows the Kubernetes API adapter contract."""

    def __init__(self) -> None:
        self.scaled = False
        self.requests: list[tuple[str, str, JsonMapping | None]] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: JsonMapping | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> KubernetesApiResponse:
        del query, headers, timeout_seconds
        self.requests.append((method, path, body))
        if method == "GET" and path == "/apis/apps/v1/namespaces/default/deployments/example":
            return KubernetesApiResponse(200, self._deployment())
        if (
            method == "PATCH"
            and path == "/apis/apps/v1/namespaces/default/deployments/example/scale"
        ):
            self.scaled = True
            return KubernetesApiResponse(200, {"metadata": {"resourceVersion": "rv-after"}})
        raise AssertionError(f"unexpected Kubernetes API request: {method} {path}")

    def _deployment(self) -> dict[str, JsonValue]:
        ready = 3 if self.scaled else 1
        resource_version = "rv-after" if self.scaled else "rv-before"
        return {
            "metadata": {
                "name": "example",
                "resourceVersion": resource_version,
                "generation": 2,
            },
            "spec": {"replicas": 3},
            "status": {
                "readyReplicas": ready,
                "availableReplicas": ready,
                "updatedReplicas": ready,
                "observedGeneration": 2,
                "conditions": [{"type": "Available", "status": "True"}],
            },
        }


def execute(capability: str, *observations: str) -> Decision:
    arguments: dict[str, JsonValue] = {"name": "example", "namespace": "default"}
    if capability == "scale_workload":
        arguments["replicas"] = 3
    return Decision(
        DecisionType.EXECUTE,
        f"Run {capability} through Kubernetes API",
        capability=capability,
        target="deployment/example",
        arguments=immutable_json(arguments),
        expected_observations=observations,
    )


async def main() -> None:
    transport = DemoKubernetesApiTransport()
    backend = KubernetesApiBackend(
        api_server="https://cluster.example.test",
        transport=transport,
    )
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                execute("inspect_workload", "healthy"),
                execute("scale_workload", "mutation_applied"),
                execute("inspect_workload", "healthy"),
                Decision(DecisionType.FINISH, "Kubernetes API remediation verified"),
            ]
        ),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "staging"}),
    )
    result = await runtime.run(
        Goal("Verify Kubernetes API backend", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    world = components.world_model.snapshot(result.session_id)
    print(f"status={result.status.value} iterations={result.iterations}")
    print(f"healthy={world.value_for('healthy')} request_count={len(transport.requests)}")
    print(f"scale_payload={transport.requests[2][2]}")
    print("events=" + " -> ".join(event.type for event in events.events))


if __name__ == "__main__":
    asyncio.run(main())
