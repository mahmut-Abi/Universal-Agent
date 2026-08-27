from __future__ import annotations

import pytest

from universal_agent.agentd.routing import (
    _console_domain_package_route,
    _console_domain_route,
    _console_session_route,
    _distributed_cancel_route,
    _distributed_lock_lease_route,
    _distributed_schedule_action_route,
    _distributed_schedule_session_route,
    _distributed_schedule_task_route,
    _distributed_worker_action_route,
    _distributed_worker_registration_payload,
    _domain_package_route,
    _match_path,
    _optional_query_value,
    _profile_route,
    _session_route,
)
from universal_agent.core import ActionId, SessionId, TaskId, immutable_json
from universal_agent.distributed import DistributedLockLeaseId, WorkerId, WorkItemId


def test_agentd_route_helpers_match_starlette_path_templates() -> None:
    session_id, suffix = _session_route("/v1/sessions/session-1/events/stream")
    task_session_id, task_id = _distributed_schedule_task_route(
        "/v1/distributed/sessions/session-1/tasks/task-1/schedule"
    )
    action_session_id, action_task_id, action_id = _distributed_schedule_action_route(
        "/v1/distributed/sessions/session-1/tasks/task-1/actions/action-1/schedule"
    )
    worker_id, worker_action = _distributed_worker_action_route(
        "/v1/distributed/workers/worker-1/run-once"
    )
    lease_id, lease_action = _distributed_lock_lease_route(
        "/v1/distributed/lock-leases/lock-lease-1/release"
    )

    assert session_id == SessionId("session-1")
    assert suffix == "events/stream"
    assert _console_session_route("/console/sessions/session-1/world") == (
        SessionId("session-1"),
        "world",
    )
    assert _console_domain_route("/console/domains/kubernetes/0.2.0") == (
        "kubernetes",
        "0.2.0",
    )
    assert _console_domain_package_route("/console/domain-packages/kubernetes/0.2.0") == (
        "kubernetes",
        "0.2.0",
    )
    assert worker_id == WorkerId("worker-1")
    assert worker_action == "run-once"
    assert lease_id == DistributedLockLeaseId("lock-lease-1")
    assert lease_action == "release"
    assert _distributed_schedule_session_route(
        "/v1/distributed/sessions/session-1/schedule"
    ) == SessionId("session-1")
    assert task_session_id == SessionId("session-1")
    assert task_id == TaskId("task-1")
    assert action_session_id == SessionId("session-1")
    assert action_task_id == TaskId("task-1")
    assert action_id == ActionId("action-1")
    assert _distributed_cancel_route(
        "/v1/distributed/work-items/work-1/cancel"
    ) == WorkItemId("work-1")
    assert _profile_route("/v1/profiles/production-operator") == "production-operator"
    assert _domain_package_route("/v1/domain-packages/kubernetes/0.2.0") == (
        "kubernetes",
        "0.2.0",
    )


def test_agentd_route_helpers_ignore_query_and_trailing_slashes() -> None:
    assert _session_route("/v1/sessions/session-1/events?limit=1") == (
        SessionId("session-1"),
        "events",
    )
    assert _domain_package_route("/v1/domain-packages/kubernetes/?tag=ops") == (
        "kubernetes",
        None,
    )
    assert _distributed_cancel_route("/v1/distributed/work-items/work-1/cancel/") == WorkItemId(
        "work-1"
    )


def test_agentd_query_helpers_use_starlette_query_params_contract() -> None:
    assert _optional_query_value("/v1/domain-packages?tag=ops", "tag") == "ops"
    assert _optional_query_value("/v1/domain-packages?tag=ops%20team", "tag") == "ops team"
    assert _optional_query_value("/v1/domain-packages", "tag") is None

    with pytest.raises(ValueError, match="tag must be specified once"):
        _optional_query_value("/v1/domain-packages?tag=ops&tag=platform", "tag")
    with pytest.raises(ValueError, match="tag must not be empty"):
        _optional_query_value("/v1/domain-packages?tag=", "tag")


def test_agentd_request_payload_errors_preserve_indexed_pydantic_paths() -> None:
    with pytest.raises(
        ValueError,
        match=r"distributed worker capabilities\[0\] must be a non-empty string",
    ):
        _distributed_worker_registration_payload(immutable_json({"capabilities": [1]}))


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
    assert _match_path(
        "/v1/sessions/session-1/events",
        "/v1/sessions/{session_id}/{first_suffix}/{second_suffix}",
    ) is None


def test_agentd_route_helpers_reject_blank_or_unknown_paths() -> None:
    assert _console_session_route("/console/sessions/%20") == (SessionId("%20"), "")
    assert _profile_route("/v1/profiles/") is None
    assert _distributed_worker_action_route("/v1/distributed/workers/worker-1") == (None, "")
    assert _distributed_schedule_action_route(
        "/v1/distributed/sessions/session-1/tasks/task-1/schedule"
    ) == (None, None, None)
