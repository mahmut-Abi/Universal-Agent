from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType

import pytest

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
from universal_agent.distributed import (
    DistributedLockOwnerId,
    DistributedRuntimeCoordinator,
    WorkerId,
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
    DomainPackageView,
    DomainView,
    EvaluatorView,
    HealthView,
    MemoryView,
    PolicyView,
    ProfileView,
    ReadyView,
    RuntimeConfigDomainView,
    RuntimeConfigView,
    RuntimeSecretRefView,
    SessionExplorerView,
    ToolView,
    WorldEntityView,
    WorldFactEvidenceView,
    WorldFactHistoryView,
    WorldFactView,
    WorldNeighborhoodView,
    WorldRelationView,
)
from universal_agent.web import (
    WebCatalogPage,
    WebConsoleSnapshot,
    render_web_catalog,
    render_web_console,
    render_web_distributed,
    render_web_doctor,
    render_web_domain_detail,
    render_web_domain_package_detail,
    render_web_evidence_explorer,
    render_web_multi_agent,
    render_web_profile_catalog,
    render_web_session_detail,
    render_web_sessions,
    render_web_settings,
    render_web_world_model_explorer,
)


@pytest.mark.behavior
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
            domain_package_paths=(),
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
            domains=(
                RuntimeConfigDomainView(
                    "kubernetes",
                    "0.2.0",
                    True,
                    "kubernetes_api",
                    MappingProxyType(
                        {
                            "api_server": "https://cluster.example.test",
                            "default_namespace": "prod",
                            "bearer_token_secret": "<redacted>",
                        }
                    ),
                ),
            ),
            secrets=(
                RuntimeSecretRefView(
                    "kubernetes_api_token",
                    "env",
                    "KUBERNETES_API_TOKEN",
                    True,
                    True,
                    "available",
                ),
            ),
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
        domain_packages=(
            DomainPackageView(
                "kubernetes",
                "0.2.0",
                "Kubernetes domain package",
                "Runtime Team",
                "kubernetes.domain:build_domain",
                ("ops", "kubernetes"),
                ("Deployment",),
                ("inspect_workload",),
                ("kubernetes_inspect_workload",),
                ("allow-read",),
                ("diagnose_workload",),
                ("kubernetes readiness",),
                ("workload-health",),
                ("kubernetes_context",),
                (),
                (DomainIdentity("observability", "1.0.0"),),
                ("kubernetes_api",),
                ">=0.1,<1",
                "agent.nantian.dev/v1alpha1",
                MappingProxyType({"side_effects": "reversible"}),
                "/domains/kubernetes",
                "/domains/kubernetes/manifest.json",
                resource_names=("resources/runbook.md", "schemas/workload.json"),
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
        doctor=DoctorReportView("ok", ()),
        distributed_snapshot=None,
        distributed_health=None,
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
            (
                WorldFactHistoryView(
                    "deployment/example",
                    "healthy",
                    WorldFactView(
                        "deployment/example",
                        "healthy",
                        True,
                        0.99,
                        timestamp,
                        ("evidence-1", "evidence-4"),
                    ),
                    (
                        WorldFactEvidenceView(
                            "evidence-1",
                            True,
                            0.99,
                            timestamp,
                            "inspect_workload:kubernetes_inspect_workload",
                        ),
                        WorldFactEvidenceView(
                            "evidence-4",
                            False,
                            0.51,
                            timestamp,
                            "stale_health_probe",
                        ),
                    ),
                    True,
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
    assert "Operational Diagnostics" in rendered
    assert "No active operational issues" in rendered
    assert "kubernetes@0.2.0" in rendered
    assert "Domain Package Catalog" in rendered
    assert "kubernetes.domain:build_domain" in rendered
    assert "resources/runbook.md" in rendered
    assert "schemas/workload.json" in rendered
    assert "observability@1.0.0" in rendered
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
    assert "World Fact History" in rendered
    assert "stale_health_probe" in rendered
    assert "World Entities" in rendered
    assert "World Relations" in rendered
    assert "Session Evidence" in rendered
    assert "deployment/example" in rendered
    assert "evidence-1" in rendered
    assert "ActionStarted" in rendered
    assert "capability=inspect_workload" in rendered
    assert "allow:allow-read" in rendered
    assert 'href="/console/doctor"' in rendered
    assert 'href="/console/multi-agent"' in rendered
    assert rendered.count('href="/console/multi-agent"') == 1
    assert "Multi-Agent registry is not configured" in rendered

    sessions_page = render_web_sessions(snapshot)

    assert "Universal Agent Runtime Sessions" in sessions_page
    assert "Sessions" in sessions_page
    assert "sessions=1 active=0 waiting=0" in sessions_page
    assert "Verify &lt;script&gt;alert(1)&lt;/script&gt;" in sessions_page
    assert "Selected Session" in sessions_page
    assert "Recent Events" in sessions_page
    assert "Audit" in sessions_page
    assert 'href="/console/doctor"' in sessions_page

    doctor_page = render_web_doctor(
        snapshot,
        DoctorReportView(
            "warn",
            (
                DoctorCheckView("runtime_health", "ok", "health status is ok"),
                DoctorCheckView(
                    "state_event_consistency",
                    "warn",
                    "sessions exist but no events were listed",
                ),
                DoctorCheckView("audit", "error", "expected 1 audit records, projected 0"),
            ),
        ),
    )

    assert "Universal Agent Runtime Doctor" in doctor_page
    assert "Runtime Doctor" in doctor_page
    assert "Doctor: warn" in doctor_page
    assert "Doctor Checks" in doctor_page
    assert "runtime_health" in doctor_page
    assert "state_event_consistency" in doctor_page
    assert "expected 1 audit records, projected 0" in doctor_page
    assert "Operational Diagnostics" in doctor_page
    assert "Runtime Configuration" in doctor_page

    distributed_missing = render_web_distributed(snapshot, None, None)

    assert "Universal Agent Runtime Distributed" in distributed_missing
    assert "Distributed Runtime" in distributed_missing
    assert "not configured" in distributed_missing
    assert "Distributed Health Checks" in distributed_missing
    assert "No distributed work items" in distributed_missing

    multi_agent_page = render_web_multi_agent(snapshot)

    assert "Universal Agent Runtime Multi-Agent" in multi_agent_page
    assert "Multi-Agent: not configured" in multi_agent_page
    assert "Multi-Agent registry is not configured" in multi_agent_page

    coordinator = DistributedRuntimeCoordinator()
    coordinator.schedule_session(SessionId("queued-session"))
    coordinator.register_worker(WorkerId("worker-a"), capabilities=("agent_session",))
    coordinator.queue.enqueue(kind="completed-session", priority=10)
    coordinator.queue.enqueue(kind="failed-session", priority=9, max_attempts=1)
    cancelled = coordinator.schedule_session(SessionId("cancelled-session"))
    completed_lease = coordinator.queue.lease(worker_id=WorkerId("worker-a"))
    failed_lease = coordinator.queue.lease(worker_id=WorkerId("worker-a"))
    assert completed_lease.lease is not None
    assert failed_lease.lease is not None
    coordinator.queue.complete(
        completed_lease.lease.lease_id,
        worker_id=WorkerId("worker-a"),
    )
    coordinator.queue.fail(
        failed_lease.lease.lease_id,
        worker_id=WorkerId("worker-a"),
        reason="terminal failure",
        retry=False,
    )
    coordinator.cancel_work_item(
        cancelled.scheduled_work_item.work_item_id,
        reason="operator cancelled",
    )
    coordinator.acquire_lock(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
    )
    distributed_page = render_web_distributed(
        snapshot,
        coordinator.snapshot(),
        coordinator.health(),
    )

    assert "Universal Agent Runtime Distributed" in distributed_page
    assert "Distributed: ok" in distributed_page
    assert "Distributed Health Checks" in distributed_page
    assert "worker_pool" in distributed_page
    assert "Distributed Work Queue" in distributed_page
    assert "queued-session" in distributed_page
    assert "agent_session" in distributed_page
    assert "<span>Completed</span>\n<strong>1</strong>" in distributed_page
    assert "<span>Failed</span>\n<strong>1</strong>" in distributed_page
    assert "<span>Cancelled</span>\n<strong>1</strong>" in distributed_page
    assert "completed-session" in distributed_page
    assert "failed-session" in distributed_page
    assert "cancelled-session" in distributed_page
    assert "Distributed Workers" in distributed_page
    assert "worker-a" in distributed_page
    assert "Distributed Locks" in distributed_page
    assert "session/session-1" in distributed_page

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
    assert "World Fact History" in session_detail
    assert "World Entities" in session_detail
    assert "World Relations" in session_detail
    assert "Session Evidence" in session_detail
    assert "ActionStarted" in session_detail
    assert "allow:allow-read" in session_detail
    assert 'href="/console/doctor"' in session_detail

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
    assert "World Fact History" in evidence_explorer
    assert "World Entities" in evidence_explorer
    assert "World Relations" in evidence_explorer

    world_explorer = render_web_world_model_explorer(snapshot)

    assert "Universal Agent Runtime World Model Explorer" in world_explorer
    assert "World Model Explorer" in world_explorer
    assert "session=session-1" in world_explorer
    assert "Verify &lt;script&gt;alert(1)&lt;/script&gt;" in world_explorer
    assert "<script>alert(1)</script>" not in world_explorer
    assert 'href="/console/sessions/session-1/evidence"' in world_explorer
    assert "World Facts" in world_explorer
    assert "World Fact History" in world_explorer
    assert "<span>Conflicts</span>\n<strong>1</strong>" in world_explorer
    assert "evidence-4:value=False confidence=0.51 source=stale_health_probe" in world_explorer
    assert "World Entities" in world_explorer
    assert "World Relations" in world_explorer
    assert "deployment/example" in world_explorer
    assert "Deployment" in world_explorer
    assert "pod/example-1" in world_explorer
    assert "healthy" in world_explorer
    assert "Session Evidence" in world_explorer
    assert "Focused World Neighborhood" in world_explorer
    assert "No focused world neighborhood selected" in world_explorer

    focused_world_explorer = render_web_world_model_explorer(
        replace(
            snapshot,
            world_neighborhood=WorldNeighborhoodView(
                WorldEntityView(
                    "deployment/example",
                    "Deployment",
                    MappingProxyType({"healthy": True}),
                    ("evidence-2",),
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
                    WorldRelationView(
                        "deployment/example",
                        "owns",
                        "pod/example-1",
                        ("evidence-3",),
                    ),
                ),
                (),
                (
                    WorldEntityView(
                        "pod/example-1",
                        "Pod",
                        MappingProxyType({"phase": "Running"}),
                        ("evidence-5",),
                    ),
                ),
            ),
        )
    )

    assert "Focused World Neighborhood" in focused_world_explorer
    assert "deployment/example" in focused_world_explorer
    assert "No incoming focused relations" in focused_world_explorer
    assert "pod/example-1" in focused_world_explorer
    assert "{&#34;phase&#34;:&#34;Running&#34;}" in focused_world_explorer

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

    domain_package_detail = render_web_domain_package_detail(
        snapshot,
        package_name="kubernetes",
        package_version="0.2.0",
    )

    assert 'href="/console/domain-packages/kubernetes/0.2.0"' in rendered
    assert "Universal Agent Runtime Domain Package" in domain_package_detail
    assert "Domain Package" in domain_package_detail
    assert "package=kubernetes@0.2.0" in domain_package_detail
    assert "kubernetes.domain:build_domain" in domain_package_detail
    assert "resources/runbook.md" in domain_package_detail
    assert "schemas/workload.json" in domain_package_detail
    assert "observability@1.0.0" in domain_package_detail
    assert "Package Security" in domain_package_detail
    assert "reversible" in domain_package_detail
    assert "Matching Active Domains" in domain_package_detail
    assert "Matching Profiles" in domain_package_detail
    assert "production-operator" in domain_package_detail

    profile_catalog = render_web_profile_catalog(snapshot)

    assert "Universal Agent Runtime Profile Catalog" in profile_catalog
    assert "Profile Catalog" in profile_catalog
    assert "profiles=1" in profile_catalog
    assert "production-operator" in profile_catalog
    assert "Active Domains" in profile_catalog
    assert "Configured Domains" in profile_catalog
    assert "Capability Catalog" in profile_catalog
    assert 'href="/console/settings"' in profile_catalog

    catalogs = {
        WebCatalogPage.DOMAINS: ("Domain Catalog", "Configured Domains", "production-operator"),
        WebCatalogPage.DOMAIN_PACKAGES: (
            "Domain Package Catalog",
            "kubernetes.domain:build_domain",
            "observability@1.0.0",
            "Resources",
        ),
        WebCatalogPage.CAPABILITIES: ("Capability Catalog", "inspect_workload", "High Risk"),
        WebCatalogPage.TOOLS: ("Tool Catalog", "kubernetes_inspect_workload", "No Side Effect"),
        WebCatalogPage.POLICIES: ("Policy Catalog", "allow-read", "Confirm"),
        WebCatalogPage.EVALUATORS: ("Evaluator Catalog", "workload-health", "Sessions"),
        WebCatalogPage.MEMORY: ("Memory Catalog", "kubernetes readiness", "Scoped"),
    }
    for page, expected in catalogs.items():
        catalog = render_web_catalog(snapshot, page)
        assert f"Universal Agent Runtime {expected[0]}" in catalog
        for text in expected:
            assert text in catalog
        assert 'href="/console/evaluations"' in catalog

    settings = render_web_settings(snapshot)

    assert "Universal Agent Runtime Settings" in settings
    assert "Settings" in settings
    assert "Runtime Configuration" in settings
    assert "Operational Diagnostics" in settings
    assert "No active operational issues" in settings
    assert "Store Backend" in settings
    assert "State/Event Commit" in settings
    assert "Model Provider" in settings
    assert "Model Name" in settings
    assert "scripted" in settings
    assert "memory" in settings
    assert "Max Iterations" in settings
    assert "20" in settings
    assert "Configured Domains" in settings
    assert "kubernetes" in settings
    assert "0.2.0" in settings
    assert "kubernetes_api" in settings
    assert "https://cluster.example.test" in settings
    assert "&lt;redacted&gt;" in settings
    assert "Runtime Secrets" in settings
    assert "kubernetes_api_token" in settings
    assert "KUBERNETES_API_TOKEN" in settings
    assert "available" in settings
    assert "No environment settings" in settings

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
            decision_rejected_count=1,
        ),
    )

    degraded_console = render_web_console(degraded)
    degraded_settings = render_web_settings(degraded)

    for degraded_rendered in (degraded_console, degraded_settings):
        assert "Operational Diagnostics" in degraded_rendered
        assert "store unavailable" in degraded_rendered
        assert "failed_goals" in degraded_rendered
        assert "tool_failures" in degraded_rendered
        assert "decisions_rejected" in degraded_rendered
        assert "recovery_exhausted" in degraded_rendered
        assert "policy_denials" in degraded_rendered
        assert "confirmations_required" in degraded_rendered
        assert "human_interventions" in degraded_rendered
        assert "resource_conflicts" in degraded_rendered
        assert "active_resource_locks" in degraded_rendered
        assert "waiting_sessions" in degraded_rendered
        assert "recoveries_planned" in degraded_rendered
