from __future__ import annotations

from typing import cast

import pytest

from universal_agent.core import ErrorCode
from universal_agent.evidence import EvidenceId
from universal_agent.multi_agent import (
    AgentProposalId,
    AgentResultMergePolicy,
    AgentResultMerger,
    AgentResultMergeStatus,
    AgentTaskId,
    AgentTaskResult,
    AgentTaskResultStatus,
    ConflictResolution,
    ConflictResolutionStatus,
    MultiAgentEvaluationExpectations,
    MultiAgentMergeEvaluator,
    decode_multi_agent_evaluation_expectations,
    decode_multi_agent_evaluation_report,
    multi_agent_evaluation_expectations_payload,
    multi_agent_evaluation_report_payload,
)


def result(
    task_id: str,
    status: AgentTaskResultStatus = AgentTaskResultStatus.COMPLETED,
    *,
    evidence_ids: tuple[EvidenceId, ...] = (EvidenceId("evidence-1"),),
) -> AgentTaskResult:
    return AgentTaskResult(
        AgentTaskId(task_id),
        status,
        result={"task": task_id},
        evidence_ids=evidence_ids,
        reason="settled",
        error_code=None if status is AgentTaskResultStatus.COMPLETED else ErrorCode.TOOL_FAILURE,
    )


def test_multi_agent_evaluator_passes_completed_merge_expectations() -> None:
    merge = AgentResultMerger().merge(
        (
            result("agent-task-a", evidence_ids=(EvidenceId("evidence-a"),)),
            result("agent-task-b", evidence_ids=(EvidenceId("evidence-b"),)),
        ),
        policy=AgentResultMergePolicy(
            expected_task_ids=(AgentTaskId("agent-task-a"), AgentTaskId("agent-task-b"))
        ),
    )
    report = MultiAgentMergeEvaluator().evaluate(
        merge,
        MultiAgentEvaluationExpectations(
            required_evidence_ids=(EvidenceId("evidence-a"), EvidenceId("evidence-b")),
            required_completed_task_ids=(AgentTaskId("agent-task-a"), AgentTaskId("agent-task-b")),
            min_completed_task_count=2,
        ),
    )

    assert report.passed
    assert report.failed_checks == ()


def test_multi_agent_evaluator_reports_missing_evidence_and_tasks() -> None:
    merge = AgentResultMerger().merge(
        (result("agent-task-a", evidence_ids=(EvidenceId("evidence-a"),)),),
        policy=AgentResultMergePolicy(
            expected_task_ids=(AgentTaskId("agent-task-a"), AgentTaskId("agent-task-b"))
        ),
    )

    report = MultiAgentMergeEvaluator().evaluate(
        merge,
        MultiAgentEvaluationExpectations(
            expected_status=AgentResultMergeStatus.WAITING,
            required_evidence_ids=(EvidenceId("evidence-b"),),
            required_completed_task_ids=(AgentTaskId("agent-task-b"),),
            max_missing_task_count=0,
        ),
    )

    failed = {check.name for check in report.failed_checks}
    assert failed == {"required_evidence_ids", "required_completed_task_ids", "missing_task_count"}


def test_multi_agent_evaluator_detects_failed_waiting_and_review_counts() -> None:
    conflict = ConflictResolution(
        resource_key="deployment/example",
        status=ConflictResolutionStatus.REQUIRES_REVIEW,
        review_proposal_ids=(AgentProposalId("proposal-a"),),
    )
    merge = AgentResultMerger().merge(
        (
            result("agent-task-a"),
            result("agent-task-b", AgentTaskResultStatus.FAILED),
            result("agent-task-c", AgentTaskResultStatus.WAITING),
        ),
        conflict_resolutions=(conflict,),
        policy=AgentResultMergePolicy(
            require_all_completed=False,
            allow_waiting=True,
            require_resolved_conflicts=False,
        ),
    )

    report = MultiAgentMergeEvaluator().evaluate(
        merge,
        MultiAgentEvaluationExpectations(
            expected_status=AgentResultMergeStatus.PARTIAL,
            forbidden_failed_task_ids=(AgentTaskId("agent-task-b"),),
            max_waiting_task_count=0,
            max_failed_task_count=0,
            max_review_conflict_count=0,
        ),
    )

    failed = {check.name for check in report.failed_checks}
    assert failed == {
        "forbidden_failed_task_ids",
        "waiting_task_count",
        "failed_task_count",
        "review_conflict_count",
    }


def test_multi_agent_evaluator_allows_relaxed_partial_expectations() -> None:
    merge = AgentResultMerger().merge(
        (
            result("agent-task-a"),
            result("agent-task-b", AgentTaskResultStatus.FAILED),
        ),
        policy=AgentResultMergePolicy(require_all_completed=False),
    )

    report = MultiAgentMergeEvaluator().evaluate(
        merge,
        MultiAgentEvaluationExpectations(
            expected_status=AgentResultMergeStatus.PARTIAL,
            max_failed_task_count=1,
            min_completed_task_count=1,
        ),
    )

    assert report.passed


def test_multi_agent_evaluation_expectations_reject_invalid_thresholds_and_duplicates() -> None:
    with pytest.raises(ValueError, match="max_failed_task_count must be non-negative"):
        MultiAgentEvaluationExpectations(max_failed_task_count=-1)

    with pytest.raises(ValueError, match="duplicate required evidence ids"):
        MultiAgentEvaluationExpectations(
            required_evidence_ids=(EvidenceId("evidence-a"), EvidenceId("evidence-a"))
        )


def test_multi_agent_evaluation_payload_is_json_safe() -> None:
    merge = AgentResultMerger().merge((result("agent-task-a"),))
    report = MultiAgentMergeEvaluator().evaluate(merge)

    payload = multi_agent_evaluation_report_payload(report)

    assert payload["passed"] is True
    assert payload["merge_status"] == "completed"
    checks = cast(list[dict[str, object]], payload["checks"])
    assert checks[0]["name"] == "merge_status"


def test_multi_agent_evaluation_payload_round_trips_report_and_expectations() -> None:
    merge = AgentResultMerger().merge(
        (result("agent-task-a", evidence_ids=(EvidenceId("evidence-a"),)),)
    )
    expectations = MultiAgentEvaluationExpectations(
        required_evidence_ids=(EvidenceId("evidence-a"),),
        required_completed_task_ids=(AgentTaskId("agent-task-a"),),
        min_completed_task_count=1,
    )
    report = MultiAgentMergeEvaluator().evaluate(merge, expectations)

    decoded_expectations = decode_multi_agent_evaluation_expectations(
        multi_agent_evaluation_expectations_payload(expectations)
    )
    decoded_report = decode_multi_agent_evaluation_report(
        multi_agent_evaluation_report_payload(report)
    )

    assert decoded_expectations.required_evidence_ids == (EvidenceId("evidence-a"),)
    assert decoded_expectations.min_completed_task_count == 1
    assert decoded_report.passed
    assert decoded_report.expectations.required_completed_task_ids == (AgentTaskId("agent-task-a"),)
    assert decoded_report.merge.evidence_ids == (EvidenceId("evidence-a"),)
