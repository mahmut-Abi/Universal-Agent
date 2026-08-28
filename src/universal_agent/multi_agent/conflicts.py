from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import NewType

from pydantic import Field

from universal_agent.core import (
    JsonMapping,
    JsonValue,
    PolicyEffect,
    RiskLevel,
    SideEffect,
    dumps_json,
    immutable_json,
)
from universal_agent.core.config_validation import (
    ConfigPayload,
    parse_payload,
    parse_string,
)
from universal_agent.evidence import EvidenceId
from universal_agent.multi_agent.contracts import AgentTaskConstraints, AgentTaskId
from universal_agent.multi_agent.registry import AgentId

AgentProposalId = NewType("AgentProposalId", str)


class ConflictResolutionStatus(StrEnum):
    NO_CONFLICT = "no_conflict"
    SELECTED = "selected"
    DENIED = "denied"
    REQUIRES_REVIEW = "requires_review"


class _ConflictResolutionPayload(ConfigPayload):
    resource_key: str
    status: str
    selected_proposal_id: str | None = None
    rejected_proposal_ids: list[str] = Field(default_factory=list)
    review_proposal_ids: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AgentActionProposal:
    proposal_id: AgentProposalId
    agent_id: AgentId
    task_id: AgentTaskId
    capability: str
    resource_key: str
    target: str | None = None
    arguments: JsonMapping = field(default_factory=immutable_json)
    evidence_ids: tuple[EvidenceId, ...] = ()
    side_effect: SideEffect = SideEffect.NONE
    risk: RiskLevel = RiskLevel.LOW
    policy_effect: PolicyEffect = PolicyEffect.ALLOW
    constraints: AgentTaskConstraints = field(default_factory=AgentTaskConstraints)
    priority: int = 100
    reason: str = ""

    def __post_init__(self) -> None:
        if not str(self.proposal_id).strip():
            raise ValueError("agent proposal id must not be empty")
        if not str(self.agent_id).strip():
            raise ValueError("agent proposal agent_id must not be empty")
        if not str(self.task_id).strip():
            raise ValueError("agent proposal task_id must not be empty")
        if not self.capability.strip():
            raise ValueError("agent proposal capability must not be empty")
        if not self.resource_key.strip():
            raise ValueError("agent proposal resource_key must not be empty")
        object.__setattr__(self, "arguments", immutable_json(self.arguments))

    @property
    def action_fingerprint(self) -> str:
        return dumps_json(
            {
                "capability": self.capability,
                "target": self.target,
                "arguments": dict(self.arguments),
            },
        )


@dataclass(frozen=True, slots=True)
class ConflictResolution:
    resource_key: str
    status: ConflictResolutionStatus
    selected_proposal_id: AgentProposalId | None = None
    rejected_proposal_ids: tuple[AgentProposalId, ...] = ()
    review_proposal_ids: tuple[AgentProposalId, ...] = ()
    supporting_evidence_ids: tuple[EvidenceId, ...] = ()
    reason: str = ""

    @property
    def requires_review(self) -> bool:
        return self.status is ConflictResolutionStatus.REQUIRES_REVIEW


def conflict_resolution_payload(resolution: ConflictResolution) -> JsonMapping:
    return MappingProxyType(
        {
            "resource_key": resolution.resource_key,
            "status": resolution.status.value,
            "selected_proposal_id": _optional_str(resolution.selected_proposal_id),
            "rejected_proposal_ids": _json_strings(resolution.rejected_proposal_ids),
            "review_proposal_ids": _json_strings(resolution.review_proposal_ids),
            "supporting_evidence_ids": _json_strings(resolution.supporting_evidence_ids),
            "reason": resolution.reason,
        }
    )


def decode_conflict_resolution(payload: JsonMapping) -> ConflictResolution:
    parsed = parse_payload(_ConflictResolutionPayload, payload)
    return ConflictResolution(
        resource_key=parsed.resource_key,
        status=_conflict_resolution_status(parsed.status),
        selected_proposal_id=_optional_agent_proposal_id(parsed.selected_proposal_id),
        rejected_proposal_ids=tuple(
            AgentProposalId(value) for value in parsed.rejected_proposal_ids
        ),
        review_proposal_ids=tuple(AgentProposalId(value) for value in parsed.review_proposal_ids),
        supporting_evidence_ids=tuple(
            EvidenceId(value) for value in parsed.supporting_evidence_ids
        ),
        reason=parsed.reason,
    )


