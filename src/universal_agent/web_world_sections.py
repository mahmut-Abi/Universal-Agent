from __future__ import annotations

from typing import Any

from universal_agent.service import SessionExplorerView, WorldNeighborhoodView
from universal_agent.web_helpers import _value_text
from universal_agent.web_ui import (
    _empty_paragraph,
    _section,
    _section_blocks,
    _table_from_cells,
    _table_section,
)


def _world_facts(explorer: SessionExplorerView | None) -> str:
    facts = () if explorer is None else explorer.world_facts
    return _table_section(
        "World Facts",
        ("Subject", "Claim", "Value", "Confidence", "Evidence"),
        _world_fact_rows(facts),
        empty_message="No world facts",
    )


def _world_fact_history(explorer: SessionExplorerView | None) -> str:
    histories = () if explorer is None else explorer.world_fact_histories
    return _table_section(
        "World Fact History",
        ("Subject", "Claim", "Current", "Conflicting", "Candidates"),
        (
            (
                history.subject,
                history.claim,
                _value_text(history.current.value),
                "yes" if history.conflicting else "no",
                _fact_history_candidates_text(history.candidates),
            )
            for history in histories
        ),
        empty_message="No world fact history",
    )


def _world_neighborhood(neighborhood: WorldNeighborhoodView | None) -> str:
    if neighborhood is None:
        return _section(
            "Focused World Neighborhood",
            _empty_paragraph("No focused world neighborhood selected"),
        )
    root = (
        _empty_paragraph("No root entity matched the requested focus")
        if neighborhood.root is None
        else _table_from_cells(
            ("Entity", "Kind", "Attributes", "Evidence"),
            (_world_entity_row(neighborhood.root),),
        )
    )
    return _section_blocks(
        "Focused World Neighborhood",
        (
            root,
            _table_from_cells(
                ("Fact Subject", "Claim", "Value", "Confidence", "Evidence"),
                _world_fact_rows(neighborhood.facts),
                empty_message="No focused world facts",
            ),
            _table_from_cells(
                ("Source", "Relation", "Target", "Evidence"),
                _world_relation_rows(
                    neighborhood.outgoing_relations,
                ),
                empty_message="No outgoing focused relations",
            ),
            _table_from_cells(
                ("Source", "Relation", "Target", "Evidence"),
                _world_relation_rows(
                    neighborhood.incoming_relations,
                ),
                empty_message="No incoming focused relations",
            ),
            _table_from_cells(
                ("Related Entity", "Kind", "Attributes", "Evidence"),
                _world_entity_rows(
                    neighborhood.related_entities,
                ),
                empty_message="No related focused entities",
            ),
        ),
    )


def _world_entities(explorer: SessionExplorerView | None) -> str:
    entities = () if explorer is None else explorer.world_entities
    return _table_section(
        "World Entities",
        ("Entity", "Kind", "Attributes", "Evidence"),
        _world_entity_rows(entities),
        empty_message="No world entities",
    )


def _world_relations(explorer: SessionExplorerView | None) -> str:
    relations = () if explorer is None else explorer.world_relations
    return _table_section(
        "World Relations",
        ("Source", "Relation", "Target", "Evidence"),
        _world_relation_rows(relations),
        empty_message="No world relations",
    )


def _evidence(explorer: SessionExplorerView | None) -> str:
    evidence = () if explorer is None else explorer.evidence
    return _table_section(
        "Session Evidence",
        ("Evidence", "Subject", "Claim", "Value", "Source", "Confidence"),
        (
            (
                item.evidence_id,
                item.subject,
                item.claim,
                _value_text(item.value),
                item.source,
                f"{item.confidence:.2f}",
            )
            for item in evidence
        ),
        empty_message="No evidence",
    )


def _world_fact_rows(facts: tuple[Any, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            fact.subject,
            fact.claim,
            _value_text(fact.value),
            f"{fact.confidence:.2f}",
            ", ".join(fact.evidence_ids),
        )
        for fact in facts
    )


def _world_entity_rows(entities: tuple[Any, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(_world_entity_row(entity) for entity in entities)


def _world_entity_row(entity: Any) -> tuple[object, ...]:
    return (
        entity.entity_id,
        entity.kind,
        _value_text(entity.attributes),
        ", ".join(entity.evidence_ids),
    )


def _world_relation_rows(relations: tuple[Any, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            relation.source,
            relation.relation,
            relation.target,
            ", ".join(relation.evidence_ids),
        )
        for relation in relations
    )


def _fact_history_candidates_text(candidates: tuple[Any, ...]) -> str:
    if not candidates:
        return "none"
    return "; ".join(
        (
            f"{candidate.evidence_id}:"
            f"value={_value_text(candidate.value)}"
            f" confidence={candidate.confidence:.2f}"
            f" source={candidate.source}"
        )
        for candidate in candidates
    )
