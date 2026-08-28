from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from pydantic import Field

from universal_agent.core import JsonMapping
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    duplicate_values,
    parse_payload,
    parse_string,
)
from universal_agent.evidence import EvidenceId
from universal_agent.multi_agent.conflicts import (
    ConflictResolution,
    ConflictResolutionStatus,
    conflict_resolution_payload,
    decode_conflict_resolution,
)
from universal_agent.multi_agent.contracts import (
    AgentTaskId,
    AgentTaskResult,
    AgentTaskResultStatus,
    agent_task_result_payload,
    decode_agent_task_result,
)


class AgentResultMergeStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    WAITING = "waiting"
    REQUIRES_REVIEW = "requires_review"


class _AgentResultMergePayload(ConfigPayload):
    status: str
    passed: bool | None = None
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)
    completed_task_ids: list[str] = Field(default_factory=list)
    waiting_task_ids: list[str] = Field(default_factory=list)
    failed_task_ids: list[str] = Field(default_factory=list)
    missing_task_ids: list[str] = Field(default_factory=list)
    missing_evidence_task_ids: list[str] = Field(default_factory=list)
    results: list[dict[str, PydanticJsonValue]] = Field(default_factory=list)
    conflict_resolutions: list[dict[str, PydanticJsonValue]] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AgentResultMergePolicy:
    expected_task_ids: tuple[AgentTaskId, ...] = ()
    require_all_completed: bool = True
    require_evidence_for_completed: bool = True
    require_resolved_conflicts: bool = True
    fail_on_denied_conflicts: bool = True
    allow_waiting: bool = False

    def __post_init__(self) -> None:
        duplicates = _duplicate_task_ids(self.expected_task_ids)
        if duplicates:
            raise ValueError("duplicate expected agent task ids: " + ", ".join(duplicates))


@dataclass(frozen=True, slots=True)
class AgentResultMerge:
    status: AgentResultMergeStatus
    results: tuple[AgentTaskResult, ...]
    evidence_ids: tuple[EvidenceId, ...]
    completed_task_ids: tuple[AgentTaskId, ...] = ()
    waiting_task_ids: tuple[AgentTaskId, ...] = ()
    failed_task_ids: tuple[AgentTaskId, ...] = ()
    missing_task_ids: tuple[AgentTaskId, ...] = ()
    missing_evidence_task_ids: tuple[AgentTaskId, ...] = ()
    conflict_resolutions: tuple[ConflictResolution, ...] = ()
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.status is AgentResultMergeStatus.COMPLETED

    @property
    def requires_review(self) -> bool:
        return self.status is AgentResultMergeStatus.REQUIRES_REVIEW


class AgentResultMerger:
    """Merge child Agent results without treating result payloads as truth.

    The merge report carries status, child task identity and Evidence references.
    It does not create new Evidence and it does not update the World Model; those
    remain evaluator/runtime responsibilities.
    """

    def merge(
        self,
        results: tuple[AgentTaskResult, ...],
        *,
        conflict_resolutions: tuple[ConflictResolution, ...] = (),
        policy: AgentResultMergePolicy | None = None,
    ) -> AgentResultMerge:
        if not results:
            raise ValueError("agent result merge requires results")
        policy = policy or AgentResultMergePolicy()
        duplicates = _duplicate_task_ids(tuple(result.task_id for result in results))
        if duplicates:
            raise ValueError("duplicate agent task results: " + ", ".join(duplicates))

        completed = _task_ids_with_status(results, AgentTaskResultStatus.COMPLETED)
        waiting = _task_ids_with_status(results, AgentTaskResultStatus.WAITING)
        failed = tuple(
            result.task_id
            for result in results
            if result.status
            in {
                AgentTaskResultStatus.FAILED,
                AgentTaskResultStatus.CANCELLED,
                AgentTaskResultStatus.REJECTED,
            }
        )
        missing = _missing_task_ids(policy.expected_task_ids, results)
        missing_evidence = tuple(
            result.task_id
            for result in results
            if result.status is AgentTaskResultStatus.COMPLETED and not result.evidence_ids
        )
        evidence_ids = _merged_evidence_ids(results, conflict_resolutions)
        status, reason = _merge_status(
            results,
            conflict_resolutions,
            policy,
            missing=missing,
            waiting=waiting,
            failed=failed,
            missing_evidence=missing_evidence,
        )
        return AgentResultMerge(
            status=status,
            results=results,
            evidence_ids=evidence_ids,
            completed_task_ids=completed,
            waiting_task_ids=waiting,
            failed_task_ids=failed,
            missing_task_ids=missing,
            missing_evidence_task_ids=missing_evidence,
            conflict_resolutions=conflict_resolutions,
            reason=reason,
        )


def agent_result_merge_payload(merge: AgentResultMerge) -> JsonMapping:
    return MappingProxyType(
        {
            "status": merge.status.value,
            "passed": merge.passed,
            "reason": merge.reason,
            "evidence": [str(evidence_id) for evidence_id in merge.evidence_ids],
            "completed_task_ids": [str(task_id) for task_id in merge.completed_task_ids],
            "waiting_task_ids": [str(task_id) for task_id in merge.waiting_task_ids],
            "failed_task_ids": [str(task_id) for task_id in merge.failed_task_ids],
            "missing_task_ids": [str(task_id) for task_id in merge.missing_task_ids],
            "missing_evidence_task_ids": [
                str(task_id) for task_id in merge.missing_evidence_task_ids
            ],
            "results": [dict(agent_task_result_payload(result)) for result in merge.results],
            "conflict_resolutions": [
                dict(conflict_resolution_payload(resolution))
                for resolution in merge.conflict_resolutions
            ],
        }
    )


