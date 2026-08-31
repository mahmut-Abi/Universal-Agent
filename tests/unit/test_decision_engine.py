from __future__ import annotations

import pytest

from universal_agent.core import (
    CapabilityCategory,
    CapabilitySummary,
    Decision,
    DecisionContext,
    DecisionType,
    GoalId,
    RiskLevel,
    SessionId,
    TaskId,
)
from universal_agent.model import ScriptedModelAdapter
from universal_agent.runtime.decision import (
    DecisionEngine,
    DecisionError,
    ModelBackedDecisionEngine,
    RuleBasedDecisionEngine,
    ask_user_when_no_capability,
    recover_when_no_capability,
)


def _context(capabilities: tuple[CapabilitySummary, ...] = ()) -> DecisionContext:
    return DecisionContext(
        session_id=SessionId("session-1"),
        goal_id=GoalId("goal-1"),
        goal_description="goal",
        task_id=TaskId("task-1"),
        task_description="task",
        iteration=1,
        satisfied_criteria={},
        latest_observation=None,
        capabilities=capabilities,
    )


def _capability(name: str = "inspect_pod") -> CapabilitySummary:
    return CapabilitySummary(
        name=name,
        description="describe",
        category=CapabilityCategory.OBSERVATION,
        risk=RiskLevel.LOW,
    )


def _execute_decision() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "the pod is unhealthy and needs inspection",
        capability="inspect_pod",
        target="pod/dify-api",
        arguments={"name": "dify-api"},
        expected_observations=("container_state", "recent_logs"),
    )


async def test_model_backed_returns_execute_decision() -> None:
    engine: DecisionEngine = ModelBackedDecisionEngine(ScriptedModelAdapter([_execute_decision()]))
    decision = await engine.decide(_context((_capability(),)))

    assert decision.type is DecisionType.EXECUTE
    assert decision.capability == "inspect_pod"
    assert decision.target == "pod/dify-api"
    assert decision.arguments == {"name": "dify-api"}
    assert decision.expected_observations == ("container_state", "recent_logs")
    assert decision.reason


async def test_model_backed_raises_on_invalid_decision() -> None:
    invalid = Decision(DecisionType.EXECUTE, "missing capability and observations")
    engine = ModelBackedDecisionEngine(ScriptedModelAdapter([invalid]))

    with pytest.raises(DecisionError):
        await engine.decide(_context((_capability(),)))


async def test_model_backed_raises_when_model_fails() -> None:
    class BrokenAdapter:
        async def decide(self, context: DecisionContext) -> Decision:
            raise RuntimeError("boom")

    engine = ModelBackedDecisionEngine(BrokenAdapter())

    with pytest.raises(DecisionError):
        await engine.decide(_context((_capability(),)))


async def test_rule_based_asks_user_when_no_capability() -> None:
    engine = RuleBasedDecisionEngine(
        rules=[ask_user_when_no_capability],
        fallback=ModelBackedDecisionEngine(ScriptedModelAdapter([_execute_decision()])),
    )
    decision = await engine.decide(_context(()))

    assert decision.type is DecisionType.ASK_USER
    assert decision.message


async def test_rule_based_falls_back_to_model_when_rule_misses() -> None:
    engine = RuleBasedDecisionEngine(
        rules=[ask_user_when_no_capability],
        fallback=ModelBackedDecisionEngine(ScriptedModelAdapter([_execute_decision()])),
    )
    decision = await engine.decide(_context((_capability(),)))

    assert decision.type is DecisionType.EXECUTE


async def test_rule_based_recovers_when_no_capability() -> None:
    engine = RuleBasedDecisionEngine(rules=[recover_when_no_capability])
    decision = await engine.decide(_context(()))

    assert decision.type is DecisionType.FINISH


async def test_rule_based_raises_when_no_match_and_no_fallback() -> None:
    def never_matches(context: DecisionContext) -> Decision | None:
        return None

    engine: RuleBasedDecisionEngine = RuleBasedDecisionEngine(rules=[never_matches])

    with pytest.raises(DecisionError):
        await engine.decide(_context((_capability(),)))
