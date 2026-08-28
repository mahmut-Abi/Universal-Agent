from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from pydantic import Field, field_validator

from universal_agent.core import JsonMapping
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    duplicate_values,
    parse_optional_non_negative_int,
    parse_payload,
)
from universal_agent.evidence import EvidenceId
from universal_agent.multi_agent.contracts import AgentTaskId
from universal_agent.multi_agent.merge import (
    AgentResultMerge,
    AgentResultMergeStatus,
    agent_result_merge_payload,
    decode_agent_result_merge,
)


class _MultiAgentEvaluationExpectationsPayload(ConfigPayload):
    expected_status: AgentResultMergeStatus = AgentResultMergeStatus.COMPLETED
    required_evidence_ids: list[str] = Field(default_factory=list)
    required_completed_task_ids: list[str] = Field(default_factory=list)
    forbidden_failed_task_ids: list[str] = Field(default_factory=list)
    max_missing_task_count: int | None = 0
    max_waiting_task_count: int | None = 0
    max_failed_task_count: int | None = 0
    max_review_conflict_count: int | None = 0
    min_completed_task_count: int | None = None

    @field_validator("expected_status", mode="before")
    @classmethod
    def _parse_expected_status(cls, value: object) -> AgentResultMergeStatus:
        return _parse_merge_status(value)


class _MultiAgentEvaluationCheckPayload(ConfigPayload):
    name: str
    passed: bool
    message: str


class _MultiAgentEvaluationReportPayload(ConfigPayload):
    passed: bool | None = None
    merge_status: AgentResultMergeStatus | None = None
    merge: dict[str, PydanticJsonValue]
    expectations: dict[str, PydanticJsonValue]
    checks: list[_MultiAgentEvaluationCheckPayload] = Field(default_factory=list)

    @field_validator("merge_status", mode="before")
    @classmethod
    def _parse_merge_status(cls, value: object) -> AgentResultMergeStatus | None:
        if value is None:
            return None
        return _parse_merge_status(value)


@dataclass(frozen=True, slots=True)
class MultiAgentEvaluationExpectations:
    expected_status: AgentResultMergeStatus = AgentResultMergeStatus.COMPLETED
    required_evidence_ids: tuple[EvidenceId, ...] = ()
    required_completed_task_ids: tuple[AgentTaskId, ...] = ()
    forbidden_failed_task_ids: tuple[AgentTaskId, ...] = ()
    max_missing_task_count: int | None = 0
    max_waiting_task_count: int | None = 0
    max_failed_task_count: int | None = 0
    max_review_conflict_count: int | None = 0
    min_completed_task_count: int | None = None

    def __post_init__(self) -> None:
        _validate_non_negative("max_missing_task_count", self.max_missing_task_count)
        _validate_non_negative("max_waiting_task_count", self.max_waiting_task_count)
        _validate_non_negative("max_failed_task_count", self.max_failed_task_count)
        _validate_non_negative("max_review_conflict_count", self.max_review_conflict_count)
        _validate_non_negative("min_completed_task_count", self.min_completed_task_count)
        _reject_duplicates("required evidence ids", self.required_evidence_ids)
        _reject_duplicates("required completed task ids", self.required_completed_task_ids)
        _reject_duplicates("forbidden failed task ids", self.forbidden_failed_task_ids)


