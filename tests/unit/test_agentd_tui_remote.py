"""Round-trip and end-to-end coverage for the remote TUI snapshot provider."""

from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType

import httpx
import pytest

from universal_agent.agentd.client import AgentdClient
from universal_agent.agentd.representations import (
    audit_records_body,
    cost_body,
    doctor_body,
    event_batch_body,
    health_body,
    metrics_body,
    ready_body,
    session_batch_body,
)
from universal_agent.agentd.server import build_agentd_asgi_app
from universal_agent.agentd.session_representations import (
    session_body,
    session_explorer_body,
    world_entity_body,
    world_fact_body,
    world_relation_body,
)
from universal_agent.agentd.tui_remote import (
    _audit_record_from_body,
    _event_from_body,
    _evidence_from_body,
    _world_entity_from_body,
    _world_fact_from_body,
    _world_relation_from_body,
    agentd_snapshot_provider,
    cost_from_body,
    doctor_from_body,
    health_from_body,
    metrics_from_body,
    ready_from_body,
    session_summary_from_body,
    session_view_from_body,
)
from universal_agent.core import (
    ActionId,
    ErrorCode,
    GoalId,
    GoalStatus,
    JsonMapping,
    ObservationId,
    SessionId,
    TaskId,
    TaskStatus,
)
from universal_agent.evidence import EvidenceId
from universal_agent.operations import (
    AuditRecordView,
    DoctorCheckView,
    DoctorReportView,
    ModelCostBreakdownView,
    RuntimeCostView,
    RuntimeMetricsView,
)
from universal_agent.runtime import (
    EvidenceView,
    RuntimeEventBatch,
    RuntimeEventView,
    RuntimeSessionBatch,
    SessionSummaryView,
    SessionView,
)
from universal_agent.service import (
    HealthView,
    ReadyView,
    SessionExplorerView,
    WorldEntityView,
    WorldFactView,
    WorldRelationView,
)

pytestmark = pytest.mark.contract


def _sample_session_view() -> SessionView:
    return SessionView(
        SessionId("s-1"),
        GoalId("goal-1"),
        "Verify workload health",
        GoalStatus.COMPLETED,
        TaskId("task-1"),
        "Inspect workload",
        TaskStatus.COMPLETED,
        2,
        (),
        MappingProxyType({"healthy": True}),
        None,
        None,
        "done",
        ErrorCode.INVALID_STATE,
        "kubernetes",
        "0.2.0",
    )


