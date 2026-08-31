from __future__ import annotations

import pytest

from universal_agent.agentd._routes_distributed import _DISTRIBUTED_ROUTES
from universal_agent.agentd._routes_session import _SESSION_ROUTES
from universal_agent.agentd.app import (
    _DETAIL_GET_ROUTES,
    _OPENAPI_ROUTE_DEFINITIONS,
)
from universal_agent.agentd.console_routes import _CONSOLE_ROUTES
from universal_agent.agentd.openapi import build_agentd_openapi_schema
from universal_agent.agentd.routing import (
    AgentdRouteDefinition,
    AgentdRouteMatcher,
    _distributed_worker_registration_payload,
    _match_path,
    _optional_bool_query,
    _optional_float_query,
    _optional_positive_int_query,
    _optional_query_value,
)
from universal_agent.core import JsonValue, immutable_json


def json_object(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


@pytest.mark.unit
def test_agentd_route_tables_match_starlette_path_templates() -> None:
    session = _SESSION_ROUTES.match("/v1/sessions/session-1/events/stream", "GET")
    task = _DISTRIBUTED_ROUTES.match(
        "/v1/distributed/sessions/session-1/tasks/task-1/schedule",
        "POST",
    )
    action = _DISTRIBUTED_ROUTES.match(
        "/v1/distributed/sessions/session-1/tasks/task-1/actions/action-1/schedule",
        "POST",
    )
    worker = _DISTRIBUTED_ROUTES.match("/v1/distributed/workers/worker-1/run-once", "POST")
    lease = _DISTRIBUTED_ROUTES.match(
        "/v1/distributed/lock-leases/lock-lease-1/release",
        "POST",
    )
    console_session = _CONSOLE_ROUTES.match("/console/sessions/session-1/world", "GET")
    console_domain = _CONSOLE_ROUTES.match("/console/domains/kubernetes/0.2.0", "GET")
    console_package = _CONSOLE_ROUTES.match(
        "/console/domain-packages/kubernetes/0.2.0",
        "GET",
    )
    profile = _DETAIL_GET_ROUTES.match("/v1/profiles/production-operator", "GET")
    package = _DETAIL_GET_ROUTES.match(
        "/v1/domain-packages/kubernetes/0.2.0",
        "GET",
    )

    assert session is not None
    assert session.name == "session_events_stream"
    assert session.path_params == {"session_id": "session-1"}
    assert task is not None
    assert task.name == "distributed_schedule_task"
    assert task.path_params == {"session_id": "session-1", "task_id": "task-1"}
    assert action is not None
    assert action.name == "distributed_schedule_action"
    assert action.path_params == {
        "session_id": "session-1",
        "task_id": "task-1",
        "action_id": "action-1",
    }
    assert worker is not None
    assert worker.path_params == {"worker_id": "worker-1", "action": "run-once"}
    assert lease is not None
    assert lease.path_params == {"lease_id": "lock-lease-1", "action": "release"}
    assert console_session is not None
    assert console_session.path_params == {"session_id": "session-1", "suffix": "world"}
    assert console_domain is not None
    assert console_domain.path_params == {"name": "kubernetes", "version": "0.2.0"}
    assert console_package is not None
    assert console_package.path_params == {"name": "kubernetes", "version": "0.2.0"}
    assert profile is not None
    assert profile.path_params == {"profile": "production-operator"}
    assert package is not None
    assert package.path_params == {"name": "kubernetes", "version": "0.2.0"}


@pytest.mark.contract
def test_agentd_openapi_schema_is_generated_from_runtime_route_definitions() -> None:
    schema = build_agentd_openapi_schema(_OPENAPI_ROUTE_DEFINITIONS)
    paths = schema["paths"]
    assert isinstance(paths, dict)

    sessions = paths["/v1/sessions"]
    stream = paths["/v1/sessions/{session_id}/events/stream"]
    distributed_goals = paths["/v1/distributed/goals"]
    metrics_prometheus = paths["/v1/metrics/prometheus"]
    assert isinstance(sessions, dict)
    assert isinstance(stream, dict)
    assert isinstance(distributed_goals, dict)
    assert isinstance(metrics_prometheus, dict)

    sessions_post = sessions["post"]
    stream_get = stream["get"]
    distributed_goals_post = distributed_goals["post"]
    metrics_prometheus_get = metrics_prometheus["get"]
    assert isinstance(sessions_post, dict)
    assert isinstance(stream_get, dict)
    assert isinstance(distributed_goals_post, dict)
    assert isinstance(metrics_prometheus_get, dict)
    assert sessions_post["operationId"] == "sessions_post"
    assert "201" in json_object(sessions_post["responses"])
    assert "202" in json_object(distributed_goals_post["responses"])
    assert json_object(json_object(stream_get["responses"])["200"])["content"] == {
        "text/event-stream": {"schema": {"type": "string"}}
    }
    assert json_object(json_object(metrics_prometheus_get["responses"])["200"])["content"] == {
        "text/plain": {"schema": {"type": "string"}}
    }


@pytest.mark.unit
def test_agentd_route_tables_ignore_query_and_trailing_slashes() -> None:
    session = _SESSION_ROUTES.match("/v1/sessions/session-1/events?limit=1", "GET")
    package = _DETAIL_GET_ROUTES.match("/v1/domain-packages/kubernetes/?tag=ops", "GET")
    cancelled = _DISTRIBUTED_ROUTES.match(
        "/v1/distributed/work-items/work-1/cancel/",
        "POST",
    )

    assert session is not None
    assert session.name == "session_events"
    assert session.path_params == {"session_id": "session-1"}
    assert package is not None
    assert package.name == "domain_package"
    assert package.path_params == {"name": "kubernetes"}
    assert cancelled is not None
    assert cancelled.path_params == {"work_item_id": "work-1"}


@pytest.mark.unit
def test_agentd_query_helpers_use_starlette_query_params_contract() -> None:
    assert _optional_query_value("/v1/domain-packages?tag=ops", "tag") == "ops"
    assert _optional_query_value("/v1/domain-packages?tag=ops%20team", "tag") == "ops team"
    assert _optional_query_value("/v1/domain-packages", "tag") is None

    with pytest.raises(ValueError, match="tag must be specified once"):
        _optional_query_value("/v1/domain-packages?tag=ops&tag=platform", "tag")
    with pytest.raises(ValueError, match="tag must not be empty"):
        _optional_query_value("/v1/domain-packages?tag=", "tag")


@pytest.mark.unit
def test_agentd_query_scalar_helpers_use_pydantic_parsing_with_stable_errors() -> None:
    assert _optional_bool_query("/v1/sessions/session-1/events/stream?wait=yes", "wait") is True
    assert _optional_bool_query("/v1/sessions/session-1/events/stream?wait=0", "wait") is False
    assert _optional_bool_query("/v1/sessions/session-1/events/stream", "wait") is None
    assert _optional_positive_int_query("/v1/sessions?limit=10", "limit") == 10
    assert (
        _optional_float_query(
            "/v1/sessions/session-1/events/stream?timeout_seconds=0.25",
            "timeout_seconds",
            default=10.0,
            minimum=0.0,
            maximum=30.0,
        )
        == 0.25
    )

    with pytest.raises(ValueError, match="wait must be a boolean"):
        _optional_bool_query("/v1/sessions/session-1/events/stream?wait=maybe", "wait")
    with pytest.raises(ValueError, match="limit must be a positive integer"):
        _optional_positive_int_query("/v1/sessions?limit=0", "limit")
    with pytest.raises(ValueError, match="timeout_seconds must be a number"):
        _optional_float_query(
            "/v1/sessions/session-1/events/stream?timeout_seconds=abc",
            "timeout_seconds",
            default=10.0,
            minimum=0.0,
            maximum=30.0,
        )
    with pytest.raises(ValueError, match="timeout_seconds must be between 0 and 30"):
        _optional_float_query(
            "/v1/sessions/session-1/events/stream?timeout_seconds=31",
            "timeout_seconds",
            default=10.0,
            minimum=0.0,
            maximum=30.0,
        )


@pytest.mark.contract
def test_agentd_request_payload_errors_preserve_indexed_pydantic_paths() -> None:
    with pytest.raises(
        ValueError,
        match=r"distributed worker capabilities\[0\] must be a non-empty string",
    ):
        _distributed_worker_registration_payload(immutable_json({"capabilities": [1]}))


@pytest.mark.unit
def test_agentd_path_matching_uses_starlette_route_contract_without_unquoting() -> None:
    assert _match_path(
        "/v1/sessions/session-1/events/stream?limit=1",
        "/v1/sessions/{session_id}/{first_suffix}/{second_suffix}",
    ) == {
        "session_id": "session-1",
        "first_suffix": "events",
        "second_suffix": "stream",
    }
    assert _match_path("/console/sessions/a%2Fb", "/console/sessions/{session_id}") == {
        "session_id": "a%2Fb"
    }
    assert (
        _match_path(
            "/v1/sessions/session-1/events",
            "/v1/sessions/{session_id}/{first_suffix}/{second_suffix}",
        )
        is None
    )


@pytest.mark.unit
def test_agentd_route_matcher_uses_starlette_paths_and_preserves_method_contract() -> None:
    matcher = AgentdRouteMatcher(
        (
            AgentdRouteDefinition("health", "/health"),
            AgentdRouteDefinition("session", "/v1/sessions/{session_id}", ("GET", "POST")),
        )
    )

    health = matcher.match("/health?ignored=true", "post")
    session = matcher.match("/v1/sessions/session-1", "POST")

    assert health is not None
    assert health.name == "health"
    assert health.allowed_methods == ("GET",)
    assert not health.method_allowed
    assert session is not None
    assert session.name == "session"
    assert session.path_params == {"session_id": "session-1"}
    assert session.allowed_methods == ("GET", "POST")
    assert session.method_allowed
    assert matcher.match("/missing", "GET") is None


@pytest.mark.contract
def test_agentd_route_tables_reject_unknown_paths_and_preserve_encoded_segments() -> None:
    encoded_session = _CONSOLE_ROUTES.match("/console/sessions/%20", "GET")
    task_schedule = _DISTRIBUTED_ROUTES.match(
        "/v1/distributed/sessions/session-1/tasks/task-1/schedule",
        "POST",
    )

    assert encoded_session is not None
    assert encoded_session.name == "console_session"
    assert encoded_session.path_params == {"session_id": "%20"}
    assert _DETAIL_GET_ROUTES.match("/v1/profiles/", "GET") is None
    assert _DISTRIBUTED_ROUTES.match("/v1/distributed/workers/worker-1", "POST") is None
    assert task_schedule is not None
    assert task_schedule.name == "distributed_schedule_task"