@dataclass(frozen=True, slots=True)
class MultiAgentEvaluationCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class MultiAgentEvaluationReport:
    merge: AgentResultMerge
    expectations: MultiAgentEvaluationExpectations
    checks: tuple[MultiAgentEvaluationCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_checks(self) -> tuple[MultiAgentEvaluationCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)


class MultiAgentMergeEvaluator:
    """Evaluate a Multi-Agent merge report without re-running child Agents."""

    def evaluate(
        self,
        merge: AgentResultMerge,
        expectations: MultiAgentEvaluationExpectations | None = None,
    ) -> MultiAgentEvaluationReport:
        expectations = expectations or MultiAgentEvaluationExpectations()
        checks = (
            _status_check(merge, expectations),
            _required_evidence_check(merge, expectations.required_evidence_ids),
            _required_completed_tasks_check(merge, expectations.required_completed_task_ids),
            _forbidden_failed_tasks_check(merge, expectations.forbidden_failed_task_ids),
            _max_count_check(
                "missing_task_count",
                len(merge.missing_task_ids),
                expectations.max_missing_task_count,
            ),
            _max_count_check(
                "waiting_task_count",
                len(merge.waiting_task_ids),
                expectations.max_waiting_task_count,
            ),
            _max_count_check(
                "failed_task_count",
                len(merge.failed_task_ids),
                expectations.max_failed_task_count,
            ),
            _max_count_check(
                "review_conflict_count",
                _review_conflict_count(merge),
                expectations.max_review_conflict_count,
            ),
            _min_count_check(
                "completed_task_count",
                len(merge.completed_task_ids),
                expectations.min_completed_task_count,
            ),
        )
        return MultiAgentEvaluationReport(merge, expectations, checks)


def multi_agent_evaluation_report_payload(report: MultiAgentEvaluationReport) -> JsonMapping:
    return MappingProxyType(
        {
            "passed": report.passed,
            "merge_status": report.merge.status.value,
            "failed_checks": [check.name for check in report.failed_checks],
            "merge": dict(agent_result_merge_payload(report.merge)),
            "expectations": dict(multi_agent_evaluation_expectations_payload(report.expectations)),
            "checks": [
                {"name": check.name, "passed": check.passed, "message": check.message}
                for check in report.checks
            ],
        }
    )


def multi_agent_evaluation_expectations_payload(
    expectations: MultiAgentEvaluationExpectations,
) -> JsonMapping:
    return MappingProxyType(
        {
            "expected_status": expectations.expected_status.value,
            "required_evidence_ids": [
                str(evidence_id) for evidence_id in expectations.required_evidence_ids
            ],
            "required_completed_task_ids": [
                str(task_id) for task_id in expectations.required_completed_task_ids
            ],
            "forbidden_failed_task_ids": [
                str(task_id) for task_id in expectations.forbidden_failed_task_ids
            ],
            "max_missing_task_count": expectations.max_missing_task_count,
            "max_waiting_task_count": expectations.max_waiting_task_count,
            "max_failed_task_count": expectations.max_failed_task_count,
            "max_review_conflict_count": expectations.max_review_conflict_count,
            "min_completed_task_count": expectations.min_completed_task_count,
        }
    )


def decode_multi_agent_evaluation_expectations(
    payload: JsonMapping,
) -> MultiAgentEvaluationExpectations:
    parsed = parse_payload(_MultiAgentEvaluationExpectationsPayload, payload)
    return MultiAgentEvaluationExpectations(
        expected_status=parsed.expected_status,
        required_evidence_ids=tuple(EvidenceId(value) for value in parsed.required_evidence_ids),
        required_completed_task_ids=tuple(
            AgentTaskId(value) for value in parsed.required_completed_task_ids
        ),
        forbidden_failed_task_ids=tuple(
            AgentTaskId(value) for value in parsed.forbidden_failed_task_ids
        ),
        max_missing_task_count=parsed.max_missing_task_count,
        max_waiting_task_count=parsed.max_waiting_task_count,
        max_failed_task_count=parsed.max_failed_task_count,
        max_review_conflict_count=parsed.max_review_conflict_count,
        min_completed_task_count=parsed.min_completed_task_count,
    )


def decode_multi_agent_evaluation_report(payload: JsonMapping) -> MultiAgentEvaluationReport:
    parsed = parse_payload(_MultiAgentEvaluationReportPayload, payload)
    report = MultiAgentEvaluationReport(
        merge=decode_agent_result_merge(parsed.merge),
        expectations=decode_multi_agent_evaluation_expectations(parsed.expectations),
        checks=tuple(_decode_evaluation_check(item) for item in parsed.checks),
    )
    if parsed.passed is not None and parsed.passed is not report.passed:
        raise ValueError("multi-agent evaluation passed flag does not match checks")
    if parsed.merge_status is not None and parsed.merge_status is not report.merge.status:
        raise ValueError("multi-agent evaluation merge_status does not match merge")
    return report


def _status_check(
    merge: AgentResultMerge,
    expectations: MultiAgentEvaluationExpectations,
) -> MultiAgentEvaluationCheck:
    passed = merge.status is expectations.expected_status
    return MultiAgentEvaluationCheck(
        "merge_status",
        passed,
        f"expected {expectations.expected_status.value}, observed {merge.status.value}",
    )


def _required_evidence_check(
    merge: AgentResultMerge,
    required_evidence_ids: tuple[EvidenceId, ...],
) -> MultiAgentEvaluationCheck:
    missing = tuple(
        evidence_id
        for evidence_id in required_evidence_ids
        if evidence_id not in merge.evidence_ids
    )
    message = (
        "missing required evidence ids: " + _format_values(missing)
        if missing
        else "all required evidence ids present"
    )
    return MultiAgentEvaluationCheck(
        "required_evidence_ids",
        not missing,
        message,
    )


def _required_completed_tasks_check(
    merge: AgentResultMerge,
    required_task_ids: tuple[AgentTaskId, ...],
) -> MultiAgentEvaluationCheck:
    missing = tuple(
        task_id for task_id in required_task_ids if task_id not in merge.completed_task_ids
    )
    message = (
        "missing completed task ids: " + _format_values(missing)
        if missing
        else "all required tasks completed"
    )
    return MultiAgentEvaluationCheck(
        "required_completed_task_ids",
        not missing,
        message,
    )


def _forbidden_failed_tasks_check(
    merge: AgentResultMerge,
    forbidden_task_ids: tuple[AgentTaskId, ...],
) -> MultiAgentEvaluationCheck:
    present = tuple(task_id for task_id in forbidden_task_ids if task_id in merge.failed_task_ids)
    message = (
        "forbidden failed task ids present: " + _format_values(present)
        if present
        else "no forbidden failed tasks present"
    )
    return MultiAgentEvaluationCheck(
        "forbidden_failed_task_ids",
        not present,
        message,
    )


def _max_count_check(
    name: str,
    observed: int,
    maximum: int | None,
) -> MultiAgentEvaluationCheck:
    if maximum is None:
        return MultiAgentEvaluationCheck(name, True, f"observed {observed}; no maximum configured")
    return MultiAgentEvaluationCheck(
        name,
        observed <= maximum,
        f"observed {observed}, maximum {maximum}",
    )


def _min_count_check(
    name: str,
    observed: int,
    minimum: int | None,
) -> MultiAgentEvaluationCheck:
    if minimum is None:
        return MultiAgentEvaluationCheck(name, True, f"observed {observed}; no minimum configured")
    return MultiAgentEvaluationCheck(
        name,
        observed >= minimum,
        f"observed {observed}, minimum {minimum}",
    )


def _review_conflict_count(merge: AgentResultMerge) -> int:
    return sum(1 for resolution in merge.conflict_resolutions if resolution.requires_review)


def _validate_non_negative(name: str, value: int | None) -> None:
    parse_optional_non_negative_int(
        value,
        name,
        range_template="{path} must be non-negative",
    )


def _reject_duplicates(label: str, values: tuple[object, ...]) -> None:
    duplicates = duplicate_values(values)
    if duplicates:
        raise ValueError(f"duplicate {label}: " + ", ".join(duplicates))


def _format_values(values: tuple[object, ...]) -> str:
    return ", ".join(str(value) for value in values)


def _decode_evaluation_check(
    payload: _MultiAgentEvaluationCheckPayload,
) -> MultiAgentEvaluationCheck:
    return MultiAgentEvaluationCheck(
        name=payload.name,
        passed=payload.passed,
        message=payload.message,
    )


def _parse_merge_status(value: object) -> AgentResultMergeStatus:
    if isinstance(value, AgentResultMergeStatus):
        return value
    if not isinstance(value, str):
        raise ValueError(f"unsupported agent result merge status: {value}")
    try:
        return AgentResultMergeStatus(value)
    except ValueError as exc:
        raise ValueError(f"unsupported agent result merge status: {value}") from exc
