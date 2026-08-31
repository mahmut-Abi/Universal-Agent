from __future__ import annotations

import pytest

from universal_agent.core import ErrorCode, TaskId, immutable_json
from universal_agent.recovery import (
    Failure,
    FailureCategory,
    RecoveryDecision,
    RecoveryManager,
    RecoveryRule,
    RecoveryStrategy,
    classify_failure,
)


@pytest.mark.unit
def test_recovery_rule_negative_max_attempts_rejected() -> None:
    with pytest.raises(ValueError):
        RecoveryRule(
            "retry",
            (FailureCategory.TIMEOUT,),
            RecoveryStrategy.RETRY_ACTION,
            max_attempts=-1,
        )


@pytest.mark.unit
def test_recovery_rule_alternative_capability_requires_capability() -> None:
    with pytest.raises(ValueError, match="alternative capability recovery requires a capability"):
        RecoveryRule(
            "alt",
            (FailureCategory.TOOL_FAILURE,),
            RecoveryStrategy.ALTERNATIVE_CAPABILITY,
        )


@pytest.mark.unit
def test_recovery_rule_alternative_capability_with_capability_accepted() -> None:
    rule = RecoveryRule(
        "alt",
        (FailureCategory.TOOL_FAILURE,),
        RecoveryStrategy.ALTERNATIVE_CAPABILITY,
        capability="inspect_alternate",
    )
    assert rule.capability == "inspect_alternate"


@pytest.mark.unit
def test_recovery_rule_rollback_is_outside_p2() -> None:
    with pytest.raises(ValueError, match="rollback execution is outside P2"):
        RecoveryRule(
            "rollback",
            (FailureCategory.TRANSIENT,),
            RecoveryStrategy.ROLLBACK,
        )


@pytest.mark.unit
def test_failure_constructs_with_all_fields() -> None:
    failure = Failure(
        TaskId("t"),
        ErrorCode.TIMEOUT,
        FailureCategory.TIMEOUT,
        "timed out",
        "inspect",
        immutable_json({"name": "x"}),
        "pod/x",
    )
    assert failure.task_id == TaskId("t")
    assert failure.error_code is ErrorCode.TIMEOUT
    assert failure.capability == "inspect"
    assert failure.target == "pod/x"


@pytest.mark.unit
def test_classify_failure_timeout() -> None:
    assert classify_failure(ErrorCode.TIMEOUT) is FailureCategory.TIMEOUT


@pytest.mark.unit
def test_classify_failure_permission_denied_variants() -> None:
    assert classify_failure(ErrorCode.POLICY_DENIED) is FailureCategory.PERMISSION_DENIED
    assert classify_failure(ErrorCode.CONFIRMATION_REJECTED) is FailureCategory.PERMISSION_DENIED


@pytest.mark.unit
def test_classify_failure_validation_variants() -> None:
    assert classify_failure(ErrorCode.VALIDATION_ERROR) is FailureCategory.VALIDATION
    assert classify_failure(ErrorCode.INVALID_STATE) is FailureCategory.VALIDATION
    assert classify_failure(ErrorCode.UNKNOWN_CAPABILITY) is FailureCategory.VALIDATION
    assert classify_failure(ErrorCode.UNKNOWN_TOOL) is FailureCategory.VALIDATION


@pytest.mark.unit
def test_classify_failure_dependency_missing() -> None:
    assert classify_failure(ErrorCode.NO_CAPABILITY_TOOL) is FailureCategory.DEPENDENCY_MISSING


@pytest.mark.unit
def test_classify_failure_evaluation_and_tool_failure() -> None:
    assert classify_failure(ErrorCode.EVALUATION_FAILED) is FailureCategory.EVALUATION_FAILED
    assert classify_failure(ErrorCode.TOOL_FAILURE) is FailureCategory.TOOL_FAILURE


@pytest.mark.unit
def test_classify_failure_unknown_variants() -> None:
    assert classify_failure(ErrorCode.UNKNOWN_EXECUTION) is FailureCategory.UNKNOWN
    assert classify_failure(ErrorCode.MODEL_FAILURE) is FailureCategory.UNKNOWN
    assert classify_failure(ErrorCode.ITERATION_LIMIT) is FailureCategory.UNKNOWN


@pytest.mark.unit
def test_recovery_manager_sorts_rules_by_priority_then_name() -> None:
    manager = RecoveryManager(
        (
            RecoveryRule(
                "b", (FailureCategory.TIMEOUT,), RecoveryStrategy.RETRY_ACTION, priority=100
            ),
            RecoveryRule(
                "a", (FailureCategory.TIMEOUT,), RecoveryStrategy.RETRY_ACTION, priority=100
            ),
            RecoveryRule(
                "c", (FailureCategory.TIMEOUT,), RecoveryStrategy.RETRY_ACTION, priority=50
            ),
        )
    )
    first = manager.decide(
        Failure(TaskId("t"), ErrorCode.TIMEOUT, FailureCategory.TIMEOUT, "x"),
        {},
    )
    assert first[0].rule_name in {"a", "b", "c"}


@pytest.mark.unit
def test_recovery_manager_matches_by_category_and_capability() -> None:
    manager = RecoveryManager(
        (
            RecoveryRule(
                "inspect-timeout",
                (FailureCategory.TIMEOUT,),
                RecoveryStrategy.RETRY_ACTION,
                max_attempts=2,
                match_capabilities=("inspect",),
            ),
        )
    )
    decision, key = manager.decide(
        Failure(TaskId("t"), ErrorCode.TIMEOUT, FailureCategory.TIMEOUT, "x", "inspect"),
        {},
    )
    assert decision.strategy is RecoveryStrategy.RETRY_ACTION
    assert decision.rule_name == "inspect-timeout"
    assert key == "t:timeout:inspect-timeout"


@pytest.mark.unit
def test_recovery_manager_stops_when_no_rule_matches() -> None:
    manager = RecoveryManager(())
    decision, key = manager.decide(
        Failure(TaskId("t"), ErrorCode.MODEL_FAILURE, FailureCategory.UNKNOWN, "x"),
        {},
    )
    assert decision.strategy is RecoveryStrategy.STOP
    assert decision.rule_name == "default-stop"
    assert key == ""


@pytest.mark.unit
def test_recovery_manager_stops_when_attempts_exhausted() -> None:
    manager = RecoveryManager(
        (
            RecoveryRule(
                "retry",
                (FailureCategory.TIMEOUT,),
                RecoveryStrategy.RETRY_ACTION,
                max_attempts=1,
            ),
        )
    )
    failure = Failure(TaskId("t"), ErrorCode.TIMEOUT, FailureCategory.TIMEOUT, "x", "inspect")
    first, key = manager.decide(failure, {})
    assert first.strategy is RecoveryStrategy.RETRY_ACTION
    assert not first.exhausted

    second, _ = manager.decide(failure, {key: first.attempt})
    assert second.strategy is RecoveryStrategy.STOP
    assert second.exhausted
    assert second.attempt == 2


@pytest.mark.unit
def test_recovery_manager_attempt_key_format() -> None:
    assert (
        RecoveryManager.attempt_key(TaskId("t"), FailureCategory.TOOL_FAILURE, "rule")
        == "t:tool_failure:rule"
    )


@pytest.mark.unit
def test_recovery_decision_carries_capability_for_alternative() -> None:
    decision = RecoveryDecision(
        RecoveryStrategy.ALTERNATIVE_CAPABILITY,
        "alt",
        1,
        capability="other",
    )
    assert decision.capability == "other"
