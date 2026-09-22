from __future__ import annotations

import re

from pydantic import ValidationError as PydanticValidationError

from universal_agent.core import PolicyContext, PolicyEffect, PolicyResult
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticNonEmptyString,
    parse_bounded_int,
    pydantic_error_details,
)


class _KubernetesEnvironmentPayload(ConfigPayload):
    environment: PydanticNonEmptyString


class _ScaleWorkloadArgumentsPayload(ConfigPayload):
    name: PydanticNonEmptyString
    namespace: PydanticNonEmptyString
    replicas: int


class _RestartWorkloadArgumentsPayload(ConfigPayload):
    name: PydanticNonEmptyString
    namespace: PydanticNonEmptyString


class KubernetesScalePolicy:
    name = "kubernetes-scale-safety"
    description = "bounded workload scaling with environment and goal-scope checks"
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

        target = _workload_target(context)
        if target is None:
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
        if target != f"deployment/{name}":
            return PolicyResult(
                PolicyEffect.DENY,
                "scale_workload target does not match the workload name",
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
        try:
            parse_bounded_int(
                replicas,
                "scale_workload replicas",
                minimum=1,
                maximum=self._max_replicas,
            )
        except ValueError as exc:
            return PolicyResult(
                PolicyEffect.DENY,
                str(exc),
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


def _workload_target(context: PolicyContext) -> str | None:
    """Resolve the deployment target for a mutation decision.

    Prefers the explicit decision target; falls back to the decision's
    workload ``name`` argument so models that omit the optional ``target``
    field are not spuriously denied (UA-LIVE-2026-09-21 F4). An explicitly
    provided non-deployment target is returned unchanged and denied by the
    callers' prefix/mismatch checks.
    """
    target = context.target
    name = context.arguments.get("name")
    name_str = name.strip() if isinstance(name, str) else None
    if isinstance(target, str) and target.strip():
        normalized = target.strip()
        # Model target encodings seen in the wild (UA-LIVE-2026-09-21 F4,
        # baseline S3): "deployment/<name>:<container>" and
        # "<namespace>/<name>". Both normalize to "deployment/<name>" only
        # when the workload part matches the decision's name argument;
        # anything else is returned unchanged and denied by the callers'
        # prefix/mismatch checks.
        head, sep, _suffix = normalized.partition(":")
        if sep and name_str and head == f"deployment/{name_str}":
            return head
        ns_head, ns_sep, ns_name = normalized.partition("/")
        namespace = context.arguments.get("namespace")
        if (
            ns_sep
            and ns_head != "deployment"
            and name_str
            and ns_name.strip() == name_str
            and isinstance(namespace, str)
            and ns_head.strip() == namespace.strip()
        ):
            return f"deployment/{name_str}"
        return normalized
    if name_str:
        if "/" in name_str:
            return name_str
        return f"deployment/{name_str}"
    return None


def _environment_name(context: PolicyContext) -> str | None:
    try:
        payload = _KubernetesEnvironmentPayload.model_validate(dict(context.environment))
    except PydanticValidationError:
        return None
    return payload.environment.strip()


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


class KubernetesRestartPolicy:
    """Guard for the restart_workload mutation capability.

    Mirrors the scale-safety policy: environment must be identified, only
    deployment targets are allowed, goal-scope criteria are enforced, and
    production restarts always require human confirmation.
    """

    name = "kubernetes-restart-safety"
    description = "deployment rolling restarts with production confirmation"
    _allowed_environments = frozenset({"development", "staging", "production"})
    _protected_environments = frozenset({"production"})

    def evaluate(self, context: PolicyContext) -> PolicyResult | None:
        if context.capability.name != "restart_workload":
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

        target = _workload_target(context)
        if target is None:
            return PolicyResult(
                PolicyEffect.DENY,
                "restart_workload requires a deployment target",
                self.name,
            )
        arguments = _restart_workload_arguments(context, self.name)
        if isinstance(arguments, PolicyResult):
            return arguments
        if arguments.name and target != f"deployment/{arguments.name}":
            return PolicyResult(
                PolicyEffect.DENY,
                "restart_workload target does not match the workload name",
                self.name,
            )
        expected_resource = _expected_criterion(context, "resource")
        if expected_resource is not None and target != expected_resource:
            return PolicyResult(
                PolicyEffect.DENY,
                "restart_workload target is outside the requested workload scope",
                self.name,
            )
        expected_namespace = _expected_criterion(context, "namespace")
        if expected_namespace is not None and arguments.namespace != expected_namespace:
            return PolicyResult(
                PolicyEffect.DENY,
                "restart_workload namespace is outside the requested workload scope",
                self.name,
            )
        if environment in self._protected_environments:
            return PolicyResult(
                PolicyEffect.REQUIRE_CONFIRMATION,
                "production workload restart requires confirmation",
                self.name,
            )
        return PolicyResult(
            PolicyEffect.ALLOW,
            "bounded Kubernetes workload restart allowed",
            self.name,
        )


def _restart_workload_arguments(
    context: PolicyContext,
    policy_name: str,
) -> _RestartWorkloadArgumentsPayload | PolicyResult:
    try:
        return _RestartWorkloadArgumentsPayload.model_validate(dict(context.arguments))
    except PydanticValidationError as exc:
        field = pydantic_error_details(exc).path
        if field == "namespace":
            return PolicyResult(
                PolicyEffect.DENY,
                "restart_workload requires a namespace",
                policy_name,
            )
        return PolicyResult(
            PolicyEffect.DENY,
            "restart_workload requires a workload name",
            policy_name,
        )


class _SetImageArgumentsPayload(ConfigPayload):
    name: PydanticNonEmptyString
    namespace: PydanticNonEmptyString
    container: PydanticNonEmptyString
    image: PydanticNonEmptyString


class KubernetesSetImagePolicy:
    """Guard for the set_image mutation capability.

    Mirrors the scale/restart safety policies: identified environment,
    deployment targets only, goal-scope criteria enforced, image reference
    validated, and production image changes always require human confirmation
    (UA-LIVE-2026-09-21 F3).
    """

    name = "kubernetes-set-image-safety"
    description = "deployment image changes with validation and production confirmation"
    _allowed_environments = frozenset({"development", "staging", "production"})
    _protected_environments = frozenset({"production"})

    def evaluate(self, context: PolicyContext) -> PolicyResult | None:
        if context.capability.name != "set_image":
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

        target = _workload_target(context)
        if target is None:
            return PolicyResult(
                PolicyEffect.DENY,
                "set_image requires a deployment target",
                self.name,
            )
        arguments = _set_image_arguments(context, self.name)
        if isinstance(arguments, PolicyResult):
            return arguments
        if target != f"deployment/{arguments.name}":
            return PolicyResult(
                PolicyEffect.DENY,
                "set_image target does not match the workload name",
                self.name,
            )
        expected_resource = _expected_criterion(context, "resource")
        if expected_resource is not None and target != expected_resource:
            return PolicyResult(
                PolicyEffect.DENY,
                "set_image target is outside the requested workload scope",
                self.name,
            )
        expected_namespace = _expected_criterion(context, "namespace")
        if expected_namespace is not None and arguments.namespace != expected_namespace:
            return PolicyResult(
                PolicyEffect.DENY,
                "set_image namespace is outside the requested workload scope",
                self.name,
            )
        if not _image_reference_is_valid(arguments.image):
            return PolicyResult(
                PolicyEffect.DENY,
                "set_image image must be a fully-qualified container image reference",
                self.name,
            )
        if environment in self._protected_environments:
            return PolicyResult(
                PolicyEffect.REQUIRE_CONFIRMATION,
                "production workload image change requires confirmation",
                self.name,
            )
        return PolicyResult(
            PolicyEffect.ALLOW,
            "bounded Kubernetes workload image change allowed",
            self.name,
        )


def _image_reference_is_valid(image: str) -> bool:
    """Accept a conservative container image reference shape: one optional
    registry host with port, a name path, and an optional tag or digest."""
    return (
        re.fullmatch(
            r"[a-z0-9]+((\.|:|-)[a-z0-9]+)*(/[a-z0-9]+((\.|:|-|_)[a-z0-9]+)*)*"
            r"(:[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127})?(@sha256:[a-f0-9]{64})?",
            image.strip(),
        )
        is not None
    )


def _set_image_arguments(
    context: PolicyContext,
    policy_name: str,
) -> _SetImageArgumentsPayload | PolicyResult:
    try:
        return _SetImageArgumentsPayload.model_validate(dict(context.arguments))
    except PydanticValidationError as exc:
        field = pydantic_error_details(exc).path
        if field == "namespace":
            return PolicyResult(
                PolicyEffect.DENY,
                "set_image requires a namespace",
                policy_name,
            )
        if field == "container":
            return PolicyResult(
                PolicyEffect.DENY,
                "set_image requires a container name",
                policy_name,
            )
        if field == "image":
            return PolicyResult(
                PolicyEffect.DENY,
                "set_image requires an image reference",
                policy_name,
            )
        return PolicyResult(
            PolicyEffect.DENY,
            "set_image requires a workload name",
            policy_name,
        )


def _expected_criterion(context: PolicyContext, key: str) -> str | None:
    for criterion in context.goal_success_criteria:
        if criterion.key == key and isinstance(criterion.expected, str):
            expected = criterion.expected.strip()
            if expected:
                return expected
    return None
