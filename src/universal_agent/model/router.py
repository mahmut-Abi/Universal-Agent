from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from universal_agent.core import CapabilityCategory, RiskLevel, SideEffect
from universal_agent.model.adapter import ModelAdapter

_RISK_RANK: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}


class NoModelRouteError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ModelRoute:
    adapter: ModelAdapter
    reason: str
    estimated_cost: float | None = None


@dataclass(frozen=True, slots=True)
class ModelSelectionContext:
    risk: RiskLevel
    capability_category: CapabilityCategory | None = None
    side_effect: SideEffect | None = None
    readonly: bool = False
    max_cost: float | None = None
    preferred_adapter: str | None = None


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    name: str
    adapter: ModelAdapter
    cost: float = 0.0
    risk_tolerance: RiskLevel = RiskLevel.LOW
    weight: float = 1.0


@runtime_checkable
class ModelRouter(Protocol):
    async def select(self, context: ModelSelectionContext) -> ModelRoute: ...


def _required_tolerance(context: ModelSelectionContext) -> RiskLevel:
    if context.risk is RiskLevel.HIGH or context.side_effect is SideEffect.DESTRUCTIVE:
        return RiskLevel.HIGH
    if (
        context.risk is RiskLevel.MEDIUM
        or context.side_effect is SideEffect.REVERSIBLE
        or context.capability_category is CapabilityCategory.MUTATION
    ):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


class RiskAwareModelRouter:
    def __init__(self, candidates: list[ModelCandidate]) -> None:
        self._candidates = tuple(candidates)

    def _within_budget(
        self,
        context: ModelSelectionContext,
    ) -> list[ModelCandidate]:
        if context.max_cost is None:
            return list(self._candidates)
        return [c for c in self._candidates if c.cost <= context.max_cost]

    async def select(self, context: ModelSelectionContext) -> ModelRoute:
        eligible = self._within_budget(context)

        if context.preferred_adapter is not None:
            for candidate in eligible:
                if candidate.name == context.preferred_adapter:
                    return ModelRoute(
                        adapter=candidate.adapter,
                        reason=(
                            f"preferred adapter {candidate.name!r} selected "
                            f"for {context.risk.value} risk"
                        ),
                        estimated_cost=candidate.cost or None,
                    )

        required = _required_tolerance(context)
        required_rank = _RISK_RANK[required]
        meeting = [c for c in eligible if _RISK_RANK[c.risk_tolerance] >= required_rank]

        if meeting:
            if required is RiskLevel.HIGH:
                chosen = max(meeting, key=lambda c: (c.weight, -c.cost))
                reason = (
                    f"{required.value} risk context routed to highest-capability "
                    f"adapter {chosen.name!r}"
                )
            else:
                chosen = min(meeting, key=lambda c: (c.cost, -c.weight))
                reason = (
                    f"{required.value} risk context routed to lowest-cost "
                    f"capable adapter {chosen.name!r}"
                )
            return ModelRoute(
                adapter=chosen.adapter,
                reason=reason,
                estimated_cost=chosen.cost or None,
            )

        if not eligible:
            raise NoModelRouteError(
                f"no model candidate within cost budget for context risk={context.risk.value}"
            )

        best_effort = max(eligible, key=lambda c: (_RISK_RANK[c.risk_tolerance], c.weight, -c.cost))
        if _RISK_RANK[best_effort.risk_tolerance] < required_rank - 1:
            raise NoModelRouteError(
                f"no model candidate tolerant of {required.value} risk "
                f"(best available is {best_effort.risk_tolerance.value}) "
                f"for context risk={context.risk.value}"
            )

        return ModelRoute(
            adapter=best_effort.adapter,
            reason=(
                f"degraded route to {best_effort.name!r}: no candidate meets "
                f"{required.value} risk within budget, using closest capable adapter"
            ),
            estimated_cost=best_effort.cost or None,
        )
