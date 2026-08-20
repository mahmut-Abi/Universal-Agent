from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from universal_agent.core import (
    CapabilityCategory,
    PolicyContext,
    PolicyEffect,
    PolicyResult,
    RiskLevel,
)


class Policy(Protocol):
    @property
    def name(self) -> str: ...

    def evaluate(self, context: PolicyContext) -> PolicyResult | None: ...


@dataclass(frozen=True, slots=True)
class PolicyRule:
    name: str
    effect: PolicyEffect
    reason: str
    capabilities: tuple[str, ...] = ()
    categories: tuple[CapabilityCategory, ...] = ()
    risks: tuple[RiskLevel, ...] = ()

    def evaluate(self, context: PolicyContext) -> PolicyResult | None:
        if self.capabilities and context.capability.name not in self.capabilities:
            return None
        if self.categories and context.capability.category not in self.categories:
            return None
        if self.risks and context.tool.risk not in self.risks:
            return None
        return PolicyResult(self.effect, self.reason, self.name)


class PolicyEngine:
    def __init__(self, policies: tuple[Policy, ...]) -> None:
        self._policies = policies

    @property
    def summary(self) -> tuple[str, ...]:
        return tuple(policy.name for policy in self._policies)

    def check(self, context: PolicyContext) -> PolicyResult:
        results = [result for policy in self._policies if (result := policy.evaluate(context))]
        denied = next((item for item in results if item.effect is PolicyEffect.DENY), None)
        if denied is not None:
            return denied
        confirmation = next(
            (item for item in results if item.effect is PolicyEffect.REQUIRE_CONFIRMATION), None
        )
        if confirmation is not None:
            if not context.confirmed:
                return confirmation
            return PolicyResult(
                PolicyEffect.ALLOW,
                "user confirmation satisfied",
                confirmation.policy_name,
            )
        allowed = next((item for item in results if item.effect is PolicyEffect.ALLOW), None)
        if allowed is not None:
            return allowed
        if context.capability.category is CapabilityCategory.MUTATION:
            return PolicyResult(
                PolicyEffect.DENY,
                "mutation capability has no explicit allow policy",
                "mutation-default-deny",
            )
        return PolicyResult(PolicyEffect.ALLOW, "read-only capability allowed", "read-only-default")
