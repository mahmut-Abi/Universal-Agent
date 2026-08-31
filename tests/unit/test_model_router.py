from __future__ import annotations

import pytest

from universal_agent.core import (
    CapabilityCategory,
    Decision,
    DecisionContext,
    RiskLevel,
    SideEffect,
)
from universal_agent.model.router import (
    ModelCandidate,
    ModelRoute,
    ModelRouter,
    ModelSelectionContext,
    NoModelRouteError,
    RiskAwareModelRouter,
)


class FakeAdapter:
    def __init__(self, name: str) -> None:
        self.name = name

    async def decide(self, context: DecisionContext) -> Decision:
        raise AssertionError("router must not invoke the model")


def make_context(
    *,
    risk: RiskLevel = RiskLevel.LOW,
    capability_category: CapabilityCategory | None = None,
    side_effect: SideEffect | None = None,
    readonly: bool = False,
    max_cost: float | None = None,
    preferred_adapter: str | None = None,
) -> ModelSelectionContext:
    return ModelSelectionContext(
        risk=risk,
        capability_category=capability_category,
        side_effect=side_effect,
        readonly=readonly,
        max_cost=max_cost,
        preferred_adapter=preferred_adapter,
    )


def tier_adapters() -> dict[str, FakeAdapter]:
    return {
        "light": FakeAdapter("light"),
        "standard": FakeAdapter("standard"),
        "powerful": FakeAdapter("powerful"),
    }


def router_with_three_tiers() -> tuple[RiskAwareModelRouter, dict[str, FakeAdapter]]:
    adapters = tier_adapters()
    router = RiskAwareModelRouter(
        [
            ModelCandidate(
                name="light",
                adapter=adapters["light"],
                cost=0.01,
                risk_tolerance=RiskLevel.LOW,
                weight=1.0,
            ),
            ModelCandidate(
                name="standard",
                adapter=adapters["standard"],
                cost=0.10,
                risk_tolerance=RiskLevel.MEDIUM,
                weight=2.0,
            ),
            ModelCandidate(
                name="powerful",
                adapter=adapters["powerful"],
                cost=1.00,
                risk_tolerance=RiskLevel.HIGH,
                weight=5.0,
            ),
        ]
    )
    return router, adapters


async def test_readonly_routes_to_lowest_cost() -> None:
    router, adapters = router_with_three_tiers()
    route = await router.select(make_context(risk=RiskLevel.LOW, readonly=True))
    assert isinstance(route, ModelRoute)
    assert route.adapter is adapters["light"]
    assert route.estimated_cost == 0.01


async def test_low_risk_mutation_routes_to_lowest_cost() -> None:
    router, adapters = router_with_three_tiers()
    route = await router.select(make_context(risk=RiskLevel.LOW))
    assert route.adapter is adapters["light"]


async def test_medium_risk_routes_to_capable_adapter() -> None:
    router, adapters = router_with_three_tiers()
    route = await router.select(
        make_context(risk=RiskLevel.MEDIUM, side_effect=SideEffect.REVERSIBLE)
    )
    assert route.adapter is adapters["standard"]


async def test_high_risk_routes_to_most_capable() -> None:
    router, adapters = router_with_three_tiers()
    route = await router.select(
        make_context(risk=RiskLevel.HIGH, side_effect=SideEffect.DESTRUCTIVE)
    )
    assert route.adapter is adapters["powerful"]


async def test_mutation_category_without_high_risk_still_requires_tolerance() -> None:
    router, adapters = router_with_three_tiers()
    route = await router.select(
        make_context(
            risk=RiskLevel.LOW,
            capability_category=CapabilityCategory.MUTATION,
        )
    )
    assert route.adapter is adapters["standard"]


async def test_preferred_adapter_takes_precedence() -> None:
    router, adapters = router_with_three_tiers()
    route = await router.select(make_context(risk=RiskLevel.HIGH, preferred_adapter="light"))
    assert route.adapter is adapters["light"]


async def test_cost_budget_excludes_expensive_candidates() -> None:
    router, adapters = router_with_three_tiers()
    route = await router.select(
        make_context(
            risk=RiskLevel.HIGH,
            max_cost=0.15,
            side_effect=SideEffect.DESTRUCTIVE,
        )
    )
    assert route.adapter is adapters["standard"]


async def test_preferred_over_cost_budget_falls_through_to_budgeted() -> None:
    router, adapters = router_with_three_tiers()
    route = await router.select(
        make_context(
            risk=RiskLevel.LOW,
            max_cost=0.05,
            preferred_adapter="powerful",
        )
    )
    assert route.adapter is adapters["light"]


async def test_no_tolerant_candidate_raises() -> None:
    limited = RiskAwareModelRouter(
        [
            ModelCandidate(
                name="light",
                adapter=FakeAdapter("light"),
                cost=0.01,
                risk_tolerance=RiskLevel.LOW,
                weight=1.0,
            )
        ]
    )
    with pytest.raises(NoModelRouteError) as exc:
        await limited.select(make_context(risk=RiskLevel.HIGH))
    assert isinstance(exc.value, ValueError)


async def test_no_candidate_at_all_raises() -> None:
    empty = RiskAwareModelRouter([])
    with pytest.raises(NoModelRouteError):
        await empty.select(make_context(risk=RiskLevel.LOW))


async def test_selection_is_deterministic() -> None:
    router, adapters = router_with_three_tiers()
    first = await router.select(make_context(risk=RiskLevel.HIGH))
    second = await router.select(make_context(risk=RiskLevel.HIGH))
    assert first.adapter is second.adapter is adapters["powerful"]


async def test_router_satisfies_protocol() -> None:
    router, _ = router_with_three_tiers()
    typed: ModelRouter = router
    assert typed is router
