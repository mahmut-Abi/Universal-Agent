from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

import httpx
import pytest

from universal_agent.core import (
    JsonMapping,
    JsonValue,
    ObservationStatus,
    ToolCall,
    immutable_json,
    new_action_id,
)
from universal_agent.domains.kubernetes import (
    HttpxKubernetesApiTransport,
    KubectlBackend,
    KubectlCommandError,
    KubectlResult,
    KubernetesApiBackend,
    KubernetesApiConflictError,
    KubernetesApiError,
    KubernetesApiResponse,
    SubprocessKubectlRunner,
)
from universal_agent.domains.kubernetes.tools import KubernetesScaleTool
from universal_agent.tools import ToolRegistry, ToolRuntime


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


KubernetesApiFixtureResponse = JsonValue | KubernetesApiResponse


class RecordingKubernetesApiTransport:
    def __init__(
        self,
        responses: dict[tuple[str, str, tuple[tuple[str, str], ...]], KubernetesApiFixtureResponse],
    ) -> None:
        self._responses = responses
        self.requests: list[
            tuple[
                str,
                str,
                tuple[tuple[str, str], ...],
                JsonMapping | None,
                dict[str, str],
                float | None,
            ]
        ] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: JsonMapping | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> KubernetesApiResponse:
        key = (method, path, tuple(sorted((query or {}).items())))
        self.requests.append((method, path, key[2], body, dict(headers or {}), timeout_seconds))
        try:
            response = self._responses[key]
        except KeyError as exc:
            raise AssertionError(f"unexpected Kubernetes API request: {key}") from exc
        if isinstance(response, KubernetesApiResponse):
            return response
        return KubernetesApiResponse(200, response)


@pytest.mark.asyncio
@pytest.mark.contract
async def test_httpx_kubernetes_api_transport_builds_request_and_decodes_response() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"ok": True},
            headers={"x-kubernetes-test": "yes"},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        transport = HttpxKubernetesApiTransport(
            "https://kube.example.test",
            bearer_token="kube-token",
            client=client,
        )
        response = await transport.request(
            "PATCH",
            "/apis/apps/v1/namespaces/default/deployments/api/scale",
            query={"fieldManager": "universal-agent"},
            body=immutable_json({"spec": {"replicas": 3}}),
            headers={"content-type": "application/merge-patch+json"},
            timeout_seconds=4.0,
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert response.payload == {"ok": True}
    assert response.headers["x-kubernetes-test"] == "yes"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "PATCH"
    assert str(request.url) == (
        "https://kube.example.test/apis/apps/v1/namespaces/default/deployments/"
        "api/scale?fieldManager=universal-agent"
    )
    assert request.headers["authorization"] == "Bearer kube-token"
    assert request.headers["accept"] == "application/json"
    assert request.headers["content-type"] == "application/merge-patch+json"
    assert json.loads(request.content.decode("utf-8")) == {"spec": {"replicas": 3}}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_httpx_kubernetes_api_transport_maps_request_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        transport = HttpxKubernetesApiTransport("https://kube.example.test", client=client)
        with pytest.raises(KubernetesApiError, match="Kubernetes API request failed"):
            await transport.request("GET", "/api/v1/nodes")
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_httpx_kubernetes_api_transport_preserves_api_server_base_path() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": []}, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        transport = HttpxKubernetesApiTransport(
            "https://gateway.example.test/kubernetes/prod/",
            client=client,
        )
        await transport.request("GET", "/api/v1/nodes")
    finally:
        await client.aclose()

    assert str(requests[0].url) == "https://gateway.example.test/kubernetes/prod/api/v1/nodes"


@pytest.mark.asyncio
@pytest.mark.contract
async def test_kubectl_backend_rejects_non_object_json_output() -> None:
    runner = RecordingKubectlRunner({("get", "nodes", "-o", "json"): []})
    backend = KubectlBackend(runner=runner)

    with pytest.raises(KubectlCommandError, match="kubectl returned JSON that was not an object"):
        await backend.inspect("inspect_cluster", immutable_json())


