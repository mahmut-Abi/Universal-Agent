from __future__ import annotations

from datetime import UTC, datetime

from universal_agent.core import (
    JsonValue,
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
from universal_agent.world import FactWorldUpdater, InMemoryWorldModel


def main() -> None:
    task = Task("Inspect selector-backed workload", ("healthy",))
    data: dict[str, JsonValue] = {
        "resource": "deployment/api",
        "healthy": False,
        "root_cause": "crash_loop_back_off",
        "pods": [
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
    observation = Observation(
        new_observation_id(),
        new_action_id(),
        task.id,
        "kubernetes_inspect_workload",
        ObservationStatus.SUCCEEDED,
        immutable_json(data),
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    session_id = SessionId("session-kubernetes")
    evidence = KubernetesEvidenceExtractor().extract(EvidenceContext(session_id, task, observation))
    model = InMemoryWorldModel()
    updater = FactWorldUpdater()
    for item in evidence:
        updater.apply(model, item)

    world = model.snapshot(session_id)
    deployment = world.neighborhood_for("deployment/api")
    pod = world.entity_for("pod/api-123")
    owned_pods = ",".join(str(item.target) for item in deployment.outgoing_relations)

    print(f"evidence={len(evidence)} facts={len(world.facts)} relations={len(world.relations)}")
    print(f"deployment_root_cause={world.value_for('root_cause', subject='deployment/api')}")
    print(f"owned_pods={owned_pods}")
    print(f"pod_kind={pod.kind if pod else ''}")
    print(f"pod_restart_count={world.value_for('restart_count', subject='pod/api-123')}")
    print(f"pod_root_cause={world.value_for('root_cause', subject='pod/api-123')}")


if __name__ == "__main__":
    main()
