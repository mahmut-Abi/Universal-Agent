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
    ModelUsage,
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


class FakeCostBackend:
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


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload with usage tracking",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Health evidence is present")


async def main() -> None:
    backend = FakeCostBackend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [inspect_workload(), finish()],
            usage=[
                ModelUsage(
                    "scripted",
                    "fixture-model",
                    input_tokens=120,
                    output_tokens=30,
                    estimated_cost_micros=42,
                ),
                ModelUsage(
                    "scripted",
                    "fixture-model",
                    input_tokens=40,
                    output_tokens=10,
                    estimated_cost_micros=14,
                ),
            ],
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
        Goal("Verify workload with cost tracking", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    cost = await service.cost(run.result.session_id)
    metrics = await service.metrics()

    print(f"status={run.result.status.value}")
    print(f"model_calls={cost.model_call_count} tokens={cost.total_tokens}")
    print(f"estimated_cost_micros={cost.estimated_cost_micros} currency={cost.currency}")
    print(f"metrics_model_tokens={metrics.model_total_token_count}")


if __name__ == "__main__":
    asyncio.run(main())
