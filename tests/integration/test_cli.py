from __future__ import annotations

import json
from dataclasses import replace
from io import StringIO
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import fromstring

import pytest

from universal_agent import (
    AgentProfile,
    AgentRuntime,
    Decision,
    DecisionType,
    DomainConfig,
    DomainLoader,
    Goal,
    InMemoryEventSink,
    InMemoryStateStore,
    ModelUsage,
    ProfileConfig,
    RuntimeAPI,
    RuntimeBuilder,
    RuntimeConfig,
    RuntimeLimitsConfig,
    RuntimeService,
    ScriptedModelAdapter,
    StoreConfig,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.agentd import AgentdHttpServer
from universal_agent.cli import run_cli
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.evaluation.recording import (
    FileEvaluationReportStore,
    FileReplayRecordingStore,
    encode_evaluation_report,
)


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


def invalid_scale_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Invalid scale through CLI test service",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 0}),
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


def cli_profile() -> AgentProfile:
    domain = DomainConfig("kubernetes", "0.2.0")
    return AgentProfile(
        "production-operator",
        "1.0.0",
        "Production Kubernetes operator",
        domain,
        RuntimeConfig(environment=immutable_json({"environment": "staging"}), domain=domain),
        (domain,),
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
    config = RuntimeConfig(
        environment=immutable_json({"environment": "staging"}),
        store=StoreConfig.memory(),
        limits=RuntimeLimitsConfig(max_iterations=12, max_recovery_steps=4),
        domain=DomainConfig("kubernetes", "0.2.0"),
    )
    return RuntimeService(
        runtime_api=api,
        components=components,
        profiles=(cli_profile(),),
        config=config,
    ), backend


def read_json(buffer: StringIO) -> dict[str, Any]:
    loaded: object = json.loads(buffer.getvalue())
    assert isinstance(loaded, dict)
    return loaded


def write_evaluation_suite_file(
    path: Path,
    *,
    quality_gate: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "name": "file evaluation suite",
        "tags": ["file", "kubernetes"],
        "scenarios": [
            {
                "name": "file healthy workload",
                "kind": "regression",
                "tags": ["smoke", "file"],
                "goal": {
                    "description": "Evaluate workload health from file",
                    "success_criteria": {"healthy": True},
                },
                "task": {
                    "description": "Inspect workload from file",
                    "required_criteria": ["healthy"],
                },
                "expectations": {
                    "expected_status": "completed",
                    "expected_criteria": {"healthy": True},
                    "required_events": ["GoalCompleted", "EvaluationCompleted"],
                    "required_evidence_claims": ["healthy"],
                    "required_capabilities": ["inspect_workload"],
                    "allowed_capabilities": ["inspect_workload"],
                    "max_actions": 1,
                },
            }
        ],
    }
    if quality_gate is not None:
        payload["quality_gate"] = quality_gate
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.asyncio
async def test_cli_init_writes_parseable_profile_config(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "profile.json"
    store_path = tmp_path / "store"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--profile",
            "production-operator",
            "--environment",
            "production",
            "--store-path",
            str(store_path),
        ],
        stdout=output,
    )
    payload = read_json(output)
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert payload == {
        "status": "created",
        "profile": "production-operator",
        "path": str(profile_path),
    }
    assert profile.name == "production-operator"
    assert profile.domain == DomainConfig("kubernetes", "0.2.0")
    assert profile.runtime.store == StoreConfig.file(str(store_path))
    assert profile.runtime.environment["environment"] == "production"


@pytest.mark.asyncio
async def test_cli_init_can_write_sqlite_profile_config(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "sqlite-profile.json"
    store_path = tmp_path / "runtime.sqlite3"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--store-backend",
            "sqlite",
            "--store-path",
            str(store_path),
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.runtime.store == StoreConfig.sqlite(str(store_path))


@pytest.mark.asyncio
async def test_cli_init_can_write_memory_profile_config(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "memory-profile.json"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--store-backend",
            "memory",
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.runtime.store == StoreConfig.memory()


@pytest.mark.asyncio
async def test_cli_init_rejects_existing_profile_without_force(tmp_path: Path) -> None:
    output = StringIO()
    error = StringIO()
    profile_path = tmp_path / "profile.json"
    profile_path.write_text("{}", encoding="utf-8")

    status = await run_cli(
        ["init", "--output", str(profile_path)],
        stdout=output,
        stderr=error,
    )

    assert status == 2
    assert output.getvalue() == ""
    assert f"profile config already exists: {profile_path}" in error.getvalue()


@pytest.mark.asyncio
async def test_cli_init_force_overwrites_existing_profile(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "profile.json"
    profile_path.write_text("{}", encoding="utf-8")

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--profile",
            "replacement-profile",
            "--environment",
            "staging",
            "--force",
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.name == "replacement-profile"
    assert profile.runtime.environment["environment"] == "staging"


@pytest.mark.asyncio
async def test_cli_profile_config_drives_run_and_persisted_session_reads(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    store_path = tmp_path / "runtime-store"
    init_output = StringIO()
    run_output = StringIO()
    list_output = StringIO()
    config_output = StringIO()

    init_status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--profile",
            "configured-operator",
            "--environment",
            "production",
            "--store-backend",
            "file",
            "--store-path",
            str(store_path),
        ],
        stdout=init_output,
    )
    run_status = await run_cli(
        [
            "--profile-config",
            str(profile_path),
            "run",
            "configured-operator",
            "Verify configured workload health",
        ],
        stdout=run_output,
    )
    run_payload = read_json(run_output)
    result = run_payload["result"]
    assert isinstance(result, dict)
    session_id = result["session_id"]
    assert isinstance(session_id, str)

    list_status = await run_cli(
        ["--profile-config", str(profile_path), "session", "list"],
        stdout=list_output,
    )
    config_status = await run_cli(
        ["--profile-config", str(profile_path), "config", "show"],
        stdout=config_output,
    )
    list_payload = read_json(list_output)
    config_payload = read_json(config_output)

    assert init_status == 0
    assert run_status == 0
    assert list_status == 0
    assert config_status == 0
    assert result["status"] == "completed"
    assert run_payload["session"]["goal_description"] == "Verify configured workload health"
    assert list_payload["sessions"][0]["session_id"] == session_id
    assert config_payload["environment"] == {"environment": "production"}
    assert config_payload["store"] == {"backend": "file", "path": str(store_path)}
    assert list((store_path / "sessions").glob("*.json"))
    assert (store_path / "events.jsonl").exists()


@pytest.mark.asyncio
async def test_cli_run_submits_goal_through_service() -> None:
    service, backend = build_cli_service([inspect_workload(), finish()])
    output = StringIO()

    status = await run_cli(
        ["run", "production-operator", "Verify workload health"],
        service=service,
        stdout=output,
    )
    payload = read_json(output)

    assert status == 0
    assert payload["result"]["status"] == "completed"
    assert payload["session"]["goal_description"] == "Verify workload health"
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_tui_renders_runtime_service_snapshot() -> None:
    service, backend = build_cli_service([inspect_workload(), finish()])
    run_output = StringIO()
    tui_output = StringIO()

    run_status = await run_cli(
        ["run", "production-operator", "Verify workload health"],
        service=service,
        stdout=run_output,
    )
    run_payload = read_json(run_output)
    session_id = run_payload["result"]["session_id"]
    assert isinstance(session_id, str)

    tui_status = await run_cli(
        ["tui", "--session-id", session_id, "--event-limit", "20"],
        service=service,
        stdout=tui_output,
    )
    rendered = tui_output.getvalue()

    assert run_status == 0
    assert tui_status == 0
    assert "Universal Agent Runtime TUI" in rendered
    assert "Health: ok | Ready: yes" in rendered
    assert "Active Domains" in rendered
    assert "kubernetes@0.2.0" in rendered
    assert "Selected Session" in rendered
    assert "Verify workload health" in rendered
    assert "Recent Events" in rendered
    assert "ActionStarted" in rendered
    assert "capability=inspect_workload" in rendered
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_session_diagnostics_renders_evidence_and_world_facts() -> None:
    service, backend = build_cli_service([inspect_workload(), finish()])
    run_output = StringIO()
    diagnostics_output = StringIO()

    run_status = await run_cli(
        ["run", "production-operator", "Verify workload health"],
        service=service,
        stdout=run_output,
    )
    run_payload = read_json(run_output)
    session_id = run_payload["result"]["session_id"]
    assert isinstance(session_id, str)

    diagnostics_status = await run_cli(
        ["session", "diagnostics", session_id],
        service=service,
        stdout=diagnostics_output,
    )
    diagnostics = read_json(diagnostics_output)
    evidence_claims = {item["claim"]: item for item in diagnostics["evidence"]}
    world_claims = {item["claim"]: item for item in diagnostics["world_facts"]}

    assert run_status == 0
    assert diagnostics_status == 0
    assert diagnostics["session"]["session_id"] == session_id
    assert evidence_claims["healthy"]["value"] is True
    assert world_claims["healthy"]["value"] is True
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_run_rejects_unknown_profile() -> None:
    service, _ = build_cli_service([])
    output = StringIO()
    error = StringIO()

    status = await run_cli(
        ["run", "missing-profile", "Verify workload health"],
        service=service,
        stdout=output,
        stderr=error,
    )

    assert status == 2
    assert output.getvalue() == ""
    assert "unknown profile: missing-profile" in error.getvalue()


@pytest.mark.asyncio
async def test_cli_profile_show_exposes_one_profile() -> None:
    service, _ = build_cli_service([])
    output = StringIO()

    status = await run_cli(
        ["profile", "show", "production-operator"],
        service=service,
        stdout=output,
    )
    payload = read_json(output)

    assert status == 0
    assert payload == {
        "name": "production-operator",
        "version": "1.0.0",
        "description": "Production Kubernetes operator",
        "domain_name": "kubernetes",
        "domain_version": "0.2.0",
        "domains": [{"name": "kubernetes", "version": "0.2.0"}],
    }


@pytest.mark.asyncio
async def test_cli_profile_show_rejects_unknown_profile() -> None:
    service, _ = build_cli_service([])
    output = StringIO()
    error = StringIO()

    status = await run_cli(
        ["profile", "show", "missing-profile"],
        service=service,
        stdout=output,
        stderr=error,
    )

    assert status == 2
    assert output.getvalue() == ""
    assert "unknown profile: missing-profile" in error.getvalue()


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
async def test_cli_config_show_exposes_runtime_configuration() -> None:
    service, _ = build_cli_service([])
    output = StringIO()

    status = await run_cli(["config", "show"], service=service, stdout=output)
    payload = read_json(output)

    assert status == 0
    assert payload == {
        "environment": {"environment": "staging"},
        "store": {"backend": "memory", "path": None},
        "limits": {"max_iterations": 12, "max_recovery_steps": 4},
        "domains": [{"name": "kubernetes", "version": "0.2.0", "primary": True}],
    }


@pytest.mark.asyncio
async def test_cli_controls_waiting_session_lifecycle_through_service() -> None:
    service, backend = build_cli_service([wait(), inspect_workload(), finish()])
    waiting = await service.run_goal(*goal_task())
    session_id = str(waiting.result.session_id)
    list_output = StringIO()
    pause_output = StringIO()
    events_output = StringIO()
    sse_events_output = StringIO()
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
    sse_events_status = await run_cli(
        ["session", "events", session_id, "--limit", "2", "--format", "sse"],
        service=service,
        stdout=sse_events_output,
    )
    resume_status = await run_cli(
        ["session", "resume", session_id], service=service, stdout=resume_output
    )

    list_payload = read_json(list_output)
    pause_payload = read_json(pause_output)
    events_payload = read_json(events_output)
    sse_events = sse_events_output.getvalue()
    resume_payload = read_json(resume_output)
    assert list_status == 0
    assert pause_status == 0
    assert events_status == 0
    assert sse_events_status == 0
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
    assert "event: GoalCreated\n" in sse_events
    assert "data: " in sse_events
    assert ": next_cursor=" in sse_events
    assert resume_payload["result"]["status"] == "completed"
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_session_list_supports_cursor_and_limit() -> None:
    service, _ = build_cli_service(
        [
            inspect_workload(),
            finish(),
            inspect_workload(),
            finish(),
            inspect_workload(),
            finish(),
        ]
    )
    for index in range(3):
        await service.run_goal(
            Goal(f"Verify workload {index}", (SuccessCriterion("healthy", True),)),
            Task(f"Inspect workload {index}", ("healthy",)),
        )
    first_output = StringIO()
    second_output = StringIO()

    first_status = await run_cli(
        ["session", "list", "--limit", "2"],
        service=service,
        stdout=first_output,
    )
    first_payload = read_json(first_output)
    first_sessions = first_payload["sessions"]
    assert isinstance(first_sessions, list)
    cursor = first_payload["next_cursor"]
    assert isinstance(cursor, str)
    second_status = await run_cli(
        ["session", "list", "--after", cursor, "--limit", "2"],
        service=service,
        stdout=second_output,
    )
    second_payload = read_json(second_output)
    second_sessions = second_payload["sessions"]

    assert first_status == 0
    assert second_status == 0
    assert len(first_sessions) == 2
    assert cursor == first_sessions[-1]["session_id"]
    assert isinstance(second_sessions, list)
    assert len(second_sessions) == 1
    assert second_payload["next_cursor"] == second_sessions[-1]["session_id"]


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
    prometheus_metrics_output = StringIO()
    cost_output = StringIO()
    doctor_output = StringIO()
    audit_output = StringIO()
    session_audit_output = StringIO()
    session_cost_output = StringIO()
    logs_output = StringIO()
    session_logs_output = StringIO()
    traces_output = StringIO()
    session_traces_output = StringIO()
    otlp_traces_output = StringIO()
    session_otlp_traces_output = StringIO()

    metrics_status = await run_cli(["metrics"], service=service, stdout=metrics_output)
    prometheus_metrics_status = await run_cli(
        ["metrics", "--format", "prometheus"],
        service=service,
        stdout=prometheus_metrics_output,
    )
    cost_status = await run_cli(["cost"], service=service, stdout=cost_output)
    logs_status = await run_cli(["logs"], service=service, stdout=logs_output)
    traces_status = await run_cli(["traces"], service=service, stdout=traces_output)
    otlp_traces_status = await run_cli(
        ["traces", "--format", "otlp"],
        service=service,
        stdout=otlp_traces_output,
    )
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
    session_logs_status = await run_cli(
        ["session", "logs", session_id],
        service=service,
        stdout=session_logs_output,
    )
    session_traces_status = await run_cli(
        ["session", "traces", session_id],
        service=service,
        stdout=session_traces_output,
    )
    session_otlp_traces_status = await run_cli(
        ["session", "traces", session_id, "--format", "otlp"],
        service=service,
        stdout=session_otlp_traces_output,
    )

    metrics = read_json(metrics_output)
    cost = read_json(cost_output)
    logs = read_json(logs_output)
    traces = read_json(traces_output)
    otlp_traces = read_json(otlp_traces_output)
    doctor = read_json(doctor_output)
    audit = read_json(audit_output)
    session_audit = read_json(session_audit_output)
    session_cost = read_json(session_cost_output)
    session_logs = read_json(session_logs_output)
    session_traces = read_json(session_traces_output)
    session_otlp_traces = read_json(session_otlp_traces_output)
    assert metrics_status == 0
    assert prometheus_metrics_status == 0
    assert cost_status == 0
    assert logs_status == 0
    assert traces_status == 0
    assert otlp_traces_status == 0
    assert doctor_status == 0
    assert audit_status == 0
    assert session_audit_status == 0
    assert session_cost_status == 0
    assert session_logs_status == 0
    assert session_traces_status == 0
    assert session_otlp_traces_status == 0
    assert metrics["completed_goal_count"] == 1
    assert metrics["action_started_count"] == 2
    assert metrics["model_call_count"] == 3
    assert metrics["model_total_token_count"] == 165
    prometheus_metrics = prometheus_metrics_output.getvalue()
    assert "universal_agent_runtime_completed_goals 1\n" in prometheus_metrics
    assert "universal_agent_runtime_action_started_count" not in prometheus_metrics
    assert "universal_agent_runtime_actions_started 2\n" in prometheus_metrics
    assert "universal_agent_runtime_model_total_tokens 165\n" in prometheus_metrics
    assert cost == session_cost
    assert cost["model_call_count"] == 3
    assert cost["total_tokens"] == 165
    assert cost["estimated_cost_micros"] == 27
    assert logs == session_logs
    log_items = logs["logs"]
    assert isinstance(log_items, list)
    assert log_items[-1]["event_type"] == "GoalCompleted"
    assert traces == session_traces
    span_items = traces["spans"]
    assert isinstance(span_items, list)
    assert [
        item["name"]
        for item in span_items
        if isinstance(item, dict) and str(item["name"]).startswith("runtime.action.")
    ] == [
        "runtime.action.scale_workload",
        "runtime.action.inspect_workload",
    ]
    phase_span_names = {
        item["name"]
        for item in span_items
        if isinstance(item, dict) and not str(item["name"]).startswith("runtime.action.")
    }
    assert phase_span_names >= {
        "runtime.session",
        "runtime.decision",
        "runtime.model_usage",
        "runtime.policy",
        "runtime.observation",
        "runtime.evaluation",
    }
    assert otlp_traces == session_otlp_traces
    resource_spans = otlp_traces["resourceSpans"]
    assert isinstance(resource_spans, list)
    resource_span = resource_spans[0]
    assert isinstance(resource_span, dict)
    scope_spans = resource_span["scopeSpans"]
    assert isinstance(scope_spans, list)
    scope_span = scope_spans[0]
    assert isinstance(scope_span, dict)
    exported_spans = scope_span["spans"]
    assert isinstance(exported_spans, list)
    assert len(exported_spans) > 3
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


@pytest.mark.asyncio
async def test_cli_serve_starts_agentd_http_server_with_injected_runner() -> None:
    service, _ = build_cli_service([])
    output = StringIO()
    observed_urls: list[str] = []

    def runner(server: AgentdHttpServer) -> None:
        observed_urls.append(server.base_url)

    try:
        status = await run_cli(
            ["serve", "--port", "0"],
            service=service,
            server_runner=runner,
            stdout=output,
        )
    except PermissionError as exc:
        pytest.skip(f"local socket bind unavailable: {exc}")
    payload = read_json(output)

    assert status == 0
    assert payload["status"] == "serving"
    assert payload["base_url"] == observed_urls[0]
    assert payload["base_url"].startswith("http://127.0.0.1:")


@pytest.mark.asyncio
async def test_cli_eval_list_applies_kind_and_tag_filters() -> None:
    service, _ = build_cli_service([])
    output = StringIO()

    status = await run_cli(
        [
            "eval",
            "list",
            "production-operator",
            "--kind",
            "policy",
            "--tag",
            "kubernetes",
        ],
        service=service,
        stdout=output,
    )
    payload = read_json(output)

    assert status == 0
    assert payload["suite_name"] == "local evaluation suite"
    assert payload["suite_tags"] == ["local", "kubernetes"]
    assert payload["scenario_count"] == 1
    assert payload["scenarios"] == [
        {
            "scenario_name": "invalid scale policy",
            "kind": "policy",
            "tags": ["policy", "kubernetes"],
            "goal": {
                "description": "Evaluate workload health",
                "success_criteria": ["healthy"],
            },
            "task": {
                "description": "Inspect workload",
                "required_criteria": ["healthy"],
            },
        }
    ]


@pytest.mark.asyncio
async def test_cli_eval_list_loads_suite_file(tmp_path: Path) -> None:
    service, _ = build_cli_service([])
    suite_path = tmp_path / "suite.json"
    write_evaluation_suite_file(suite_path)
    output = StringIO()

    status = await run_cli(
        [
            "eval",
            "list",
            "production-operator",
            "--suite-file",
            str(suite_path),
            "--kind",
            "regression",
            "--tag",
            "file",
        ],
        service=service,
        stdout=output,
    )
    payload = read_json(output)

    assert status == 0
    assert payload["suite_name"] == "file evaluation suite"
    assert payload["suite_tags"] == ["file", "kubernetes"]
    assert payload["scenario_count"] == 1
    assert payload["scenarios"][0]["scenario_name"] == "file healthy workload"
    assert payload["scenarios"][0]["task"]["description"] == "Inspect workload from file"


@pytest.mark.asyncio
async def test_cli_eval_run_rejects_empty_scenario_selection() -> None:
    service, _ = build_cli_service([])
    output = StringIO()
    error = StringIO()

    status = await run_cli(
        ["eval", "run", "production-operator", "--kind", "recovery"],
        service=service,
        stdout=output,
        stderr=error,
    )

    assert status == 2
    assert output.getvalue() == ""
    assert "evaluation run requires at least one scenario" in error.getvalue()


@pytest.mark.asyncio
async def test_cli_eval_run_executes_suite_and_persists_report(tmp_path: Path) -> None:
    service, backend = build_cli_service([inspect_workload(), finish()])
    output = StringIO()
    report_dir = tmp_path / "reports"

    status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--report-dir",
            str(report_dir),
            "--max-average-actions",
            "1.0",
            "--kind",
            "regression",
            "--tag",
            "smoke",
            "--exclude-tag",
            "slow",
        ],
        service=service,
        stdout=output,
    )
    payload = read_json(output)
    stored = FileEvaluationReportStore(report_dir).load("local evaluation suite")

    assert status == 0
    assert payload["passed"] is True
    assert payload["suite"]["summary"]["scenario_count"] == 1
    assert payload["suite"]["summary"]["action_started_count"] == 1
    scenario_payload = payload["suite"]["scenarios"][0]
    assert scenario_payload["kind"] == "regression"
    assert scenario_payload["tags"] == ["smoke", "kubernetes"]
    assert scenario_payload["evidence_claims"] == ["resource", "healthy"]
    assert payload["gate"]["passed"] is True
    assert payload["report_dir"] == str(report_dir)
    assert stored.suite_name == "local evaluation suite"
    assert stored.scenarios[0].kind is not None
    assert stored.scenarios[0].kind.value == "regression"
    assert stored.scenarios[0].tags == ("smoke", "kubernetes")
    assert stored.scenarios[0].evidence_claims == ("resource", "healthy")
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_eval_run_can_emit_junit_xml() -> None:
    service, backend = build_cli_service([inspect_workload(), finish()])
    output = StringIO()

    status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--kind",
            "regression",
            "--tag",
            "smoke",
            "--format",
            "junit",
        ],
        service=service,
        stdout=output,
    )
    root = fromstring(output.getvalue())

    assert status == 0
    assert root.tag == "testsuite"
    assert root.attrib["name"] == "local evaluation suite"
    assert root.attrib["tests"] == "2"
    assert root.attrib["failures"] == "0"
    assert [item.attrib["name"] for item in root.findall("testcase")] == [
        "healthy workload",
        "pass_rate",
    ]
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_eval_reports_lists_persisted_reports(tmp_path: Path) -> None:
    service, backend = build_cli_service([inspect_workload(), finish()])
    report_dir = tmp_path / "reports"
    run_output = StringIO()
    reports_output = StringIO()
    console_output = StringIO()

    run_status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--suite",
            "daily regression suite",
            "--report-dir",
            str(report_dir),
            "--kind",
            "regression",
        ],
        service=service,
        stdout=run_output,
    )
    reports_status = await run_cli(
        ["eval", "reports", "--report-dir", str(report_dir)],
        service=service,
        stdout=reports_output,
    )
    console_status = await run_cli(
        ["eval", "console", "--report-dir", str(report_dir)],
        service=service,
        stdout=console_output,
    )
    payload = read_json(reports_output)
    console = console_output.getvalue()
    reports = payload["reports"]
    assert isinstance(reports, list)
    report = reports[0]
    assert isinstance(report, dict)

    assert run_status == 0
    assert reports_status == 0
    assert console_status == 0
    assert payload["report_dir"] == str(report_dir)
    assert payload["report_count"] == 1
    assert report["suite_name"] == "daily regression suite"
    assert report["passed"] is True
    assert report["scenario_count"] == 1
    assert report["passed_count"] == 1
    assert report["failed_count"] == 0
    assert report["gate_passed"] is True
    assert report["failed_scenarios"] == []
    assert report["model_total_token_count"] == 0
    assert "Evaluation Console" in console
    assert "daily regression suite" in console
    assert "healthy workload" in console
    assert "Quality Gate Checks" in console
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_eval_run_executes_suite_file_and_persists_report(tmp_path: Path) -> None:
    service, backend = build_cli_service([inspect_workload(), finish()])
    suite_path = tmp_path / "suite.json"
    report_dir = tmp_path / "reports"
    write_evaluation_suite_file(suite_path)
    output = StringIO()

    status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--suite-file",
            str(suite_path),
            "--report-dir",
            str(report_dir),
            "--min-action-success-rate",
            "1.0",
        ],
        service=service,
        stdout=output,
    )
    payload = read_json(output)
    stored = FileEvaluationReportStore(report_dir).load("file evaluation suite")

    assert status == 0
    assert payload["passed"] is True
    assert payload["suite"]["suite_name"] == "file evaluation suite"
    assert payload["suite"]["scenarios"][0]["scenario_name"] == "file healthy workload"
    assert payload["gate"]["passed"] is True
    assert stored.suite_name == "file evaluation suite"
    assert stored.scenarios[0].scenario_name == "file healthy workload"
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_eval_run_uses_suite_file_quality_gate_and_cli_overrides(
    tmp_path: Path,
) -> None:
    suite_path = tmp_path / "suite.json"
    write_evaluation_suite_file(
        suite_path,
        quality_gate={"max_average_actions_per_scenario": 0.0},
    )

    failing_output = StringIO()
    failing_status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--suite-file",
            str(suite_path),
            "--fail-on-fail",
        ],
        service=build_cli_service([inspect_workload(), finish()])[0],
        stdout=failing_output,
    )
    failing_payload = read_json(failing_output)
    failed_checks = {
        item["name"]
        for item in failing_payload["gate"]["checks"]
        if isinstance(item, dict) and not item["passed"]
    }

    passing_output = StringIO()
    passing_status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--suite-file",
            str(suite_path),
            "--max-average-actions",
            "1.0",
            "--fail-on-fail",
        ],
        service=build_cli_service([inspect_workload(), finish()])[0],
        stdout=passing_output,
    )
    passing_payload = read_json(passing_output)

    assert failing_status == 1
    assert failing_payload["passed"] is False
    assert "average_actions_per_scenario" in failed_checks
    assert passing_status == 0
    assert passing_payload["passed"] is True
    assert passing_payload["gate"]["passed"] is True


