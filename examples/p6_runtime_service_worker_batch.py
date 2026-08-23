from __future__ import annotations

import asyncio

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DistributedRuntimeCoordinator,
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
    WorkerId,
    immutable_json,
)
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class Backend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload after distributed resume",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def build_service() -> RuntimeService:
    backend = Backend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    state_store = InMemoryStateStore()
    event_sink = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                Decision(DecisionType.WAIT, "pause first session before worker resume"),
                Decision(DecisionType.WAIT, "pause second session before worker resume"),
                inspect_workload(),
                Decision(DecisionType.FINISH, "First session verified"),
                inspect_workload(),
                Decision(DecisionType.FINISH, "Second session verified"),
            ]
        ),
        state_store=state_store,
        components=components,
        event_sink=event_sink,
        environment=immutable_json({"environment": "staging"}),
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(
            runtime=runtime,
            session_store=state_store,
            event_reader=event_sink,
        ),
        components=components,
        distributed_coordinator=DistributedRuntimeCoordinator(),
    )


async def main() -> None:
    service = build_service()
    goal = Goal("Verify workload through batch worker", (SuccessCriterion("healthy", True),))
    task = Task("Inspect workload", ("healthy",))
    first = await service.run_goal(goal, task)
    second = await service.run_goal(goal, task)

    service.distributed_schedule_session(first.result.session_id)
    service.distributed_schedule_session(second.result.session_id)
    results = await service.distributed_run_worker_until_idle(
        WorkerId("agent-worker-a"),
        max_items=5,
    )
    first_completed = await service.get_session(first.result.session_id)
    second_completed = await service.get_session(second.result.session_id)

    print(f"results={[result.status.value for result in results or ()]}")
    print(f"first_session={first_completed.goal_status.value}")
    print(f"second_session={second_completed.goal_status.value}")


if __name__ == "__main__":
    asyncio.run(main())
