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
    ModelUsage,
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


def scale_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Scale workload through CLI test service",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 3}),
        expected_observations=("mutation_applied",),
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


def build_cli_service(
    decisions: list[Decision],
    *,
    usage: list[ModelUsage] | None = None,
) -> tuple[RuntimeService, CliBackend]:
    backend = CliBackend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions, usage=usage or ()),
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


@pytest.mark.asyncio
async def test_cli_exposes_operations_commands_through_service() -> None:
    service, backend = build_cli_service(
        [scale_workload(), inspect_workload(), finish()],
        usage=[
            ModelUsage(
                "scripted",
                "cli-test",
                input_tokens=80,
                output_tokens=20,
                estimated_cost_micros=18,
            ),
            ModelUsage(
                "scripted",
                "cli-test",
                input_tokens=40,
                output_tokens=10,
                estimated_cost_micros=9,
            ),
            ModelUsage("scripted", "cli-test", input_tokens=10, output_tokens=5),
        ],
    )
    run = await service.run_goal(*goal_task())
    session_id = str(run.result.session_id)
    metrics_output = StringIO()
    cost_output = StringIO()
    doctor_output = StringIO()
    audit_output = StringIO()
    session_audit_output = StringIO()
    session_cost_output = StringIO()

    metrics_status = await run_cli(["metrics"], service=service, stdout=metrics_output)
    cost_status = await run_cli(["cost"], service=service, stdout=cost_output)
    doctor_status = await run_cli(["doctor"], service=service, stdout=doctor_output)
    audit_status = await run_cli(["audit"], service=service, stdout=audit_output)
    session_audit_status = await run_cli(
        ["session", "audit", session_id],
        service=service,
        stdout=session_audit_output,
    )
    session_cost_status = await run_cli(
        ["session", "cost", session_id],
        service=service,
        stdout=session_cost_output,
    )

    metrics = read_json(metrics_output)
    cost = read_json(cost_output)
    doctor = read_json(doctor_output)
    audit = read_json(audit_output)
    session_audit = read_json(session_audit_output)
    session_cost = read_json(session_cost_output)
    assert metrics_status == 0
    assert cost_status == 0
    assert doctor_status == 0
    assert audit_status == 0
    assert session_audit_status == 0
    assert session_cost_status == 0
    assert metrics["completed_goal_count"] == 1
    assert metrics["action_started_count"] == 2
    assert metrics["model_call_count"] == 3
    assert metrics["model_total_token_count"] == 165
    assert cost == session_cost
    assert cost["model_call_count"] == 3
    assert cost["total_tokens"] == 165
    assert cost["estimated_cost_micros"] == 27
    assert doctor["status"] == "ok"
    assert audit == session_audit
    audit_items = audit["audit_records"]
    assert isinstance(audit_items, list)
    assert len(audit_items) == 1
    record = audit_items[0]
    assert isinstance(record, dict)
    assert record["session_id"] == session_id
    assert record["capability"] == "scale_workload"
    assert record["status"] == "succeeded"
    assert backend.inspect_calls == 1
