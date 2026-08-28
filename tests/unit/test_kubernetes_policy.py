from __future__ import annotations

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


def test_kubernetes_scale_policy_allows_bounded_non_production_scaling() -> None:
    result = KubernetesScalePolicy().evaluate(scale_policy_context())

    assert result is not None
    assert result.effect is PolicyEffect.ALLOW
    assert result.reason == "bounded Kubernetes workload scaling allowed"


def test_kubernetes_scale_policy_requires_confirmation_in_production() -> None:
    result = KubernetesScalePolicy().evaluate(scale_policy_context(environment="production"))

    assert result is not None
    assert result.effect is PolicyEffect.REQUIRE_CONFIRMATION
    assert result.reason == "production workload scaling requires confirmation"


def test_kubernetes_scale_policy_uses_pydantic_strict_argument_types() -> None:
    result = KubernetesScalePolicy().evaluate(scale_policy_context(arguments={"replicas": True}))

    assert result is not None
    assert result.effect is PolicyEffect.DENY
    assert result.reason == "scale_workload replicas must be an integer"


def test_kubernetes_scale_policy_rejects_unbounded_replicas() -> None:
    low = KubernetesScalePolicy().evaluate(scale_policy_context(arguments={"replicas": 0}))
    high = KubernetesScalePolicy().evaluate(scale_policy_context(arguments={"replicas": 11}))

    assert low is not None
    assert low.effect is PolicyEffect.DENY
    assert low.reason == "scale_workload replicas must be between 1 and 10"
    assert high is not None
    assert high.effect is PolicyEffect.DENY
    assert high.reason == "scale_workload replicas must be between 1 and 10"


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