@pytest.mark.asyncio
async def test_cli_eval_run_can_fail_process_on_gate_failure() -> None:
    service, _ = build_cli_service([inspect_workload(), finish(), invalid_scale_workload()])
    output = StringIO()
    error = StringIO()

    status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--max-average-actions",
            "0.0",
            "--fail-on-fail",
        ],
        service=service,
        stdout=output,
        stderr=error,
    )
    payload = read_json(output)

    assert status == 1
    assert error.getvalue() == ""
    assert payload["passed"] is False
    assert payload["gate"]["passed"] is False


@pytest.mark.asyncio
async def test_cli_eval_run_exposes_quality_gate_thresholds() -> None:
    service, _ = build_cli_service(
        [inspect_workload(), finish(), invalid_scale_workload()],
        usage=[
            ModelUsage(
                "scripted",
                "cli-gate-test",
                input_tokens=80,
                output_tokens=20,
                estimated_cost_micros=18,
            ),
            ModelUsage(
                "scripted",
                "cli-gate-test",
                input_tokens=40,
                output_tokens=10,
                estimated_cost_micros=9,
            ),
            ModelUsage("scripted", "cli-gate-test", input_tokens=10, output_tokens=5),
        ],
    )
    output = StringIO()
    error = StringIO()

    status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--min-goal-completion-rate",
            "1.0",
            "--min-action-success-rate",
            "1.0",
            "--max-tool-failure-rate",
            "0.0",
            "--max-policy-denial-rate",
            "0.0",
            "--max-average-recoveries",
            "0.0",
            "--max-average-model-calls",
            "1.0",
            "--max-average-model-tokens",
            "1.0",
            "--max-total-model-cost-micros",
            "1",
            "--fail-on-fail",
        ],
        service=service,
        stdout=output,
        stderr=error,
    )
    payload = read_json(output)
    failed_checks = {
        item["name"]
        for item in payload["gate"]["checks"]
        if isinstance(item, dict) and not item["passed"]
    }

    assert status == 1
    assert error.getvalue() == ""
    assert payload["passed"] is False
    assert failed_checks >= {
        "goal_completion_rate",
        "policy_denial_rate",
        "average_model_calls_per_scenario",
        "average_model_tokens_per_scenario",
        "total_model_estimated_cost_micros",
    }


