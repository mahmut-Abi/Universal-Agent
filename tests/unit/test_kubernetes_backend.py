from __future__ import annotations

import json

import pytest

from universal_agent.core import JsonValue, immutable_json
from universal_agent.domains.kubernetes import KubectlBackend, KubectlCommandError, KubectlResult


class RecordingKubectlRunner:
    def __init__(self, responses: dict[tuple[str, ...], object]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[float | None] = []

    async def run(
        self,
        args: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
    ) -> KubectlResult:
        self.calls.append(args)
        self.timeouts.append(timeout_seconds)
        try:
            response = self._responses[args]
        except KeyError as exc:
            raise AssertionError(f"unexpected kubectl command: {args}") from exc
        if isinstance(response, KubectlResult):
            if response.returncode != 0:
                raise KubectlCommandError(response.stderr)
            return response
        return KubectlResult(
            args=args,
            stdout=json.dumps(response),
            stderr="",
            returncode=0,
        )


@pytest.mark.asyncio
async def test_kubectl_backend_inspects_workload_health_and_command_scope() -> None:
    runner = RecordingKubectlRunner(
        {
            (
                "--context",
                "prod",
                "--kubeconfig",
                "/tmp/kubeconfig",
                "get",
                "deployment",
                "api",
                "--namespace",
                "default",
                "-o",
                "json",
            ): {
                "metadata": {"name": "api", "resourceVersion": "rv-1", "generation": 4},
                "spec": {"replicas": 3},
                "status": {
                    "readyReplicas": 1,
                    "availableReplicas": 1,
                    "updatedReplicas": 2,
                    "observedGeneration": 3,
                    "conditions": [
                        {
                            "type": "Available",
                            "status": "False",
                            "reason": "MinimumReplicasUnavailable",
                            "message": "Deployment does not have minimum availability.",
                        }
                    ],
                },
            }
        }
    )
    backend = KubectlBackend(
        runner=runner,
        context="prod",
        kubeconfig="/tmp/kubeconfig",
        timeout_seconds=3.5,
    )

    result = await backend.inspect("inspect_workload", immutable_json({"name": "deployment/api"}))

    assert result["resource"] == "deployment/api"
    assert result["namespace"] == "default"
    assert result["healthy"] is False
    assert result["desired_replicas"] == 3
    assert result["ready_replicas"] == 1
    assert result["resource_version"] == "rv-1"
    assert result["root_cause"] == "minimum_replicas_unavailable"
    assert runner.timeouts == [3.5]


@pytest.mark.asyncio
async def test_kubectl_backend_inspects_pod_container_diagnostics() -> None:
    runner = RecordingKubectlRunner(
        {
            (
                "get",
                "pod",
                "api-123",
                "--namespace",
                "prod",
                "-o",
                "json",
            ): {
                "metadata": {"name": "api-123", "resourceVersion": "rv-2"},
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
                            "name": "sidecar",
                            "ready": True,
                            "restartCount": 0,
                            "state": {"running": {"startedAt": "2026-01-01T00:00:00Z"}},
                        },
                    ],
                },
            }
        }
    )
    backend = KubectlBackend(runner=runner)

    result = await backend.inspect(
        "inspect_pod",
        immutable_json({"name": "api-123", "namespace": "prod"}),
    )

    assert result["resource"] == "pod/api-123"
    assert result["phase"] == "Running"
    assert result["ready"] is False
    assert result["restart_count"] == 5
    assert result["root_cause"] == "crash_loop_back_off"
    containers = result["containers"]
    assert isinstance(containers, list)
    assert containers[0] == {
        "name": "api",
        "ready": False,
        "restart_count": 5,
        "state": "waiting",
        "reason": "CrashLoopBackOff",
    }


@pytest.mark.asyncio
async def test_kubectl_backend_reads_logs_and_events() -> None:
    logs = "first line\nsecond line\n"
    runner = RecordingKubectlRunner(
        {
            (
                "logs",
                "pod/api-123",
                "--namespace",
                "prod",
                "--tail",
                "2",
                "--container",
                "api",
            ): KubectlResult((), logs, "", 0),
            (
                "get",
                "events",
                "--namespace",
                "prod",
                "--field-selector",
                "involvedObject.name=api",
                "-o",
                "json",
            ): {
                "items": [
                    _event("Pulled", "Normal"),
                    _event("Unhealthy", "Warning"),
                    _event("BackOff", "Warning"),
                ]
            },
        }
    )
    backend = KubectlBackend(runner=runner)

    log_result = await backend.inspect(
        "inspect_logs",
        immutable_json(
            {"name": "pod/api-123", "namespace": "prod", "tail_lines": 2, "container": "api"}
        ),
    )
    event_result = await backend.inspect(
        "inspect_events",
        immutable_json({"name": "deployment/api", "namespace": "prod", "limit": 2}),
    )

    assert log_result["resource"] == "pod/api-123"
    assert log_result["line_count"] == 2
    assert log_result["recent_logs"] == logs
    assert event_result["event_count"] == 3
    recent_events = event_result["recent_events"]
    assert isinstance(recent_events, list)
    assert [item["reason"] for item in recent_events if isinstance(item, dict)] == [
        "Unhealthy",
        "BackOff",
    ]


@pytest.mark.asyncio
async def test_kubectl_backend_scales_workload_with_current_replicas_guard() -> None:
    runner = RecordingKubectlRunner(
        {
            (
                "get",
                "deployment",
                "api",
                "--namespace",
                "prod",
                "-o",
                "json",
            ): {
                "metadata": {"name": "api", "resourceVersion": "rv-before"},
                "spec": {"replicas": 3},
            },
            (
                "scale",
                "deployment/api",
                "--namespace",
                "prod",
                "--replicas=5",
                "--current-replicas=3",
            ): KubectlResult((), "deployment.apps/api scaled", "", 0),
        }
    )
    backend = KubectlBackend(runner=runner)

    result = await backend.mutate(
        "scale_workload",
        immutable_json({"name": "api", "namespace": "prod", "replicas": 5, "current_replicas": 3}),
    )

    assert result["resource"] == "deployment/api"
    assert result["mutation_applied"] is True
    assert result["previous_replicas"] == 3
    assert result["replicas"] == 5
    assert result["resource_version"] == "rv-before"
    assert result["mutation_id"] == "kubectl-scale:deployment.apps/api scaled"


@pytest.mark.asyncio
async def test_kubectl_backend_inspects_cluster_summary() -> None:
    runner = RecordingKubectlRunner(
        {
            ("get", "nodes", "-o", "json"): {
                "items": [
                    {"status": {"conditions": [{"type": "Ready", "status": "True"}]}},
                    {"status": {"conditions": [{"type": "Ready", "status": "False"}]}},
                ]
            },
            ("get", "namespaces", "-o", "json"): {"items": [{}, {}, {}]},
        }
    )
    backend = KubectlBackend(runner=runner)

    result = await backend.inspect("inspect_cluster", immutable_json())

    assert result == {
        "resource": "cluster",
        "node_count": 2,
        "ready_node_count": 1,
        "namespace_count": 3,
        "healthy": False,
    }


def _event(reason: str, event_type: str) -> dict[str, JsonValue]:
    return {
        "type": event_type,
        "reason": reason,
        "message": f"{reason} message",
        "count": 1,
        "lastTimestamp": "2026-01-01T00:00:00Z",
        "involvedObject": {"kind": "Deployment", "name": "api"},
    }
