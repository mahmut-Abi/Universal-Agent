from __future__ import annotations

from universal_agent.core import JsonValue
from universal_agent.tasks import TaskExpansionContext, TaskSpec

_POD_LOG_ROOT_CAUSES = frozenset(
    {
        "crash_loop_back_off",
        "containers_not_ready",
        "create_container_config_error",
        "create_container_error",
        "err_image_pull",
        "image_pull_back_off",
        "pending",
    }
)


class KubernetesRemediationExpander:
    name = "kubernetes-remediation"
    capability_names = (
        "inspect_workload",
        "inspect_pod",
        "inspect_logs",
        "inspect_events",
        "scale_workload",
    )

    def expand(self, context: TaskExpansionContext) -> tuple[TaskSpec, ...]:
        facts = {fact.claim: fact.value for fact in context.world.facts}
        current_criteria = set(context.task.required_criteria)
        depends_on = (context.task.id,)
        specs: list[TaskSpec] = []

        if (
            facts.get("healthy") is False
            and facts.get("resource") is not None
            and facts.get("root_cause") is None
        ):
            specs.append(
                TaskSpec(
                    "diagnose-unhealthy-workload",
                    "Diagnose unhealthy Kubernetes workload",
                    ("root_cause",),
                    depends_on,
                )
            )

        root_cause = facts.get("root_cause")
        if (
            root_cause in _POD_LOG_ROOT_CAUSES
            and facts.get("pod_diagnostics_observed") is None
            and _has_owned_pod(facts.get("relation:owns"))
        ):
            specs.append(
                TaskSpec(
                    "collect-pod-diagnostics",
                    "Collect logs from the failing Kubernetes pod",
                    ("pod_diagnostics_observed",),
                    depends_on,
                )
            )

        if root_cause == "under_replicated" and facts.get("mutation_applied") is None:
            specs.append(
                TaskSpec(
                    "remediate-unhealthy-workload",
                    "Scale the under-replicated Kubernetes workload",
                    ("mutation_applied",),
                    depends_on,
                )
            )

        if facts.get("mutation_applied") is True and "mutation_applied" in current_criteria:
            specs.append(
                TaskSpec(
                    "verify-remediation",
                    "Verify Kubernetes workload health after remediation",
                    ("verification_observed",),
                    depends_on,
                )
            )

        if (
            facts.get("verification_observed") is True
            and facts.get("healthy") is False
            and "verification_observed" in current_criteria
        ):
            specs.append(
                TaskSpec(
                    "diagnose-after-remediation",
                    "Diagnose the workload after unsuccessful remediation",
                    ("post_remediation_root_cause",),
                    depends_on,
                )
            )

        return tuple(specs)


def _has_owned_pod(value: JsonValue | None) -> bool:
    if isinstance(value, str):
        return value.startswith("pod/") and value != "pod/"
    if isinstance(value, list):
        return any(
            isinstance(item, str) and item.startswith("pod/") and item != "pod/" for item in value
        )
    return False
