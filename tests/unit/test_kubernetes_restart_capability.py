"""Tests for the restart_workload capability in the Kubernetes domain."""

from __future__ import annotations

from typing import cast

import pytest

from universal_agent.core import (
    ActionId,
    CapabilityCategory,
    GoalId,
    JsonMapping,
    PolicyContext,
    PolicyEffect,
    RiskLevel,
    SessionId,
    SideEffect,
    TaskId,
    ToolDefinition,
    immutable_json,
)
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.domains.kubernetes.backend import KubernetesBackend, KubernetesMutationBackend
from universal_agent.domains.kubernetes.policy import KubernetesRestartPolicy


class RecordingMutationBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, JsonMapping]] = []

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.calls.append((capability, arguments))
        return immutable_json({"resource": "deployment/api", "mutation_applied": True})


class NullInspectionBackend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        raise AssertionError(f"unexpected inspect: {capability}")


def remediation_domain() -> tuple[KubernetesRemediationDomain, RecordingMutationBackend]:
    backend = RecordingMutationBackend()
    domain = KubernetesRemediationDomain(
        cast(KubernetesBackend, NullInspectionBackend()),
        cast(KubernetesMutationBackend, backend),
    )
    return domain, backend


def test_remediation_domain_registers_restart_workload_capability() -> None:
    domain, _ = remediation_domain()

    capabilities = {item.name: item for item in domain.capabilities()}

    assert "restart_workload" in capabilities
    restart = capabilities["restart_workload"]
    assert restart.category is CapabilityCategory.MUTATION
    assert restart.risk is RiskLevel.LOW


def test_remediation_domain_registers_restart_tool_with_schema() -> None:
    domain, _ = remediation_domain()

    tools = {item.definition.name: item.definition for item in domain.tools()}
    definition = tools["kubernetes_restart_workload"]

    assert definition.capabilities == ("restart_workload",)
    assert definition.required_arguments == ("name", "namespace")
    assert definition.side_effect is SideEffect.REVERSIBLE
    assert definition.risk is RiskLevel.LOW


@pytest.mark.asyncio
async def test_restart_tool_executes_rollout_restart_through_backend() -> None:
    domain, backend = remediation_domain()

    tool = next(
        item for item in domain.tools() if item.definition.name == "kubernetes_restart_workload"
    )
    result = await tool.execute(
        immutable_json({"name": "api", "namespace": "prod", "restart_strategy": "rolling"})
    )

    assert result["mutation_applied"] is True
    assert backend.calls == [
        (
            "restart_workload",
            immutable_json({"name": "api", "namespace": "prod", "restart_strategy": "rolling"}),
        )
    ]


def restart_policy_context(
    *,
    environment: str = "production",
    target: str | None = "deployment/api",
) -> PolicyContext:
    domain, _ = remediation_domain()
    capability = next(item for item in domain.capabilities() if item.name == "restart_workload")
    return PolicyContext(
        SessionId("session-kubernetes"),
        GoalId("goal-kubernetes"),
        TaskId("task-kubernetes"),
        ActionId("action-kubernetes"),
        capability,
        ToolDefinition(
            "kubernetes_restart_workload",
            "Restart Kubernetes workload",
            ("restart_workload",),
            side_effect=SideEffect.REVERSIBLE,
        ),
        target,
        immutable_json({"name": "api", "namespace": "prod"}),
        environment=immutable_json({"environment": environment}),
    )


def test_restart_policy_requires_confirmation_in_production() -> None:
    result = KubernetesRestartPolicy().evaluate(restart_policy_context())

    assert result is not None
    assert result.effect is PolicyEffect.REQUIRE_CONFIRMATION


def test_restart_policy_allows_bounded_non_production_restart() -> None:
    result = KubernetesRestartPolicy().evaluate(restart_policy_context(environment="staging"))

    assert result is not None
    assert result.effect is PolicyEffect.ALLOW


def test_restart_policy_denies_non_deployment_target() -> None:
    result = KubernetesRestartPolicy().evaluate(restart_policy_context(target="pod/api-123"))

    assert result is not None
    assert result.effect is PolicyEffect.DENY
