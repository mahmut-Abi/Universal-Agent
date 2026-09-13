"""Deterministic Diagnosis/Proposal projections for the Kubernetes vertical.

P1 spec §8/§9: a diagnosis must reference evidence (no unsupported claims),
and a remediation proposal must carry action, target, reason, evidence refs,
expected effect, risk and whether confirmation is required. Both projections
are pure functions over collected Evidence so golden scenarios stay
deterministic (spec §18) - no LLM summarization happens here.

Proposal arguments use ``JsonMapping`` (a ``dict[str, JsonValue]``) rather
than the wider ``JsonValue`` so callers can always render them as objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from universal_agent.core import JsonMapping, JsonValue, immutable_json
from universal_agent.evidence import Evidence, EvidenceId
from universal_agent.runtime import EvidenceView

_ROOT_CAUSE_TO_EXPECTED_EFFECT = {
    "crash_loop_back_off": "recreate unhealthy pods with a rolling restart",
    "containers_not_ready": "recreate containers so they restart cleanly",
    "create_container_config_error": "recreate containers with a fresh configuration read",
    "create_container_error": "recreate containers to retry container startup",
    "err_image_pull": "re-pull the image with fresh containers",
    "image_pull_back_off": "re-pull the image with fresh containers",
    "pending": "reschedule pods onto schedulable nodes",
    "under_replicated": "scale the workload back to its desired replica count",
}
_POD_LEVEL_ROOT_CAUSES = frozenset(_ROOT_CAUSE_TO_EXPECTED_EFFECT) - {"under_replicated"}
_MUTATION_RISKS = {"restart_workload": "low", "scale_workload": "medium"}
_PROTECTED_ENVIRONMENTS = frozenset({"production"})


@runtime_checkable
class DiagnosisEvidence(Protocol):
    """Minimal evidence surface for diagnosis building.

    Satisfied by both the kernel ``Evidence`` dataclass and the API
    ``EvidenceView``, so projections work on-store and over the wire.
    Members are read-only properties so frozen dataclasses satisfy the
    protocol under strict type checking.
    """

    @property
    def id(self) -> EvidenceId: ...

    @property
    def subject(self) -> str: ...

    @property
    def claim(self) -> str: ...

    @property
    def value(self) -> JsonValue: ...

    @property
    def source(self) -> str: ...

    @property
    def confidence(self) -> float: ...


@dataclass(frozen=True, slots=True)
class WorkloadDiagnosis:
    """Evidence-referenced diagnosis of a Kubernetes workload (spec §8)."""

    summary: str
    confidence: float
    evidence_refs: tuple[EvidenceId, ...]
    affected_resources: tuple[str, ...]
    root_cause: str | None
    healthy: bool | None


@dataclass(frozen=True, slots=True)
class RemediationProposal:
    """A remediation proposal derived from a diagnosis (spec §9)."""

    action: str
    target: str
    reason: str
    evidence_refs: tuple[EvidenceId, ...]
    expected_effect: str
    risk: str
    arguments: JsonMapping
    requires_confirmation: bool | None


def view_evidence(view: EvidenceView) -> Evidence:
    """Adapt an API EvidenceView onto the kernel Evidence shape."""
    return Evidence(
        view.session_id,
        view.task_id,
        view.action_id,
        view.observation_id,
        view.subject,
        view.claim,
        view.value,
        view.source,
        view.confidence,
        id=view.evidence_id,
        observed_at=view.observed_at,
        domain_name=view.domain_name,
        domain_version=view.domain_version,
    )


def build_diagnosis(
    evidence: tuple[DiagnosisEvidence, ...],
    *,
    goal_description: str,
) -> WorkloadDiagnosis | None:
    """Aggregate collected evidence into an evidence-referenced diagnosis.

    Returns ``None`` when no evidence has been collected, so callers can
    distinguish "nothing investigated yet" from "investigated and healthy".
    """
    if not evidence:
        return None

    supporting: list[DiagnosisEvidence] = []
    root_cause: str | None = None
    root_cause_evidence: DiagnosisEvidence | None = None
    healthy: bool | None = None
    affected: list[str] = []

    for item in evidence:
        if item.claim == "root_cause" and isinstance(item.value, str) and root_cause is None:
            root_cause = item.value.strip() or None
            root_cause_evidence = item
            supporting.append(item)
            if item.subject not in affected:
                affected.append(item.subject)
        elif item.claim == "healthy" and isinstance(item.value, bool) and healthy is None:
            healthy = item.value
            supporting.append(item)
            if item.subject not in affected:
                affected.append(item.subject)

    if root_cause_evidence is None and healthy is None:
        # Nothing workload-health related was observed; do not fabricate a
        # diagnosis from unrelated evidence.
        return None

    refs = tuple(item.id for item in supporting)
    confidence = (
        sum(item.confidence for item in supporting) / len(supporting) if supporting else 0.0
    )
    return WorkloadDiagnosis(
        summary=_summary(goal_description, root_cause, healthy),
        confidence=round(confidence, 4),
        evidence_refs=refs,
        affected_resources=tuple(affected),
        root_cause=root_cause,
        healthy=healthy,
    )


def build_proposal(
    diagnosis: WorkloadDiagnosis | None,
    *,
    action: str,
    target: str,
    environment: str | None,
    arguments: JsonMapping | None = None,  # plain mapping: renders as a JSON object
) -> RemediationProposal | None:
    """Project a diagnosis into a remediation proposal, or ``None`` if none applies.

    ``requires_confirmation`` mirrors the domain mutation policies: production
    mutations always require confirmation, non-production scale/restart are
    allowed. A healthy workload (or a diagnosis without an actionable root
    cause) yields no proposal.

    ``arguments`` carries mutation-specific inputs (for example the target
    replica count for scale_workload); restart_workload needs none.
    """
    if diagnosis is None or (_is_true(diagnosis.healthy) and diagnosis.root_cause is None):
        return None
    if action not in _MUTATION_RISKS:
        return None

    expected_effect = _ROOT_CAUSE_TO_EXPECTED_EFFECT.get(diagnosis.root_cause or "")
    if expected_effect is None:
        return None

    requires_confirmation: bool | None = None
    if environment is not None:
        requires_confirmation = environment in _PROTECTED_ENVIRONMENTS

    return RemediationProposal(
        action=action,
        target=target,
        reason=diagnosis.summary,
        evidence_refs=diagnosis.evidence_refs,
        expected_effect=expected_effect,
        risk=_MUTATION_RISKS[action],
        arguments=immutable_json(arguments if arguments is not None else {}),
        requires_confirmation=requires_confirmation,
    )


def pod_level_root_cause(root_cause: str | None) -> bool:
    """Whether a root cause is a pod-level failure (restartable, not scalable)."""
    return root_cause in _POD_LEVEL_ROOT_CAUSES


def _summary(goal_description: str, root_cause: str | None, healthy: bool | None) -> str:
    workload = goal_description.strip() or "the workload"
    if root_cause is not None:
        return f"{workload}: unhealthy ({root_cause})"
    if _is_false(healthy):
        return f"{workload}: unhealthy (root cause not yet determined)"
    if _is_true(healthy):
        return f"{workload}: healthy"
    return f"{workload}: no health evidence collected"


def _is_true(value: bool | None) -> bool:
    # Strict bool semantics: None (fact absent) is neither True nor False.
    # isinstance avoids both identity-with-literal and == True comparisons.
    return isinstance(value, bool) and value


def _is_false(value: bool | None) -> bool:
    return isinstance(value, bool) and not value
