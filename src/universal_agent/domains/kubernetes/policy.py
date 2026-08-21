from __future__ import annotations

from universal_agent.core import PolicyContext, PolicyEffect, PolicyResult


class KubernetesScalePolicy:
    name = "kubernetes-scale-safety"
    _allowed_environments = frozenset({"development", "staging", "production"})
    _protected_environments = frozenset({"production"})
    _max_replicas = 10

    def evaluate(self, context: PolicyContext) -> PolicyResult | None:
        if context.capability.name != "scale_workload":
            return None

        environment = context.environment.get("environment")
        if not isinstance(environment, str) or not environment:
            return PolicyResult(
                PolicyEffect.DENY,
                "Kubernetes mutation requires an identified environment",
                self.name,
            )
        if environment not in self._allowed_environments:
            return PolicyResult(
                PolicyEffect.DENY,
                f"Kubernetes mutation is not allowed in environment: {environment}",
                self.name,
            )

        target = context.target
        name = context.arguments.get("name")
        namespace = context.arguments.get("namespace")
        replicas = context.arguments.get("replicas")
        if not isinstance(target, str) or not target.startswith("deployment/"):
            return PolicyResult(
                PolicyEffect.DENY,
                "scale_workload requires a deployment target",
                self.name,
            )
        if not isinstance(name, str) or not name or target != f"deployment/{name}":
            return PolicyResult(
                PolicyEffect.DENY,
                "scale_workload target does not match the workload name",
                self.name,
            )
        if not isinstance(namespace, str) or not namespace:
            return PolicyResult(
                PolicyEffect.DENY,
                "scale_workload requires a namespace",
                self.name,
            )
        if not isinstance(replicas, int) or isinstance(replicas, bool):
            return PolicyResult(
                PolicyEffect.DENY,
                "scale_workload replicas must be an integer",
                self.name,
            )
        if replicas < 1 or replicas > self._max_replicas:
            return PolicyResult(
                PolicyEffect.DENY,
                f"scale_workload replicas must be between 1 and {self._max_replicas}",
                self.name,
            )
        if environment in self._protected_environments:
            return PolicyResult(
                PolicyEffect.REQUIRE_CONFIRMATION,
                "production workload scaling requires confirmation",
                self.name,
            )
        return PolicyResult(
            PolicyEffect.ALLOW,
            "bounded Kubernetes workload scaling allowed",
            self.name,
        )
