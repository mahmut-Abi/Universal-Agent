from __future__ import annotations

import asyncio
import json

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
from universal_agent.core import JsonValue
from universal_agent.domains.kubernetes import KubectlBackend, KubectlResult, KubernetesDomain


class DemoKubectlRunner:
    """Fixture runner that shows the kubectl adapter contract without touching a cluster."""

    async def run(
        self,
        args: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
    ) -> KubectlResult:
        del timeout_seconds
        responses: dict[tuple[str, ...], dict[str, JsonValue]] = {
            (
                "get",
                "deployment",
                "example",
                "--namespace",
                "default",
                "-o",
                "json",
            ): {
                "metadata": {"name": "example", "resourceVersion": "rv-1", "generation": 2},
                "spec": {"replicas": 3},
                "status": {
                    "readyReplicas": 3,
                    "availableReplicas": 3,
                    "updatedReplicas": 3,
                    "observedGeneration": 2,
                    "conditions": [{"type": "Available", "status": "True"}],
                },
            }
        }
        return KubectlResult(args, json.dumps(responses[args]), "", 0)


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload through kubectl backend",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default"}),
        expected_observations=("healthy",),
    )


async def main() -> None:
    backend = KubectlBackend(runner=DemoKubectlRunner())
    components = RuntimeBuilder().build(DomainLoader().load(KubernetesDomain(backend)))
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                inspect_workload(),
                Decision(DecisionType.FINISH, "Health evaluation completed"),
            ]
        ),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=events,
    )
    result = await runtime.run(
        Goal("Verify kubectl-backed workload", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    print(f"status={result.status.value} iterations={result.iterations}")
    print("events=" + " -> ".join(event.type for event in events.events))


if __name__ == "__main__":
    asyncio.run(main())
