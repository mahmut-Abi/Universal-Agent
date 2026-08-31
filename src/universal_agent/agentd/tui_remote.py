"""Remote TUI snapshot provider: assemble TuiSnapshot from agentd HTTP projections.

agentd owns the runtime state; the TUI is a thin client. This module maps the
agentd Runtime API JSON projections onto the typed view objects the TUI
dashboard consumes, so ``agent tui --api-url`` renders the same dashboard the
embedded runtime would — without hosting any runtime state locally.

The parsers mirror ``agentd.representations`` (which serializes views via
``core.to_json_object``): enums travel as values, datetimes as RFC 3339
strings, ids as plain strings.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from types import MappingProxyType

from universal_agent.agentd.client import AgentdClient, quote_path_segment
from universal_agent.core import (
    ActionId,
    ErrorCode,
    EvaluationStatus,
    GoalId,
    GoalStatus,
    JsonMapping,
    JsonValue,
    ObservationId,
    SessionId,
    TaskId,
    TaskStatus,
)
from universal_agent.evidence import EvidenceId
from universal_agent.operations import (
    AuditRecordView,
    DoctorCheckView,
    DoctorReportView,
    ModelCostBreakdownView,
    RuntimeCostView,
    RuntimeMetricsView,
)
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
    HealthView,
    ReadyView,
    RuntimeConfigView,
    SessionExplorerView,
    WorldEntityView,
    WorldFactView,
    WorldRelationView,
)
from universal_agent.terminal.tui import TuiSnapshot
from universal_agent.terminal.tui_app import TuiActions

SnapshotProvider = Callable[[SessionId | None], Awaitable[TuiSnapshot]]


def _datetime(value: JsonValue) -> datetime:
    return datetime.fromisoformat(str(value))


def _as_int(value: JsonValue) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"expected an integer projection value, got: {value!r}")
    return value


def _as_float(value: JsonValue) -> float:
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"expected a numeric projection value, got: {value!r}")
        return float(value)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


def _as_str(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise ValueError(f"expected a string projection value, got: {value!r}")
    return value


def _as_mapping(value: JsonValue | None) -> MappingProxyType[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"expected an object projection value, got: {value!r}")
    return MappingProxyType(dict(value))


def _as_dict_list(value: JsonValue | None) -> list[JsonMapping]:
    if not isinstance(value, list):
        return []
    return [MappingProxyType(dict(item)) for item in value if isinstance(item, dict)]


def _as_str_list(value: JsonValue | None) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def _optional_str(body: JsonMapping, key: str) -> str | None:
    value = body.get(key)
    return str(value) if value is not None else None


def _optional_datetime(body: JsonMapping, key: str) -> datetime | None:
    value = body.get(key)
    return _datetime(value) if value is not None else None


def _optional_error_code(body: JsonMapping, key: str) -> ErrorCode | None:
    value = body.get(key)
    return ErrorCode(str(value)) if value is not None else None


def health_from_body(body: JsonMapping) -> HealthView:
    return HealthView(_as_str(body["status"]), _as_str(body["service"]))


def ready_from_body(body: JsonMapping) -> ReadyView:
    return ReadyView(
        bool(body["ready"]),
        _as_str(body["reason"]),
        _as_int(body["domain_count"]),
        _as_int(body["capability_count"]),
        _as_int(body["tool_count"]),
    )


def metrics_from_body(body: JsonMapping) -> RuntimeMetricsView:
    def count(key: str) -> int:
        return _as_int(body.get(key, 0))

    return RuntimeMetricsView(
        session_count=count("session_count"),
        active_session_count=count("active_session_count"),
        waiting_session_count=count("waiting_session_count"),
        completed_goal_count=count("completed_goal_count"),
        failed_goal_count=count("failed_goal_count"),
        cancelled_goal_count=count("cancelled_goal_count"),
        event_count=count("event_count"),
        action_started_count=count("action_started_count"),
        action_completed_count=count("action_completed_count"),
        tool_failure_count=count("tool_failure_count"),
        policy_denial_count=count("policy_denial_count"),
        confirmation_required_count=count("confirmation_required_count"),
        recovery_planned_count=count("recovery_planned_count"),
        recovery_exhausted_count=count("recovery_exhausted_count"),
        human_intervention_count=count("human_intervention_count"),
        resource_lock_acquired_count=count("resource_lock_acquired_count"),
        resource_lock_released_count=count("resource_lock_released_count"),
        resource_conflict_count=count("resource_conflict_count"),
        active_resource_lock_count=count("active_resource_lock_count"),
        decision_generated_count=count("decision_generated_count"),
        decision_validated_count=count("decision_validated_count"),
        decision_rejected_count=count("decision_rejected_count"),
        policy_checked_count=count("policy_checked_count"),
        evaluation_count=count("evaluation_count"),
        evaluation_success_count=count("evaluation_success_count"),
    )


def cost_from_body(body: JsonMapping) -> RuntimeCostView:
    breakdowns = tuple(
        ModelCostBreakdownView(
            provider=_as_str(item["provider"]),
            model=_as_str(item["model"]),
            call_count=_as_int(item["call_count"]),
            input_tokens=_as_int(item["input_tokens"]),
            output_tokens=_as_int(item["output_tokens"]),
            total_tokens=_as_int(item["total_tokens"]),
            estimated_cost_micros=_as_int(item["estimated_cost_micros"]),
            currency=_as_str(item["currency"]),
        )
        for item in _as_dict_list(body.get("by_model"))
    )
    return RuntimeCostView(
        model_call_count=_as_int(body["model_call_count"]),
        input_tokens=_as_int(body["input_tokens"]),
        output_tokens=_as_int(body["output_tokens"]),
        total_tokens=_as_int(body["total_tokens"]),
        estimated_cost_micros=_as_int(body["estimated_cost_micros"]),
        currency=_as_str(body["currency"]),
        by_model=breakdowns,
    )


def doctor_from_body(body: JsonMapping) -> DoctorReportView:
    checks = tuple(
        DoctorCheckView(
            name=_as_str(item["name"]),
            status=_as_str(item["status"]),
            message=_as_str(item["message"]),
        )
        for item in _as_dict_list(body.get("checks"))
    )
    return DoctorReportView(status=_as_str(body["status"]), checks=checks)


def session_summary_from_body(body: JsonMapping) -> SessionSummaryView:
    return SessionSummaryView(
        session_id=SessionId(_as_str(body["session_id"])),
        goal_id=GoalId(_as_str(body["goal_id"])),
        goal_description=_as_str(body["goal_description"]),
        goal_status=GoalStatus(_as_str(body["goal_status"])),
        current_task_id=TaskId(_as_str(body["current_task_id"])),
        current_task_description=_as_str(body["current_task_description"]),
        current_task_status=TaskStatus(_as_str(body["current_task_status"])),
        iteration=_as_int(body["iteration"]),
        task_count=_as_int(body["task_count"]),
        pending_action=bool(body.get("pending_action", False)),
        termination_reason=_optional_str(body, "termination_reason"),
        error_code=_optional_error_code(body, "error_code"),
        domain_name=_as_str(body["domain_name"]),
        domain_version=_as_str(body["domain_version"]),
        created_at=_datetime(body["created_at"]),
    )


def _task_from_body(body: JsonMapping) -> TaskView:
    return TaskView(
        task_id=TaskId(_as_str(body["task_id"])),
        description=_as_str(body["description"]),
        status=TaskStatus(_as_str(body["status"])),
        required_criteria=_as_str_list(body.get("required_criteria")),
        depends_on=tuple(TaskId(item) for item in _as_str_list(body.get("depends_on"))),
    )


def _pending_action_from_body(body: JsonMapping) -> PendingActionView:
    return PendingActionView(
        action_id=ActionId(_as_str(body["action_id"])),
        capability=_as_str(body["capability"]),
        tool_name=_as_str(body["tool_name"]),
        target=_optional_str(body, "target"),
        arguments=_as_mapping(body.get("arguments", {})),
        domain_name=_as_str(body["domain_name"]),
        domain_version=_as_str(body["domain_version"]),
        idempotency_key=_as_str(body["idempotency_key"]),
        parameters_hash=_as_str(body["parameters_hash"]),
        attempt=_as_int(body["attempt"]),
        resource_key=_as_str(body["resource_key"]),
        resource_version=_optional_str(body, "resource_version"),
    )


def _evaluation_from_body(body: JsonMapping) -> EvaluationView:
    return EvaluationView(
        status=EvaluationStatus(_as_str(body["status"])),
        reason=_as_str(body["reason"]),
        evaluator_name=_as_str(body["evaluator_name"]),
        matched_criteria=_as_mapping(body.get("matched_criteria", {})),
        task_completed=bool(body["task_completed"]),
        goal_completed=bool(body["goal_completed"]),
    )


def session_view_from_body(body: JsonMapping) -> SessionView:
    return SessionView(
        session_id=SessionId(_as_str(body["session_id"])),
        goal_id=GoalId(_as_str(body["goal_id"])),
        goal_description=_as_str(body["goal_description"]),
        goal_status=GoalStatus(_as_str(body["goal_status"])),
        current_task_id=TaskId(_as_str(body["current_task_id"])),
        current_task_description=_as_str(body["current_task_description"]),
        current_task_status=TaskStatus(_as_str(body["current_task_status"])),
        iteration=_as_int(body["iteration"]),
        tasks=tuple(_task_from_body(item) for item in _as_dict_list(body.get("tasks"))),
        satisfied_criteria=_as_mapping(body.get("satisfied_criteria", {})),
        pending_action=(
            _pending_action_from_body(item)
            if (item := body.get("pending_action")) is not None and isinstance(item, dict)
            else None
        ),
        latest_evaluation=(
            _evaluation_from_body(item)
            if (item := body.get("latest_evaluation")) is not None and isinstance(item, dict)
            else None
        ),
        termination_reason=_optional_str(body, "termination_reason"),
        error_code=_optional_error_code(body, "error_code"),
        domain_name=_as_str(body["domain_name"]),
        domain_version=_as_str(body["domain_version"]),
    )


def _event_from_body(body: JsonMapping) -> RuntimeEventView:
    return RuntimeEventView(
        event_id=_as_str(body["event_id"]),
        type=_as_str(body["type"]),
        session_id=SessionId(_as_str(body["session_id"])),
        goal_id=GoalId(_as_str(body["goal_id"])),
        task_id=TaskId(_as_str(body["task_id"])),
        action_id=ActionId(_as_str(body["action_id"])) if body.get("action_id") else None,
        data=_as_mapping(body.get("data", {})),
        occurred_at=_datetime(body["occurred_at"]),
    )


def _audit_record_from_body(body: JsonMapping) -> AuditRecordView:
    return AuditRecordView(
        record_id=_as_str(body["record_id"]),
        session_id=SessionId(_as_str(body["session_id"])),
        goal_id=GoalId(_as_str(body["goal_id"])),
        task_id=TaskId(_as_str(body["task_id"])),
        action_id=ActionId(_as_str(body["action_id"])) if body.get("action_id") else None,
        capability=_as_str(body["capability"]),
        tool_name=_as_str(body["tool_name"]),
        side_effect=_as_str(body["side_effect"]),
        risk=_as_str(body["risk"]),
        policy_effect=_as_str(body["policy_effect"]),
        policy_name=_as_str(body["policy_name"]),
        status=_as_str(body["status"]),
        occurred_at=_datetime(body["occurred_at"]),
        completed_at=_optional_datetime(body, "completed_at"),
        error_code=_optional_error_code(body, "error_code"),
    )


def _evidence_from_body(body: JsonMapping) -> EvidenceView:
    return EvidenceView(
        evidence_id=EvidenceId(_as_str(body["evidence_id"])),
        session_id=SessionId(_as_str(body["session_id"])),
        task_id=TaskId(_as_str(body["task_id"])),
        action_id=ActionId(_as_str(body["action_id"])),
        observation_id=ObservationId(_as_str(body["observation_id"])),
        subject=_as_str(body["subject"]),
        claim=_as_str(body["claim"]),
        value=body.get("value"),
        source=_as_str(body["source"]),
        confidence=_as_float(body.get("confidence", 1.0)),
        observed_at=_datetime(body["observed_at"]),
        domain_name=_as_str(body.get("domain_name", "")),
        domain_version=_as_str(body.get("domain_version", "")),
    )


def _world_fact_from_body(body: JsonMapping) -> WorldFactView:
    return WorldFactView(
        subject=_as_str(body["subject"]),
        claim=_as_str(body["claim"]),
        value=body.get("value"),
        confidence=_as_float(body.get("confidence", 1.0)),
        observed_at=_datetime(body["observed_at"]),
        evidence_ids=_as_str_list(body.get("evidence_ids")),
    )


def _world_entity_from_body(body: JsonMapping) -> WorldEntityView:
    return WorldEntityView(
        entity_id=_as_str(body["entity_id"]),
        kind=_as_str(body["kind"]),
        attributes=_as_mapping(body.get("attributes", {})),
        evidence_ids=_as_str_list(body.get("evidence_ids")),
    )


def _world_relation_from_body(body: JsonMapping) -> WorldRelationView:
    return WorldRelationView(
        source=_as_str(body["source"]),
        relation=_as_str(body["relation"]),
        target=_as_str(body["target"]),
        evidence_ids=_as_str_list(body.get("evidence_ids")),
    )


def _remote_config() -> RuntimeConfigView:
    return RuntimeConfigView(
        environment=MappingProxyType({"runtime": "remote-agentd"}),
        domain_package_paths=(),
        store_backend="remote",
        store_path=None,
        distributed_queue_backend="remote",
        distributed_queue_path=None,
        distributed_locks_backend="remote",
        distributed_locks_path=None,
        distributed_workers_backend="remote",
        distributed_workers_path=None,
        max_iterations=0,
        max_recovery_steps=0,
        domains=(),
        secrets=(),
    )


def agentd_tui_actions(client: AgentdClient) -> TuiActions:
    """Operator actions backed by agentd Runtime API POST endpoints."""

    async def pause(session_id: SessionId, reason: str | None) -> object:
        body: dict[str, JsonValue] = {"reason": reason} if reason else {}
        return await client.post_json(
            f"/v1/sessions/{quote_path_segment(str(session_id))}/pause",
            body=body,
        )

    async def resume(session_id: SessionId, confirmed: bool | None) -> object:
        body: dict[str, JsonValue] = {"confirmed": confirmed} if confirmed is not None else {}
        return await client.post_json(
            f"/v1/sessions/{quote_path_segment(str(session_id))}/resume",
            body=body,
        )

    async def cancel(session_id: SessionId, reason: str | None) -> object:
        body: dict[str, JsonValue] = {"reason": reason} if reason else {}
        return await client.post_json(
            f"/v1/sessions/{quote_path_segment(str(session_id))}/cancel",
            body=body,
        )

    async def chat(goal_text: str) -> JsonMapping:
        return await client.post_json(
            "/v1/sessions",
            body={
                "goal": {"description": goal_text},
                "task": {"description": "Chat turn"},
            },
        )

    return TuiActions(pause=pause, resume=resume, cancel=cancel, chat=chat)


def agentd_snapshot_provider(
    client: AgentdClient,
    *,
    session_limit: int = 5,
    event_limit: int = 12,
) -> Callable[[SessionId | None], Awaitable[TuiSnapshot]]:
    """Build a TUI ``snapshot_provider`` backed by an agentd Runtime API client.

    The provider fetches only the projections the live dashboard renders; the
    runtime state stays behind agentd, so ``agent tui --api-url`` works against
    a remote (e.g. containerized) agentd without local state.
    """

    async def provider(session_id: SessionId | None) -> TuiSnapshot:
        health_b, ready_b, metrics_b, cost_b, doctor_b, sessions_b = await asyncio.gather(
            client.get_json("/health"),
            client.get_json("/ready"),
            client.get_json("/v1/metrics"),
            client.get_json("/v1/cost"),
            client.get_json("/v1/doctor"),
            client.get_json("/v1/sessions", query={"limit": session_limit}),
        )
        sessions = tuple(
            session_summary_from_body(item) for item in _as_dict_list(sessions_b.get("sessions"))
        )

        selected_id = session_id
        if selected_id is None and sessions:
            selected_id = sessions[0].session_id

        selected_session: SessionView | None = None
        explorer: SessionExplorerView | None = None
        events: tuple[RuntimeEventView, ...] = ()
        audit_records: tuple[AuditRecordView, ...] = ()

        if selected_id is not None:
            quoted = quote_path_segment(str(selected_id))
            detail_b, evidence_b, world_b, events_b, audit_b = await asyncio.gather(
                client.get_json(f"/v1/sessions/{quoted}"),
                client.get_json(f"/v1/sessions/{quoted}/evidence"),
                client.get_json(f"/v1/sessions/{quoted}/world"),
                client.get_json(f"/v1/sessions/{quoted}/events", query={"limit": event_limit}),
                client.get_json(f"/v1/sessions/{quoted}/audit"),
            )
            selected_session = session_view_from_body(detail_b)
            explorer = SessionExplorerView(
                session=selected_session,
                evidence=tuple(
                    _evidence_from_body(item) for item in _as_dict_list(evidence_b.get("evidence"))
                ),
                world_facts=tuple(
                    _world_fact_from_body(item)
                    for item in _as_dict_list(world_b.get("world_facts"))
                ),
                world_entities=tuple(
                    _world_entity_from_body(item)
                    for item in _as_dict_list(world_b.get("world_entities"))
                ),
                world_relations=tuple(
                    _world_relation_from_body(item)
                    for item in _as_dict_list(world_b.get("world_relations"))
                ),
            )
            events = tuple(_event_from_body(item) for item in _as_dict_list(events_b.get("events")))
            audit_records = tuple(
                _audit_record_from_body(item)
                for item in _as_dict_list(audit_b.get("audit_records"))
            )

        return TuiSnapshot(
            health=health_from_body(health_b),
            ready=ready_from_body(ready_b),
            config=_remote_config(),
            domains=(),
            domain_packages=(),
            profiles=(),
            capabilities=(),
            tools=(),
            policies=(),
            evaluators=(),
            memories=(),
            metrics=metrics_from_body(metrics_b),
            cost=cost_from_body(cost_b),
            doctor=doctor_from_body(doctor_b),
            distributed_snapshot=None,
            distributed_health=None,
            sessions=sessions,
            selected_session=selected_session,
            session_explorer=explorer,
            events=events,
            audit_records=audit_records,
        )

    return provider


__all__ = ["agentd_snapshot_provider", "agentd_tui_actions"]
