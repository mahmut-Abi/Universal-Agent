from __future__ import annotations

from typing import Any

from universal_agent.service import SessionExplorerView, WorldNeighborhoodView
from universal_agent.web_helpers import _value_text
from universal_agent.web_ui import _html, _section, _table


def _world_facts(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = [
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(fact.subject)}</td>",
                    f"<td>{_html(fact.claim)}</td>",
                    f"<td>{_html(_value_text(fact.value))}</td>",
                    f"<td>{fact.confidence:.2f}</td>",
                    f"<td>{_html(', '.join(fact.evidence_ids))}</td>",
                    "</tr>",
                )
            )
            for fact in explorer.world_facts
        ]
    if not rows:
        rows.append('<tr><td colspan="5">No world facts</td></tr>')
    return _section(
        "World Facts",
        _table(("Subject", "Claim", "Value", "Confidence", "Evidence"), tuple(rows)),
    )


def _world_fact_history(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = [
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(history.subject)}</td>",
                    f"<td>{_html(history.claim)}</td>",
                    f"<td>{_html(_value_text(history.current.value))}</td>",
                    f"<td>{'yes' if history.conflicting else 'no'}</td>",
                    f"<td>{_html(_fact_history_candidates_text(history.candidates))}</td>",
                    "</tr>",
                )
            )
            for history in explorer.world_fact_histories
        ]
    if not rows:
        rows.append('<tr><td colspan="5">No world fact history</td></tr>')
    return _section(
        "World Fact History",
        _table(("Subject", "Claim", "Current", "Conflicting", "Candidates"), tuple(rows)),
    )


def _world_neighborhood(neighborhood: WorldNeighborhoodView | None) -> str:
    if neighborhood is None:
        return _section(
            "Focused World Neighborhood",
            '<p class="empty">No focused world neighborhood selected</p>',
        )
    root = (
        '<p class="empty">No root entity matched the requested focus</p>'
        if neighborhood.root is None
        else _table(
            ("Entity", "Kind", "Attributes", "Evidence"),
            (_world_entity_row(neighborhood.root),),
        )
    )
    return _section(
        "Focused World Neighborhood",
        root
        + _table(
            ("Fact Subject", "Claim", "Value", "Confidence", "Evidence"),
            _world_fact_rows(neighborhood.facts, empty="No focused world facts"),
        )
        + _table(
            ("Source", "Relation", "Target", "Evidence"),
            _world_relation_rows(
                neighborhood.outgoing_relations,
                empty="No outgoing focused relations",
            ),
        )
        + _table(
            ("Source", "Relation", "Target", "Evidence"),
            _world_relation_rows(
                neighborhood.incoming_relations,
                empty="No incoming focused relations",
            ),
        )
        + _table(
            ("Related Entity", "Kind", "Attributes", "Evidence"),
            _world_entity_rows(
                neighborhood.related_entities,
                empty="No related focused entities",
            ),
        ),
    )


def _world_entities(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = list(_world_entity_rows(explorer.world_entities, empty=""))
    if not rows:
        rows.append('<tr><td colspan="4">No world entities</td></tr>')
    return _section(
        "World Entities",
        _table(("Entity", "Kind", "Attributes", "Evidence"), tuple(rows)),
    )


def _world_relations(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = list(_world_relation_rows(explorer.world_relations, empty=""))
    if not rows:
        rows.append('<tr><td colspan="4">No world relations</td></tr>')
    return _section(
        "World Relations",
        _table(("Source", "Relation", "Target", "Evidence"), tuple(rows)),
    )


def _evidence(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = [
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(item.evidence_id)}</td>",
                    f"<td>{_html(item.subject)}</td>",
                    f"<td>{_html(item.claim)}</td>",
                    f"<td>{_html(_value_text(item.value))}</td>",
                    f"<td>{_html(item.source)}</td>",
                    f"<td>{item.confidence:.2f}</td>",
                    "</tr>",
                )
            )
            for item in explorer.evidence
        ]
    if not rows:
        rows.append('<tr><td colspan="6">No evidence</td></tr>')
    return _section(
        "Session Evidence",
        _table(("Evidence", "Subject", "Claim", "Value", "Source", "Confidence"), tuple(rows)),
    )


def _world_fact_rows(facts: tuple[Any, ...], *, empty: str) -> tuple[str, ...]:
    rows = tuple(
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(fact.subject)}</td>",
                f"<td>{_html(fact.claim)}</td>",
                f"<td>{_html(_value_text(fact.value))}</td>",
                f"<td>{fact.confidence:.2f}</td>",
                f"<td>{_html(', '.join(fact.evidence_ids))}</td>",
                "</tr>",
            )
        )
        for fact in facts
    )
    if rows or not empty:
        return rows
    return (f'<tr><td colspan="5">{_html(empty)}</td></tr>',)


def _world_entity_rows(entities: tuple[Any, ...], *, empty: str) -> tuple[str, ...]:
    rows = tuple(_world_entity_row(entity) for entity in entities)
    if rows or not empty:
        return rows
    return (f'<tr><td colspan="4">{_html(empty)}</td></tr>',)


def _world_entity_row(entity: Any) -> str:
    return "\n".join(
        (
            "<tr>",
            f"<td>{_html(entity.entity_id)}</td>",
            f"<td>{_html(entity.kind)}</td>",
            f"<td>{_html(_value_text(entity.attributes))}</td>",
            f"<td>{_html(', '.join(entity.evidence_ids))}</td>",
            "</tr>",
        )
    )


def _world_relation_rows(relations: tuple[Any, ...], *, empty: str) -> tuple[str, ...]:
    rows = tuple(
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(relation.source)}</td>",
                f"<td>{_html(relation.relation)}</td>",
                f"<td>{_html(relation.target)}</td>",
                f"<td>{_html(', '.join(relation.evidence_ids))}</td>",
                "</tr>",
            )
        )
        for relation in relations
    )
    if rows or not empty:
        return rows
    return (f'<tr><td colspan="4">{_html(empty)}</td></tr>',)


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
