from __future__ import annotations

from universal_agent.recovery.models import (
    FailureCategory,
    RecoveryRule,
    RecoveryStrategy,
)


def retry_transient_rule() -> RecoveryRule:
    return RecoveryRule(
        name="retry-transient",
        categories=(FailureCategory.TRANSIENT, FailureCategory.TIMEOUT),
        strategy=RecoveryStrategy.RETRY_ACTION,
        max_attempts=3,
        priority=25,
    )


def retry_tool_failure_rule() -> RecoveryRule:
    return RecoveryRule(
        name="retry-tool-failure",
        categories=(FailureCategory.TOOL_FAILURE,),
        strategy=RecoveryStrategy.RETRY_ACTION,
        max_attempts=2,
        priority=20,
    )


def alternative_capability_rule(*, substitute_capability: str) -> RecoveryRule:
    return RecoveryRule(
        name="alternative-capability",
        categories=(
            FailureCategory.TOOL_FAILURE,
            FailureCategory.DEPENDENCY_MISSING,
        ),
        strategy=RecoveryStrategy.ALTERNATIVE_CAPABILITY,
        max_attempts=1,
        capability=substitute_capability,
        priority=30,
    )


def diagnose_health_rule() -> RecoveryRule:
    return RecoveryRule(
        name="diagnose-health",
        categories=(
            FailureCategory.EVALUATION_FAILED,
            FailureCategory.VALIDATION,
        ),
        strategy=RecoveryStrategy.EXPAND_DIAGNOSIS_TASK,
        max_attempts=1,
        priority=40,
    )


def stop_on_policy_denied_rule() -> RecoveryRule:
    return RecoveryRule(
        name="stop-on-policy-denied",
        categories=(FailureCategory.PERMISSION_DENIED,),
        strategy=RecoveryStrategy.STOP,
        max_attempts=0,
        priority=10,
    )


def ask_user_rule() -> RecoveryRule:
    return RecoveryRule(
        name="ask-user",
        categories=(FailureCategory.UNKNOWN,),
        strategy=RecoveryStrategy.ASK_USER,
        max_attempts=1,
        priority=50,
    )


def default_recovery_rules() -> tuple[RecoveryRule, ...]:
    return (
        stop_on_policy_denied_rule(),
        retry_tool_failure_rule(),
        retry_transient_rule(),
        alternative_capability_rule(substitute_capability="escalate_to_user"),
        diagnose_health_rule(),
        ask_user_rule(),
    )
