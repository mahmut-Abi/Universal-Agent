from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

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
from universal_agent.evaluation import DeterministicClock, DeterministicRuntimeMode


class FakeDeterministicBackend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "inspect_workload"
        return immutable_json(
            {
                "resource": f"deployment/{arguments['name']}",
                "healthy": True,
                "capability": capability,
            }
        )

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
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
        "Inspect workload under deterministic runtime primitives",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Health evidence is present")


def build_service() -> RuntimeService:
    backend = FakeDeterministicBackend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter([inspect_workload(), finish()]),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "staging"}),
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )


async def main() -> None:
    with DeterministicRuntimeMode(
        clock=DeterministicClock(
            start=datetime(2026, 1, 1, tzinfo=UTC),
            step=timedelta(seconds=1),
        )
    ):
        goal = Goal("Verify workload deterministically", (SuccessCriterion("healthy", True),))
        task = Task("Inspect workload", ("healthy",))
        service = build_service()
        run = await service.run_goal(goal, task)
        events = await service.list_events(run.result.session_id)

    print(f"session_id={run.result.session_id}")
    print(f"goal_id={goal.id} task_id={task.id}")
    print(f"first_event={events[0].event_id}:{events[0].occurred_at.isoformat()}")
    print(f"last_event={events[-1].event_id}:{events[-1].type}")


if __name__ == "__main__":
    asyncio.run(main())
