from __future__ import annotations

from universal_agent.tasks import TaskExpansionContext, TaskSpec


class KubernetesRemediationExpander:
    name = "kubernetes-remediation"
    capability_names = (
        "inspect_workload",
        "inspect_pod",
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

        if (
            facts.get("root_cause") == "under_replicated"
            and facts.get("mutation_applied") is None
            and "root_cause" in current_criteria
        ):
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
