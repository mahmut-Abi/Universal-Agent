from __future__ import annotations

from universal_agent.agentd.http import (
    HttpRequest,
    HttpResponse,
    bad_request,
    json_response,
    method_not_allowed,
    not_found,
    parse_goal_submission,
)
from universal_agent.agentd.representations import (
    _stream_events_for_sse,
    audit_integrity_body,
    audit_records_body,
    cost_body,
    event_batch_body,
    log_records_body,
    runtime_run_body,
    runtime_run_status_code,
    session_batch_body,
    sse_event_batch_response,
    trace_spans_body,
)
from universal_agent.agentd.routing import (
    AgentdRouteDefinition,
    AgentdRouteMatcher,
    _optional_event_cursor,
    _optional_positive_int_query,
    _optional_query_value,
    _optional_session_cursor,
    _session_message_payload,
    _session_reason_payload,
    _session_resume_payload,
)
from universal_agent.agentd.session_representations import (
    session_body,
    session_evidence_body,
    session_explorer_body,
    session_world_body,
)
from universal_agent.core import JsonValue, SessionId, immutable_json
from universal_agent.operations.llm_calls import llm_calls_body
from universal_agent.runtime import RuntimeSessionBatch
from universal_agent.service import RuntimeService
from universal_agent.state import StateNotFoundError

_SESSION_ROUTE_DEFINITIONS = (
    AgentdRouteDefinition("sessions", "/v1/sessions", ("GET", "POST")),
    AgentdRouteDefinition("session", "/v1/sessions/{session_id}"),
    AgentdRouteDefinition("session_diagnostics", "/v1/sessions/{session_id}/diagnostics"),
    AgentdRouteDefinition("session_evidence", "/v1/sessions/{session_id}/evidence"),
    AgentdRouteDefinition("session_world", "/v1/sessions/{session_id}/world"),
    AgentdRouteDefinition("session_events", "/v1/sessions/{session_id}/events"),
    AgentdRouteDefinition(
        "session_messages",
        "/v1/sessions/{session_id}/messages",
        ("POST",),
    ),
    AgentdRouteDefinition("session_events_stream", "/v1/sessions/{session_id}/events/stream"),
    AgentdRouteDefinition("session_audit", "/v1/sessions/{session_id}/audit"),
    AgentdRouteDefinition(
        "session_audit_integrity",
        "/v1/sessions/{session_id}/audit/integrity",
    ),
    AgentdRouteDefinition("session_cost", "/v1/sessions/{session_id}/cost"),
    AgentdRouteDefinition("session_logs", "/v1/sessions/{session_id}/logs"),
    AgentdRouteDefinition("session_traces", "/v1/sessions/{session_id}/traces"),
    AgentdRouteDefinition("session_traces_otlp", "/v1/sessions/{session_id}/traces/otlp"),
    AgentdRouteDefinition("session_llm_calls", "/v1/sessions/{session_id}/llm-calls"),
    AgentdRouteDefinition("session_pause", "/v1/sessions/{session_id}/pause", ("POST",)),
    AgentdRouteDefinition("session_resume", "/v1/sessions/{session_id}/resume", ("POST",)),
    AgentdRouteDefinition("session_cancel", "/v1/sessions/{session_id}/cancel", ("POST",)),
)
_SESSION_ROUTES = AgentdRouteMatcher(_SESSION_ROUTE_DEFINITIONS)