def decode_agent_result_merge(payload: JsonMapping) -> AgentResultMerge:
    parsed = _parse_result_merge_payload(payload)
    merge = AgentResultMerge(
        status=_merge_status_value(parsed.status),
        results=tuple(decode_agent_task_result(item) for item in parsed.results),
        evidence_ids=tuple(EvidenceId(value) for value in parsed.evidence),
        completed_task_ids=_agent_task_ids(parsed.completed_task_ids),
        waiting_task_ids=_agent_task_ids(parsed.waiting_task_ids),
        failed_task_ids=_agent_task_ids(parsed.failed_task_ids),
        missing_task_ids=_agent_task_ids(parsed.missing_task_ids),
        missing_evidence_task_ids=_agent_task_ids(parsed.missing_evidence_task_ids),
        conflict_resolutions=tuple(
            decode_conflict_resolution(item) for item in parsed.conflict_resolutions
        ),
        reason=parsed.reason,
    )
    if parsed.passed is not None and parsed.passed is not merge.passed:
        raise ValueError("agent result merge passed flag does not match status")
    return merge


def _parse_result_merge_payload(payload: JsonMapping) -> _AgentResultMergePayload:
    return parse_payload(_AgentResultMergePayload, payload)


def _merge_status(
    results: tuple[AgentTaskResult, ...],
    conflict_resolutions: tuple[ConflictResolution, ...],
    policy: AgentResultMergePolicy,
    *,
    missing: tuple[AgentTaskId, ...],
    waiting: tuple[AgentTaskId, ...],
    failed: tuple[AgentTaskId, ...],
    missing_evidence: tuple[AgentTaskId, ...],
) -> tuple[AgentResultMergeStatus, str]:
    review_conflicts = tuple(
        resolution for resolution in conflict_resolutions if resolution.requires_review
    )
    denied_conflicts = tuple(
        resolution
        for resolution in conflict_resolutions
        if resolution.status is ConflictResolutionStatus.DENIED
    )
    if policy.require_resolved_conflicts and review_conflicts:
        return (
            AgentResultMergeStatus.REQUIRES_REVIEW,
            "one or more multi-agent conflicts require review",
        )
    if policy.fail_on_denied_conflicts and denied_conflicts:
        return AgentResultMergeStatus.FAILED, "one or more multi-agent conflicts were denied"
    if missing:
        return AgentResultMergeStatus.WAITING, "expected child agent results are missing"
    if waiting and not policy.allow_waiting:
        return AgentResultMergeStatus.WAITING, "one or more child agent results are waiting"
    if failed and policy.require_all_completed:
        return AgentResultMergeStatus.FAILED, "one or more child agent results failed"
    if policy.require_evidence_for_completed and missing_evidence:
        return (
            AgentResultMergeStatus.PARTIAL,
            "one or more completed child agent results lack evidence handoff",
        )
    if failed:
        return AgentResultMergeStatus.PARTIAL, "merged completed child results with failures"
    if waiting:
        return AgentResultMergeStatus.PARTIAL, "merged completed child results with waiting results"
    if all(result.status is AgentTaskResultStatus.COMPLETED for result in results):
        return AgentResultMergeStatus.COMPLETED, "all child agent results completed"
    return AgentResultMergeStatus.PARTIAL, "child agent results are partially mergeable"


def _task_ids_with_status(
    results: tuple[AgentTaskResult, ...],
    status: AgentTaskResultStatus,
) -> tuple[AgentTaskId, ...]:
    return tuple(result.task_id for result in results if result.status is status)


def _missing_task_ids(
    expected: tuple[AgentTaskId, ...],
    results: tuple[AgentTaskResult, ...],
) -> tuple[AgentTaskId, ...]:
    observed = {result.task_id for result in results}
    return tuple(task_id for task_id in expected if task_id not in observed)


def _merged_evidence_ids(
    results: tuple[AgentTaskResult, ...],
    conflict_resolutions: tuple[ConflictResolution, ...],
) -> tuple[EvidenceId, ...]:
    selected: list[EvidenceId] = []
    seen: set[EvidenceId] = set()
    for evidence_id in _result_evidence_ids(results) + _conflict_evidence_ids(conflict_resolutions):
        if evidence_id not in seen:
            seen.add(evidence_id)
            selected.append(evidence_id)
    return tuple(selected)


def _result_evidence_ids(results: tuple[AgentTaskResult, ...]) -> tuple[EvidenceId, ...]:
    return tuple(evidence_id for result in results for evidence_id in result.evidence_ids)


def _conflict_evidence_ids(
    conflict_resolutions: tuple[ConflictResolution, ...],
) -> tuple[EvidenceId, ...]:
    return tuple(
        evidence_id
        for resolution in conflict_resolutions
        for evidence_id in resolution.supporting_evidence_ids
    )


def _duplicate_task_ids(task_ids: tuple[AgentTaskId, ...]) -> tuple[str, ...]:
    return duplicate_values(task_ids)


def _agent_task_ids(values: list[str]) -> tuple[AgentTaskId, ...]:
    return tuple(AgentTaskId(item) for item in values)


def _merge_status_value(value: object) -> AgentResultMergeStatus:
    raw = parse_string(value, "status")
    try:
        return AgentResultMergeStatus(raw)
    except ValueError as exc:
        raise ValueError(f"unsupported agent result merge status: {raw}") from exc
