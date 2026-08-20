from __future__ import annotations

import pytest

from universal_agent.context import BasicContextCompiler
from universal_agent.core import (
    AgentState,
    CapabilityCategory,
    CapabilityDefinition,
    Decision,
    DecisionType,
    Goal,
    SuccessCriterion,
    Task,
    immutable_json,
    new_session_id,
)
from universal_agent.state import InMemoryStateStore, StateNotFoundError


def test_decision_contract_rejects_invalid_shapes() -> None:
    with pytest.raises(ValueError, match="capability"):
        Decision(
            type=DecisionType.EXECUTE,
            reason="inspect",
            expected_observations=("healthy",),
        ).validate()
    with pytest.raises(ValueError, match="action"):
        Decision(
            type=DecisionType.FINISH,
            reason="done",
            capability="inspect_service",
        ).validate()
    with pytest.raises(ValueError, match="message"):
        Decision(type=DecisionType.ASK_USER, reason="need input").validate()


def test_basic_context_exposes_capabilities_not_tools() -> None:
    state = AgentState(
        session_id=new_session_id(),
        goal=Goal("Verify service", (SuccessCriterion("healthy", True),)),
        current_task=Task("Probe service", ("healthy",)),
        satisfied_criteria={"healthy": False},
    )
    context = BasicContextCompiler().compile(
        state,
        (
            CapabilityDefinition(
                "inspect_service",
                "Inspect service",
                CapabilityCategory.OBSERVATION,
            ),
        ),
        ("read-only",),
        (),
    )
    assert context.goal_id == state.goal.id
    assert context.satisfied_criteria == immutable_json({"healthy": False})
    assert context.capabilities[0].name == "inspect_service"
    assert context.policy_summary == ("read-only",)


@pytest.mark.asyncio
async def test_state_store_controls_session_lifecycle() -> None:
    store = InMemoryStateStore()
    state = AgentState(
        session_id=new_session_id(),
        goal=Goal("Verify", ()),
        current_task=Task("Inspect", ()),
    )
    await store.create(state)
    assert await store.load(state.session_id) is state
    with pytest.raises(ValueError, match="already exists"):
        await store.create(state)
    with pytest.raises(StateNotFoundError):
        await store.load(new_session_id())