class RecordingScaleBackend:
    def __init__(self) -> None:
        self.arguments: JsonMapping | None = None

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.arguments = arguments
        return immutable_json({"resource": "deployment/api", "mutation_applied": True})


@pytest.mark.asyncio
@pytest.mark.contract
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
@pytest.mark.contract
async def test_kubectl_backend_treats_zero_replica_deployment_as_unhealthy() -> None:
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
                "metadata": {"name": "api", "resourceVersion": "rv-9"},
                "spec": {"replicas": 0},
                "status": {},
            }
        }
    )
    backend = KubectlBackend(
        runner=runner,
        context="prod",
        kubeconfig="/tmp/kubeconfig",
    )

    result = await backend.inspect("inspect_workload", immutable_json({"name": "deployment/api"}))

    assert result["desired_replicas"] == 0
    assert result["ready_replicas"] == 0
    # A zero-replica deployment has no capacity: ready (0) >= desired (0) does
    # not make it healthy, otherwise the remediation loop would never scale it
    # back up.
    assert result["healthy"] is False


@pytest.mark.asyncio
@pytest.mark.contract
async def test_kubectl_backend_inspect_waits_for_availability_when_requested() -> None:
    runner = RecordingKubectlRunner(
        {
            (
                "--context",
                "prod",
                "--kubeconfig",
                "/tmp/kubeconfig",
                "wait",
                "deployment/api",
                "--namespace",
                "default",
                "--for=condition=Available",
                "--timeout=20s",
            ): {},
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
                "metadata": {"name": "api", "resourceVersion": "rv-2"},
                "spec": {"replicas": 1},
                "status": {"readyReplicas": 1, "availableReplicas": 1},
            },
        }
    )
    backend = KubectlBackend(
        runner=runner,
        context="prod",
        kubeconfig="/tmp/kubeconfig",
    )

    result = await backend.inspect(
        "inspect_workload",
        immutable_json({"name": "deployment/api", "wait_seconds": 20}),
    )

    assert result["healthy"] is True
    assert result["desired_replicas"] == 1
    assert result["ready_replicas"] == 1
    assert runner.calls[0][-6:] == (
        "wait",
        "deployment/api",
        "--namespace",
        "default",
        "--for=condition=Available",
        "--timeout=20s",
    )
    assert runner.calls[1][4:] == (
        "get",
        "deployment",
        "api",
        "--namespace",
        "default",
        "-o",
        "json",
    )


@pytest.mark.asyncio
@pytest.mark.contract
async def test_kubectl_backend_inspect_survives_availability_wait_timeout() -> None:
    runner = RecordingKubectlRunner(
        {
            (
                "--context",
                "prod",
                "--kubeconfig",
                "/tmp/kubeconfig",
                "wait",
                "deployment/api",
                "--namespace",
                "default",
                "--for=condition=Available",
                "--timeout=20s",
            ): KubectlResult(
                args=("wait",),
                stdout="",
                stderr="error: timed out waiting for the condition on deployment/api",
                returncode=1,
            ),
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
                "metadata": {"name": "api", "resourceVersion": "rv-3"},
                "spec": {"replicas": 1},
                "status": {"readyReplicas": 1, "availableReplicas": 1},
            },
        }
    )
    backend = KubectlBackend(
        runner=runner,
        context="prod",
        kubeconfig="/tmp/kubeconfig",
    )

    result = await backend.inspect(
        "inspect_workload",
        immutable_json({"name": "deployment/api", "wait_seconds": 20}),
    )

    # The wait timed out but the follow-up get still reports real state.
    assert result["healthy"] is True
    assert [call[-1] for call in runner.calls] == ["--timeout=20s", "json"]


