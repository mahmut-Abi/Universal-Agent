"""Unit tests for the world-model explorer projection and console route.

The explorer payload adds operator navigation to the existing world view:
per-entity relation/evidence/domain rollups and the conflicting fact list —
without re-implementing world-model merging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from universal_agent.agentd.console_routes import handle_console_route
from universal_agent.core import ActionId, ObservationId, SessionId, TaskId
from universal_agent.evidence import EvidenceId
from universal_agent.runtime import EvidenceView
from universal_agent.service.views import (
    SessionWorldView,
    WorldEntityView,
    WorldFactEvidenceView,
    WorldFactHistoryView,
    WorldFactView,
    WorldRelationView,
)
from universal_agent.service.world_explorer import world_explorer_body
from universal_agent.state import StateNotFoundError

NOW = datetime.now(UTC)
SESSION = SessionId("session-1")


def _obj(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _arr(value: object) -> list[Any]:
    assert isinstance(value, list)
    return value


def _evidence(evidence_id: str, domain: str, version: str = "") -> EvidenceView:
    return EvidenceView(
        EvidenceId(evidence_id),
        SESSION,
        TaskId("task-1"),
        ActionId("action-1"),
        ObservationId("observation-1"),
        "deployment/api",
        "deployment is scaled",
        {"replicas": 3},
        "world-updater",
        0.9,
        NOW,
        domain,
        version,
    )


def _world_view() -> SessionWorldView:
    return SessionWorldView(
        SESSION,
        (
            WorldFactView("deployment/api", "replicas", 3, 0.9, NOW, ("ev-1",)),
            WorldFactView("deployment/api", "replicas", 5, 0.8, NOW, ("ev-2",)),
        ),
        (
            WorldFactHistoryView(
                "deployment/api",
                "replicas",
                WorldFactView("deployment/api", "replicas", 3, 0.9, NOW, ("ev-1",)),
                (
                    WorldFactEvidenceView("ev-1", 3, 0.9, NOW, "kubernetes"),
                    WorldFactEvidenceView("ev-2", 5, 0.8, NOW, "observability"),
                ),
                True,
            ),
        ),
        (
            WorldEntityView("deployment/api", "Deployment", {"namespace": "prod"}, ("ev-1",)),
            WorldEntityView("pod/api-1", "Pod", {}, ("ev-2",)),
        ),
        (
            WorldRelationView("deployment/api", "scales", "pod/api-1", ("ev-1",)),
            WorldRelationView("pod/api-1", "belongs_to", "deployment/api", ("ev-2",)),
        ),
    )


@pytest.mark.unit
def test_explorer_lists_entity_relations_and_domains() -> None:
    body = _obj(world_explorer_body(_world_view(), (_evidence("ev-1", "kubernetes", "0.2.0"),)))

    assert body["entity_count"] == 2
    assert body["relation_count"] == 2
    entities = {entity["entity_id"]: entity for entity in _arr(body["entities"])}

    deployment = entities["deployment/api"]
    assert deployment["kind"] == "Deployment"
    assert deployment["contributing_domains"] == ["kubernetes@0.2.0"]
    assert len(deployment["outgoing_relations"]) == 1
    assert len(deployment["incoming_relations"]) == 1
    outgoing = _obj(deployment["outgoing_relations"][0])
    assert outgoing["relation"] == "scales"
    assert outgoing["target"] == "pod/api-1"

    pod = entities["pod/api-1"]
    assert pod["contributing_domains"] == []  # ev-2 has no domain attribution


@pytest.mark.unit
def test_explorer_surfaces_conflicting_facts_with_domains() -> None:
    evidence = (
        _evidence("ev-1", "kubernetes", "0.2.0"),
        _evidence("ev-2", "observability"),
    )
    body = _obj(world_explorer_body(_world_view(), evidence))

    assert body["conflict_count"] == 1
    conflict = _obj(_arr(body["conflicts"])[0])
    assert conflict["subject"] == "deployment/api"
    assert conflict["claim"] == "replicas"
    candidates = _arr(conflict["candidates"])
    assert _obj(candidates[0])["domain"] == "kubernetes@0.2.0"
    assert _obj(candidates[1])["domain"] == "observability"
    assert _obj(candidates[1])["value"] == 5


@pytest.mark.unit
def test_explorer_without_conflicts_reports_zero() -> None:
    view = SessionWorldView(SESSION, (), (), (), ())
    body = _obj(world_explorer_body(view, ()))
    assert body["entity_count"] == 0
    assert body["conflict_count"] == 0
    assert body["relation_count"] == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_console_world_explorer_route_serves_payload() -> None:
    @dataclass
    class StubService:
        calls: list[str] = field(default_factory=list)

        async def session_explorer(self, session_id: SessionId) -> Any:
            self.calls.append("explorer")
            return _ExplorerStub()

        async def session_world(self, session_id: SessionId) -> SessionWorldView:
            self.calls.append("world")
            return _world_view()

    class _ExplorerStub:
        session = None
        evidence = (_evidence("ev-1", "kubernetes", "0.2.0"),)
        world_facts: tuple[Any, ...] = ()
        world_entities: tuple[Any, ...] = ()
        world_relations: tuple[Any, ...] = ()
        world_fact_histories: tuple[Any, ...] = ()

    service = StubService()
    response = await handle_console_route(
        service,  # type: ignore[arg-type]
        None,
        _StubRequest(),
        "GET",
        "/console/sessions/session-1/world-explorer",
    )
    assert response is not None
    assert response.status_code == 200
    body = _obj(response.body)
    assert body["entity_count"] == 2
    assert body["conflict_count"] == 1
    assert service.calls == ["explorer", "world"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_console_world_explorer_route_reports_missing_session() -> None:
    class StubService:
        async def session_explorer(self, session_id: SessionId) -> Any:
            raise StateNotFoundError(f"session not found: {session_id}")

    response = await handle_console_route(
        StubService(),  # type: ignore[arg-type]
        None,
        _StubRequest(),
        "GET",
        "/console/sessions/session-missing/world-explorer",
    )
    assert response is not None
    assert response.status_code == 404


@dataclass
class _StubRequest:
    body: dict[str, Any] = field(default_factory=dict)
