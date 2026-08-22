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
    RuntimeAPI,
    RuntimeBuilder,
    RuntimeService,
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class FakeTraceBackend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json(
            {
                "healthy": True,
                "resource": f"deployment/{arguments['name']}",
                "capability": capability,
            }
        )

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json(
            {
                "resource": f"deployment/{arguments['name']}",
                "mutation_applied": True,
                "capability": capability,
            }
        )


def scale_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Apply safe scaling action before trace verification",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 3}),
        expected_observations=("mutation_applied",),
    )


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload for trace verification",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


async def main() -> None:
    backend = FakeTraceBackend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                scale_workload(),
                inspect_workload(),
                Decision(DecisionType.FINISH, "Health evidence is present"),
            ]
        ),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "staging"}),
    )
    service = RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )
    run = await service.run_goal(
        Goal("Verify workload with trace spans", (SuccessCriterion("healthy", True),)),
        Task("Scale then inspect workload", ("healthy",)),
    )
    spans = await service.traces(run.result.session_id)

    print(f"status={run.result.status.value} spans={len(spans)}")
    for span in spans:
        parent = span.parent_span_id or "root"
        print(f"{span.name}:{span.status}:parent={parent}:duration_ms={span.duration_ms}")


if __name__ == "__main__":
    asyncio.run(main())
