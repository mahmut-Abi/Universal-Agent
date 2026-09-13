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
        "restart_workload",
    )

    def expand(self, context: TaskExpansionContext) -> tuple[TaskSpec, ...]:
        facts = {fact.claim: fact.value for fact in context.world.facts}
        current_criteria = set(context.task.required_criteria)
        depends_on = (context.task.id,)
        specs: list[TaskSpec] = []

        if (
            _fact_is(facts.get("healthy"), False)
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

        if (
            root_cause in _POD_LOG_ROOT_CAUSES
            and facts.get("pod_diagnostics_observed") is not None
            and facts.get("mutation_applied") is None
            and "healthy" in current_criteria
        ):
            # Pod-level failures (crash loops, image errors, pending) are not
            # repairable by scaling; a rolling restart re-pulls the image and
            # re-creates containers while preserving the workload spec. Only
            # goals that actually require workload health expand this task —
            # diagnostics-only goals stop after evidence collection.
            specs.append(
                TaskSpec(
                    "remediate-unhealthy-workload",
                    "Rolling-restart the workload to recover failed pods",
                    ("mutation_applied",),
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
        if _fact_is(facts.get("mutation_applied"), True) and "mutation_applied" in current_criteria:
            specs.append(
                TaskSpec(
                    "verify-remediation",
                    "Verify Kubernetes workload health after remediation",
                    ("verification_observed",),
                    depends_on,
                )
            )

        if (
            _fact_is(facts.get("verification_observed"), True)
            and _fact_is(facts.get("healthy"), False)
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


def _fact_is(value: object, expected: bool) -> bool:
    """Strict bool fact match: None never equals True/False.

    Uses variable-to-variable identity internally so fact semantics (missing
    is not False) stay exact without identity checks against literals.
    """

    return value is expected


def _has_owned_pod(value: JsonValue | None) -> bool:
    if isinstance(value, str):
        return value.startswith("pod/") and value != "pod/"
    if isinstance(value, list):
        return any(
            isinstance(item, str) and item.startswith("pod/") and item != "pod/" for item in value
        )
    return False
