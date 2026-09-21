"""Unit tests for the kubernetes set_image mutation safety policy and the
kubectl backend's set_image mutation (UA-LIVE-2026-09-21 F3)."""

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
from universal_agent.domains.kubernetes.policy import KubernetesSetImagePolicy


def set_image_policy_context(
    *,
    environment: JsonValue = "staging",
    target: str | None = None,
    arguments: dict[str, JsonValue] | None = None,
    criteria: tuple[SuccessCriterion, ...] = (),
) -> PolicyContext:
    return PolicyContext(
        SessionId("session-kubernetes"),
        GoalId("goal-kubernetes"),
        TaskId("task-kubernetes"),
        ActionId("action-kubernetes"),
        CapabilityDefinition(
            "set_image",
            "Set the container image of a deployment",
            CapabilityCategory.MUTATION,
        ),
        ToolDefinition(
            "kubernetes_set_image",
            "Set the container image of a Kubernetes deployment",
            ("set_image",),
            side_effect=SideEffect.REVERSIBLE,
        ),
        target,
        immutable_json(
            {
                "name": "api",
                "namespace": "prod",
                "container": "nginx",
                "image": "nginx:1.27",
                **(arguments or {}),
            }
        ),
        environment=immutable_json({"environment": environment}),
        goal_success_criteria=criteria,
    )


@pytest.mark.behavior
def test_set_image_policy_allows_bounded_non_production_change() -> None:
    result = KubernetesSetImagePolicy().evaluate(set_image_policy_context())

    assert result is not None
    assert result.effect is PolicyEffect.ALLOW


@pytest.mark.behavior
def test_set_image_policy_requires_confirmation_in_production() -> None:
    result = KubernetesSetImagePolicy().evaluate(set_image_policy_context(environment="production"))

    assert result is not None
    assert result.effect is PolicyEffect.REQUIRE_CONFIRMATION


@pytest.mark.behavior
def test_set_image_policy_derives_target_from_arguments() -> None:
    """F4 companion: no explicit decision target is required."""

    result = KubernetesSetImagePolicy().evaluate(set_image_policy_context(target=None))

    assert result is not None
    assert result.effect is PolicyEffect.ALLOW


@pytest.mark.behavior
def test_set_image_policy_rejects_mismatched_target() -> None:
    result = KubernetesSetImagePolicy().evaluate(
        set_image_policy_context(target="deployment/other")
    )

    assert result is not None
    assert result.effect is PolicyEffect.DENY
    assert result.reason == "set_image target does not match the workload name"


@pytest.mark.behavior
def test_set_image_policy_rejects_invalid_image_reference() -> None:
    for bad in ("nginx..bad::tag", "NGINX:1.27 with spaces", "a b:c"):
        result = KubernetesSetImagePolicy().evaluate(
            set_image_policy_context(arguments={"image": bad})
        )
        assert result is not None
        assert result.effect is PolicyEffect.DENY
        assert result.reason.startswith("set_image image must be")


@pytest.mark.behavior
def test_set_image_policy_requires_container_and_image() -> None:
    missing_container = KubernetesSetImagePolicy().evaluate(
        set_image_policy_context(arguments={"container": ""})
    )
    assert missing_container is not None
    assert missing_container.effect is PolicyEffect.DENY
    assert missing_container.reason == "set_image requires a container name"

    missing_image = KubernetesSetImagePolicy().evaluate(
        set_image_policy_context(arguments={"image": ""})
    )
    assert missing_image is not None
    assert missing_image.effect is PolicyEffect.DENY
    assert missing_image.reason == "set_image requires an image reference"


@pytest.mark.behavior
def test_set_image_policy_enforces_goal_scope_criteria() -> None:
    result = KubernetesSetImagePolicy().evaluate(
        set_image_policy_context(
            criteria=(
                SuccessCriterion("resource", "deployment/worker"),
                SuccessCriterion("namespace", "prod"),
            )
        )
    )

    assert result is not None
    assert result.effect is PolicyEffect.DENY
    assert result.reason == "set_image target is outside the requested workload scope"
