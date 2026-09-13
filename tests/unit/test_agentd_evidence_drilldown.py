"""Unit tests for the evidence drill-down projection and console route."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from universal_agent.agentd.console_routes import handle_console_route
from universal_agent.agentd.evidence_drilldown import evidence_drilldown_body
from universal_agent.core import ActionId, ObservationId, SessionId, TaskId
from universal_agent.evidence import EvidenceId
from universal_agent.runtime import EvidenceView
from universal_agent.state import StateNotFoundError

NOW = datetime.now(UTC)
SESSION = SessionId("session-1")


def _obj(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _arr(value: object) -> list[Any]:
    assert isinstance(value, list)
    return value


def _evidence(
    evidence_id: str,
    subject: str,
    domain: str = "",
    version: str = "",
) -> EvidenceView:
    return EvidenceView(
        EvidenceId(evidence_id),
        SESSION,
        TaskId("task-1"),
        ActionId("action-1"),
        ObservationId("observation-1"),
        subject,
        f"{subject} is healthy",
        {"healthy": True},
        "evaluator",
        0.95,
        NOW,
        domain,
        version,
    )


@pytest.mark.unit
def test_drilldown_links_evidence_to_action_and_observation() -> None:
    body = _obj(
        evidence_drilldown_body(
            SESSION,
            (_evidence("ev-1", "deployment/api", "kubernetes", "0.2.0"),),
        )
    )
    assert body["session_id"] == "session-1"
    assert body["evidence_count"] == 1

    record = _obj(_arr(body["evidence"])[0])
    assert record["evidence_id"] == "ev-1"
    assert record["claim"] == "deployment/api is healthy"
    assert record["source"] == "evaluator"
    assert record["action_id"] == "action-1"
    assert record["observation_id"] == "observation-1"
    assert record["task_id"] == "task-1"
    assert record["domain"] == "kubernetes@0.2.0"
    assert record["confidence"] == 0.95


@pytest.mark.unit
def test_drilldown_collects_subject_and_domain_filters() -> None:
    body = _obj(
        evidence_drilldown_body(
            SESSION,
            (
                _evidence("ev-1", "deployment/api", "kubernetes", "0.2.0"),
                _evidence("ev-2", "pod/api-1"),
                _evidence("ev-3", "deployment/api", "observability"),
            ),
        )
    )
    assert body["subjects"] == ["deployment/api", "pod/api-1"]
    assert body["domains"] == ["kubernetes@0.2.0", "observability"]


@pytest.mark.unit
def test_drilldown_empty_evidence_reports_zero() -> None:
    body = _obj(evidence_drilldown_body(SESSION, ()))
    assert body["evidence_count"] == 0
    assert body["subjects"] == []
    assert body["domains"] == []
    assert body["evidence"] == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_console_evidence_drilldown_route_serves_payload() -> None:
    @dataclass
    class _ExplorerStub:
        evidence: tuple[EvidenceView, ...] = (
            _evidence("ev-1", "deployment/api", "kubernetes", "0.2.0"),
        )
        session: Any = None
        world_facts: tuple[Any, ...] = ()
        world_entities: tuple[Any, ...] = ()
        world_relations: tuple[Any, ...] = ()
        world_fact_histories: tuple[Any, ...] = ()

    class StubService:
        async def session_explorer(self, session_id: SessionId) -> _ExplorerStub:
            return _ExplorerStub()

    response = await handle_console_route(
        StubService(),  # type: ignore[arg-type]
        None,
        _StubRequest(),
        "GET",
        "/console/sessions/session-1/evidence-drilldown",
    )
    assert response is not None
    assert response.status_code == 200
    body = _obj(response.body)
    assert body["evidence_count"] == 1
    assert _obj(_arr(body["evidence"])[0])["domain"] == "kubernetes@0.2.0"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_console_evidence_drilldown_route_reports_missing_session() -> None:
    class StubService:
        async def session_explorer(self, session_id: SessionId) -> Any:
            raise StateNotFoundError(f"session not found: {session_id}")

    response = await handle_console_route(
        StubService(),  # type: ignore[arg-type]
        None,
        _StubRequest(),
        "GET",
        "/console/sessions/session-missing/evidence-drilldown",
    )
    assert response is not None
    assert response.status_code == 404


@dataclass
class _StubRequest:
    body: dict[str, Any] = field(default_factory=dict)
