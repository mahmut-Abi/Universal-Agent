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
