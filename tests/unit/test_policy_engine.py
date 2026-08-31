from __future__ import annotations

import pytest

from universal_agent.core import (
    ActionId,
    CapabilityCategory,
    CapabilityDefinition,
    GoalId,
    PolicyContext,
    PolicyEffect,
    RiskLevel,
    SessionId,
    TaskId,
    ToolDefinition,
    immutable_json,
)
from universal_agent.policy import PolicyEngine, PolicyRule


def make_context(
    *,
    capability_name: str = "inspect",
    category: CapabilityCategory = CapabilityCategory.OBSERVATION,
    risk: RiskLevel = RiskLevel.LOW,
    tool_risk: RiskLevel = RiskLevel.LOW,
    confirmed: bool = False,
) -> PolicyContext:
    capability = CapabilityDefinition(
        capability_name,
        f"{capability_name} capability",
        category,
        risk,
    )
    tool = ToolDefinition(
        "noop",
        "noop tool",
        (capability_name,),
        risk=tool_risk,
    )
    return PolicyContext(
        SessionId("s"),
        GoalId("g"),
        TaskId("t"),
        ActionId("a"),
        capability,
        tool,
        None,
        immutable_json(),
        confirmed=confirmed,
    )


@pytest.mark.unit
def test_policy_rule_does_not_match_unlisted_capability() -> None:
    rule = PolicyRule("deny-inspect", PolicyEffect.DENY, "no inspect", capabilities=("inspect",))
    result = rule.evaluate(make_context(capability_name="scale"))
    assert result is None


@pytest.mark.unit
def test_policy_rule_matches_by_capability() -> None:
    rule = PolicyRule("allow-inspect", PolicyEffect.ALLOW, "ok", capabilities=("inspect",))
    result = rule.evaluate(make_context(capability_name="inspect"))
    assert result is not None
    assert result.effect is PolicyEffect.ALLOW
    assert result.policy_name == "allow-inspect"


@pytest.mark.unit
def test_policy_rule_matches_by_category() -> None:
    rule = PolicyRule(
        "deny-mutation",
        PolicyEffect.DENY,
        "no mutation",
        categories=(CapabilityCategory.MUTATION,),
    )
    result = rule.evaluate(make_context(category=CapabilityCategory.MUTATION))
    assert result is not None
    assert result.effect is PolicyEffect.DENY


@pytest.mark.unit
def test_policy_rule_does_not_match_other_category() -> None:
    rule = PolicyRule(
        "deny-mutation",
        PolicyEffect.DENY,
        "no mutation",
        categories=(CapabilityCategory.MUTATION,),
    )
    assert rule.evaluate(make_context(category=CapabilityCategory.OBSERVATION)) is None


@pytest.mark.unit
def test_policy_rule_matches_by_risk_on_tool() -> None:
    rule = PolicyRule("deny-high", PolicyEffect.DENY, "no high risk", risks=(RiskLevel.HIGH,))
    result = rule.evaluate(make_context(risk=RiskLevel.LOW, tool_risk=RiskLevel.HIGH))
    assert result is not None
    assert result.effect is PolicyEffect.DENY


@pytest.mark.unit
def test_policy_engine_summary_lists_policy_names() -> None:
    engine = PolicyEngine(
        (
            PolicyRule("a", PolicyEffect.ALLOW, "a"),
            PolicyRule("b", PolicyEffect.DENY, "b"),
        )
    )
    assert engine.summary == ("a", "b")


@pytest.mark.unit
def test_policy_engine_deny_overrides_allow() -> None:
    engine = PolicyEngine(
        (
            PolicyRule("allow-all", PolicyEffect.ALLOW, "allowed"),
            PolicyRule("deny-inspect", PolicyEffect.DENY, "denied", capabilities=("inspect",)),
        )
    )
    result = engine.check(make_context(capability_name="inspect"))
    assert result.effect is PolicyEffect.DENY
    assert result.reason == "denied"


@pytest.mark.unit
def test_policy_engine_require_confirmation_blocks_when_unconfirmed() -> None:
    engine = PolicyEngine(
        (
            PolicyRule(
                "confirm-inspect",
                PolicyEffect.REQUIRE_CONFIRMATION,
                "needs confirmation",
                capabilities=("inspect",),
            ),
        )
    )
    result = engine.check(make_context(capability_name="inspect", confirmed=False))
    assert result.effect is PolicyEffect.REQUIRE_CONFIRMATION
    assert result.reason == "needs confirmation"


@pytest.mark.unit
def test_policy_engine_require_confirmation_allows_when_confirmed() -> None:
    engine = PolicyEngine(
        (
            PolicyRule(
                "confirm-inspect",
                PolicyEffect.REQUIRE_CONFIRMATION,
                "needs confirmation",
                capabilities=("inspect",),
            ),
        )
    )
    result = engine.check(make_context(capability_name="inspect", confirmed=True))
    assert result.effect is PolicyEffect.ALLOW
    assert result.reason == "user confirmation satisfied"


@pytest.mark.unit
def test_policy_engine_explicit_allow_branch() -> None:
    engine = PolicyEngine(
        (PolicyRule("allow-inspect", PolicyEffect.ALLOW, "allowed", capabilities=("inspect",)),)
    )
    result = engine.check(make_context(capability_name="inspect"))
    assert result.effect is PolicyEffect.ALLOW
    assert result.policy_name == "allow-inspect"


@pytest.mark.unit
def test_policy_engine_mutation_default_deny() -> None:
    engine = PolicyEngine(())
    result = engine.check(make_context(category=CapabilityCategory.MUTATION))
    assert result.effect is PolicyEffect.DENY
    assert result.policy_name == "mutation-default-deny"


@pytest.mark.unit
def test_policy_engine_read_only_default_allow() -> None:
    engine = PolicyEngine(())
    result = engine.check(make_context(category=CapabilityCategory.OBSERVATION))
    assert result.effect is PolicyEffect.ALLOW
    assert result.policy_name == "read-only-default"
