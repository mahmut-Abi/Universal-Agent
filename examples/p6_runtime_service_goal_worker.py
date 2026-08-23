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
                Decision(
                    DecisionType.EXECUTE,
                    "Inspect workload from scheduled goal",
                    capability="inspect_workload",
                    target="deployment/example",
                    arguments=immutable_json({"name": "example"}),
                    expected_observations=("healthy",),
                ),
                Decision(DecisionType.FINISH, "Required evidence is present"),
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
    scheduled = service.distributed_schedule_goal(
        Goal("Verify workload through scheduled goal", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
        priority=4,
    )
    worker = await service.distributed_run_worker_once(WorkerId("agent-worker-a"))
    sessions = await service.list_sessions()

    assert scheduled is not None
    assert worker is not None
    assert len(sessions) == 1
    print(f"scheduled_kind={scheduled.scheduled_work_item.kind}")
    print(f"worker={worker.status.value}")
    print(f"session={sessions[0].goal_status.value}")


if __name__ == "__main__":
    asyncio.run(main())
