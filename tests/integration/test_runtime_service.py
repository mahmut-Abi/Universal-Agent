from __future__ import annotations

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
from universal_agent.core import (
    ExecutionStatus,
    JsonMapping,
    RiskLevel,
    SideEffect,
)
from universal_agent.domain import RuntimeComponents
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class ServiceBackend:
    def __init__(self) -> None:
        self.inspect_calls = 0
        self.mutation_calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls += 1
        assert capability == "inspect_workload"
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.mutation_calls += 1
        assert capability == "scale_workload"
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def scale_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Scale workload",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 3}),
        expected_observations=("mutation_applied",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Required evidence is present")


def goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )


def build_service(
    decisions: list[Decision],
    *,
    usage: list[ModelUsage] | None = None,
) -> tuple[RuntimeService, ServiceBackend]:
    backend = ServiceBackend()
    active = DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    components = RuntimeBuilder().build(active)
    api = build_api(components, decisions, usage=usage)
    return RuntimeService(runtime_api=api, components=components), backend


def build_api(
    components: RuntimeComponents,
    decisions: list[Decision],
    *,
    usage: list[ModelUsage] | None = None,
) -> RuntimeAPI:
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions, usage=usage or ()),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "staging"}),
    )
    return RuntimeAPI(runtime=runtime, session_store=store, event_reader=events)


def test_runtime_service_exposes_agentd_foundation_metadata() -> None:
    service, _ = build_service([])

    health = service.health()
    ready = service.ready()
    domains = service.domains()
    capabilities = service.capabilities()
    tools = service.tools()

    assert health.status == "ok"
    assert health.service == "universal-agent-runtime"
    assert ready.ready
    assert ready.reason == "ready"
    assert ready.domain_count == 1
    assert ready.capability_count == 6
    assert ready.tool_count == 6
    assert domains[0].name == "kubernetes"
    assert domains[0].version == "0.2.0"
    assert domains[0].primary
    assert "scale_workload" in domains[0].capability_names

    scale = next(item for item in capabilities if item.name == "scale_workload")
    assert scale.domain_name == "kubernetes"
    assert scale.domain_version == "0.2.0"
    assert scale.risk is RiskLevel.MEDIUM
    assert scale.tool_names == ("kubernetes_scale_workload",)

    scale_tool = next(item for item in tools if item.name == "kubernetes_scale_workload")
    assert scale_tool.domain_name == "kubernetes"
    assert scale_tool.capabilities == ("scale_workload",)
    assert scale_tool.required_arguments == ("name", "namespace", "replicas")
    assert scale_tool.side_effect is SideEffect.REVERSIBLE


@pytest.mark.asyncio
async def test_runtime_service_delegates_execution_to_runtime_api() -> None:
    service, backend = build_service([inspect_workload(), finish()])

    run = await service.run_goal(*goal_task())
    session = await service.get_session(run.result.session_id)
    sessions = await service.list_sessions()
    events = await service.list_events(run.result.session_id)

    assert run.result.status is ExecutionStatus.COMPLETED
    assert session.session_id == run.result.session_id
    assert [item.session_id for item in sessions] == [run.result.session_id]
    assert sessions[0].goal_status is session.goal_status
    assert sessions[0].current_task_status is session.current_task_status
    assert session.latest_evaluation is not None
    assert session.latest_evaluation.goal_completed
    assert [event.type for event in events][-1] == "GoalCompleted"
    assert backend.inspect_calls == 1
    assert backend.mutation_calls == 0


@pytest.mark.asyncio
async def test_runtime_service_builds_session_explorer_projection() -> None:
    service, backend = build_service([inspect_workload(), finish()])

    run = await service.run_goal(*goal_task())
    explorer = await service.session_explorer(run.result.session_id)

    evidence_claims = {item.claim: item for item in explorer.evidence}
    world_claims = {item.claim: item for item in explorer.world_facts}
    assert explorer.session.session_id == run.result.session_id
    assert evidence_claims["healthy"].value is True
    assert world_claims["healthy"].value is True
    assert world_claims["healthy"].subject == "deployment/example"
    assert str(evidence_claims["healthy"].evidence_id) in {
        evidence_id for fact in explorer.world_facts for evidence_id in fact.evidence_ids
    }
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_runtime_service_derives_metrics_doctor_and_audit_from_events() -> None:
    service, backend = build_service(
        [scale_workload(), inspect_workload(), finish()],
        usage=[
            ModelUsage("scripted", "runtime-test", input_tokens=100, output_tokens=25),
            ModelUsage(
                "scripted",
                "runtime-test",
                input_tokens=50,
                output_tokens=10,
                estimated_cost_micros=12,
            ),
            ModelUsage("scripted", "runtime-test", input_tokens=20, output_tokens=5),
        ],
    )

    run = await service.run_goal(*goal_task())
    metrics = await service.metrics()
    cost = await service.cost(run.result.session_id)
    doctor = await service.doctor()
    logs = await service.logs(run.result.session_id)
    audit = await service.audit_records(run.result.session_id)

    assert run.result.status is ExecutionStatus.COMPLETED
    assert metrics.session_count == 1
    assert metrics.completed_goal_count == 1
    assert metrics.action_started_count == 2
    assert metrics.action_completed_count == 2
    assert metrics.policy_denial_count == 0
    assert metrics.model_call_count == 3
    assert metrics.model_total_token_count == 210
    assert metrics.model_estimated_cost_micros == 12
    assert cost.model_call_count == 3
    assert cost.by_model[0].provider == "scripted"
    assert cost.by_model[0].model == "runtime-test"
    assert cost.by_model[0].total_tokens == 210
    assert doctor.status == "ok"
    assert {check.name for check in doctor.checks} >= {
        "service_health",
        "readiness",
        "event_stream",
        "structured_logs",
        "traces",
        "audit",
        "cost_tracking",
    }
    assert logs[-1].event_type == "GoalCompleted"
    assert any(record.event_type == "ModelUsageRecorded" for record in logs)
    assert len(audit) == 1
    assert audit[0].capability == "scale_workload"
    assert audit[0].tool_name == "kubernetes_scale_workload"
    assert audit[0].side_effect == "reversible"
    assert audit[0].risk == "medium"
    assert audit[0].policy_effect == "allow"
    assert audit[0].status == "succeeded"
    assert backend.inspect_calls == 1
    assert backend.mutation_calls == 1
