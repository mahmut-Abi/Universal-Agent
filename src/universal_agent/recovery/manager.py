from __future__ import annotations

from collections.abc import Mapping

from universal_agent.core import ErrorCode, TaskId
from universal_agent.recovery.models import (
    Failure,
    FailureCategory,
    RecoveryDecision,
    RecoveryRule,
    RecoveryStrategy,
)


def classify_failure(error_code: ErrorCode) -> FailureCategory:
    if error_code is ErrorCode.TIMEOUT:
        return FailureCategory.TIMEOUT
    if error_code in {ErrorCode.POLICY_DENIED, ErrorCode.CONFIRMATION_REJECTED}:
        return FailureCategory.PERMISSION_DENIED
    if error_code in {
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.INVALID_STATE,
        ErrorCode.UNKNOWN_CAPABILITY,
        ErrorCode.UNKNOWN_TOOL,
    }:
        return FailureCategory.VALIDATION
    if error_code is ErrorCode.NO_CAPABILITY_TOOL:
        return FailureCategory.DEPENDENCY_MISSING
    if error_code is ErrorCode.EVALUATION_FAILED:
        return FailureCategory.EVALUATION_FAILED
    if error_code is ErrorCode.TOOL_FAILURE:
        return FailureCategory.TOOL_FAILURE
    if error_code is ErrorCode.UNKNOWN_EXECUTION:
        return FailureCategory.UNKNOWN
    return FailureCategory.UNKNOWN


class RecoveryManager:
    def __init__(self, rules: tuple[RecoveryRule, ...]) -> None:
        self._rules = tuple(sorted(rules, key=lambda item: (item.priority, item.name)))

    def decide(
        self,
        failure: Failure,
        attempts: Mapping[str, int],
    ) -> tuple[RecoveryDecision, str]:
        rule = next(
            (
                item
                for item in self._rules
                if failure.category in item.categories
                and (not item.match_capabilities or failure.capability in item.match_capabilities)
            ),
            None,
        )
        if rule is None:
            return RecoveryDecision(RecoveryStrategy.STOP, "default-stop", 0), ""
        key = self.attempt_key(failure.task_id, failure.category, rule.name)
        attempt = attempts.get(key, 0) + 1
        exhausted = attempt > rule.max_attempts
        return (
            RecoveryDecision(
                RecoveryStrategy.STOP if exhausted else rule.strategy,
                rule.name,
                attempt,
                exhausted,
                rule.capability,
            ),
            key,
        )

    @staticmethod
    def attempt_key(task_id: TaskId, category: FailureCategory, rule_name: str) -> str:
        return f"{task_id}:{category.value}:{rule_name}"
