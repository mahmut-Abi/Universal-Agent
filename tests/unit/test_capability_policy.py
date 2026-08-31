from __future__ import annotations

import pytest

from universal_agent.capability import (
    CapabilityRegistry,
    CapabilityResolver,
    CapabilityUnavailableError,
)
from universal_agent.core import (
    ActionId,
    CapabilityCategory,
    CapabilityDefinition,
    DomainIdentity,
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
from universal_agent.policy import PolicyEngine, PolicyRule
from universal_agent.tools import ToolRegistry


class NoopTool:
    def __init__(
        self,
        name: str,
        priority: int,
        side_effect: SideEffect = SideEffect.NONE,
    ) -> None:
        self.definition = ToolDefinition(
            name,
            name,
            ("inspect",),
            side_effect=side_effect,
            priority=priority,
        )

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return immutable_json()


def policy_context(
    capability: CapabilityDefinition,
    tool: NoopTool,
    *,
    confirmed: bool = False,
) -> PolicyContext:
    return PolicyContext(
        SessionId("s"),
        GoalId("g"),
        TaskId("t"),
        ActionId("a"),
        capability,
        tool.definition,
        None,
        immutable_json(),
        confirmed=confirmed,
    )


@pytest.mark.unit
def test_capability_registry_rejects_empty_capability_name() -> None:
    capability = CapabilityDefinition(
        " ",
        "Inspect",
        CapabilityCategory.OBSERVATION,
    )

    with pytest.raises(ValueError, match="capability name must not be empty"):
        CapabilityRegistry().register(capability)


@pytest.mark.unit
def test_capability_resolver_selects_lowest_priority_tool() -> None:
    capabilities = CapabilityRegistry()
    capability = CapabilityDefinition(
        "inspect",
        "Inspect",
        CapabilityCategory.OBSERVATION,
    )
    capabilities.register(capability)
    tools = ToolRegistry()
    tools.register(NoopTool("fallback", 100))
    tools.register(NoopTool("preferred", 10))

    resolved, tool = CapabilityResolver(capabilities, tools).resolve("inspect")
    assert resolved is capability
    assert tool.definition.name == "preferred"


@pytest.mark.unit
def test_capability_resolver_preserves_domain_ownership() -> None:
    identity = DomainIdentity("test", "1.0.0")
    capabilities = CapabilityRegistry()
    capability = CapabilityDefinition(
        "inspect",
        "Inspect",
        CapabilityCategory.OBSERVATION,
    )
    capabilities.register(capability, identity)
    tools = ToolRegistry()
    tools.register(NoopTool("preferred", 10), identity)

    resolution = CapabilityResolver(capabilities, tools).resolve_registration("inspect")

    assert resolution.capability is capability
    assert resolution.tool.definition.name == "preferred"
    assert resolution.capability_domain == identity
    assert resolution.tool_domain == identity


@pytest.mark.unit
def test_capability_resolver_rejects_cross_domain_tool_match() -> None:
    capabilities = CapabilityRegistry()
    capabilities.register(
        CapabilityDefinition("inspect", "Inspect", CapabilityCategory.OBSERVATION),
        DomainIdentity("alpha", "1.0.0"),
    )
    tools = ToolRegistry()
    tools.register(NoopTool("preferred", 10), DomainIdentity("beta", "1.0.0"))

    with pytest.raises(CapabilityUnavailableError, match="domain mismatch"):
        CapabilityResolver(capabilities, tools).resolve_registration("inspect")


@pytest.mark.unit
def test_capability_without_tool_is_unavailable() -> None:
    capabilities = CapabilityRegistry()
    capabilities.register(
        CapabilityDefinition("inspect", "Inspect", CapabilityCategory.OBSERVATION)
    )
    with pytest.raises(CapabilityUnavailableError):
        CapabilityResolver(capabilities, ToolRegistry()).resolve("inspect")


@pytest.mark.behavior
def test_policy_deny_precedes_confirmation_and_allow() -> None:
    capability = CapabilityDefinition(
        "inspect",
        "Inspect",
        CapabilityCategory.OBSERVATION,
        RiskLevel.HIGH,
    )
    tool = NoopTool("tool", 1)
    tool.definition = ToolDefinition(
        "tool",
        "tool",
        ("inspect",),
        risk=RiskLevel.HIGH,
        priority=1,
    )
    engine = PolicyEngine(
        (
            PolicyRule("allow", PolicyEffect.ALLOW, "allowed", capabilities=("inspect",)),
            PolicyRule(
                "confirm",
                PolicyEffect.REQUIRE_CONFIRMATION,
                "confirm",
                capabilities=("inspect",),
            ),
            PolicyRule("deny", PolicyEffect.DENY, "denied", risks=(RiskLevel.HIGH,)),
        )
    )
    assert engine.check(policy_context(capability, tool)).effect is PolicyEffect.DENY


@pytest.mark.unit
def test_confirmation_becomes_allow_after_user_confirms() -> None:
    capability = CapabilityDefinition(
        "inspect",
        "Inspect",
        CapabilityCategory.OBSERVATION,
    )
    tool = NoopTool("tool", 1)
    engine = PolicyEngine(
        (
            PolicyRule(
                "confirm",
                PolicyEffect.REQUIRE_CONFIRMATION,
                "confirm",
                capabilities=("inspect",),
            ),
        )
    )
    assert (
        engine.check(policy_context(capability, tool)).effect is PolicyEffect.REQUIRE_CONFIRMATION
    )
    assert (
        engine.check(policy_context(capability, tool, confirmed=True)).effect is PolicyEffect.ALLOW
    )


@pytest.mark.unit
def test_mutation_without_explicit_allow_is_denied() -> None:
    capability = CapabilityDefinition("change", "Change", CapabilityCategory.MUTATION)
    tool = NoopTool("tool", 1, SideEffect.REVERSIBLE)
    assert PolicyEngine(()).check(policy_context(capability, tool)).effect is PolicyEffect.DENY
