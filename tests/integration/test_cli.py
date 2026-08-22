from __future__ import annotations

import json
from io import StringIO
from typing import Any

import pytest

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


class CliBackend:
    def __init__(self) -> None:
        self.inspect_calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls += 1
        assert capability == "inspect_workload"
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload through CLI test service",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def wait() -> Decision:
    return Decision(DecisionType.WAIT, "CLI test waiting point")


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "CLI test finished")


def goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Verify workload from CLI", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )


def build_cli_service(decisions: list[Decision]) -> tuple[RuntimeService, CliBackend]:
    backend = CliBackend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "staging"}),
    )
    api = RuntimeAPI(runtime=runtime, session_store=store, event_reader=events)
    return RuntimeService(runtime_api=api, components=components), backend


def read_json(buffer: StringIO) -> dict[str, Any]:
    loaded: object = json.loads(buffer.getvalue())
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.asyncio
async def test_cli_exposes_service_catalog_commands() -> None:
    service, _ = build_cli_service([])
    output = StringIO()

    status = await run_cli(["capabilities", "list"], service=service, stdout=output)
    payload = read_json(output)

    assert status == 0
    capabilities = payload["capabilities"]
    assert isinstance(capabilities, list)
    assert {item["name"] for item in capabilities if isinstance(item, dict)} >= {
        "inspect_workload",
        "scale_workload",
    }


@pytest.mark.asyncio
async def test_cli_controls_waiting_session_lifecycle_through_service() -> None:
    service, backend = build_cli_service([wait(), inspect_workload(), finish()])
    waiting = await service.run_goal(*goal_task())
    session_id = str(waiting.result.session_id)
    list_output = StringIO()
    pause_output = StringIO()
    events_output = StringIO()
    resume_output = StringIO()

    list_status = await run_cli(
        ["session", "list"],
        service=service,
        stdout=list_output,
    )
    pause_status = await run_cli(
        ["session", "pause", session_id, "--reason", "operator paused from test"],
        service=service,
        stdout=pause_output,
    )
    events_status = await run_cli(
        ["session", "events", session_id, "--limit", "2"],
        service=service,
        stdout=events_output,
    )
    resume_status = await run_cli(
        ["session", "resume", session_id], service=service, stdout=resume_output
    )

    list_payload = read_json(list_output)
    pause_payload = read_json(pause_output)
    events_payload = read_json(events_output)
    resume_payload = read_json(resume_output)
    assert list_status == 0
    assert pause_status == 0
    assert events_status == 0
    assert resume_status == 0
    session_items = list_payload["sessions"]
    assert isinstance(session_items, list)
    assert len(session_items) == 1
    listed_session = session_items[0]
    assert isinstance(listed_session, dict)
    assert listed_session["session_id"] == session_id
    assert listed_session["goal_status"] == "waiting"
    assert listed_session["pending_action"] is False
    assert pause_payload["result"]["status"] == "waiting"
    assert len(events_payload["events"]) == 2
    assert events_payload["next_cursor"] == events_payload["events"][-1]["event_id"]
    assert resume_payload["result"]["status"] == "completed"
    assert backend.inspect_calls == 1
