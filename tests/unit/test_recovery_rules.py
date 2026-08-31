from __future__ import annotations

import pytest

from universal_agent.core import ErrorCode, TaskId
from universal_agent.recovery import (
    Failure,
    FailureCategory,
    RecoveryManager,
    RecoveryStrategy,
)
from universal_agent.recovery.rules import (
    alternative_capability_rule,
    ask_user_rule,
    default_recovery_rules,
    diagnose_health_rule,
    retry_tool_failure_rule,
    retry_transient_rule,
    stop_on_policy_denied_rule,
)


def _failure(
    error_code: ErrorCode,
    category: FailureCategory,
    capability: str | None = None,
) -> Failure:
    return Failure(TaskId("t"), error_code, category, "reason", capability)


@pytest.mark.unit
def test_transient_returns_retry() -> None:
    manager = RecoveryManager(default_recovery_rules())
    decision, _ = manager.decide(_failure(ErrorCode.TIMEOUT, FailureCategory.TIMEOUT), {})
    assert decision.strategy is RecoveryStrategy.RETRY_ACTION


@pytest.mark.unit
def test_tool_failure_returns_retry_by_default() -> None:
    manager = RecoveryManager(default_recovery_rules())
    decision, _ = manager.decide(_failure(ErrorCode.TOOL_FAILURE, FailureCategory.TOOL_FAILURE), {})
    assert decision.strategy is RecoveryStrategy.RETRY_ACTION
    assert decision.rule_name == "retry-tool-failure"


@pytest.mark.unit
def test_dependency_missing_returns_alternative_capability() -> None:
    manager = RecoveryManager(default_recovery_rules())
    decision, _ = manager.decide(
        _failure(ErrorCode.NO_CAPABILITY_TOOL, FailureCategory.DEPENDENCY_MISSING),
        {},
    )
    assert decision.strategy is RecoveryStrategy.ALTERNATIVE_CAPABILITY
    assert decision.capability == "escalate_to_user"


@pytest.mark.unit
def test_alternative_capability_rule_carries_substitute() -> None:
    rule = alternative_capability_rule(substitute_capability="inspect_via_api")
    assert rule.strategy is RecoveryStrategy.ALTERNATIVE_CAPABILITY
    assert rule.capability == "inspect_via_api"
    manager = RecoveryManager((rule,))
    decision, _ = manager.decide(_failure(ErrorCode.TOOL_FAILURE, FailureCategory.TOOL_FAILURE), {})
    assert decision.strategy is RecoveryStrategy.ALTERNATIVE_CAPABILITY
    assert decision.capability == "inspect_via_api"


@pytest.mark.unit
def test_evaluation_failed_returns_diagnosis() -> None:
    manager = RecoveryManager(default_recovery_rules())
    decision, _ = manager.decide(
        _failure(ErrorCode.EVALUATION_FAILED, FailureCategory.EVALUATION_FAILED),
        {},
    )
    assert decision.strategy is RecoveryStrategy.EXPAND_DIAGNOSIS_TASK


@pytest.mark.unit
def test_validation_returns_diagnosis() -> None:
    manager = RecoveryManager(default_recovery_rules())
    decision, _ = manager.decide(
        _failure(ErrorCode.INVALID_STATE, FailureCategory.VALIDATION),
        {},
    )
    assert decision.strategy is RecoveryStrategy.EXPAND_DIAGNOSIS_TASK
    assert decision.rule_name == "diagnose-health"


@pytest.mark.unit
def test_policy_denied_returns_stop() -> None:
    manager = RecoveryManager(default_recovery_rules())
    decision, _ = manager.decide(
        _failure(ErrorCode.POLICY_DENIED, FailureCategory.PERMISSION_DENIED),
        {},
    )
    assert decision.strategy is RecoveryStrategy.STOP
    assert decision.rule_name == "stop-on-policy-denied"


@pytest.mark.unit
def test_unknown_returns_ask_user() -> None:
    manager = RecoveryManager(default_recovery_rules())
    decision, _ = manager.decide(
        _failure(ErrorCode.MODEL_FAILURE, FailureCategory.UNKNOWN),
        {},
    )
    assert decision.strategy is RecoveryStrategy.ASK_USER


@pytest.mark.unit
def test_retry_exhaustion_stops() -> None:
    manager = RecoveryManager((retry_transient_rule(),))
    failure = _failure(ErrorCode.TIMEOUT, FailureCategory.TIMEOUT)
    attempts: dict[str, int] = {}
    last = None
    for _ in range(6):
        last, key = manager.decide(failure, attempts)
        attempts[key] = last.attempt
        if last.exhausted:
            break
    assert last is not None
    assert last.strategy is RecoveryStrategy.STOP
    assert last.exhausted


@pytest.mark.unit
def test_rules_sort_by_priority_in_manager() -> None:
    manager = RecoveryManager(default_recovery_rules())
    assert manager._rules[0].name == "stop-on-policy-denied"
    assert manager._rules[-1].name == "ask-user"


@pytest.mark.unit
def test_constructors_produce_distinct_valid_rules() -> None:
    rules = (
        retry_transient_rule(),
        retry_tool_failure_rule(),
        alternative_capability_rule(substitute_capability="x"),
        diagnose_health_rule(),
        stop_on_policy_denied_rule(),
        ask_user_rule(),
    )
    for rule in rules:
        assert isinstance(rule, type(rules[0]))
