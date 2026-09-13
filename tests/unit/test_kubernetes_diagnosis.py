"""Tests for the deterministic Diagnosis/Proposal projections (P1 spec §8/§9)."""

from __future__ import annotations

import pytest

from universal_agent.core import (
    ActionId,
    JsonValue,
    ObservationId,
    SessionId,
    TaskId,
)
from universal_agent.domains.kubernetes.diagnosis import (
    build_diagnosis,
    build_proposal,
)
from universal_agent.evidence import Evidence, EvidenceId


def make_evidence(
    *,
    subject: str,
    claim: str,
    value: JsonValue,
    source: str,
    sequence: int,
    confidence: float = 1.0,
) -> Evidence:
    return Evidence(
        SessionId("session-dx"),
        TaskId("task-1"),
        ActionId(f"action-{sequence}"),
        ObservationId(f"observation-{sequence}"),
        subject,
        claim,
        value,
        source,
        confidence,
        id=EvidenceId(f"ev-{sequence}"),
    )


def test_diagnosis_aggregates_root_cause_and_health_with_evidence_refs() -> None:
    evidence = (
        make_evidence(
            subject="deployment/checkout",
            claim="healthy",
            value=False,
            source="inspect_workload:kubernetes_inspect_workload",
            sequence=1,
        ),
        make_evidence(
            subject="deployment/checkout",
            claim="root_cause",
            value="crash_loop_back_off",
            source="inspect_logs:kubernetes_inspect_logs",
            sequence=2,
            confidence=0.8,
        ),
        make_evidence(
            subject="pod/checkout-abc",
            claim="restart_count",
            value=7,
            source="inspect_pod:kubernetes_inspect_pod",
            sequence=3,
        ),
    )

    diagnosis = build_diagnosis(evidence, goal_description="fix checkout")

    assert diagnosis is not None
    assert diagnosis.root_cause == "crash_loop_back_off"
    assert diagnosis.healthy is False
    assert "deployment/checkout" in diagnosis.affected_resources
    # Confidence covers exactly the evidence the refs point at (1.0 + 0.8)/2.
    assert diagnosis.confidence == pytest.approx(0.9)
    # Refs point only at the evidence that directly establishes the diagnosis
    # (healthy + root_cause); corroborating details stay in the evidence view.
    assert {str(ref) for ref in diagnosis.evidence_refs} == {"ev-1", "ev-2"}
    assert "checkout" in diagnosis.summary


def test_diagnosis_is_none_without_evidence() -> None:
    assert build_diagnosis((), goal_description="fix checkout") is None


def test_diagnosis_without_root_cause_still_reports_health() -> None:
    evidence = (
        make_evidence(
            subject="deployment/checkout",
            claim="healthy",
            value=True,
            source="inspect_workload:kubernetes_inspect_workload",
            sequence=1,
        ),
    )

    diagnosis = build_diagnosis(evidence, goal_description="fix checkout")

    assert diagnosis is not None
    assert diagnosis.healthy is True
    assert diagnosis.root_cause is None


def test_proposal_maps_pod_root_cause_to_restart() -> None:
    evidence = (
        make_evidence(
            subject="deployment/checkout",
            claim="healthy",
            value=False,
            source="inspect_workload:kubernetes_inspect_workload",
            sequence=1,
        ),
        make_evidence(
            subject="deployment/checkout",
            claim="root_cause",
            value="crash_loop_back_off",
            source="inspect_logs:kubernetes_inspect_logs",
            sequence=2,
        ),
    )
    diagnosis = build_diagnosis(evidence, goal_description="fix checkout")
    assert diagnosis is not None

    proposal = build_proposal(
        diagnosis,
        action="restart_workload",
        target="deployment/checkout",
        environment="production",
    )

    assert proposal is not None
    assert proposal.action == "restart_workload"
    assert proposal.target == "deployment/checkout"
    assert proposal.risk == "low"
    assert proposal.requires_confirmation is True
    assert set(proposal.evidence_refs) <= {str(r) for r in diagnosis.evidence_refs}


def test_proposal_maps_under_replicated_to_scale() -> None:
    evidence = (
        make_evidence(
            subject="deployment/checkout",
            claim="root_cause",
            value="under_replicated",
            source="inspect_workload:kubernetes_inspect_workload",
            sequence=1,
        ),
    )
    diagnosis = build_diagnosis(evidence, goal_description="fix checkout")

    proposal = build_proposal(
        diagnosis,
        action="scale_workload",
        target="deployment/checkout",
        environment="staging",
        arguments={"replicas": 3},
    )

    assert proposal is not None
    assert proposal.action == "scale_workload"
    assert proposal.risk == "medium"
    assert proposal.requires_confirmation is False


def test_proposal_requires_confirmation_in_production_for_scale() -> None:
    evidence = (
        make_evidence(
            subject="deployment/checkout",
            claim="root_cause",
            value="under_replicated",
            source="inspect_workload:kubernetes_inspect_workload",
            sequence=1,
        ),
    )
    diagnosis = build_diagnosis(evidence, goal_description="fix checkout")

    proposal = build_proposal(
        diagnosis,
        action="scale_workload",
        target="deployment/checkout",
        environment="production",
        arguments={"replicas": 3},
    )

    assert proposal is not None
    assert proposal.requires_confirmation is True


def test_proposal_none_for_healthy_workload() -> None:
    evidence = (
        make_evidence(
            subject="deployment/checkout",
            claim="healthy",
            value=True,
            source="inspect_workload:kubernetes_inspect_workload",
            sequence=1,
        ),
    )
    diagnosis = build_diagnosis(evidence, goal_description="fix checkout")

    assert (
        build_proposal(
            diagnosis,
            action="restart_workload",
            target="deployment/checkout",
            environment="production",
        )
        is None
    )


def test_proposal_includes_goal_alignment_note_from_evidence() -> None:
    evidence = (
        make_evidence(
            subject="deployment/checkout",
            claim="root_cause",
            value="err_image_pull",
            source="inspect_logs:kubernetes_inspect_logs",
            sequence=1,
        ),
    )
    diagnosis = build_diagnosis(evidence, goal_description="fix checkout")

    proposal = build_proposal(
        diagnosis,
        action="restart_workload",
        target="deployment/checkout",
        environment="staging",
    )

    assert proposal is not None
    assert proposal.expected_effect


def test_diagnosis_ignores_goal_without_healthy_root_cause_context() -> None:
    evidence = (
        make_evidence(
            subject="config/map",
            claim="unrelated",
            value="value",
            source="inspect_cluster:kubernetes_inspect_cluster",
            sequence=1,
        ),
    )

    # Unrelated evidence must not fabricate a diagnosis.
    assert build_diagnosis(evidence, goal_description="totally unrelated goal") is None
