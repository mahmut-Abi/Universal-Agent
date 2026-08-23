from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType

from universal_agent.core import (
    ActionId,
    CapabilityCategory,
    DomainIdentity,
    GoalId,
    GoalStatus,
    ObservationId,
    PolicyEffect,
    RiskLevel,
    SessionId,
    SideEffect,
    TaskId,
    TaskStatus,
)
from universal_agent.evidence import EvidenceId
from universal_agent.memory import MemoryKind
from universal_agent.operations import AuditRecordView, RuntimeCostView, RuntimeMetricsView
from universal_agent.runtime import (
    EvidenceView,
    RuntimeEventView,
    SessionSummaryView,
    SessionView,
    TaskView,
)
from universal_agent.service import (
    CapabilityView,
    DomainView,
    EvaluatorView,
    HealthView,
    MemoryView,
    PolicyView,
    ProfileView,
    ReadyView,
    RuntimeConfigDomainView,
    RuntimeConfigView,
    SessionExplorerView,
    ToolView,
    WorldFactView,
)
from universal_agent.web import (
    WebConsoleSnapshot,
    render_web_console,
    render_web_domain_detail,
    render_web_evidence_explorer,
    render_web_session_detail,
    render_web_settings,
    render_web_world_model_explorer,
)


def test_web_console_renderer_projects_and_escapes_runtime_snapshot() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    session_id = SessionId("session-1")
    goal_id = GoalId("goal-1")
    task_id = TaskId("task-1")
    goal_description = "Verify <script>alert(1)</script>"
    selected_session = SessionView(
        session_id,
        goal_id,
        goal_description,
        GoalStatus.COMPLETED,
        task_id,
        "Inspect workload",
        TaskStatus.COMPLETED,
        2,
        (TaskView(task_id, "Inspect workload", TaskStatus.COMPLETED, ("healthy",), ()),),
        MappingProxyType({"healthy": True}),
        None,
        None,
        "done",
        None,
        "kubernetes",
        "0.2.0",
    )
    snapshot = WebConsoleSnapshot(
        health=HealthView("ok", "universal-agent-runtime"),
        ready=ReadyView(True, "ready", 1, 1, 1),
        config=RuntimeConfigView(
            environment={},
            store_backend="memory",
            store_path=None,
            distributed_queue_backend="memory",
            distributed_queue_path=None,
            max_iterations=20,
            max_recovery_steps=8,
            domains=(RuntimeConfigDomainView("kubernetes", "0.2.0", True),),
        ),
        domains=(
            DomainView(
                "kubernetes",
                "0.2.0",
                "Kubernetes domain",
                True,
                ("Deployment",),
                ("inspect_workload",),
                ("criteria",),
            ),
        ),
        profiles=(
            ProfileView(
                "production-operator",
                "1.0.0",
                "Production Kubernetes operator",
                "kubernetes",
                "0.2.0",
                (DomainIdentity("kubernetes", "0.2.0"),),
            ),
        ),
        capabilities=(
            CapabilityView(
                "inspect_workload",
                "Inspect workload health",
                CapabilityCategory.OBSERVATION,
                RiskLevel.LOW,
                "kubernetes",
                "0.2.0",
                ("kubernetes_inspect_workload",),
            ),
        ),
        tools=(
            ToolView(
                "kubernetes_inspect_workload",
                "Inspect workload with Kubernetes backend",
                ("inspect_workload",),
                ("name",),
                SideEffect.NONE,
                RiskLevel.LOW,
                5.0,
                0,
                "kubernetes",
                "0.2.0",
            ),
        ),
        policies=(
            PolicyView(
                "allow-read",
                "read-only Kubernetes inspection allowed",
                "PolicyRule",
                PolicyEffect.ALLOW,
                (),
                (CapabilityCategory.OBSERVATION,),
                (),
                "kubernetes",
                "0.2.0",
            ),
        ),
        evaluators=(
            EvaluatorView("workload-health", "WorkloadHealthEvaluator", "kubernetes", "0.2.0"),
        ),
        memories=(
            MemoryView(
                "memory-1",
                MemoryKind.SEMANTIC,
                "kubernetes readiness",
                "Ready replicas should match desired replicas.",
                "kubernetes",
                0.95,
                None,
                timestamp,
            ),
        ),
        metrics=RuntimeMetricsView(
            session_count=1,
            active_session_count=0,
            waiting_session_count=0,
            completed_goal_count=1,
            failed_goal_count=0,
            cancelled_goal_count=0,
            event_count=3,
            action_started_count=1,
            action_completed_count=1,
            tool_failure_count=0,
            policy_denial_count=0,
            confirmation_required_count=0,
            recovery_planned_count=0,
            recovery_exhausted_count=0,
            human_intervention_count=0,
            resource_lock_acquired_count=0,
            resource_lock_released_count=0,
            resource_conflict_count=0,
            active_resource_lock_count=0,
        ),
        cost=RuntimeCostView(0, 0, 0, 0, 0, "USD", ()),
        sessions=(
            SessionSummaryView(
                session_id,
                goal_id,
                goal_description,
                GoalStatus.COMPLETED,
                task_id,
                "Inspect workload",
                TaskStatus.COMPLETED,
                2,
                1,
                False,
                "done",
                None,
                "kubernetes",
                "0.2.0",
                timestamp,
            ),
        ),
        selected_session=selected_session,
        session_explorer=SessionExplorerView(
            selected_session,
            (
                EvidenceView(
                    EvidenceId("evidence-1"),
                    session_id,
                    task_id,
                    ActionId("action-1"),
                    ObservationId("observation-1"),
                    "deployment/example",
                    "healthy",
                    True,
                    "inspect_workload:kubernetes_inspect_workload",
                    0.99,
                    timestamp,
                ),
            ),
            (
                WorldFactView(
                    "deployment/example",
                    "healthy",
                    True,
                    0.99,
                    timestamp,
                    ("evidence-1",),
                ),
            ),
        ),
        events=(
            RuntimeEventView(
                "event-1",
                "ActionStarted",
                session_id,
                goal_id,
                task_id,
                ActionId("action-1"),
                MappingProxyType({"capability": "inspect_workload"}),
                timestamp,
            ),
        ),
        audit_records=(
            AuditRecordView(
                "audit-1",
                session_id,
                goal_id,
                task_id,
                ActionId("action-1"),
                "inspect_workload",
                "kubernetes_inspect_workload",
                "none",
                "low",
                "allow",
                "allow-read",
                "succeeded",
                timestamp,
            ),
        ),
    )

    rendered = render_web_console(snapshot)

    assert "<!doctype html>" in rendered
    assert "Runtime Console" in rendered
    assert "Health: ok" in rendered
    assert "Ready: yes" in rendered
    assert "kubernetes@0.2.0" in rendered
    assert "Verify &lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<script>alert(1)</script>" not in rendered
    assert "Profile Catalog" in rendered
    assert "production-operator" in rendered
    assert "Capability Catalog" in rendered
    assert "inspect_workload" in rendered
    assert "Tool Catalog" in rendered
    assert "kubernetes_inspect_workload" in rendered
    assert "Policy Catalog" in rendered
    assert "allow-read" in rendered
    assert "Evaluator Catalog" in rendered
    assert "workload-health" in rendered
    assert "Memory Catalog" in rendered
    assert "kubernetes readiness" in rendered
    assert 'href="/console/settings"' in rendered
    assert 'href="/console/sessions/session-1"' in rendered
    assert "World Facts" in rendered
    assert "Session Evidence" in rendered
    assert "deployment/example" in rendered
    assert "evidence-1" in rendered
    assert "ActionStarted" in rendered
    assert "capability=inspect_workload" in rendered
    assert "allow:allow-read" in rendered

    session_detail = render_web_session_detail(snapshot)

    assert "Universal Agent Runtime Session Detail" in session_detail
    assert "Session Detail" in session_detail
    assert "session=session-1" in session_detail
    assert "Verify &lt;script&gt;alert(1)&lt;/script&gt;" in session_detail
    assert "<script>alert(1)</script>" not in session_detail
    assert 'href="/console"' in session_detail
    assert "Task Timeline" in session_detail
    assert "task-1" in session_detail
    assert "healthy" in session_detail
    assert "World Facts" in session_detail
    assert "Session Evidence" in session_detail
    assert "ActionStarted" in session_detail
    assert "allow:allow-read" in session_detail

    evidence_explorer = render_web_evidence_explorer(snapshot)

    assert "Universal Agent Runtime Evidence Explorer" in evidence_explorer
    assert "Evidence Explorer" in evidence_explorer
    assert "session=session-1" in evidence_explorer
    assert "Verify &lt;script&gt;alert(1)&lt;/script&gt;" in evidence_explorer
    assert "<script>alert(1)</script>" not in evidence_explorer
    assert 'href="/console/sessions/session-1/world"' in evidence_explorer
    assert "Session Evidence" in evidence_explorer
    assert "evidence-1" in evidence_explorer
    assert "deployment/example" in evidence_explorer
    assert "World Facts" in evidence_explorer

    world_explorer = render_web_world_model_explorer(snapshot)

    assert "Universal Agent Runtime World Model Explorer" in world_explorer
    assert "World Model Explorer" in world_explorer
    assert "session=session-1" in world_explorer
    assert "Verify &lt;script&gt;alert(1)&lt;/script&gt;" in world_explorer
    assert "<script>alert(1)</script>" not in world_explorer
    assert 'href="/console/sessions/session-1/evidence"' in world_explorer
    assert "World Facts" in world_explorer
    assert "deployment/example" in world_explorer
    assert "healthy" in world_explorer
    assert "Session Evidence" in world_explorer

    domain_detail = render_web_domain_detail(
        snapshot,
        domain_name="kubernetes",
        domain_version="0.2.0",
    )

    assert 'href="/console/domains/kubernetes/0.2.0"' in rendered
    assert "Universal Agent Runtime Domain Manager" in domain_detail
    assert "Domain Manager" in domain_detail
    assert "domain=kubernetes@0.2.0" in domain_detail
    assert "Ontology" in domain_detail
    assert "Deployment" in domain_detail
    assert "production-operator" in domain_detail
    assert "inspect_workload" in domain_detail
    assert "kubernetes_inspect_workload" in domain_detail
    assert "allow-read" in domain_detail
    assert "workload-health" in domain_detail
    assert "kubernetes readiness" in domain_detail

    settings = render_web_settings(snapshot)

    assert "Universal Agent Runtime Settings" in settings
    assert "Settings" in settings
    assert "Runtime Configuration" in settings
    assert "Store Backend" in settings
    assert "memory" in settings
    assert "Max Iterations" in settings
    assert "20" in settings
    assert "Configured Domains" in settings
    assert "kubernetes" in settings
    assert "0.2.0" in settings
    assert "No environment settings" in settings
