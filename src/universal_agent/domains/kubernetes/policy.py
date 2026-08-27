from __future__ import annotations

from pydantic import ValidationError as PydanticValidationError

from universal_agent.core import PolicyContext, PolicyEffect, PolicyResult
from universal_agent.core.config_validation import ConfigPayload, pydantic_error_details


class _KubernetesEnvironmentPayload(ConfigPayload):
    environment: str


class _ScaleWorkloadArgumentsPayload(ConfigPayload):
    name: str
    namespace: str
    replicas: int


class KubernetesScalePolicy:
    name = "kubernetes-scale-safety"
    _allowed_environments = frozenset({"development", "staging", "production"})
    _protected_environments = frozenset({"production"})
    _max_replicas = 10

    def evaluate(self, context: PolicyContext) -> PolicyResult | None:
        if context.capability.name != "scale_workload":
            return None

        environment = _environment_name(context)
        if environment is None:
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
        if not isinstance(target, str) or not target.startswith("deployment/"):
            return PolicyResult(
                PolicyEffect.DENY,
                "scale_workload requires a deployment target",
                self.name,
            )
        arguments = _scale_workload_arguments(context, self.name)
        if isinstance(arguments, PolicyResult):
            return arguments
        name = arguments.name
        namespace = arguments.namespace
        replicas = arguments.replicas
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
        expected_resource = _expected_criterion(context, "resource")
        if expected_resource is not None and target != expected_resource:
            return PolicyResult(
                PolicyEffect.DENY,
                "scale_workload target is outside the requested workload scope",
                self.name,
            )
        expected_namespace = _expected_criterion(context, "namespace")
        if expected_namespace is not None and namespace != expected_namespace:
            return PolicyResult(
                PolicyEffect.DENY,
                "scale_workload namespace is outside the requested workload scope",
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


def _environment_name(context: PolicyContext) -> str | None:
    try:
        payload = _KubernetesEnvironmentPayload.model_validate(dict(context.environment))
    except PydanticValidationError:
        return None
    environment = payload.environment.strip()
    return environment or None


def _scale_workload_arguments(
    context: PolicyContext,
    policy_name: str,
) -> _ScaleWorkloadArgumentsPayload | PolicyResult:
    try:
        return _ScaleWorkloadArgumentsPayload.model_validate(dict(context.arguments))
    except PydanticValidationError as exc:
        field = pydantic_error_details(exc).path
        if field == "namespace":
            return PolicyResult(
                PolicyEffect.DENY,
                "scale_workload requires a namespace",
                policy_name,
            )
        if field == "replicas":
            return PolicyResult(
                PolicyEffect.DENY,
                "scale_workload replicas must be an integer",
                policy_name,
            )
        return PolicyResult(
            PolicyEffect.DENY,
            "scale_workload target does not match the workload name",
            policy_name,
        )


def _expected_criterion(context: PolicyContext, key: str) -> str | None:
    for criterion in context.goal_success_criteria:
        if criterion.key == key and isinstance(criterion.expected, str):
            expected = criterion.expected.strip()
            if expected:
                return expected
    return None
