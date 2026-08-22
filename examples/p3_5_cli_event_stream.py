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
from universal_agent.cli import run_cli
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class FakeCliBackend:
    def __init__(self) -> None:
        self.inspect_calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls += 1
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


def wait() -> Decision:
    return Decision(DecisionType.WAIT, "Pause before CLI-controlled resume")


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload after CLI resume",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def build_service() -> tuple[RuntimeService, FakeCliBackend]:
    backend = FakeCliBackend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                wait(),
                inspect_workload(),
                Decision(DecisionType.FINISH, "Health evaluation completed after CLI resume"),
            ]
        ),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "staging"}),
    )
    return (
        RuntimeService(
            runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
            components=components,
        ),
        backend,
    )


async def main() -> None:
    service, backend = build_service()
    await run_cli(["ready"], service=service)

    waiting = await service.run_goal(
        Goal("Verify workload through CLI", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    session_id = str(waiting.result.session_id)
    await run_cli(
        ["session", "pause", session_id, "--reason", "operator paused from example"],
        service=service,
    )
    await run_cli(["session", "events", session_id, "--limit", "3"], service=service)
    await run_cli(["session", "resume", session_id], service=service)
    print(f"inspections={backend.inspect_calls}")


if __name__ == "__main__":
    asyncio.run(main())
