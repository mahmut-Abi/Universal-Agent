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
    agent_result_merge_payload,
)


def result(
    task_id: str,
    status: AgentTaskResultStatus = AgentTaskResultStatus.COMPLETED,
    *,
    evidence_ids: tuple[EvidenceId, ...] = (EvidenceId("evidence-1"),),
    reason: str = "child result settled",
) -> AgentTaskResult:
    return AgentTaskResult(
        AgentTaskId(task_id),
        status,
        result={"task": task_id},
        evidence_ids=evidence_ids,
        reason=reason,
        error_code=None if status is AgentTaskResultStatus.COMPLETED else ErrorCode.TOOL_FAILURE,
    )


def test_result_merger_completes_with_all_results_and_evidence() -> None:
    merge = AgentResultMerger().merge(
        (
            result("agent-task-a", evidence_ids=(EvidenceId("evidence-1"),)),
            result(
                "agent-task-b",
                evidence_ids=(EvidenceId("evidence-1"), EvidenceId("evidence-2")),
            ),
        ),
        policy=AgentResultMergePolicy(
            expected_task_ids=(AgentTaskId("agent-task-a"), AgentTaskId("agent-task-b"))
        ),
    )

    assert merge.status is AgentResultMergeStatus.COMPLETED
    assert merge.passed
    assert merge.completed_task_ids == (AgentTaskId("agent-task-a"), AgentTaskId("agent-task-b"))
    assert merge.evidence_ids == (EvidenceId("evidence-1"), EvidenceId("evidence-2"))


def test_result_merger_waits_for_missing_expected_results() -> None:
    merge = AgentResultMerger().merge(
        (result("agent-task-a"),),
        policy=AgentResultMergePolicy(
            expected_task_ids=(AgentTaskId("agent-task-a"), AgentTaskId("agent-task-b"))
        ),
    )

    assert merge.status is AgentResultMergeStatus.WAITING
    assert merge.missing_task_ids == (AgentTaskId("agent-task-b"),)


def test_result_merger_fails_when_required_child_result_failed() -> None:
    merge = AgentResultMerger().merge(
        (
            result("agent-task-a"),
            result("agent-task-b", AgentTaskResultStatus.FAILED, reason="tool failed"),
        )
    )

    assert merge.status is AgentResultMergeStatus.FAILED
    assert merge.failed_task_ids == (AgentTaskId("agent-task-b"),)


def test_result_merger_can_merge_partial_when_not_all_children_required() -> None:
    merge = AgentResultMerger().merge(
        (
            result("agent-task-a"),
            result("agent-task-b", AgentTaskResultStatus.FAILED, reason="tool failed"),
        ),
        policy=AgentResultMergePolicy(require_all_completed=False),
    )

    assert merge.status is AgentResultMergeStatus.PARTIAL
    assert merge.completed_task_ids == (AgentTaskId("agent-task-a"),)
    assert merge.failed_task_ids == (AgentTaskId("agent-task-b"),)


def test_result_merger_marks_completed_result_without_evidence_as_partial() -> None:
    merge = AgentResultMerger().merge((result("agent-task-a", evidence_ids=()),))

    assert merge.status is AgentResultMergeStatus.PARTIAL
    assert merge.missing_evidence_task_ids == (AgentTaskId("agent-task-a"),)


def test_result_merger_requires_review_for_unresolved_conflicts() -> None:
    conflict = ConflictResolution(
        resource_key="deployment/example",
        status=ConflictResolutionStatus.REQUIRES_REVIEW,
        review_proposal_ids=(AgentProposalId("proposal-a"), AgentProposalId("proposal-b")),
        supporting_evidence_ids=(EvidenceId("evidence-conflict"),),
    )

    merge = AgentResultMerger().merge((result("agent-task-a"),), conflict_resolutions=(conflict,))

    assert merge.status is AgentResultMergeStatus.REQUIRES_REVIEW
    assert merge.requires_review
    assert merge.evidence_ids == (EvidenceId("evidence-1"), EvidenceId("evidence-conflict"))


def test_result_merger_rejects_duplicate_results_and_expected_ids() -> None:
    with pytest.raises(ValueError, match="duplicate agent task results"):
        AgentResultMerger().merge((result("agent-task-a"), result("agent-task-a")))

    with pytest.raises(ValueError, match="duplicate expected agent task ids"):
        AgentResultMergePolicy(
            expected_task_ids=(AgentTaskId("agent-task-a"), AgentTaskId("agent-task-a"))
        )


def test_result_merge_payload_is_json_safe() -> None:
    conflict = ConflictResolution(
        resource_key="deployment/example",
        status=ConflictResolutionStatus.SELECTED,
        selected_proposal_id=AgentProposalId("proposal-a"),
    )
    merge = AgentResultMerger().merge((result("agent-task-a"),), conflict_resolutions=(conflict,))

    payload = agent_result_merge_payload(merge)

    assert payload["status"] == "completed"
    assert payload["passed"] is True
    assert payload["evidence"] == ["evidence-1"]
    results = cast(list[dict[str, object]], payload["results"])
    conflicts = cast(list[dict[str, object]], payload["conflict_resolutions"])
    assert results[0]["task_id"] == "agent-task-a"
    assert conflicts[0]["selected_proposal_id"] == "proposal-a"
