from __future__ import annotations

from universal_agent.agentd.json_values import _object_body
from universal_agent.core import JsonMapping, JsonValue, immutable_json
from universal_agent.runtime import (
    EvaluationView,
    EvidenceView,
    PendingActionView,
    RuntimeEventView,
    SessionSummaryView,
    SessionView,
    TaskView,
)
from universal_agent.service import (
    SessionExplorerView,
    SessionWorldView,
    WorldEntityView,
    WorldFactEvidenceView,
    WorldFactHistoryView,
    WorldFactView,
    WorldNeighborhoodView,
    WorldRelationView,
)


def session_body(view: SessionView) -> JsonMapping:
    return immutable_json(_object_body(view))


def session_explorer_body(view: SessionExplorerView) -> JsonMapping:
    return immutable_json(_object_body(view))


def session_evidence_body(view: SessionExplorerView) -> JsonMapping:
    return immutable_json(
        {
            "session_id": str(view.session.session_id),
            "evidence": [_object_body(item) for item in view.evidence],
        }
    )


def session_world_body(view: SessionWorldView) -> JsonMapping:
    return immutable_json(_object_body(view))


def evidence_body(view: EvidenceView) -> dict[str, JsonValue]:
    return _object_body(view)


def world_fact_body(view: WorldFactView) -> dict[str, JsonValue]:
    return _object_body(view)


def world_fact_evidence_body(view: WorldFactEvidenceView) -> dict[str, JsonValue]:
    return _object_body(view)


def world_fact_history_body(view: WorldFactHistoryView) -> dict[str, JsonValue]:
    return _object_body(view)


def world_entity_body(view: WorldEntityView) -> dict[str, JsonValue]:
    return _object_body(view)


def world_relation_body(view: WorldRelationView) -> dict[str, JsonValue]:
    return _object_body(view)


def world_neighborhood_body(view: WorldNeighborhoodView) -> dict[str, JsonValue]:
    return _object_body(view)


def session_summary_body(view: SessionSummaryView) -> dict[str, JsonValue]:
    return _object_body(view)


def task_body(view: TaskView) -> dict[str, JsonValue]:
    return _object_body(view)


def pending_action_body(view: PendingActionView | None) -> JsonValue:
    if view is None:
        return None
    return _object_body(view)


def evaluation_body(view: EvaluationView | None) -> JsonValue:
    if view is None:
        return None
    return _object_body(view)


def event_body(view: RuntimeEventView) -> dict[str, JsonValue]:
    return _object_body(view)
