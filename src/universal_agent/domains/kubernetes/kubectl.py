from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol

from universal_agent.core import JsonMapping, JsonValue, immutable_json
from universal_agent.domains.kubernetes import resources as k8s


@dataclass(frozen=True, slots=True)
class KubectlResult:
    args: tuple[str, ...]
    stdout: str
    stderr: str
    returncode: int


class KubectlCommandError(RuntimeError):
    pass


class KubectlCommandRunner(Protocol):
    async def run(
        self,
        args: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
    ) -> KubectlResult: ...


class SubprocessKubectlRunner:
    """Run kubectl without adding a Kubernetes SDK dependency."""

    def __init__(self, binary: str = "kubectl") -> None:
        if not binary.strip():
            raise ValueError("kubectl binary must not be empty")
        self._binary = binary

    async def run(
        self,
        args: tuple[str, ...],
        *,
        timeout_seconds: float | None = None,
    ) -> KubectlResult:
        process = await asyncio.create_subprocess_exec(
            self._binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        result = KubectlResult(
            args=args,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            returncode=process.returncode or 0,
        )
        if result.returncode != 0:
            raise KubectlCommandError(_command_error(result))
        return result


class KubectlBackend:
    """Kubernetes backend implemented with kubectl command invocations.

    This is a Domain Runtime adapter. It satisfies the Kubernetes backend
    protocols used by the Domain without adding kubectl branches to the Kernel.
    """

    def __init__(
        self,
        *,
        runner: KubectlCommandRunner | None = None,
        default_namespace: str = "default",
        context: str | None = None,
        kubeconfig: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not default_namespace.strip():
            raise ValueError("default_namespace must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._runner = runner or SubprocessKubectlRunner()
        self._default_namespace = default_namespace
        self._context = context
        self._kubeconfig = kubeconfig
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
        raise ValueError(f"unsupported kubectl inspection capability: {capability}")

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        if capability == "scale_workload":
            return await self._scale_workload(arguments)
        raise ValueError(f"unsupported kubectl mutation capability: {capability}")

    async def _inspect_cluster(self) -> JsonMapping:
        nodes = await self._run_json("get", "nodes", "-o", "json")
        namespaces = await self._run_json("get", "namespaces", "-o", "json")
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
            arguments, default_kind="deployment", default_namespace=self._default_namespace
        )
        payload = await self._run_json(
            "get", ref.kind, ref.name, "--namespace", ref.namespace, "-o", "json"
        )
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
        payload = await self._run_json(
            "get",
            "pods",
            "--namespace",
            namespace,
            "-l",
            k8s.label_selector(selector_labels),
            "-o",
            "json",
        )
        return k8s.pod_summaries(payload)

    async def _inspect_pod(self, arguments: JsonMapping) -> JsonMapping:
        ref = k8s.resource_ref(
            arguments, default_kind="pod", default_namespace=self._default_namespace
        )
        payload = await self._run_json(
            "get", ref.kind, ref.name, "--namespace", ref.namespace, "-o", "json"
        )
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
            arguments, default_kind="pod", default_namespace=self._default_namespace
        )
        tail_lines = k8s.positive_int(arguments.get("tail_lines"), default=100)
        command = [
            "logs",
            ref.resource,
            "--namespace",
            ref.namespace,
            "--tail",
            str(tail_lines),
        ]
        container = k8s.optional_string(arguments.get("container"))
        if container is not None:
            command.extend(("--container", container))
        result = await self._run(*command)
        lines = result.stdout.splitlines()
        return immutable_json(
            {
                "resource": ref.resource,
                "namespace": ref.namespace,
                "line_count": len(lines),
                "recent_logs": result.stdout,
                "container": container or "",
            }
        )

    async def _inspect_events(self, arguments: JsonMapping) -> JsonMapping:
        ref = k8s.resource_ref(
            arguments, default_kind="deployment", default_namespace=self._default_namespace
        )
        limit = k8s.positive_int(arguments.get("limit"), default=20)
        payload = await self._run_json(
            "get",
            "events",
            "--namespace",
            ref.namespace,
            "--field-selector",
            f"involvedObject.name={ref.name}",
            "-o",
            "json",
        )
        events = [k8s.event_summary(item) for item in k8s.items(payload)]
        recent_events = events[-limit:]
        return immutable_json(
            {
                "resource": ref.resource,
                "namespace": ref.namespace,
                "event_count": len(events),
                "recent_events": recent_events,
            }
        )

    async def _scale_workload(self, arguments: JsonMapping) -> JsonMapping:
        ref = k8s.resource_ref(
            arguments, default_kind="deployment", default_namespace=self._default_namespace
        )
        replicas = k8s.required_int(arguments, "replicas")
        before = await self._run_json(
            "get", ref.kind, ref.name, "--namespace", ref.namespace, "-o", "json"
        )
        metadata = k8s.object_value(before.get("metadata"))
        spec = k8s.object_value(before.get("spec"))
        previous = k8s.optional_int(spec.get("replicas")) or 0
        command = [
            "scale",
            ref.resource,
            "--namespace",
            ref.namespace,
            f"--replicas={replicas}",
        ]
        current = k8s.optional_int(arguments.get("current_replicas"))
        if current is not None:
            command.append(f"--current-replicas={current}")
        resource_version = k8s.optional_resource_version(arguments.get("resource_version"))
        if resource_version is not None:
            command.append(f"--resource-version={resource_version}")
        result = await self._run(*command)
        return immutable_json(
            {
                "resource": ref.resource,
                "namespace": ref.namespace,
                "mutation_applied": True,
                "previous_replicas": previous,
                "replicas": replicas,
                "resource_version": k8s.string_value(metadata.get("resourceVersion")),
                "mutation_id": k8s.stable_mutation_id(result.stdout),
            }
        )

    async def _run_json(self, *args: str) -> dict[str, JsonValue]:
        result = await self._run(*args)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise KubectlCommandError(f"kubectl returned invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise KubectlCommandError("kubectl returned JSON that was not an object")
        return k8s.json_object(payload)

    async def _run(self, *args: str) -> KubectlResult:
        command = self._base_args() + tuple(args)
        return await self._runner.run(command, timeout_seconds=self._timeout_seconds)

    def _base_args(self) -> tuple[str, ...]:
        args: list[str] = []
        if self._context is not None:
            args.extend(("--context", self._context))
        if self._kubeconfig is not None:
            args.extend(("--kubeconfig", self._kubeconfig))
        return tuple(args)


def _command_error(result: KubectlResult) -> str:
    message = result.stderr.strip() or result.stdout.strip() or "kubectl command failed"
    return f"kubectl exited {result.returncode}: {message}"