@pytest.mark.asyncio
@pytest.mark.contract
async def test_kubectl_backend_includes_workload_pod_summaries_from_selector() -> None:
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
                "metadata": {"name": "api", "resourceVersion": "rv-1", "generation": 4},
                "spec": {
                    "replicas": 2,
                    "selector": {"matchLabels": {"tier": "web", "app": "api"}},
                },
                "status": {"readyReplicas": 1, "availableReplicas": 1},
            },
            (
                "get",
                "pods",
                "--namespace",
                "prod",
                "-l",
                "app=api,tier=web",
                "-o",
                "json",
            ): {"items": [_crash_loop_pod()]},
        }
    )
    backend = KubectlBackend(runner=runner)

    result = await backend.inspect(
        "inspect_workload",
        immutable_json({"name": "deployment/api", "namespace": "prod"}),
    )

    assert result["selector_labels"] == {"app": "api", "tier": "web"}
    assert result["pod_count"] == 1
    assert result["ready_pod_count"] == 0
    assert result["root_cause"] == "crash_loop_back_off"
    pods = result["pods"]
    assert isinstance(pods, list)
    pod = pods[0]
    assert isinstance(pod, dict)
    assert pod["resource"] == "pod/api-123"
    assert pod["restart_count"] == 5


@pytest.mark.asyncio
@pytest.mark.contract
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
@pytest.mark.unit
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
@pytest.mark.unit
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
                "--resource-version=rv-before",
            ): KubectlResult((), "deployment.apps/api scaled", "", 0),
        }
    )
    backend = KubectlBackend(runner=runner)

    result = await backend.mutate(
        "scale_workload",
        immutable_json(
            {
                "name": "api",
                "namespace": "prod",
                "replicas": 5,
                "current_replicas": 3,
                "resource_version": "rv-before",
            }
        ),
    )

    assert result["resource"] == "deployment/api"
    assert result["mutation_applied"] is True
    assert result["previous_replicas"] == 3
    assert result["replicas"] == 5
    assert result["resource_version"] == "rv-before"
    assert result["mutation_id"] == "kubectl-scale:deployment.apps/api scaled"


@pytest.mark.asyncio
@pytest.mark.contract
async def test_scale_tool_schema_accepts_concurrency_guards() -> None:
    backend = RecordingScaleBackend()
    registry = ToolRegistry()
    registry.register(KubernetesScaleTool(backend))

    result = await ToolRuntime(registry).execute(
        call=ToolCall(
            new_action_id(),
            "kubernetes_scale_workload",
            "scale_workload",
            immutable_json(
                {
                    "name": "api",
                    "namespace": "prod",
                    "replicas": 5,
                    "current_replicas": 3,
                    "resource_version": "rv-before",
                }
            ),
        )
    )

    assert result.status is ObservationStatus.SUCCEEDED
    assert backend.arguments is not None
    assert backend.arguments["current_replicas"] == 3
    assert backend.arguments["resource_version"] == "rv-before"


@pytest.mark.asyncio
@pytest.mark.contract
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


