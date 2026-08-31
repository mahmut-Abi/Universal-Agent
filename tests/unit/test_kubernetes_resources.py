from __future__ import annotations

import pytest

from universal_agent.core import JsonValue
from universal_agent.domains.kubernetes import resources as k8s


@pytest.mark.contract
def test_kubernetes_items_use_structured_json_object_parsing() -> None:
    payload: dict[str, JsonValue] = {
        "items": [
            {"metadata": {"name": "api"}},
            "ignored",
            3,
            {"metadata": {"name": "worker"}},
        ]
    }

    assert k8s.items(payload) == (
        {"metadata": {"name": "api"}},
        {"metadata": {"name": "worker"}},
    )


@pytest.mark.contract
def test_pod_summary_uses_structured_container_payload_defaults() -> None:
    summary = k8s.pod_summary(
        {
            "metadata": {
                "name": "api-123",
                "namespace": "prod",
                "resourceVersion": "rv-pod",
            },
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {
                        "name": "api",
                        "ready": False,
                        "restartCount": 5,
                        "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    },
                    {
                        "name": 123,
                        "ready": "yes",
                        "restartCount": True,
                        "state": [],
                    },
                    "ignored",
                ],
            },
        }
    )

    assert isinstance(summary, dict)
    assert summary["resource"] == "pod/api-123"
    assert summary["ready"] is False
    assert summary["restart_count"] == 5
    assert summary["root_cause"] == "crash_loop_back_off"
    assert summary["containers"] == [
        {
            "name": "api",
            "ready": False,
            "restart_count": 5,
            "state": "waiting",
            "reason": "CrashLoopBackOff",
        },
        {
            "name": "",
            "ready": False,
            "restart_count": 0,
            "state": "unknown",
            "reason": "",
        },
    ]


@pytest.mark.unit
def test_event_summary_maps_kubernetes_alias_fields() -> None:
    assert k8s.event_summary(
        {
            "type": "Warning",
            "reason": "BackOff",
            "message": "Back-off restarting failed container",
            "count": 7,
            "firstTimestamp": "2026-01-01T00:00:00Z",
            "lastTimestamp": "2026-01-01T00:05:00Z",
            "eventTime": "2026-01-01T00:05:01Z",
            "metadata": {"creationTimestamp": "2026-01-01T00:00:01Z"},
            "involvedObject": {"kind": "Pod", "name": "api-123"},
        }
    ) == {
        "type": "Warning",
        "reason": "BackOff",
        "message": "Back-off restarting failed container",
        "count": 7,
        "first_timestamp": "2026-01-01T00:00:00Z",
        "last_timestamp": "2026-01-01T00:05:00Z",
        "event_time": "2026-01-01T00:05:01Z",
        "creation_timestamp": "2026-01-01T00:00:01Z",
        "involved_object_kind": "Pod",
        "involved_object_name": "api-123",
    }


@pytest.mark.unit
def test_kubernetes_argument_helpers_use_structured_scalar_validation() -> None:
    assert k8s.required_string({"name": "api"}, "name") == "api"
    assert k8s.required_int({"replicas": 3}, "replicas") == 3
    assert k8s.positive_int(2, default=100) == 2
    assert k8s.positive_int(None, default=100) == 100

    with pytest.raises(ValueError, match="Kubernetes argument name must be a non-empty string"):
        k8s.required_string({"name": True}, "name")
    with pytest.raises(ValueError, match="Kubernetes argument replicas must be an integer"):
        k8s.required_int({"replicas": True}, "replicas")
    with pytest.raises(ValueError, match="Kubernetes integer argument must be positive"):
        k8s.positive_int("10", default=100)


@pytest.mark.contract
def test_kubernetes_optional_helpers_remain_tolerant_for_cluster_json() -> None:
    assert k8s.optional_int(3) == 3
    assert k8s.optional_int(True) is None
    assert k8s.optional_int("3") is None
    assert k8s.optional_string("api") == "api"
    assert k8s.optional_string(3) is None
    assert k8s.optional_resource_version("rv-1") == "rv-1"
    assert k8s.optional_resource_version(42) == "42"
    assert k8s.optional_resource_version(True) is None


@pytest.mark.unit
def test_snake_case_uses_library_case_conversion_with_kubernetes_separators() -> None:
    assert k8s.snake_case("CrashLoopBackOff") == "crash_loop_back_off"
    assert k8s.snake_case("HTTPProbeFailed") == "http_probe_failed"
    assert k8s.snake_case("Back-off restarting.failed") == "back_off_restarting_failed"
