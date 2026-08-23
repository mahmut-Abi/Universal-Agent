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
    def __init__(self) -> None:
        self.scaled = False

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json(
            {
                "resource": "deployment/example",
                "healthy": self.scaled,
                "verification_observed": self.scaled,
            }
        )

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.scaled = True
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
                    "Scale workload after distributed confirmation",
                    capability="scale_workload",
                    target="deployment/example",
                    arguments=immutable_json(
                        {"name": "example", "namespace": "default", "replicas": 3}
                    ),
                    expected_observations=("mutation_applied",),
                ),
                Decision(
                    DecisionType.EXECUTE,
                    "Verify workload after distributed action",
                    capability="inspect_workload",
                    target="deployment/example",
                    arguments=immutable_json({"name": "example"}),
                    expected_observations=("verification_observed", "healthy"),
                ),
                Decision(DecisionType.FINISH, "Required evidence is present"),
            ]
        ),
        state_store=state_store,
        components=components,
        event_sink=event_sink,
        environment=immutable_json({"environment": "production"}),
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
    waiting = await service.run_goal(
        Goal("Restore workload through distributed action", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    pending = waiting.session.pending_action
    if pending is None:
        raise RuntimeError("expected production scale action to require confirmation")

    scheduled = service.distributed_schedule_action(
        waiting.result.session_id,
        waiting.session.current_task_id,
        pending.action_id,
        confirmed=True,
        priority=4,
    )
    worker = await service.distributed_run_worker_once(WorkerId("agent-worker-a"))
    completed = await service.get_session(waiting.result.session_id)

    assert scheduled is not None
    assert worker is not None
    print(f"waiting={waiting.result.status.value}")
    print(f"scheduled_kind={scheduled.scheduled_work_item.kind}")
    print(f"scheduled_action={scheduled.scheduled_work_item.action_id}")
    print(f"worker={worker.status.value}")
    print(f"session={completed.goal_status.value}")


if __name__ == "__main__":
    asyncio.run(main())