class AgentConflictResolver:
    """Deterministic Multi-Agent proposal resolver.

    The resolver operates on structured proposals and policy outcomes. It does
    not ask a model to adjudicate conflicts and it never executes the selected
    proposal; execution remains behind the target Runtime policy boundary.
    """

    def resolve(self, proposals: tuple[AgentActionProposal, ...]) -> ConflictResolution:
        if not proposals:
            raise ValueError("agent conflict resolution requires proposals")
        resource_key = _shared_resource_key(proposals)
        denied, review, allowed = self._partition(proposals)

        if review:
            return ConflictResolution(
                resource_key=resource_key,
                status=ConflictResolutionStatus.REQUIRES_REVIEW,
                rejected_proposal_ids=tuple(item.proposal_id for item in denied),
                review_proposal_ids=tuple(item.proposal_id for item in review + allowed),
                supporting_evidence_ids=_evidence_ids(review + allowed),
                reason="one or more proposals require confirmation or violate delegation limits",
            )
        if not allowed:
            return ConflictResolution(
                resource_key=resource_key,
                status=ConflictResolutionStatus.DENIED,
                rejected_proposal_ids=tuple(item.proposal_id for item in denied),
                reason="all proposals were denied by policy or delegation constraints",
            )

        if _all_same_action(allowed):
            selected = _highest_priority(allowed)
            return ConflictResolution(
                resource_key=resource_key,
                status=ConflictResolutionStatus.NO_CONFLICT,
                selected_proposal_id=selected.proposal_id,
                rejected_proposal_ids=tuple(item.proposal_id for item in denied),
                supporting_evidence_ids=_evidence_ids(allowed),
                reason="all allowed proposals agree on the same action",
            )

        ranked = sorted(allowed, key=_proposal_rank)
        winner = ranked[0]
        if len(ranked) > 1 and _proposal_rank(winner) == _proposal_rank(ranked[1]):
            return ConflictResolution(
                resource_key=resource_key,
                status=ConflictResolutionStatus.REQUIRES_REVIEW,
                rejected_proposal_ids=tuple(item.proposal_id for item in denied),
                review_proposal_ids=tuple(item.proposal_id for item in allowed),
                supporting_evidence_ids=_evidence_ids(allowed),
                reason="conflicting proposals have equal safety and priority rank",
            )
        return ConflictResolution(
            resource_key=resource_key,
            status=ConflictResolutionStatus.SELECTED,
            selected_proposal_id=winner.proposal_id,
            rejected_proposal_ids=tuple(
                item.proposal_id for item in denied + _without(allowed, winner)
            ),
            supporting_evidence_ids=_evidence_ids((winner,)),
            reason="selected the safest allowed proposal by side effect, risk and priority",
        )

    def resolve_all(
        self,
        proposals: tuple[AgentActionProposal, ...],
    ) -> tuple[ConflictResolution, ...]:
        groups: dict[str, list[AgentActionProposal]] = {}
        for proposal in proposals:
            groups.setdefault(proposal.resource_key, []).append(proposal)
        return tuple(
            self.resolve(tuple(groups[resource_key])) for resource_key in sorted(groups.keys())
        )

    def _partition(
        self,
        proposals: tuple[AgentActionProposal, ...],
    ) -> tuple[list[AgentActionProposal], list[AgentActionProposal], list[AgentActionProposal]]:
        denied: list[AgentActionProposal] = []
        review: list[AgentActionProposal] = []
        allowed: list[AgentActionProposal] = []
        for proposal in proposals:
            if proposal.policy_effect is PolicyEffect.DENY or _violates_read_only(proposal):
                denied.append(proposal)
            elif proposal.policy_effect is PolicyEffect.REQUIRE_CONFIRMATION:
                review.append(proposal)
            else:
                allowed.append(proposal)
        return denied, review, allowed


def _shared_resource_key(proposals: tuple[AgentActionProposal, ...]) -> str:
    resource_key = proposals[0].resource_key
    if any(proposal.resource_key != resource_key for proposal in proposals):
        raise ValueError("agent conflict resolution proposals must share one resource_key")
    return resource_key


def _violates_read_only(proposal: AgentActionProposal) -> bool:
    return proposal.constraints.read_only and proposal.side_effect is not SideEffect.NONE


def _all_same_action(proposals: list[AgentActionProposal]) -> bool:
    first = proposals[0].action_fingerprint
    return all(proposal.action_fingerprint == first for proposal in proposals)


def _proposal_rank(proposal: AgentActionProposal) -> tuple[int, int, int]:
    return (_side_effect_rank(proposal.side_effect), _risk_rank(proposal.risk), -proposal.priority)


def _highest_priority(proposals: list[AgentActionProposal]) -> AgentActionProposal:
    return sorted(proposals, key=lambda item: (-item.priority, str(item.proposal_id)))[0]


def _without(
    proposals: list[AgentActionProposal],
    selected: AgentActionProposal,
) -> list[AgentActionProposal]:
    return [proposal for proposal in proposals if proposal.proposal_id != selected.proposal_id]


def _evidence_ids(
    proposals: tuple[AgentActionProposal, ...] | list[AgentActionProposal],
) -> tuple[EvidenceId, ...]:
    seen: set[EvidenceId] = set()
    selected: list[EvidenceId] = []
    for proposal in proposals:
        for evidence_id in proposal.evidence_ids:
            if evidence_id not in seen:
                seen.add(evidence_id)
                selected.append(evidence_id)
    return tuple(selected)


def _side_effect_rank(side_effect: SideEffect) -> int:
    ranks = {
        SideEffect.NONE: 0,
        SideEffect.REVERSIBLE: 1,
        SideEffect.DESTRUCTIVE: 2,
    }
    return ranks[side_effect]


def _risk_rank(risk: RiskLevel) -> int:
    ranks = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
    }
    return ranks[risk]


def _json_strings(values: tuple[object, ...]) -> list[JsonValue]:
    return [str(value) for value in values]


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_agent_proposal_id(value: str | None) -> AgentProposalId | None:
    if value is None:
        return None
    return AgentProposalId(value)


def _conflict_resolution_status(value: object) -> ConflictResolutionStatus:
    raw = parse_string(value, "status")
    try:
        return ConflictResolutionStatus(raw)
    except ValueError as exc:
        raise ValueError(f"unsupported conflict resolution status: {raw}") from exc
