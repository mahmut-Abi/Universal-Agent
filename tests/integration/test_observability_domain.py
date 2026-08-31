from __future__ import annotations

import pytest

from universal_agent.core import (
    Decision,
    DecisionType,
    ExecutionStatus,
    Goal,
    JsonMapping,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.domain import DomainComposition, DomainLoader, RuntimeBuilder
from universal_agent.domains.kubernetes import KubernetesDomain
from universal_agent.domains.observability import ObservabilityDomain, StaticMetricsBackend
from universal_agent.model import ScriptedModelAdapter
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
from universal_agent.state import InMemoryStateStore


class UnhealthyWorkloadBackend:
    def __init__(self) -> None:
        self.calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        assert capability == "inspect_workload"
        return immutable_json(
            {
                "resource": "deployment/example",
                "kind": "Deployment",
                "healthy": False,
                "desired_replicas": 3,
                "ready_replicas": 1,
                "root_cause": "metrics_required",
            }
        )


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "inspect workload before metrics",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def query_metrics() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "query CPU throttling metrics",
        capability="query_metrics",
        target="deployment/example",
        arguments=immutable_json(
            {
                "query": "container_cpu_cfs_throttled_periods_total",
                "subject": "deployment/example",
            }
        ),
        expected_observations=("cpu_throttling",),
    )


def query_metric_range() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "query replica trend over a range",
        capability="query_metric_ranges",
        target="deployment/example",
        arguments=immutable_json(
            {
                "query": "kube_deployment_status_replicas",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-01T00:10:00Z",
                "step": "60s",
            }
        ),
        expected_observations=("last_value",),
    )


def inspect_alert_rules() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "inspect firing alerts",
        capability="inspect_alert_rules",
        target="deployment/example",
        arguments=immutable_json({}),
        expected_observations=("firing_alert_count",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "cross-domain evidence is present")


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_kubernetes_and_observability_domains_share_world_evidence() -> None:
    kubernetes_backend = UnhealthyWorkloadBackend()
    metrics_backend = StaticMetricsBackend(
        default_response=immutable_json(
            {
                "sample_count": 1,
                "metric_value": 0.91,
                "cpu_throttling": True,
            }
        )
    )
    loader = DomainLoader()
    components = RuntimeBuilder().build(
        DomainComposition(
            (
                loader.load(KubernetesDomain(kubernetes_backend)),
                loader.load(ObservabilityDomain(metrics_backend)),
            )
        )
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter([inspect_workload(), query_metrics(), finish()]),
        state_store=store,
        components=components,
        event_sink=events,
    )
    api = RuntimeAPI(runtime=runtime, session_store=store, event_reader=events)
    goal = Goal(
        "Explain workload health with telemetry",
        (
            SuccessCriterion("healthy", False),
            SuccessCriterion("cpu_throttling", True),
        ),
    )
    task = Task("Inspect workload and metrics", ("healthy",))

    run = await api.run_goal(goal, task)
    diagnostics = await api.get_session_diagnostics(run.result.session_id)

    assert run.result.status is ExecutionStatus.COMPLETED
    assert kubernetes_backend.calls == 1
    assert len(metrics_backend.calls) == 1
    evidence_by_claim = {item.claim: item for item in diagnostics.evidence}
    assert evidence_by_claim["healthy"].domain_name == "kubernetes"
    assert evidence_by_claim["cpu_throttling"].domain_name == "observability"
    assert run.session.satisfied_criteria["healthy"] is False
    assert run.session.satisfied_criteria["cpu_throttling"] is True


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_observability_range_and_alert_evidence_reaches_shared_world() -> None:
    metrics_backend = StaticMetricsBackend(
        default_response={
            "sample_count": 1,
            "metric_value": 0.91,
            "cpu_throttling": True,
        },
        range_responses={
            "kube_deployment_status_replicas": {
                "series_count": 1,
                "samples_total": 3,
                "last_value": 2,
                "resource_subject": "deployment/example",
            }
        },
        rules_response={
            "rule_count": 3,
            "alerting_rule_count": 2,
            "recording_rule_count": 1,
        },
        alerts_response={
            "alert_count": 2,
            "firing_alert_count": 1,
            "resource_subjects": ["pod/example-failing"],
        },
    )
    components = RuntimeBuilder().build(
        DomainComposition((DomainLoader().load(ObservabilityDomain(metrics_backend)),))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [query_metrics(), query_metric_range(), inspect_alert_rules(), finish()]
        ),
        state_store=store,
        components=components,
        event_sink=events,
    )
    api = RuntimeAPI(runtime=runtime, session_store=store, event_reader=events)
    goal = Goal(
        "Explain replica trend and alerts with telemetry",
        (
            SuccessCriterion("cpu_throttling", True),
            SuccessCriterion("last_value", 2),
            SuccessCriterion("firing_alert_count", 1),
        ),
    )
    task = Task("Query metrics, range and alerts", ("cpu_throttling",))

    run = await api.run_goal(goal, task)
    diagnostics = await api.get_session_diagnostics(run.result.session_id)

    assert run.result.status is ExecutionStatus.COMPLETED
    assert len(metrics_backend.calls) == 1
    assert len(metrics_backend.range_calls) == 1
    assert len(metrics_backend.rule_calls) == 1
    assert len(metrics_backend.alert_calls) == 1
    evidence_by_claim = {item.claim: item for item in diagnostics.evidence}
    assert evidence_by_claim["last_value"].subject == "deployment/example"
    assert evidence_by_claim["firing_alert_count"].subject == "pod/example-failing"
    assert all(item.domain_name == "observability" for item in diagnostics.evidence)
    assert run.session.satisfied_criteria["cpu_throttling"] is True
    assert run.session.satisfied_criteria["last_value"] == 2
    assert run.session.satisfied_criteria["firing_alert_count"] == 1
    world = components.world_model.snapshot(run.result.session_id)
    assert world.value_for("last_value", subject="deployment/example") == 2
