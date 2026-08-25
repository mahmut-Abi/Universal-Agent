from __future__ import annotations

from dataclasses import replace
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
from universal_agent.operations import (
    AuditRecordView,
    DoctorCheckView,
    DoctorReportView,
    RuntimeCostView,
    RuntimeMetricsView,
)
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
    WorldEntityView,
    WorldFactView,
    WorldRelationView,
)
from universal_agent.tui import TuiSnapshot, render_tui_snapshot


def test_tui_renderer_projects_runtime_snapshot() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    session_id = SessionId("session-1")
    goal_id = GoalId("goal-1")
    task_id = TaskId("task-1")
    selected_session = SessionView(
        session_id,
        goal_id,
        "Verify workload health",
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
    snapshot = TuiSnapshot(
        health=HealthView("ok", "universal-agent-runtime"),
        ready=ReadyView(True, "ready", 1, 1, 1),
        config=RuntimeConfigView(
            environment={},
            store_backend="memory",
            store_path=None,
            distributed_queue_backend="memory",
            distributed_queue_path=None,
            distributed_locks_backend="memory",
            distributed_locks_path=None,
            distributed_workers_backend="memory",
            distributed_workers_path=None,
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
                MappingProxyType({}),
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
        doctor=DoctorReportView(
            "ok",
            (
                DoctorCheckView("runtime_health", "ok", "health status is ok"),
                DoctorCheckView(
                    "state_event_consistency",
                    "ok",
                    "sessions=1 events=3 terminal_events_verified",
                ),
            ),
        ),
        distributed_snapshot=None,
        distributed_health=None,
        sessions=(
            SessionSummaryView(
                session_id,
                goal_id,
                "Verify workload health",
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
            (
                WorldEntityView(
                    "deployment/example",
                    "Deployment",
                    MappingProxyType({"healthy": True}),
                    ("evidence-2",),
                ),
            ),
            (
                WorldRelationView(
                    "deployment/example",
                    "owns",
                    "pod/example-1",
                    ("evidence-3",),
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

    rendered = render_tui_snapshot(snapshot)

    assert "Universal Agent Runtime TUI" in rendered
    assert "Health: ok | Ready: yes" in rendered
    assert "Operational Diagnostics" in rendered
    assert "- ok no active operational issues" in rendered
    assert "Runtime Doctor" in rendered
    assert "- status=ok checks=2" in rendered
    assert "- ok runtime_health: health status is ok" in rendered
    assert "Distributed Runtime" in rendered
    assert "- not configured" in rendered
    assert "kubernetes@0.2.0" in rendered
    assert "Agent Profiles" in rendered
    assert "production-operator@1.0.0" in rendered
    assert "Capabilities" in rendered
    assert "inspect_workload category=observation" in rendered
    assert "Tools" in rendered
    assert "kubernetes_inspect_workload side_effect=none" in rendered
    assert "Policies" in rendered
    assert "allow-read type=PolicyRule effect=allow" in rendered
    assert "Evaluators" in rendered
    assert "workload-health type=WorkloadHealthEvaluator" in rendered
    assert "Memory" in rendered
    assert "memory-1 kind=semantic subject=kubernetes readiness" in rendered
    assert "Verify workload health" in rendered
    assert "Satisfied Criteria: healthy=True" in rendered
    assert "World Facts" in rendered
    assert "deployment/example healthy=True confidence=0.99 evidence=evidence-1" in rendered
    assert "World Entities" in rendered
    assert 'deployment/example kind=Deployment attributes={"healthy": true}' in rendered
    assert "World Relations" in rendered
    assert "deployment/example -[owns]-> pod/example-1 evidence=evidence-3" in rendered
    assert "Session Evidence" in rendered
    assert "evidence-1 subject=deployment/example claim=healthy value=True" in rendered
    assert "ActionStarted" in rendered
    assert "capability=inspect_workload" in rendered
    assert "policy=allow:allow-read" in rendered

    degraded = replace(
        snapshot,
        ready=ReadyView(False, "store unavailable", 0, 1, 1),
        metrics=RuntimeMetricsView(
            session_count=1,
            active_session_count=0,
            waiting_session_count=1,
            completed_goal_count=0,
            failed_goal_count=1,
            cancelled_goal_count=0,
            event_count=8,
            action_started_count=2,
            action_completed_count=2,
            tool_failure_count=1,
            policy_denial_count=1,
            confirmation_required_count=1,
            recovery_planned_count=1,
            recovery_exhausted_count=1,
            human_intervention_count=1,
            resource_lock_acquired_count=1,
            resource_lock_released_count=0,
            resource_conflict_count=1,
            active_resource_lock_count=1,
        ),
    )

    degraded_rendered = render_tui_snapshot(degraded)

    assert "- error ready=no reason=store unavailable" in degraded_rendered
    assert "- error failed_goals=1" in degraded_rendered
    assert "- error tool_failures=1" in degraded_rendered
    assert "- error recovery_exhausted=1" in degraded_rendered
    assert "- warn policy_denials=1" in degraded_rendered
    assert "- warn confirmations_required=1" in degraded_rendered
    assert "- warn human_interventions=1" in degraded_rendered
    assert "- warn resource_conflicts=1" in degraded_rendered
    assert "- warn active_resource_locks=1" in degraded_rendered
    assert "- info waiting_sessions=1" in degraded_rendered
    assert "- info recoveries_planned=1" in degraded_rendered