def _sample_summary() -> SessionSummaryView:
    return SessionSummaryView(
        SessionId("s-1"),
        GoalId("goal-1"),
        "Verify workload health",
        GoalStatus.WAITING,
        TaskId("task-1"),
        "Inspect workload",
        TaskStatus.RUNNING,
        3,
        4,
        pending_action=True,
        termination_reason="waiting for operator",
        error_code=None,
        domain_name="kubernetes",
        domain_version="0.2.0",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _batch_items(body: JsonMapping, key: str) -> list[JsonMapping]:
    raw = body.get(key)
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def test_session_view_round_trips_through_agentd_body() -> None:
    view = _sample_session_view()
    parsed = session_view_from_body(dict(session_body(view)))

    assert parsed == view


def test_session_summary_round_trips_through_agentd_body() -> None:
    view = _sample_summary()
    body = session_batch_body(RuntimeSessionBatch(sessions=(view,), next_cursor=None))
    items = _batch_items(body, "sessions")

    assert len(items) == 1
    assert session_summary_from_body(items[0]) == view


def test_metrics_and_cost_round_trip() -> None:
    metrics = RuntimeMetricsView(
        session_count=2,
        active_session_count=1,
        waiting_session_count=1,
        completed_goal_count=0,
        failed_goal_count=0,
        cancelled_goal_count=0,
        event_count=9,
        action_started_count=3,
        action_completed_count=2,
        tool_failure_count=1,
        policy_denial_count=1,
        confirmation_required_count=1,
        recovery_planned_count=1,
        recovery_exhausted_count=0,
        human_intervention_count=0,
        resource_lock_acquired_count=0,
        resource_lock_released_count=0,
        resource_conflict_count=0,
        active_resource_lock_count=0,
        decision_generated_count=3,
        decision_validated_count=2,
        decision_rejected_count=1,
        policy_checked_count=4,
        evaluation_count=2,
        evaluation_success_count=2,
    )
    cost = RuntimeCostView(
        model_call_count=2,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        estimated_cost_micros=40,
        currency="USD",
        by_model=(
            ModelCostBreakdownView(
                provider="test",
                model="scripted",
                call_count=2,
                input_tokens=10,
                output_tokens=20,
                total_tokens=30,
                estimated_cost_micros=40,
                currency="USD",
            ),
        ),
    )

    assert metrics_from_body(dict(metrics_body(metrics))) == metrics
    assert cost_from_body(dict(cost_body(cost))) == cost


def test_health_ready_doctor_round_trip() -> None:
    health = HealthView("ok", "universal-agent-runtime")
    ready = ReadyView(True, "ready", 1, 2, 3)
    doctor = DoctorReportView(
        "ok",
        (DoctorCheckView("store", "ok", "memory store"),),
    )

    assert health_from_body(dict(health_body(health))) == health
    assert ready_from_body(dict(ready_body(ready))) == ready
    assert doctor_from_body(dict(doctor_body(doctor))) == doctor


def test_event_and_audit_round_trip() -> None:
    event = RuntimeEventView(
        "ev-1",
        "ActionStarted",
        SessionId("s-1"),
        GoalId("goal-1"),
        TaskId("task-1"),
        None,
        MappingProxyType({"capability": "inspect_workload"}),
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    audit = AuditRecordView(
        "audit-1",
        SessionId("s-1"),
        GoalId("goal-1"),
        TaskId("task-1"),
        None,
        "inspect_workload",
        "kubernetes_inspect_workload",
        "none",
        "low",
        "allow",
        "allow-read",
        "ok",
        datetime(2026, 1, 1, tzinfo=UTC),
        None,
        None,
    )

    batch_body = event_batch_body(RuntimeEventBatch(events=(event,), next_cursor=None))
    event_items = _batch_items(batch_body, "events")
    audit_items = _batch_items(audit_records_body((audit,)), "audit_records")

    assert len(event_items) == 1
    assert _event_from_body(event_items[0]) == event
    assert len(audit_items) == 1
    assert _audit_record_from_body(audit_items[0]) == audit


def test_world_and_evidence_round_trip() -> None:
    fact = WorldFactView(
        "deployment/api",
        "status",
        "degraded",
        0.9,
        datetime(2026, 1, 1, tzinfo=UTC),
        ("ev-1",),
    )
    entity = WorldEntityView(
        "deployment/api",
        "Deployment",
        MappingProxyType({"ns": "prod"}),
        ("ev-1",),
    )
    relation = WorldRelationView(
        "deployment/api",
        "owns",
        "pod/api",
        ("ev-1",),
    )
    evidence = EvidenceView(
        evidence_id=EvidenceId("ev-1"),
        session_id=SessionId("s-1"),
        task_id=TaskId("task-1"),
        action_id=ActionId("action-1"),
        observation_id=ObservationId("obs-1"),
        subject="deployment/api",
        claim="status",
        value="degraded",
        source="kubernetes",
        confidence=0.95,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert _world_fact_from_body(dict(world_fact_body(fact))) == fact
    assert _world_entity_from_body(dict(world_entity_body(entity))) == entity
    assert _world_relation_from_body(dict(world_relation_body(relation))) == relation

    explorer = SessionExplorerView(
        session=_sample_session_view(),
        evidence=(evidence,),
        world_facts=(fact,),
        world_entities=(entity,),
        world_relations=(relation,),
    )
    explorer_body = session_explorer_body(explorer)
    facts = _batch_items(explorer_body, "world_facts")
    evidence_items = _batch_items(explorer_body, "evidence")

    assert len(facts) == 1
    assert _world_fact_from_body(facts[0]) == fact
    assert len(evidence_items) == 1
    assert _evidence_from_body(evidence_items[0]) == evidence


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_agentd_snapshot_provider_builds_remote_dashboard() -> None:
    from universal_agent.agentd import AgentdApp
    from universal_agent.core import (
        Decision,
        DecisionType,
        Goal,
        SuccessCriterion,
        Task,
    )
    from universal_agent.domain import DomainLoader, RuntimeBuilder
    from universal_agent.domains.kubernetes import KubernetesRemediationDomain
    from universal_agent.model import ScriptedModelAdapter
    from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
    from universal_agent.service import RuntimeService
    from universal_agent.state import InMemoryStateStore

    class Backend:
        async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
            from universal_agent.core import immutable_json as json

            return json(
                {
                    "resource": "deployment/example",
                    "kind": "Deployment",
                    "healthy": True,
                    "desired_replicas": 3,
                    "ready_replicas": 3,
                }
            )

        async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
            from universal_agent.core import immutable_json as json

            return json(
                {
                    "resource": "deployment/example",
                    "kind": "Deployment",
                    "scaled": True,
                    "desired_replicas": 3,
                }
            )

    backend = Backend()
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            (
                Decision(
                    DecisionType.EXECUTE,
                    "Inspect workload",
                    capability="inspect_workload",
                    target="deployment/example",
                    arguments=MappingProxyType({"name": "example"}),
                    expected_observations=("healthy",),
                ),
                Decision(DecisionType.FINISH, "Evidence is present"),
            )
        ),
        state_store=store,
        components=components,
        event_sink=events,
    )
    service = RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )

    run = await service.run_goal(
        Goal(
            "Verify workload health",
            (SuccessCriterion("healthy", True),),
        ),
        Task("Inspect workload", ("healthy",)),
    )
    session_id = run.result.session_id

    app = AgentdApp(service)
    http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_agentd_asgi_app(app)))
    client = AgentdClient("http://testserver", client=http_client)
    provider = agentd_snapshot_provider(client, session_limit=5, event_limit=12)

    snapshot = await provider(None)

    assert snapshot.health.status == "ok"
    assert snapshot.ready.ready is True
    assert snapshot.sessions
    assert snapshot.selected_session is not None
    assert snapshot.selected_session.session_id == session_id
    assert snapshot.selected_session.goal_status.value == "completed"
    assert snapshot.events
    assert isinstance(snapshot.audit_records, tuple)
    assert snapshot.session_explorer is not None
    assert snapshot.session_explorer.evidence
    assert snapshot.metrics.session_count >= 1

    await client.close()
    await http_client.aclose()
