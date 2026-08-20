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


def test_capability_without_tool_is_unavailable() -> None:
    capabilities = CapabilityRegistry()
    capabilities.register(
        CapabilityDefinition("inspect", "Inspect", CapabilityCategory.OBSERVATION)
    )
    with pytest.raises(CapabilityUnavailableError):
        CapabilityResolver(capabilities, ToolRegistry()).resolve("inspect")


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


def test_mutation_without_explicit_allow_is_denied() -> None:
    capability = CapabilityDefinition("change", "Change", CapabilityCategory.MUTATION)
    tool = NoopTool("tool", 1, SideEffect.REVERSIBLE)
    assert PolicyEngine(()).check(policy_context(capability, tool)).effect is PolicyEffect.DENY