@pytest.mark.asyncio
@pytest.mark.unit
async def test_kubernetes_api_backend_inspects_workload_health() -> None:
    transport = RecordingKubernetesApiTransport(
        {
            (
                "GET",
                "/apis/apps/v1/namespaces/prod/deployments/api",
                (),
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
    backend = KubernetesApiBackend(
        api_server="https://cluster.example.test",
        transport=transport,
        default_namespace="prod",
        timeout_seconds=4.5,
    )

    result = await backend.inspect("inspect_workload", immutable_json({"name": "deployment/api"}))

    assert result["resource"] == "deployment/api"
    assert result["namespace"] == "prod"
    assert result["healthy"] is False
    assert result["desired_replicas"] == 3
    assert result["ready_replicas"] == 1
    assert result["resource_version"] == "rv-1"
    assert result["root_cause"] == "minimum_replicas_unavailable"
    assert transport.requests[0] == (
        "GET",
        "/apis/apps/v1/namespaces/prod/deployments/api",
        (),
        None,
        {},
        4.5,
    )


@pytest.mark.asyncio
@pytest.mark.contract
async def test_kubernetes_api_backend_treats_zero_replica_deployment_as_unhealthy() -> None:
    transport = RecordingKubernetesApiTransport(
        {
            (
                "GET",
                "/apis/apps/v1/namespaces/prod/deployments/api",
                (),
            ): {
                "metadata": {"name": "api", "resourceVersion": "rv-9"},
                "spec": {"replicas": 0},
                "status": {},
            }
        }
    )
    backend = KubernetesApiBackend(
        api_server="https://cluster.example.test",
        transport=transport,
        default_namespace="prod",
    )

    result = await backend.inspect("inspect_workload", immutable_json({"name": "deployment/api"}))

    assert result["desired_replicas"] == 0
    assert result["ready_replicas"] == 0
    assert result["healthy"] is False


@pytest.mark.asyncio
@pytest.mark.contract
async def test_kubernetes_api_backend_rejects_non_object_json_response() -> None:
    transport = RecordingKubernetesApiTransport(
        {
            (
                "GET",
                "/apis/apps/v1/namespaces/prod/deployments/api",
                (),
            ): [],
        }
    )
    backend = KubernetesApiBackend(
        api_server="https://cluster.example.test",
        transport=transport,
        default_namespace="prod",
    )

    with pytest.raises(
        KubernetesApiError,
        match="Kubernetes API returned JSON that was not an object",
    ):
        await backend.inspect("inspect_workload", immutable_json({"name": "deployment/api"}))


@pytest.mark.asyncio
@pytest.mark.contract
async def test_kubernetes_api_backend_includes_workload_pod_summaries_from_selector() -> None:
    transport = RecordingKubernetesApiTransport(
        {
            (
                "GET",
                "/apis/apps/v1/namespaces/prod/deployments/api",
                (),
            ): {
                "metadata": {"name": "api", "resourceVersion": "rv-1", "generation": 4},
                "spec": {
                    "replicas": 2,
                    "selector": {"matchLabels": {"tier": "web", "app": "api"}},
                },
                "status": {"readyReplicas": 1, "availableReplicas": 1},
            },
            (
                "GET",
                "/api/v1/namespaces/prod/pods",
                (("labelSelector", "app=api,tier=web"),),
            ): {"items": [_crash_loop_pod()]},
        }
    )
    backend = KubernetesApiBackend(
        api_server="https://cluster.example.test",
        transport=transport,
        default_namespace="prod",
    )

    result = await backend.inspect("inspect_workload", immutable_json({"name": "deployment/api"}))

    assert result["selector_labels"] == {"app": "api", "tier": "web"}
    assert result["pod_count"] == 1
    assert result["ready_pod_count"] == 0
    assert result["root_cause"] == "crash_loop_back_off"
    pods = result["pods"]
    assert isinstance(pods, list)
    pod = pods[0]
    assert isinstance(pod, dict)
    assert pod["resource"] == "pod/api-123"
    assert pod["restart_count"] == 5
    assert transport.requests[1][0:3] == (
        "GET",
        "/api/v1/namespaces/prod/pods",
        (("labelSelector", "app=api,tier=web"),),
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_kubernetes_api_backend_reads_logs_and_events() -> None:
    logs = "first line\nsecond line\n"
    transport = RecordingKubernetesApiTransport(
        {
            (
                "GET",
                "/api/v1/namespaces/prod/pods/api-123/log",
                (("container", "api"), ("tailLines", "2")),
            ): KubernetesApiResponse(200, text=logs),
            (
                "GET",
                "/api/v1/namespaces/prod/events",
                (("fieldSelector", "involvedObject.name=api"),),
            ): {
                "items": [
                    _event("Pulled", "Normal"),
                    _event("Unhealthy", "Warning"),
                    _event("BackOff", "Warning"),
                ]
            },
        }
    )
    backend = KubernetesApiBackend(
        api_server="https://cluster.example.test",
        transport=transport,
    )

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
@pytest.mark.contract
async def test_kubernetes_api_backend_scales_workload_with_concurrency_guards() -> None:
    transport = RecordingKubernetesApiTransport(
        {
            (
                "GET",
                "/apis/apps/v1/namespaces/prod/deployments/api",
                (),
            ): {
                "metadata": {"name": "api", "resourceVersion": "rv-before"},
                "spec": {"replicas": 3},
            },
            (
                "PATCH",
                "/apis/apps/v1/namespaces/prod/deployments/api/scale",
                (),
            ): {"metadata": {"resourceVersion": "rv-after"}},
        }
    )
    backend = KubernetesApiBackend(
        api_server="https://cluster.example.test",
        transport=transport,
    )

    result = await backend.mutate(
        "scale_workload",
        immutable_json(
            {
                "name": "api",
                "namespace": "prod",
                "replicas": 5,
                "current_replicas": 3,
                "resource_version": "rv-before",
            }
        ),
    )
    patch = transport.requests[1]

    assert result["resource"] == "deployment/api"
    assert result["mutation_applied"] is True
    assert result["previous_replicas"] == 3
    assert result["replicas"] == 5
    assert result["resource_version"] == "rv-before"
    assert result["mutation_id"] == "kubectl-scale:rv-after"
    assert patch[0:3] == (
        "PATCH",
        "/apis/apps/v1/namespaces/prod/deployments/api/scale",
        (),
    )
    assert patch[3] == {"metadata": {"resourceVersion": "rv-before"}, "spec": {"replicas": 5}}
    assert patch[4] == {"content-type": "application/merge-patch+json"}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_kubernetes_api_backend_rejects_stale_scale_guard() -> None:
    transport = RecordingKubernetesApiTransport(
        {
            (
                "GET",
                "/apis/apps/v1/namespaces/prod/deployments/api",
                (),
            ): {
                "metadata": {"name": "api", "resourceVersion": "rv-new"},
                "spec": {"replicas": 4},
            }
        }
    )
    backend = KubernetesApiBackend(
        api_server="https://cluster.example.test",
        transport=transport,
    )

    with pytest.raises(KubernetesApiConflictError, match="current replicas mismatch"):
        await backend.mutate(
            "scale_workload",
            immutable_json(
                {
                    "name": "api",
                    "namespace": "prod",
                    "replicas": 5,
                    "current_replicas": 3,
                    "resource_version": "rv-before",
                }
            ),
        )

    assert [request[0] for request in transport.requests] == ["GET"]


def _event(reason: str, event_type: str) -> dict[str, JsonValue]:
    return {
        "type": event_type,
        "reason": reason,
        "message": f"{reason} message",
        "count": 1,
        "lastTimestamp": "2026-01-01T00:00:00Z",
        "involvedObject": {"kind": "Deployment", "name": "api"},
    }


def _crash_loop_pod() -> dict[str, JsonValue]:
    return {
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
                }
            ],
        },
    }


@pytest.mark.unit
def test_kubernetes_backends_validate_constructor_inputs() -> None:
    with pytest.raises(ValueError, match="kubectl binary must not be empty"):
        SubprocessKubectlRunner(" ")
    with pytest.raises(ValueError, match="default_namespace must not be empty"):
        KubectlBackend(default_namespace=" ")
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        KubectlBackend(timeout_seconds=0)
    with pytest.raises(ValueError, match="timeout_seconds must be a number"):
        KubectlBackend(timeout_seconds=cast(float, True))
    with pytest.raises(ValueError, match="Kubernetes API server must not be empty"):
        HttpxKubernetesApiTransport(" ")
    with pytest.raises(
        ValueError, match=r"Kubernetes API server must be an absolute http\(s\) URL"
    ):
        HttpxKubernetesApiTransport("kube.example.test")
    with pytest.raises(ValueError, match="Kubernetes API server must not include query"):
        HttpxKubernetesApiTransport("https://kube.example.test?debug=true")
    with pytest.raises(ValueError, match="api_server must not be empty"):
        KubernetesApiBackend(api_server=" ", transport=RecordingKubernetesApiTransport({}))
    with pytest.raises(ValueError, match=r"api_server must be an absolute http\(s\) URL"):
        KubernetesApiBackend(
            api_server="kube.example.test", transport=RecordingKubernetesApiTransport({})
        )
    with pytest.raises(ValueError, match="default_namespace must not be empty"):
        KubernetesApiBackend(
            api_server="https://cluster.example.test",
            default_namespace=" ",
            transport=RecordingKubernetesApiTransport({}),
        )
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        KubernetesApiBackend(
            api_server="https://cluster.example.test",
            timeout_seconds=0,
            transport=RecordingKubernetesApiTransport({}),
        )
