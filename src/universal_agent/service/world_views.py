"""World-view projection helpers for RuntimeService.

Extracted from ``service.runtime`` so session/distributed/memory/profile areas
stay separate from world-model projection logic. Public RuntimeService methods
are unchanged; these are the internal seams they delegate to.
"""

from __future__ import annotations

from universal_agent.core import SessionId
from universal_agent.domain import RuntimeComponents
from universal_agent.runtime import EvidenceView
from universal_agent.service.projections import (
    build_world_snapshot,
    world_neighborhood_view,
    world_projection_views_from_snapshot,
)
from universal_agent.service.views import (
    WorldEntityView,
    WorldFactHistoryView,
    WorldFactView,
    WorldNeighborhoodView,
    WorldRelationView,
)
from universal_agent.world import WorldSnapshot


def world_snapshot(
    components: RuntimeComponents,
    session_id: SessionId,
    evidence: tuple[EvidenceView, ...],
) -> WorldSnapshot:
    return build_world_snapshot(components, session_id, evidence)


def world_projection_views(
    components: RuntimeComponents,
    session_id: SessionId,
    evidence: tuple[EvidenceView, ...],
) -> tuple[
    tuple[WorldFactView, ...],
    tuple[WorldFactHistoryView, ...],
    tuple[WorldEntityView, ...],
    tuple[WorldRelationView, ...],
]:
    return world_projection_views_from_snapshot(
        world_snapshot(components, session_id, evidence)
    )


def world_neighborhood(
    snapshot: WorldSnapshot,
    entity_id: str,
    relation: str | None,
) -> WorldNeighborhoodView | None:
    return world_neighborhood_view(snapshot.neighborhood_for(entity_id, relation=relation))


__all__ = ["world_neighborhood", "world_projection_views", "world_snapshot"]
