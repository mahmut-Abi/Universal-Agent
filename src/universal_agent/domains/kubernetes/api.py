from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter

from universal_agent.core import JsonCodecError, JsonMapping, JsonValue, immutable_json, loads_json
from universal_agent.core.config_validation import (
    PydanticJsonValue,
    parse_non_empty_string,
    parse_positive_float,
)
from universal_agent.domains.kubernetes import resources as k8s

_JSON_VALUE_ADAPTER: TypeAdapter[PydanticJsonValue] = TypeAdapter(PydanticJsonValue)


@dataclass(frozen=True, slots=True)
class KubernetesApiResponse:
    status_code: int
    payload: JsonValue = None
    text: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)


class KubernetesApiError(RuntimeError):
    pass


class KubernetesApiConflictError(KubernetesApiError):
    pass


class KubernetesApiTransport(Protocol):
    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: JsonMapping | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> KubernetesApiResponse: ...


class HttpxKubernetesApiTransport:
    """Async HTTP transport for Kubernetes API requests."""

    def __init__(
        self,
        api_server: str,
        *,
        bearer_token: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parse_non_empty_string(api_server, "Kubernetes API server")
        self._api_server = api_server.rstrip("/")
        self._bearer_token = bearer_token
        self._client = client

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
        if self._client is not None:
            return await self._request_with_client(
                self._client,
                method,
                path,
                query=dict(query or {}),
                body=None if body is None else dict(body),
                headers=dict(headers or {}),
                timeout_seconds=timeout_seconds,
            )
        async with httpx.AsyncClient() as client:
            return await self._request_with_client(
                client,
                method,
                path,
                query=dict(query or {}),
                body=None if body is None else dict(body),
                headers=dict(headers or {}),
                timeout_seconds=timeout_seconds,
            )

    async def _request_with_client(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        query: dict[str, str],
        body: dict[str, JsonValue] | None,
        headers: dict[str, str],
        timeout_seconds: float | None,
    ) -> KubernetesApiResponse:
        url = self._api_server + path
        request_headers = {"accept": "application/json", **headers}
        if self._bearer_token:
            request_headers["authorization"] = f"Bearer {self._bearer_token}"
        try:
            response = await client.request(
                method,
                url,
                params=query,
                json=body,
                headers=request_headers,
                timeout=timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise KubernetesApiError(f"Kubernetes API request failed: {exc}") from exc
        text = response.text
        return KubernetesApiResponse(
            response.status_code,
            _decode_optional_json(text),
            text,
            dict(response.headers.items()),
        )


class UrllibKubernetesApiTransport(HttpxKubernetesApiTransport):
    """Backward-compatible name for the default async Kubernetes API transport."""


class KubernetesApiBackend:
    """Kubernetes backend implemented through the Kubernetes HTTP API."""

    def __init__(
        self,
        *,
        api_server: str,
        bearer_token: str | None = None,
        transport: KubernetesApiTransport | None = None,
        default_namespace: str = "default",
        timeout_seconds: float = 10.0,
    ) -> None:
        parse_non_empty_string(api_server, "api_server")
        parse_non_empty_string(default_namespace, "default_namespace")
        parse_positive_float(timeout_seconds, "timeout_seconds")
        self._transport = transport or HttpxKubernetesApiTransport(
            api_server,
            bearer_token=bearer_token,
        )
        self._default_namespace = default_namespace
        self._timeout_seconds = timeout_seconds

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        if capability == "inspect_cluster":
            return await self._inspect_cluster()
        if capability == "inspect_workload":
            return await self._inspect_workload(arguments)
        if capability == "inspect_pod":
            return await self._inspect_pod(arguments)
        if capability == "inspect_logs":
            return await self._inspect_logs(arguments)
        if capability == "inspect_events":
            return await self._inspect_events(arguments)
        raise ValueError(f"unsupported Kubernetes API inspection capability: {capability}")

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        if capability == "scale_workload":
            return await self._scale_workload(arguments)
        raise ValueError(f"unsupported Kubernetes API mutation capability: {capability}")

    async def _inspect_cluster(self) -> JsonMapping:
        nodes = await self._request_json("GET", "/api/v1/nodes")
        namespaces = await self._request_json("GET", "/api/v1/namespaces")
        node_items = k8s.items(nodes)
        namespace_items = k8s.items(namespaces)
        ready_nodes = sum(1 for node in node_items if k8s.node_ready(node))
        return immutable_json(
            {
                "resource": "cluster",
                "node_count": len(node_items),
                "ready_node_count": ready_nodes,
                "namespace_count": len(namespace_items),
                "healthy": bool(node_items) and ready_nodes == len(node_items),
            }
        )

    async def _inspect_workload(self, arguments: JsonMapping) -> JsonMapping:
        ref = k8s.resource_ref(
            arguments,
            default_kind="deployment",
            default_namespace=self._default_namespace,
        )
        payload = await self._request_json("GET", _workload_path(ref.kind, ref.namespace, ref.name))
        metadata = k8s.object_value(payload.get("metadata"))
        spec = k8s.object_value(payload.get("spec"))
        status = k8s.object_value(payload.get("status"))
        desired = k8s.optional_int(spec.get("replicas"))
        if desired is None:
            desired = 1
        selector_labels = k8s.selector_labels(spec)
        pods = await self._workload_pods(ref.namespace, selector_labels)
        ready = k8s.optional_int(status.get("readyReplicas")) or 0
        available = k8s.optional_int(status.get("availableReplicas"))
        updated = k8s.optional_int(status.get("updatedReplicas")) or 0
        healthy = ready >= desired and (available is None or available >= desired)
        result: dict[str, JsonValue] = {
            "resource": ref.resource,
            "namespace": ref.namespace,
            "kind": ref.kind,
            "name": ref.name,
            "healthy": healthy,
            "desired_replicas": desired,
            "ready_replicas": ready,
            "available_replicas": available or 0,
            "updated_replicas": updated,
            "generation": k8s.optional_int(metadata.get("generation")) or 0,
            "observed_generation": k8s.optional_int(status.get("observedGeneration")) or 0,
            "resource_version": k8s.string_value(metadata.get("resourceVersion")),
            "conditions": k8s.condition_list(status.get("conditions")),
        }
        if selector_labels:
            result["selector_labels"] = {key: value for key, value in selector_labels.items()}
            result["pod_count"] = len(pods)
            result["ready_pod_count"] = k8s.ready_pod_count(pods)
            result["pods"] = pods
        if not healthy:
            result["root_cause"] = k8s.pod_related_root_cause(pods) or k8s.workload_root_cause(
                desired, ready, status.get("conditions")
            )
        return immutable_json(result)

    async def _workload_pods(
        self,
        namespace: str,
        selector_labels: dict[str, str],
    ) -> list[JsonValue]:
        if not selector_labels:
            return []
        payload = await self._request_json(
            "GET",
            _pods_path(namespace),
            query={"labelSelector": k8s.label_selector(selector_labels)},
        )
        return k8s.pod_summaries(payload)

    async def _inspect_pod(self, arguments: JsonMapping) -> JsonMapping:
        ref = k8s.resource_ref(
            arguments,
            default_kind="pod",
            default_namespace=self._default_namespace,
        )
        payload = await self._request_json("GET", _pod_path(ref.namespace, ref.name))
        metadata = k8s.object_value(payload.get("metadata"))
        status = k8s.object_value(payload.get("status"))
        container_statuses = k8s.container_statuses(status.get("containerStatuses"))
        ready = bool(container_statuses) and all(item.ready for item in container_statuses)
        restart_count = sum(item.restart_count for item in container_statuses)
        result: dict[str, JsonValue] = {
            "resource": ref.resource,
            "namespace": ref.namespace,
            "kind": ref.kind,
            "name": ref.name,
            "phase": k8s.string_value(status.get("phase")),
            "ready": ready,
            "restart_count": restart_count,
            "resource_version": k8s.string_value(metadata.get("resourceVersion")),
            "containers": [k8s.container_summary(item) for item in container_statuses],
        }
        root_cause = k8s.pod_root_cause(result["phase"], ready, container_statuses)
        if root_cause:
            result["root_cause"] = root_cause
        return immutable_json(result)

    async def _inspect_logs(self, arguments: JsonMapping) -> JsonMapping:
        ref = k8s.resource_ref(
            arguments,
            default_kind="pod",
            default_namespace=self._default_namespace,
        )
        tail_lines = k8s.positive_int(arguments.get("tail_lines"), default=100)
        query = {"tailLines": str(tail_lines)}
        container = k8s.optional_string(arguments.get("container"))
        if container is not None:
            query["container"] = container
        response = await self._request("GET", _pod_log_path(ref.namespace, ref.name), query=query)
        lines = response.text.splitlines()
        return immutable_json(
            {
                "resource": ref.resource,
                "namespace": ref.namespace,
                "line_count": len(lines),
                "recent_logs": response.text,
                "container": container or "",
            }
        )

    async def _inspect_events(self, arguments: JsonMapping) -> JsonMapping:
        ref = k8s.resource_ref(
            arguments,
            default_kind="deployment",
            default_namespace=self._default_namespace,
        )
        limit = k8s.positive_int(arguments.get("limit"), default=20)
        payload = await self._request_json(
            "GET",
            _events_path(ref.namespace),
            query={"fieldSelector": f"involvedObject.name={ref.name}"},
        )
        events = [k8s.event_summary(item) for item in k8s.items(payload)]
        return immutable_json(
            {
                "resource": ref.resource,
                "namespace": ref.namespace,
                "event_count": len(events),
                "recent_events": events[-limit:],
            }
        )

    async def _scale_workload(self, arguments: JsonMapping) -> JsonMapping:
        ref = k8s.resource_ref(
            arguments,
            default_kind="deployment",
            default_namespace=self._default_namespace,
        )
        replicas = k8s.required_int(arguments, "replicas")
        before = await self._request_json("GET", _workload_path(ref.kind, ref.namespace, ref.name))
        metadata = k8s.object_value(before.get("metadata"))
        spec = k8s.object_value(before.get("spec"))
        previous = k8s.optional_int(spec.get("replicas")) or 0
        current = k8s.optional_int(arguments.get("current_replicas"))
        if current is not None and current != previous:
            raise KubernetesApiConflictError(
                f"current replicas mismatch: expected {current}, observed {previous}"
            )
        observed_resource_version = k8s.string_value(metadata.get("resourceVersion"))
        expected_resource_version = k8s.optional_resource_version(arguments.get("resource_version"))
        if (
            expected_resource_version is not None
            and observed_resource_version
            and expected_resource_version != observed_resource_version
        ):
            raise KubernetesApiConflictError(
                "resource version mismatch: "
                f"expected {expected_resource_version}, observed {observed_resource_version}"
            )
        body: dict[str, JsonValue] = {"spec": {"replicas": replicas}}
        if expected_resource_version is not None:
            body["metadata"] = {"resourceVersion": expected_resource_version}
        response = await self._request_json(
            "PATCH",
            _workload_path(ref.kind, ref.namespace, ref.name, subresource="scale"),
            body=immutable_json(body),
            headers={"content-type": "application/merge-patch+json"},
        )
        response_metadata = k8s.object_value(response.get("metadata"))
        return immutable_json(
            {
                "resource": ref.resource,
                "namespace": ref.namespace,
                "mutation_applied": True,
                "previous_replicas": previous,
                "replicas": replicas,
                "resource_version": observed_resource_version,
                "mutation_id": k8s.stable_mutation_id(
                    k8s.string_value(response_metadata.get("resourceVersion"))
                    or f"{ref.resource} scaled to {replicas}"
                ),
            }
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: JsonMapping | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, JsonValue]:
        response = await self._request(method, path, query=query, body=body, headers=headers)
        payload = response.payload
        if payload is None and response.text:
            payload = _decode_optional_json(response.text)
        if not isinstance(payload, dict):
            raise KubernetesApiError("Kubernetes API returned JSON that was not an object")
        return k8s.json_object(payload)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: JsonMapping | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> KubernetesApiResponse:
        response = await self._transport.request(
            method,
            path,
            query=query,
            body=body,
            headers=headers,
            timeout_seconds=self._timeout_seconds,
        )
        if response.status_code < 200 or response.status_code >= 300:
            message = response.text.strip() or f"HTTP {response.status_code}"
            raise KubernetesApiError(f"Kubernetes API request failed: {message}")
        return response


def _workload_path(kind: str, namespace: str, name: str, *, subresource: str = "") -> str:
    suffix = "" if not subresource else "/" + _quote_path_part(subresource)
    return (
        f"/apis/apps/v1/namespaces/{_quote_path_part(namespace)}"
        f"/{_workload_plural(kind)}/{_quote_path_part(name)}{suffix}"
    )


def _pod_path(namespace: str, name: str) -> str:
    return f"/api/v1/namespaces/{_quote_path_part(namespace)}/pods/{_quote_path_part(name)}"


def _pods_path(namespace: str) -> str:
    return f"/api/v1/namespaces/{_quote_path_part(namespace)}/pods"


def _pod_log_path(namespace: str, name: str) -> str:
    return _pod_path(namespace, name) + "/log"


def _events_path(namespace: str) -> str:
    return f"/api/v1/namespaces/{_quote_path_part(namespace)}/events"


def _workload_plural(kind: str) -> str:
    plurals = {
        "deployment": "deployments",
        "statefulset": "statefulsets",
        "daemonset": "daemonsets",
        "replicaset": "replicasets",
    }
    try:
        return plurals[kind]
    except KeyError as exc:
        raise ValueError(f"unsupported Kubernetes API workload kind: {kind}") from exc


def _quote_path_part(value: str) -> str:
    return quote(value, safe="")


def _decode_optional_json(text: str) -> JsonValue:
    if not text.strip():
        return None
    try:
        loaded = loads_json(text)
    except JsonCodecError:
        return None
    return _JSON_VALUE_ADAPTER.validate_python(loaded, strict=True)
