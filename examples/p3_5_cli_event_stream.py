from __future__ import annotations

import asyncio
from io import StringIO

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
from universal_agent.core import JsonMapping, SessionId
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent_cli import run_cli


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


async def run_agent_command(args: list[str], service: RuntimeService) -> StringIO:
    output = StringIO()
    status = await run_cli(args, service=service, stdout=output)
    assert status == 0
    return output


async def main() -> None:
    service, backend = build_service()
    await run_agent_command(["ready"], service)

    waiting = await service.run_goal(
        Goal("Verify workload through CLI", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    session_id = str(waiting.result.session_id)
    await run_agent_command(["session", "list"], service)
    await run_agent_command(
        ["session", "pause", session_id, "--reason", "operator paused from example"],
        service,
    )
    await run_agent_command(["session", "events", session_id, "--limit", "3"], service)
    sse_output = await run_agent_command(
        ["session", "events", session_id, "--limit", "3", "--format", "sse"],
        service,
    )
    assert "event: GoalCreated\n" in sse_output.getvalue()
    existing_events = await service.stream_events(SessionId(session_id))
    last_cursor = existing_events.events[-1].event_id
    wait_task = asyncio.create_task(
        run_agent_command(
            [
                "session",
                "events",
                session_id,
                "--after",
                last_cursor,
                "--wait",
                "--timeout-seconds",
                "1",
                "--poll-interval-seconds",
                "0.001",
            ],
            service,
        )
    )
    await asyncio.sleep(0.01)
    await run_agent_command(["session", "resume", session_id], service)
    wait_output = await wait_task
    assert '"events": []' not in wait_output.getvalue()
    print(f"inspections={backend.inspect_calls}")
    print("event_stream=ok")


if __name__ == "__main__":
    asyncio.run(main())