@pytest.mark.asyncio
async def test_cli_eval_compare_detects_report_drift(tmp_path: Path) -> None:
    service, _ = build_cli_service([inspect_workload(), finish(), invalid_scale_workload()])
    report_output = StringIO()
    report_dir = tmp_path / "reports"
    await run_cli(
        ["eval", "run", "production-operator", "--report-dir", str(report_dir)],
        service=service,
        stdout=report_output,
    )
    expected = FileEvaluationReportStore(report_dir).load("local evaluation suite")
    actual = replace(expected, summary=replace(expected.summary, action_started_count=2))
    expected_path = tmp_path / "expected.json"
    actual_path = tmp_path / "actual.json"
    expected_path.write_text(json.dumps(encode_evaluation_report(expected)), encoding="utf-8")
    actual_path.write_text(json.dumps(encode_evaluation_report(actual)), encoding="utf-8")
    output = StringIO()

    status = await run_cli(
        ["eval", "compare", str(expected_path), str(actual_path), "--fail-on-fail"],
        stdout=output,
    )
    payload = read_json(output)

    assert status == 1
    assert payload["passed"] is False
    assert "summary" in {
        item["name"] for item in payload["failed_checks"] if isinstance(item, dict)
    }


@pytest.mark.asyncio
async def test_cli_eval_replay_records_and_replays_golden_recording(tmp_path: Path) -> None:
    recording_dir = tmp_path / "replay-recordings"
    record_output = StringIO()

    record_status = await run_cli(
        [
            "eval",
            "replay",
            "production-operator",
            "--recording-dir",
            str(recording_dir),
            "--kind",
            "regression",
            "--update",
        ],
        service=build_cli_service([inspect_workload(), finish()])[0],
        stdout=record_output,
    )
    record_payload = read_json(record_output)
    stored = FileReplayRecordingStore(recording_dir).load("healthy workload")

    recordings_output = StringIO()
    recordings_status = await run_cli(
        ["eval", "recordings", "--recording-dir", str(recording_dir)],
        service=build_cli_service([inspect_workload(), finish()])[0],
        stdout=recordings_output,
    )
    recordings_payload = read_json(recordings_output)
    recordings = recordings_payload["recordings"]
    assert isinstance(recordings, list)
    recording = recordings[0]
    assert isinstance(recording, dict)

    replay_output = StringIO()
    replay_status = await run_cli(
        [
            "eval",
            "replay",
            "production-operator",
            "--recording-dir",
            str(recording_dir),
            "--kind",
            "regression",
            "--fail-on-fail",
        ],
        service=build_cli_service([inspect_workload(), finish()])[0],
        stdout=replay_output,
    )
    replay_payload = read_json(replay_output)

    assert record_status == 0
    assert record_payload["mode"] == "record"
    assert record_payload["passed"] is True
    assert record_payload["scenario_count"] == 1
    assert stored.scenario_name == "healthy workload"
    assert recordings_status == 0
    assert recordings_payload["recording_dir"] == str(recording_dir)
    assert recordings_payload["recording_count"] == 1
    assert recording["scenario_name"] == "healthy workload"
    assert recording["result_status"] == "completed"
    assert recording["error_code"] is None
    assert recording["action_started_count"] == 1
    assert recording["policy_denial_count"] == 0
    assert recording["action_capabilities"] == ["inspect_workload"]
    assert recording["policy_effects"] == ["allow"]
    assert replay_status == 0
    assert replay_payload["mode"] == "replay"
    assert replay_payload["passed"] is True
    assert replay_payload["scenarios"][0]["failed_checks"] == []


@pytest.mark.asyncio
async def test_cli_eval_replay_can_fail_process_on_drift(tmp_path: Path) -> None:
    recording_dir = tmp_path / "replay-recordings"
    await run_cli(
        [
            "eval",
            "replay",
            "production-operator",
            "--recording-dir",
            str(recording_dir),
            "--kind",
            "regression",
            "--update",
        ],
        service=build_cli_service([inspect_workload(), finish()])[0],
        stdout=StringIO(),
    )
    output = StringIO()
    error = StringIO()

    status = await run_cli(
        [
            "eval",
            "replay",
            "production-operator",
            "--recording-dir",
            str(recording_dir),
            "--kind",
            "regression",
            "--fail-on-fail",
        ],
        service=build_cli_service([invalid_scale_workload()])[0],
        stdout=output,
        stderr=error,
    )
    payload = read_json(output)

    assert status == 1
    assert error.getvalue() == ""
    assert payload["mode"] == "replay"
    assert payload["passed"] is False
    assert {item["name"] for item in payload["scenarios"][0]["failed_checks"]} >= {
        "result_status",
        "event_types",
    }
