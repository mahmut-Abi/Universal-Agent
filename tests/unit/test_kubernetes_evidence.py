from __future__ import annotations

from datetime import UTC, datetime

from universal_agent.core import (
    Observation,
    ObservationStatus,
    SessionId,
    Task,
    immutable_json,
    new_action_id,
    new_observation_id,
)
from universal_agent.domains.kubernetes.evidence import KubernetesEvidenceExtractor
from universal_agent.evidence import EvidenceContext
from universal_agent.world import EntityId, FactWorldUpdater, InMemoryWorldModel


def test_kubernetes_workload_observation_projects_pod_world_facts() -> None:
    task = Task("Inspect Kubernetes workload", ("healthy",))
    observation = Observation(
        new_observation_id(),
        new_action_id(),
        task.id,
        "kubernetes_inspect_workload",
        ObservationStatus.SUCCEEDED,
        immutable_json(
            {
                "resource": "deployment/api",
                "healthy": False,
                "root_cause": "crash_loop_back_off",
                "pods": [
                    "ignored",
                    {"resource": "service/api"},
                    {
                        "resource": "pod/api-123",
                        "namespace": "prod",
                        "name": "api-123",
                        "phase": "Running",
                        "ready": False,
                        "restart_count": 5,
                        "resource_version": "rv-pod",
                        "root_cause": "crash_loop_back_off",
                        "containers": [
                            {
                                "name": "api",
                                "ready": False,
                                "restart_count": 5,
                                "state": "waiting",
                                "reason": "CrashLoopBackOff",
                            }
                        ],
                    }
                ],
            }
        ),
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    extracted = KubernetesEvidenceExtractor().extract(
        EvidenceContext(SessionId("session-kubernetes"), task, observation)
    )
    model = InMemoryWorldModel()
    updater = FactWorldUpdater()
    for item in extracted:
        updater.apply(model, item)

    snapshot = model.snapshot(SessionId("session-kubernetes"))
    deployment = snapshot.neighborhood_for("deployment/api")
    pod = snapshot.entity_for("pod/api-123")

    assert ("deployment/api", "relation:owns") in {(item.subject, item.claim) for item in extracted}
    assert deployment.outgoing_relations[0].target == EntityId("pod/api-123")
    assert pod is not None
    assert pod.kind == "Pod"
    assert pod.attributes["ready"] is False
    assert pod.attributes["restart_count"] == 5
    assert snapshot.value_for("root_cause", subject="pod/api-123") == "crash_loop_back_off"


def test_kubernetes_evidence_extractor_ignores_failed_observations() -> None:
    task = Task("Inspect Kubernetes workload", ("healthy",))
    observation = Observation(
        new_observation_id(),
        new_action_id(),
        task.id,
        "kubernetes_inspect_workload",
        ObservationStatus.FAILED,
        immutable_json({"resource": "deployment/api", "healthy": False}),
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert (
        KubernetesEvidenceExtractor().extract(
            EvidenceContext(SessionId("session-kubernetes"), task, observation)
        )
        == ()
    )


def test_kubernetes_log_observation_marks_pod_diagnostics_observed() -> None:
    task = Task("Collect pod logs", ("pod_diagnostics_observed",))
    observation = Observation(
        new_observation_id(),
        new_action_id(),
        task.id,
        "kubernetes_inspect_logs",
        ObservationStatus.SUCCEEDED,
        immutable_json(
            {
                "resource": "pod/api-123",
                "namespace": "prod",
                "line_count": 2,
                "recent_logs": "first\nsecond\n",
            }
        ),
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    extracted = KubernetesEvidenceExtractor().extract(
        EvidenceContext(SessionId("session-kubernetes"), task, observation)
    )

    assert ("pod/api-123", "pod_diagnostics_observed", True) in {
        (item.subject, item.claim, item.value) for item in extracted
    }
