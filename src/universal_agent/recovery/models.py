from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from universal_agent.core import ErrorCode, JsonMapping, TaskId, immutable_json
from universal_agent.core.config_validation import parse_non_empty_string, parse_non_negative_int


class FailureCategory(StrEnum):
    TIMEOUT = "timeout"
    TRANSIENT = "transient"
    PERMISSION_DENIED = "permission_denied"
    VALIDATION = "validation"
    DEPENDENCY_MISSING = "dependency_missing"
    TOOL_FAILURE = "tool_failure"
    EVALUATION_FAILED = "evaluation_failed"
    UNKNOWN = "unknown"


class RecoveryStrategy(StrEnum):
    RETRY_ACTION = "retry_action"
    REOBSERVE = "reobserve"
    EXPAND_DIAGNOSIS_TASK = "expand_diagnosis_task"
    ALTERNATIVE_CAPABILITY = "alternative_capability"
    ASK_USER = "ask_user"
    STOP = "stop"
    ROLLBACK = "rollback"


@dataclass(frozen=True, slots=True)
class Failure:
    task_id: TaskId
    error_code: ErrorCode
    category: FailureCategory
    reason: str
    capability: str | None = None
    arguments: JsonMapping = field(default_factory=immutable_json)
    target: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveryRule:
    name: str
    categories: tuple[FailureCategory, ...]
    strategy: RecoveryStrategy
    max_attempts: int = 0
    capability: str | None = None
    priority: int = 100
    match_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        parse_non_negative_int(
            self.max_attempts,
            "recovery max_attempts",
            range_template="{path} cannot be negative",
        )
        if self.strategy is RecoveryStrategy.ALTERNATIVE_CAPABILITY:
            try:
                parse_non_empty_string(self.capability or "", "alternative capability recovery")
            except ValueError as exc:
                raise ValueError("alternative capability recovery requires a capability") from exc
        if self.strategy is RecoveryStrategy.ROLLBACK:
            raise ValueError("rollback execution is outside P2")


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    strategy: RecoveryStrategy
    rule_name: str
    attempt: int
    exhausted: bool = False
    capability: str | None = None
