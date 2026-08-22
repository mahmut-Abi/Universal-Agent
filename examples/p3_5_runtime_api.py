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
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesDomain


class FakeKubernetesBackend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json(
            {
                "healthy": True,
                "resource": f"deployment/{arguments['name']}",
                "capability": capability,
            }
        )


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload through the Runtime API",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


async def main() -> None:
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesDomain(FakeKubernetesBackend()))
    )
    events = InMemoryEventSink()
    store = InMemoryStateStore()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                inspect_workload(),
                Decision(DecisionType.FINISH, "Health evaluation completed"),
            ]
        ),
        state_store=store,
        components=components,
        event_sink=events,
    )
    api = RuntimeAPI(
        runtime=runtime,
        session_store=store,
        event_reader=events,
    )

    run = await api.run_goal(
        Goal("Verify workload through RuntimeAPI", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    session = await api.get_session(run.result.session_id)
    sessions = await api.list_sessions()
    runtime_events = await api.list_events(run.result.session_id)

    print(f"status={run.result.status.value} iterations={run.result.iterations}")
    print(f"session={session.session_id} goal_status={session.goal_status.value}")
    print(f"session_count={len(sessions)}")
    print(f"current_task={session.current_task_status.value}")
    print("events=" + " -> ".join(event.type for event in runtime_events))


if __name__ == "__main__":
    asyncio.run(main())
