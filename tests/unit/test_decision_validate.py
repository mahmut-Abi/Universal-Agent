from __future__ import annotations

from datetime import UTC
from types import MappingProxyType

import pytest

from universal_agent.core import (
    Decision,
    DecisionType,
    ErrorCode,
    ObservationStatus,
    PolicyEffect,
    RiskLevel,
    SideEffect,
    immutable_json,
    new_observation_id,
    utc_now,
)


@pytest.mark.unit
def test_decision_validate_rejects_empty_reason() -> None:
    with pytest.raises(ValueError, match="decision reason must not be empty"):
        Decision(DecisionType.WAIT, "   ").validate()


@pytest.mark.unit
def test_decision_validate_execute_requires_capability() -> None:
    with pytest.raises(ValueError, match="execute decision requires capability"):
        Decision(
            DecisionType.EXECUTE,
            "run it",
            capability=None,
            expected_observations=("status",),
        ).validate()


@pytest.mark.unit
def test_decision_validate_execute_requires_expected_observations() -> None:
    with pytest.raises(ValueError, match="execute decision requires expected_observations"):
        Decision(
            DecisionType.EXECUTE,
            "run it",
            capability="inspect",
            expected_observations=(),
        ).validate()


@pytest.mark.unit
def test_decision_validate_non_execute_cannot_include_capability() -> None:
    with pytest.raises(ValueError, match="cannot include an action"):
        Decision(DecisionType.WAIT, "wait", capability="inspect").validate()


@pytest.mark.unit
def test_decision_validate_non_execute_cannot_include_arguments() -> None:
    with pytest.raises(ValueError, match="cannot include an action"):
        Decision(DecisionType.WAIT, "wait", arguments=immutable_json({"x": 1})).validate()


@pytest.mark.unit
def test_decision_validate_non_execute_cannot_include_target() -> None:
    with pytest.raises(ValueError, match="cannot include an action"):
        Decision(DecisionType.WAIT, "wait", target="pod/x").validate()


@pytest.mark.unit
def test_decision_validate_ask_user_requires_message() -> None:
    with pytest.raises(ValueError, match="ask_user decision requires message"):
        Decision(DecisionType.ASK_USER, "ask").validate()


@pytest.mark.unit
def test_decision_validate_execute_passes() -> None:
    Decision(
        DecisionType.EXECUTE,
        "run it",
        capability="inspect",
        expected_observations=("status",),
    ).validate()


@pytest.mark.unit
def test_decision_validate_wait_passes() -> None:
    Decision(DecisionType.WAIT, "wait").validate()


@pytest.mark.unit
def test_decision_validate_ask_user_passes() -> None:
    Decision(DecisionType.ASK_USER, "ask", message="confirm?").validate()


@pytest.mark.unit
def test_decision_validate_finish_passes() -> None:
    Decision(DecisionType.FINISH, "done").validate()


@pytest.mark.unit
def test_immutable_json_returns_frozen_mapping() -> None:
    source = {"a": 1}
    value = immutable_json(source)
    assert isinstance(value, MappingProxyType)
    assert dict(value) == source
    source["a"] = 2
    assert dict(value) == {"a": 1}


@pytest.mark.unit
def test_core_str_enums_expose_expected_values() -> None:
    assert DecisionType.EXECUTE.value == "execute"
    assert PolicyEffect.DENY.value == "deny"
    assert RiskLevel.HIGH.value == "high"
    assert ErrorCode.TIMEOUT.value == "timeout"
    assert ObservationStatus.SUCCEEDED.value == "succeeded"
    assert SideEffect.DESTRUCTIVE.value == "destructive"


@pytest.mark.unit
def test_new_observation_id_and_utc_now_are_deterministic_with_primitives() -> None:
    from datetime import datetime

    from universal_agent.core import runtime_primitives

    fixed = datetime(2026, 1, 1, tzinfo=UTC)
    counter = {"n": 0}

    def clock() -> datetime:
        return fixed

    def ids(prefix: str) -> str:
        counter["n"] += 1
        return f"{prefix}-{counter['n']}"

    with runtime_primitives(clock=clock, id_factory=ids):
        assert utc_now() == fixed
        assert new_observation_id() == "observation-1"
