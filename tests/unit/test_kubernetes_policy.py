from __future__ import annotations

import pytest

from universal_agent.core import (
    ActionId,
    CapabilityCategory,
    CapabilityDefinition,
    GoalId,
    JsonValue,
    PolicyContext,
    PolicyEffect,
    SessionId,
    SideEffect,
    SuccessCriterion,
    TaskId,
    ToolDefinition,
    immutable_json,
)
from universal_agent.domains.kubernetes.policy import KubernetesScalePolicy


def scale_policy_context(
    *,
    environment: JsonValue = "staging",
    target: str | None = "deployment/api",
    arguments: dict[str, JsonValue] | None = None,
    criteria: tuple[SuccessCriterion, ...] = (),
) -> PolicyContext:
    return PolicyContext(
        SessionId("session-kubernetes"),
        GoalId("goal-kubernetes"),
        TaskId("task-kubernetes"),
        ActionId("action-kubernetes"),
        CapabilityDefinition(
            "scale_workload",
            "Scale Kubernetes workload",
            CapabilityCategory.MUTATION,
        ),
        ToolDefinition(
            "kubernetes_scale_workload",
            "Scale Kubernetes workload",
            ("scale_workload",),
            side_effect=SideEffect.REVERSIBLE,
        ),
        target,
        immutable_json(
            {
                "name": "api",
                "namespace": "prod",
                "replicas": 3,
                **(arguments or {}),
            }
        ),
        environment=immutable_json({"environment": environment}),
        goal_success_criteria=criteria,
    )


@pytest.mark.behavior
def test_kubernetes_scale_policy_allows_bounded_non_production_scaling() -> None:
    result = KubernetesScalePolicy().evaluate(scale_policy_context())

    assert result is not None
    assert result.effect is PolicyEffect.ALLOW
    assert result.reason == "bounded Kubernetes workload scaling allowed"


@pytest.mark.behavior
def test_kubernetes_scale_policy_requires_confirmation_in_production() -> None:
    result = KubernetesScalePolicy().evaluate(scale_policy_context(environment="production"))

    assert result is not None
    assert result.effect is PolicyEffect.REQUIRE_CONFIRMATION
    assert result.reason == "production workload scaling requires confirmation"


@pytest.mark.behavior
def test_kubernetes_scale_policy_uses_pydantic_strict_argument_types() -> None:
    result = KubernetesScalePolicy().evaluate(scale_policy_context(arguments={"replicas": True}))

    assert result is not None
    assert result.effect is PolicyEffect.DENY
    assert result.reason == "scale_workload replicas must be an integer"


@pytest.mark.behavior
def test_kubernetes_scale_policy_uses_pydantic_non_empty_arguments() -> None:
    empty_namespace = KubernetesScalePolicy().evaluate(
        scale_policy_context(arguments={"namespace": " "})
    )
    empty_name = KubernetesScalePolicy().evaluate(scale_policy_context(arguments={"name": " "}))

    assert empty_namespace is not None
    assert empty_namespace.effect is PolicyEffect.DENY
    assert empty_namespace.reason == "scale_workload requires a namespace"
    assert empty_name is not None
    assert empty_name.effect is PolicyEffect.DENY
    assert empty_name.reason == "scale_workload target does not match the workload name"


@pytest.mark.behavior
def test_kubernetes_scale_policy_rejects_unbounded_replicas() -> None:
    low = KubernetesScalePolicy().evaluate(scale_policy_context(arguments={"replicas": 0}))
    high = KubernetesScalePolicy().evaluate(scale_policy_context(arguments={"replicas": 11}))

    assert low is not None
    assert low.effect is PolicyEffect.DENY
    assert low.reason == "scale_workload replicas must be between 1 and 10"
    assert high is not None
    assert high.effect is PolicyEffect.DENY
    assert high.reason == "scale_workload replicas must be between 1 and 10"


@pytest.mark.behavior
def test_kubernetes_scale_policy_rejects_invalid_scope() -> None:
    result = KubernetesScalePolicy().evaluate(
        scale_policy_context(
            criteria=(
                SuccessCriterion("resource", "deployment/worker"),
                SuccessCriterion("namespace", "prod"),
            ),
        )
    )

    assert result is not None
    assert result.effect is PolicyEffect.DENY
    assert result.reason == "scale_workload target is outside the requested workload scope"


@pytest.mark.behavior
def test_kubernetes_scale_policy_derives_target_from_arguments_when_missing() -> None:
    """UA-LIVE-2026-09-21 F4: real models omit the optional decision target.

    The scale guard must derive ``deployment/<name>`` from the ``name``
    argument instead of denying, while an explicit non-deployment target is
    still rejected.
    """
    derived = KubernetesScalePolicy().evaluate(scale_policy_context(target=None))

    assert derived is not None
    assert derived.effect is PolicyEffect.ALLOW

    explicit_wrong = KubernetesScalePolicy().evaluate(
        scale_policy_context(target="deployment/other")
    )
    assert explicit_wrong is not None
    assert explicit_wrong.effect is PolicyEffect.DENY
    assert explicit_wrong.reason == "scale_workload target does not match the workload name"

    missing_entirely = KubernetesScalePolicy().evaluate(
        scale_policy_context(target=None, arguments={"name": " "})
    )
    assert missing_entirely is not None
    assert missing_entirely.effect is PolicyEffect.DENY
    assert missing_entirely.reason == "scale_workload requires a deployment target"


@pytest.mark.behavior
def test_kubernetes_scale_policy_normalizes_workload_container_target_form() -> None:
    """Models may encode workload+container as 'deployment/<name>:<container>'."""
    normalized = KubernetesScalePolicy().evaluate(scale_policy_context(target="deployment/api:api"))
    assert normalized is not None
    assert normalized.effect is PolicyEffect.ALLOW

    mismatched = KubernetesScalePolicy().evaluate(
        scale_policy_context(target="deployment/other:api")
    )
    assert mismatched is not None
    assert mismatched.effect is PolicyEffect.DENY
