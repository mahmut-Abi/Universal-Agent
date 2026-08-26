from __future__ import annotations

from universal_agent.agentd.json_values import _json_value
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
    return immutable_json(
        {
            "session_id": str(view.session_id),
            "goal_id": str(view.goal_id),
            "goal_description": view.goal_description,
            "goal_status": view.goal_status.value,
            "current_task_id": str(view.current_task_id),
            "current_task_description": view.current_task_description,
            "current_task_status": view.current_task_status.value,
            "iteration": view.iteration,
            "tasks": [task_body(item) for item in view.tasks],
            "satisfied_criteria": _json_value(view.satisfied_criteria),
            "pending_action": pending_action_body(view.pending_action),
            "latest_evaluation": evaluation_body(view.latest_evaluation),
            "termination_reason": view.termination_reason,
            "error_code": view.error_code.value if view.error_code is not None else None,
            "domain_name": view.domain_name,
            "domain_version": view.domain_version,
        }
    )


def session_explorer_body(view: SessionExplorerView) -> JsonMapping:
    return immutable_json(
        {
            "session": dict(session_body(view.session)),
            "evidence": [evidence_body(item) for item in view.evidence],
            "world_facts": [world_fact_body(item) for item in view.world_facts],
            "world_fact_histories": [
                world_fact_history_body(item) for item in view.world_fact_histories
            ],
            "world_entities": [world_entity_body(item) for item in view.world_entities],
            "world_relations": [world_relation_body(item) for item in view.world_relations],
        }
    )


def session_evidence_body(view: SessionExplorerView) -> JsonMapping:
    return immutable_json(
        {
            "session_id": str(view.session.session_id),
            "evidence": [evidence_body(item) for item in view.evidence],
        }
    )


def session_world_body(view: SessionWorldView) -> JsonMapping:
    return immutable_json(
        {
            "session_id": str(view.session_id),
            "world_facts": [world_fact_body(item) for item in view.world_facts],
            "world_fact_histories": [
                world_fact_history_body(item) for item in view.world_fact_histories
            ],
            "world_entities": [world_entity_body(item) for item in view.world_entities],
            "world_relations": [world_relation_body(item) for item in view.world_relations],
            "neighborhood": (
                None if view.neighborhood is None else world_neighborhood_body(view.neighborhood)
            ),
        }
    )


def evidence_body(view: EvidenceView) -> dict[str, JsonValue]:
    return {
        "evidence_id": str(view.evidence_id),
        "session_id": str(view.session_id),
        "task_id": str(view.task_id),
        "action_id": str(view.action_id),
        "observation_id": str(view.observation_id),
        "subject": view.subject,
        "claim": view.claim,
        "value": _json_value(view.value),
        "source": view.source,
        "confidence": view.confidence,
        "observed_at": view.observed_at.isoformat(),
    }


def world_fact_body(view: WorldFactView) -> dict[str, JsonValue]:
    return {
        "subject": view.subject,
        "claim": view.claim,
        "value": _json_value(view.value),
        "confidence": view.confidence,
        "observed_at": view.observed_at.isoformat(),
        "evidence_ids": list(view.evidence_ids),
    }


def world_fact_evidence_body(view: WorldFactEvidenceView) -> dict[str, JsonValue]:
    return {
        "evidence_id": view.evidence_id,
        "value": _json_value(view.value),
        "confidence": view.confidence,
        "observed_at": view.observed_at.isoformat(),
        "source": view.source,
    }


def world_fact_history_body(view: WorldFactHistoryView) -> dict[str, JsonValue]:
    return {
        "subject": view.subject,
        "claim": view.claim,
        "current": world_fact_body(view.current),
        "candidates": [world_fact_evidence_body(item) for item in view.candidates],
        "conflicting": view.conflicting,
    }


def world_entity_body(view: WorldEntityView) -> dict[str, JsonValue]:
    return {
        "entity_id": view.entity_id,
        "kind": view.kind,
        "attributes": _json_value(view.attributes),
        "evidence_ids": list(view.evidence_ids),
    }


def world_relation_body(view: WorldRelationView) -> dict[str, JsonValue]:
    return {
        "source": view.source,
        "relation": view.relation,
        "target": view.target,
        "evidence_ids": list(view.evidence_ids),
    }


def world_neighborhood_body(view: WorldNeighborhoodView) -> dict[str, JsonValue]:
    return {
        "root": None if view.root is None else world_entity_body(view.root),
        "facts": [world_fact_body(item) for item in view.facts],
        "outgoing_relations": [world_relation_body(item) for item in view.outgoing_relations],
        "incoming_relations": [world_relation_body(item) for item in view.incoming_relations],
        "related_entities": [world_entity_body(item) for item in view.related_entities],
    }


def session_summary_body(view: SessionSummaryView) -> dict[str, JsonValue]:
    return {
        "session_id": str(view.session_id),
        "goal_id": str(view.goal_id),
        "goal_description": view.goal_description,
        "goal_status": view.goal_status.value,
        "current_task_id": str(view.current_task_id),
        "current_task_description": view.current_task_description,
        "current_task_status": view.current_task_status.value,
        "iteration": view.iteration,
        "task_count": view.task_count,
        "pending_action": view.pending_action,
        "termination_reason": view.termination_reason,
        "error_code": view.error_code.value if view.error_code is not None else None,
        "domain_name": view.domain_name,
        "domain_version": view.domain_version,
        "created_at": view.created_at.isoformat(),
    }


def task_body(view: TaskView) -> dict[str, JsonValue]:
    return {
        "task_id": str(view.task_id),
        "description": view.description,
        "status": view.status.value,
        "required_criteria": list(view.required_criteria),
        "depends_on": [str(item) for item in view.depends_on],
    }


def pending_action_body(view: PendingActionView | None) -> JsonValue:
    if view is None:
        return None
    return {
        "action_id": str(view.action_id),
        "capability": view.capability,
        "tool_name": view.tool_name,
        "target": view.target,
        "arguments": _json_value(view.arguments),
        "domain_name": view.domain_name,
        "domain_version": view.domain_version,
        "idempotency_key": view.idempotency_key,
        "parameters_hash": view.parameters_hash,
        "attempt": view.attempt,
        "resource_key": view.resource_key,
        "resource_version": view.resource_version,
    }


def evaluation_body(view: EvaluationView | None) -> JsonValue:
    if view is None:
        return None
    return {
        "status": view.status.value,
        "reason": view.reason,
        "evaluator_name": view.evaluator_name,
        "matched_criteria": _json_value(view.matched_criteria),
        "task_completed": view.task_completed,
        "goal_completed": view.goal_completed,
    }


def event_body(view: RuntimeEventView) -> dict[str, JsonValue]:
    return {
        "event_id": view.event_id,
        "type": view.type,
        "session_id": str(view.session_id),
        "goal_id": str(view.goal_id),
        "task_id": str(view.task_id),
        "action_id": str(view.action_id) if view.action_id is not None else None,
        "data": _json_value(view.data),
        "occurred_at": view.occurred_at.isoformat(),
    }
