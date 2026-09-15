"""World-model explorer projection for operator drill-down surfaces.

Builds an entity-focused explorer payload from a session's world view plus
its evidence records, so the web console and TUI can render entity detail
(relations, supporting evidence, contributing domains) and cross-domain fact
conflicts without a second world-model implementation.

Pure projection: consumes ``SessionWorldView``/``EvidenceView`` objects and
returns JSON-safe payloads. No I/O, no state.
"""

from __future__ import annotations

from typing import cast

from universal_agent.core import JsonMapping, to_json_object
from universal_agent.runtime import EvidenceView
from universal_agent.service.views import SessionWorldView

__all__ = ["world_explorer_body"]


def _evidence_domains(evidence: tuple[EvidenceView, ...]) -> dict[str, str]:
    """Map evidence id -> contributing domain label (``name@version``)."""

    domains: dict[str, str] = {}
    for record in evidence:
        if record.domain_name:
            label = (
                f"{record.domain_name}@{record.domain_version}"
                if record.domain_version
                else record.domain_name
            )
            domains[str(record.evidence_id)] = label
    return domains


def _domains_for(evidence_ids: tuple[str, ...], domains: dict[str, str]) -> list[str]:
    labels: list[str] = []
    for evidence_id in evidence_ids:
        label = domains.get(evidence_id)
        if label is not None and label not in labels:
            labels.append(label)
    return labels


def world_explorer_body(
    world: SessionWorldView,
    evidence: tuple[EvidenceView, ...],
) -> JsonMapping:
    """Entity/conflict explorer payload for console/TUI world views."""

    domains = _evidence_domains(evidence)

    outgoing: dict[str, list[JsonMapping]] = {}
    incoming: dict[str, list[JsonMapping]] = {}
    for relation in world.world_relations:
        entry = cast(
            JsonMapping,
            to_json_object(relation, fallback_to_string=True),
        )
        outgoing.setdefault(relation.source, []).append(entry)
        incoming.setdefault(relation.target, []).append(entry)

    entities = [
        cast(
            JsonMapping,
            to_json_object(
                {
                    "entity_id": entity.entity_id,
                    "kind": entity.kind,
                    "attributes": entity.attributes,
                    "evidence_ids": list(entity.evidence_ids),
                    "contributing_domains": _domains_for(entity.evidence_ids, domains),
                    "outgoing_relations": outgoing.get(entity.entity_id, []),
                    "incoming_relations": incoming.get(entity.entity_id, []),
                },
                fallback_to_string=True,
            ),
        )
        for entity in world.world_entities
    ]

    conflicts = [
        cast(
            JsonMapping,
            to_json_object(
                {
                    "subject": history.subject,
                    "claim": history.claim,
                    "current_value": history.current.value,
                    "candidates": [
                        cast(
                            JsonMapping,
                            to_json_object(
                                {
                                    "evidence_id": candidate.evidence_id,
                                    "value": candidate.value,
                                    "source": candidate.source,
                                    "confidence": candidate.confidence,
                                    "domain": domains.get(candidate.evidence_id),
                                },
                                fallback_to_string=True,
                            ),
                        )
                        for candidate in history.candidates
                    ],
                },
                fallback_to_string=True,
            ),
        )
        for history in world.world_fact_histories
        if history.conflicting
    ]

    return cast(
        JsonMapping,
        to_json_object(
            {
                "entity_count": len(entities),
                "relation_count": len(world.world_relations),
                "conflict_count": len(conflicts),
                "entities": entities,
                "relations": [
                    cast(JsonMapping, to_json_object(relation, fallback_to_string=True))
                    for relation in world.world_relations
                ],
                "conflicts": conflicts,
            },
            fallback_to_string=True,
        ),
    )