class SessionRouteHandlers:
    """HTTP route handlers for the session/goal lifecycle surface.

    Pure JSON/HTTP adaptation. All runtime behavior stays behind RuntimeService;
    this class only translates requests/responses for sessions, goals, and their
    child resources (evidence, world, events, audit, cost, logs, traces).
    """

    def __init__(self, service: RuntimeService) -> None:
        self._service = service

    async def route_response(
        self,
        request: HttpRequest,
        method: str,
        path: str,
        *,
        tenant_id: str | None = None,
    ) -> HttpResponse | None:
        route = _SESSION_ROUTES.match(path, method)
        if route is None:
            return None
        if not route.method_allowed:
            return method_not_allowed(route.allowed_methods)

        if route.name == "sessions":
            if method == "GET":
                try:
                    session_batch = await self._service.stream_sessions(
                        after_session_id=_optional_session_cursor(request.path),
                        limit=_optional_positive_int_query(request.path, "limit"),
                    )
                except ValueError as exc:
                    return bad_request(str(exc))
                if tenant_id is not None:
                    # Tenant data-plane isolation (UA-LIVE-2026-09-21 R5-7):
                    # drop sessions stamped with a foreign tenant. Sessions
                    # created before tenant scoping (tenant_id=None) predate
                    # multi-tenancy and stay visible in legacy mode only.
                    filtered = tuple(
                        view
                        for view in session_batch.sessions
                        if view.tenant_id in (None, tenant_id)
                    )
                    session_batch = RuntimeSessionBatch(
                        filtered,
                        str(filtered[-1].session_id) if filtered else session_batch.next_cursor,
                    )
                return json_response(session_batch_body(session_batch))
            try:
                submission = parse_goal_submission(request.body)
            except ValueError as exc:
                return bad_request(str(exc))
            if submission.profile_name is not None:
                profile_error = self._service.profile_selection_error(submission.profile_name)
                if profile_error is not None:
                    return bad_request(profile_error)
            if submission.compile_goal:
                run = await self._service.run_compiled_goal(
                    submission.goal,
                    timeout_seconds=submission.timeout_seconds,
                    tenant_id=tenant_id,
                )
            else:
                assert submission.task is not None
                run = await self._service.run_goal(
                    submission.goal,
                    submission.task,
                    timeout_seconds=submission.timeout_seconds,
                    read_only=submission.read_only,
                    tenant_id=tenant_id,
                )
            return json_response(
                runtime_run_body(run),
                status_code=runtime_run_status_code(run, created=True),
            )

        session_id = SessionId(route.path_params["session_id"])
        if route.name == "session":
            try:
                return json_response(session_body(await self._service.get_session(session_id)))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_diagnostics":
            try:
                return json_response(
                    session_explorer_body(await self._service.session_explorer(session_id))
                )
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_evidence":
            try:
                return json_response(
                    session_evidence_body(await self._service.session_explorer(session_id))
                )
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_world":
            try:
                return json_response(
                    session_world_body(
                        await self._service.session_world(
                            session_id,
                            entity_id=_optional_query_value(request.path, "entity_id"),
                            relation=_optional_query_value(request.path, "relation"),
                        )
                    )
                )
            except ValueError as exc:
                return bad_request(str(exc))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_events":
            try:
                batch = await self._service.stream_events(
                    session_id,
                    after_event_id=_optional_event_cursor(request.path),
                    limit=_optional_positive_int_query(request.path, "limit"),
                )
                return json_response(event_batch_body(batch))
            except StateNotFoundError as exc:
                return not_found(str(exc))
            except ValueError as exc:
                return bad_request(str(exc))
        if route.name == "session_events_stream":
            try:
                from universal_agent.agentd.representations import sse_streaming_response

                after_event_id = _optional_event_cursor(request.path)
                limit = _optional_positive_int_query(request.path, "limit")
                wait_param = _optional_query_value(request.path, "wait")
                use_streaming = (
                    limit is None and wait_param is None and hasattr(self._service, "watch_events")
                )
                if use_streaming:
                    return sse_streaming_response(
                        self._service,
                        session_id,
                        after_event_id=after_event_id,
                    )
                batch = await _stream_events_for_sse(self._service, session_id, request.path)
                return sse_event_batch_response(batch)
            except StateNotFoundError as exc:
                return not_found(str(exc))
            except ValueError as exc:
                return bad_request(str(exc))
        if route.name == "session_audit":
            try:
                return json_response(
                    audit_records_body(await self._service.audit_records(session_id))
                )
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_audit_integrity":
            try:
                return json_response(
                    audit_integrity_body(await self._service.audit_integrity(session_id))
                )
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_cost":
            try:
                return json_response(cost_body(await self._service.cost(session_id)))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_logs":
            try:
                return json_response(log_records_body(await self._service.logs(session_id)))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_messages":
            try:
                message_payload = _session_message_payload(request.body)
                run = await self._service.continue_session(
                    session_id,
                    message_payload.message,
                    timeout_seconds=message_payload.timeout_seconds,
                )
                return json_response(
                    runtime_run_body(run),
                    status_code=runtime_run_status_code(run),
                )
            except StateNotFoundError as exc:
                return not_found(str(exc))
            except ValueError as exc:
                return bad_request(str(exc))

        if route.name == "session_traces":
            detail = _optional_query_value(request.path, "detail")
            try:
                spans = trace_spans_body(await self._service.traces(session_id))
                if detail == "full":
                    batch = await self._service.stream_events(session_id)
                    merged: dict[str, JsonValue] = dict(spans)
                    merged["llm_calls"] = dict(llm_calls_body(batch.events))
                    merged["detail"] = "full"
                    return json_response(immutable_json(merged))
                return json_response(spans)
            except StateNotFoundError as exc:
                return not_found(str(exc))

        if route.name == "session_llm_calls":
            try:
                batch = await self._service.stream_events(session_id)
                return json_response(llm_calls_body(batch.events))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_traces_otlp":
            try:
                return json_response(await self._service.opentelemetry_traces(session_id))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_pause":
            try:
                pause_reason = _session_reason_payload(
                    request.body,
                    default="session paused",
                    field_name="pause reason",
                )
                run = await self._service.pause_session(session_id, reason=pause_reason)
                return json_response(runtime_run_body(run))
            except ValueError as exc:
                return bad_request(str(exc))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_resume":
            try:
                resume_payload = _session_resume_payload(request.body)
                session = await self._service.get_session(session_id)
                if session.pending_action is not None and resume_payload.confirmed is None:
                    return bad_request("resume requires boolean confirmed for pending action")
                run = await self._service.resume_session(
                    session_id,
                    confirmed=resume_payload.confirmed,
                    remember=resume_payload.remember,
                )
                return json_response(
                    runtime_run_body(run),
                    status_code=runtime_run_status_code(run),
                )
            except ValueError as exc:
                return bad_request(str(exc))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_cancel":
            try:
                session_cancel_reason = _session_reason_payload(
                    request.body,
                    default="session cancelled",
                    field_name="cancel reason",
                )
                run = await self._service.cancel_session(
                    session_id,
                    reason=session_cancel_reason,
                )
                return json_response(runtime_run_body(run))
            except ValueError as exc:
                return bad_request(str(exc))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        return None
