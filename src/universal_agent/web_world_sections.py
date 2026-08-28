from __future__ import annotations

from typing import Any

from universal_agent.service import SessionExplorerView, WorldNeighborhoodView
from universal_agent.web_helpers import _value_text
from universal_agent.web_ui import (
    _empty_paragraph,
    _empty_table_row,
    _section,
    _section_blocks,
    _table,
    _table_row,
)


def _world_facts(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = [
            _table_row(
                (
                    fact.subject,
                    fact.claim,
                    _value_text(fact.value),
                    f"{fact.confidence:.2f}",
                    ", ".join(fact.evidence_ids),
                )
            )
            for fact in explorer.world_facts
        ]
    if not rows:
        rows.append(_empty_table_row("No world facts", colspan=5))
    return _section(
        "World Facts",
        _table(("Subject", "Claim", "Value", "Confidence", "Evidence"), tuple(rows)),
    )


def _world_fact_history(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = [
            _table_row(
                (
                    history.subject,
                    history.claim,
                    _value_text(history.current.value),
                    "yes" if history.conflicting else "no",
                    _fact_history_candidates_text(history.candidates),
                )
            )
            for history in explorer.world_fact_histories
        ]
    if not rows:
        rows.append(_empty_table_row("No world fact history", colspan=5))
    return _section(
        "World Fact History",
        _table(("Subject", "Claim", "Current", "Conflicting", "Candidates"), tuple(rows)),
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
        else _table(
            ("Entity", "Kind", "Attributes", "Evidence"),
            (_world_entity_row(neighborhood.root),),
        )
    )
    return _section_blocks(
        "Focused World Neighborhood",
        (
            root,
            _table(
                ("Fact Subject", "Claim", "Value", "Confidence", "Evidence"),
                _world_fact_rows(neighborhood.facts, empty="No focused world facts"),
            ),
            _table(
                ("Source", "Relation", "Target", "Evidence"),
                _world_relation_rows(
                    neighborhood.outgoing_relations,
                    empty="No outgoing focused relations",
                ),
            ),
            _table(
                ("Source", "Relation", "Target", "Evidence"),
                _world_relation_rows(
                    neighborhood.incoming_relations,
                    empty="No incoming focused relations",
                ),
            ),
            _table(
                ("Related Entity", "Kind", "Attributes", "Evidence"),
                _world_entity_rows(
                    neighborhood.related_entities,
                    empty="No related focused entities",
                ),
            ),
        ),
    )


def _world_entities(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = list(_world_entity_rows(explorer.world_entities, empty=""))
    if not rows:
        rows.append(_empty_table_row("No world entities", colspan=4))
    return _section(
        "World Entities",
        _table(("Entity", "Kind", "Attributes", "Evidence"), tuple(rows)),
    )


def _world_relations(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = list(_world_relation_rows(explorer.world_relations, empty=""))
    if not rows:
        rows.append(_empty_table_row("No world relations", colspan=4))
    return _section(
        "World Relations",
        _table(("Source", "Relation", "Target", "Evidence"), tuple(rows)),
    )


def _evidence(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = [
            _table_row(
                (
                    item.evidence_id,
                    item.subject,
                    item.claim,
                    _value_text(item.value),
                    item.source,
                    f"{item.confidence:.2f}",
                )
            )
            for item in explorer.evidence
        ]
    if not rows:
        rows.append(_empty_table_row("No evidence", colspan=6))
    return _section(
        "Session Evidence",
        _table(("Evidence", "Subject", "Claim", "Value", "Source", "Confidence"), tuple(rows)),
    )


def _world_fact_rows(facts: tuple[Any, ...], *, empty: str) -> tuple[str, ...]:
    rows = tuple(
        _table_row(
            (
                fact.subject,
                fact.claim,
                _value_text(fact.value),
                f"{fact.confidence:.2f}",
                ", ".join(fact.evidence_ids),
            )
        )
        for fact in facts
    )
    if rows or not empty:
        return rows
    return (_empty_table_row(empty, colspan=5),)


def _world_entity_rows(entities: tuple[Any, ...], *, empty: str) -> tuple[str, ...]:
    rows = tuple(_world_entity_row(entity) for entity in entities)
    if rows or not empty:
        return rows
    return (_empty_table_row(empty, colspan=4),)


def _world_entity_row(entity: Any) -> str:
    return _table_row(
        (
            entity.entity_id,
            entity.kind,
            _value_text(entity.attributes),
            ", ".join(entity.evidence_ids),
        )
    )


def _world_relation_rows(relations: tuple[Any, ...], *, empty: str) -> tuple[str, ...]:
    rows = tuple(
        _table_row(
            (
                relation.source,
                relation.relation,
                relation.target,
                ", ".join(relation.evidence_ids),
            )
        )
        for relation in relations
    )
    if rows or not empty:
        return rows
    return (_empty_table_row(empty, colspan=4),)


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
