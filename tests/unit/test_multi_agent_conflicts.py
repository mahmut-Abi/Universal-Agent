from __future__ import annotations

import pytest

from universal_agent.core import PolicyEffect, RiskLevel, SideEffect, immutable_json
from universal_agent.evidence import EvidenceId
from universal_agent.multi_agent import (
    AgentActionProposal,
    AgentConflictResolver,
    AgentId,
    AgentProposalId,
    AgentTaskConstraints,
    AgentTaskId,
    ConflictResolutionStatus,
    conflict_resolution_payload,
    decode_conflict_resolution,
)


def proposal(
    proposal_id: str,
    *,
    capability: str = "scale_workload",
    replicas: int = 3,
    side_effect: SideEffect = SideEffect.REVERSIBLE,
    risk: RiskLevel = RiskLevel.MEDIUM,
    policy_effect: PolicyEffect = PolicyEffect.ALLOW,
    priority: int = 100,
    constraints: AgentTaskConstraints | None = None,
    evidence_ids: tuple[EvidenceId, ...] = (),
) -> AgentActionProposal:
    return AgentActionProposal(
        proposal_id=AgentProposalId(proposal_id),
        agent_id=AgentId("agent-" + proposal_id),
        task_id=AgentTaskId("task-" + proposal_id),
        capability=capability,
        resource_key="deployment/example",
        target="deployment/example",
        arguments=immutable_json({"replicas": replicas}),
        side_effect=side_effect,
        risk=risk,
        policy_effect=policy_effect,
        priority=priority,
        constraints=constraints or AgentTaskConstraints(),
        evidence_ids=evidence_ids,
    )


@pytest.mark.unit
def test_conflict_resolver_reports_no_conflict_when_actions_match() -> None:
    resolver = AgentConflictResolver()

    result = resolver.resolve(
        (
            proposal("a", evidence_ids=(EvidenceId("evidence-1"),)),
            proposal("b", evidence_ids=(EvidenceId("evidence-1"), EvidenceId("evidence-2"))),
        )
    )

    assert result.status is ConflictResolutionStatus.NO_CONFLICT
    assert result.selected_proposal_id == AgentProposalId("a")
    assert result.supporting_evidence_ids == (
        EvidenceId("evidence-1"),
        EvidenceId("evidence-2"),
    )


@pytest.mark.contract
def test_conflict_resolution_payload_round_trips() -> None:
    result = AgentConflictResolver().resolve(
        (
            proposal("a", evidence_ids=(EvidenceId("evidence-1"),)),
            proposal("b", replicas=4, evidence_ids=(EvidenceId("evidence-2"),)),
        )
    )

    decoded = decode_conflict_resolution(conflict_resolution_payload(result))

    assert decoded.resource_key == "deployment/example"
    assert decoded.status is ConflictResolutionStatus.REQUIRES_REVIEW
    assert decoded.review_proposal_ids == (AgentProposalId("a"), AgentProposalId("b"))
    assert decoded.supporting_evidence_ids == (
        EvidenceId("evidence-1"),
        EvidenceId("evidence-2"),
    )


@pytest.mark.contract
def test_conflict_resolution_decoder_rejects_invalid_pydantic_payload_shape() -> None:
    with pytest.raises(ValueError, match=r"rejected_proposal_ids\[0\] must be a string"):
        decode_conflict_resolution(
            immutable_json(
                {
                    "resource_key": "deployment/example",
                    "status": "selected",
                    "rejected_proposal_ids": [1],
                }
            )
        )


@pytest.mark.unit
def test_conflict_resolver_denies_read_only_mutation() -> None:
    resolver = AgentConflictResolver()

    result = resolver.resolve(
        (
            proposal(
                "a",
                constraints=AgentTaskConstraints(read_only=True),
                side_effect=SideEffect.REVERSIBLE,
            ),
        )
    )

    assert result.status is ConflictResolutionStatus.DENIED
    assert result.rejected_proposal_ids == (AgentProposalId("a"),)


@pytest.mark.unit
def test_conflict_resolver_selects_safer_conflicting_action() -> None:
    resolver = AgentConflictResolver()

    result = resolver.resolve(
        (
            proposal("restart", capability="restart_workload", side_effect=SideEffect.DESTRUCTIVE),
            proposal("scale", capability="scale_workload", side_effect=SideEffect.REVERSIBLE),
        )
    )

    assert result.status is ConflictResolutionStatus.SELECTED
    assert result.selected_proposal_id == AgentProposalId("scale")
    assert result.rejected_proposal_ids == (AgentProposalId("restart"),)


@pytest.mark.unit
def test_conflict_resolver_uses_priority_after_safety_rank() -> None:
    resolver = AgentConflictResolver()

    result = resolver.resolve(
        (
            proposal("a", replicas=2, priority=10),
            proposal("b", replicas=3, priority=50),
        )
    )

    assert result.status is ConflictResolutionStatus.SELECTED
    assert result.selected_proposal_id == AgentProposalId("b")


@pytest.mark.contract
def test_conflict_resolver_requires_review_for_equal_rank_conflicts() -> None:
    resolver = AgentConflictResolver()

    result = resolver.resolve((proposal("a", replicas=2), proposal("b", replicas=3)))

    assert result.status is ConflictResolutionStatus.REQUIRES_REVIEW
    assert result.review_proposal_ids == (AgentProposalId("a"), AgentProposalId("b"))


@pytest.mark.contract
def test_conflict_resolver_requires_review_for_confirmation_policy() -> None:
    resolver = AgentConflictResolver()

    result = resolver.resolve(
        (
            proposal(
                "a",
                policy_effect=PolicyEffect.REQUIRE_CONFIRMATION,
                evidence_ids=(EvidenceId("evidence-1"),),
            ),
            proposal("b", replicas=4),
        )
    )

    assert result.requires_review
    assert result.review_proposal_ids == (AgentProposalId("a"), AgentProposalId("b"))
    assert result.supporting_evidence_ids == (EvidenceId("evidence-1"),)


@pytest.mark.unit
def test_conflict_resolver_resolves_each_resource_group_in_stable_order() -> None:
    resolver = AgentConflictResolver()
    first = proposal("a")
    second = AgentActionProposal(
        proposal_id=AgentProposalId("b"),
        agent_id=AgentId("agent-b"),
        task_id=AgentTaskId("task-b"),
        capability="inspect_workload",
        resource_key="deployment/alpha",
        target="deployment/alpha",
    )

    results = resolver.resolve_all((first, second))

    assert [result.resource_key for result in results] == ["deployment/alpha", "deployment/example"]


@pytest.mark.unit
def test_conflict_resolver_rejects_mixed_resource_group() -> None:
    resolver = AgentConflictResolver()
    other = AgentActionProposal(
        proposal_id=AgentProposalId("b"),
        agent_id=AgentId("agent-b"),
        task_id=AgentTaskId("task-b"),
        capability="inspect_workload",
        resource_key="deployment/other",
    )

    with pytest.raises(ValueError, match="share one resource_key"):
        resolver.resolve((proposal("a"), other))
