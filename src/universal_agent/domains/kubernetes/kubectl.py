from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol, cast

from universal_agent.core import JsonMapping, JsonValue, immutable_json


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


@dataclass(frozen=True, slots=True)
class _ResourceRef:
    kind: str
    name: str
    namespace: str

    @property
    def resource(self) -> str:
        return f"{self.kind}/{self.name}"


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
        node_items = _items(nodes)
        namespace_items = _items(namespaces)
        ready_nodes = sum(1 for node in node_items if _node_ready(node))
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
        ref = _resource_ref(
            arguments, default_kind="deployment", default_namespace=self._default_namespace
        )
        payload = await self._run_json(
            "get", ref.kind, ref.name, "--namespace", ref.namespace, "-o", "json"
        )
        metadata = _object(payload.get("metadata"))
        spec = _object(payload.get("spec"))
        status = _object(payload.get("status"))
        desired = _optional_int(spec.get("replicas"))
        if desired is None:
            desired = 1
        ready = _optional_int(status.get("readyReplicas")) or 0
        available = _optional_int(status.get("availableReplicas"))
        updated = _optional_int(status.get("updatedReplicas")) or 0
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
            "generation": _optional_int(metadata.get("generation")) or 0,
            "observed_generation": _optional_int(status.get("observedGeneration")) or 0,
            "resource_version": _string(metadata.get("resourceVersion")),
            "conditions": _condition_list(status.get("conditions")),
        }
        if not healthy:
            result["root_cause"] = _workload_root_cause(desired, ready, status.get("conditions"))
        return immutable_json(result)

    async def _inspect_pod(self, arguments: JsonMapping) -> JsonMapping:
        ref = _resource_ref(
            arguments, default_kind="pod", default_namespace=self._default_namespace
        )
        payload = await self._run_json(
            "get", ref.kind, ref.name, "--namespace", ref.namespace, "-o", "json"
        )
        metadata = _object(payload.get("metadata"))
        status = _object(payload.get("status"))
        container_statuses = _container_statuses(status.get("containerStatuses"))
        ready = bool(container_statuses) and all(
            _bool(item.get("ready")) for item in container_statuses
        )
        restart_count = sum(
            _optional_int(item.get("restartCount")) or 0 for item in container_statuses
        )
        result: dict[str, JsonValue] = {
            "resource": ref.resource,
            "namespace": ref.namespace,
            "kind": ref.kind,
            "name": ref.name,
            "phase": _string(status.get("phase")),
            "ready": ready,
            "restart_count": restart_count,
            "resource_version": _string(metadata.get("resourceVersion")),
            "containers": [_container_summary(item) for item in container_statuses],
        }
        root_cause = _pod_root_cause(result["phase"], ready, container_statuses)
        if root_cause:
            result["root_cause"] = root_cause
        return immutable_json(result)

    async def _inspect_logs(self, arguments: JsonMapping) -> JsonMapping:
        ref = _resource_ref(
            arguments, default_kind="pod", default_namespace=self._default_namespace
        )
        tail_lines = _positive_int(arguments.get("tail_lines"), default=100)
        command = [
            "logs",
            ref.resource,
            "--namespace",
            ref.namespace,
            "--tail",
            str(tail_lines),
        ]
        container = _optional_string(arguments.get("container"))
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
        ref = _resource_ref(
            arguments, default_kind="deployment", default_namespace=self._default_namespace
        )
        limit = _positive_int(arguments.get("limit"), default=20)
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
        events = [_event_summary(item) for item in _items(payload)]
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
        ref = _resource_ref(
            arguments, default_kind="deployment", default_namespace=self._default_namespace
        )
        replicas = _required_int(arguments, "replicas")
        before = await self._run_json(
            "get", ref.kind, ref.name, "--namespace", ref.namespace, "-o", "json"
        )
        metadata = _object(before.get("metadata"))
        spec = _object(before.get("spec"))
        previous = _optional_int(spec.get("replicas")) or 0
        command = [
            "scale",
            ref.resource,
            "--namespace",
            ref.namespace,
            f"--replicas={replicas}",
        ]
        current = _optional_int(arguments.get("current_replicas"))
        if current is not None:
            command.append(f"--current-replicas={current}")
        resource_version = _optional_resource_version(arguments.get("resource_version"))
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
                "resource_version": _string(metadata.get("resourceVersion")),
                "mutation_id": _stable_mutation_id(result.stdout),
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
        return _json_object(payload)

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


def _resource_ref(
    arguments: JsonMapping,
    *,
    default_kind: str,
    default_namespace: str,
) -> _ResourceRef:
    raw_name = _required_string(arguments, "name")
    kind = _optional_string(arguments.get("kind")) or default_kind
    name = raw_name
    if "/" in raw_name:
        raw_kind, raw_resource_name = raw_name.split("/", 1)
        kind = raw_kind
        name = raw_resource_name
    if not name.strip():
        raise ValueError("Kubernetes resource name must not be empty")
    namespace = _optional_string(arguments.get("namespace")) or default_namespace
    return _ResourceRef(_normal_kind(kind), name, namespace)


def _normal_kind(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "deploy": "deployment",
        "deployments": "deployment",
        "pods": "pod",
        "statefulsets": "statefulset",
        "sts": "statefulset",
        "daemonsets": "daemonset",
        "ds": "daemonset",
        "replicasets": "replicaset",
        "rs": "replicaset",
    }
    return aliases.get(normalized, normalized)


def _items(payload: dict[str, JsonValue]) -> tuple[dict[str, JsonValue], ...]:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return ()
    return tuple(_json_object(item) for item in raw_items if isinstance(item, dict))


def _node_ready(node: dict[str, JsonValue]) -> bool:
    status = _object(node.get("status"))
    for condition in _conditions(status.get("conditions")):
        if condition.get("type") == "Ready":
            return condition.get("status") == "True"
    return False


def _workload_root_cause(
    desired: int,
    ready: int,
    raw_conditions: JsonValue,
) -> str:
    for condition in _conditions(raw_conditions):
        if condition.get("status") == "False":
            reason = _optional_string(condition.get("reason"))
            if reason:
                return _snake(reason)
    if ready < desired:
        return "under_replicated"
    return "unhealthy_workload"


def _pod_root_cause(
    phase: JsonValue,
    ready: bool,
    container_statuses: tuple[dict[str, JsonValue], ...],
) -> str:
    for status in container_statuses:
        state = _object(status.get("state"))
        waiting = _object(state.get("waiting"))
        reason = _optional_string(waiting.get("reason"))
        if reason:
            return _snake(reason)
        terminated = _object(state.get("terminated"))
        terminated_reason = _optional_string(terminated.get("reason"))
        if terminated_reason:
            return _snake(terminated_reason)
    if phase == "Pending":
        return "pending"
    if not ready:
        return "containers_not_ready"
    return ""


def _conditions(value: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        return []
    conditions: list[dict[str, JsonValue]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        raw = _json_object(item)
        conditions.append(
            {
                "type": _string(raw.get("type")),
                "status": _string(raw.get("status")),
                "reason": _string(raw.get("reason")),
                "message": _string(raw.get("message")),
            }
        )
    return conditions


def _condition_list(value: JsonValue) -> list[JsonValue]:
    conditions: list[JsonValue] = []
    for condition in _conditions(value):
        conditions.append(dict(condition))
    return conditions


def _container_statuses(value: JsonValue) -> tuple[dict[str, JsonValue], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(_json_object(item) for item in value if isinstance(item, dict))


def _container_summary(status: dict[str, JsonValue]) -> JsonValue:
    state = _object(status.get("state"))
    return {
        "name": _string(status.get("name")),
        "ready": _bool(status.get("ready")),
        "restart_count": _optional_int(status.get("restartCount")) or 0,
        "state": _container_state_name(state),
        "reason": _container_state_reason(state),
    }


def _container_state_name(state: dict[str, JsonValue]) -> str:
    for name in ("waiting", "running", "terminated"):
        if isinstance(state.get(name), dict):
            return name
    return "unknown"


def _container_state_reason(state: dict[str, JsonValue]) -> str:
    for name in ("waiting", "terminated"):
        details = _object(state.get(name))
        reason = _optional_string(details.get("reason"))
        if reason:
            return reason
    return ""


def _event_summary(item: dict[str, JsonValue]) -> JsonValue:
    involved = _object(item.get("involvedObject"))
    metadata = _object(item.get("metadata"))
    return {
        "type": _string(item.get("type")),
        "reason": _string(item.get("reason")),
        "message": _string(item.get("message")),
        "count": _optional_int(item.get("count")) or 0,
        "first_timestamp": _string(item.get("firstTimestamp")),
        "last_timestamp": _string(item.get("lastTimestamp")),
        "event_time": _string(item.get("eventTime")),
        "creation_timestamp": _string(metadata.get("creationTimestamp")),
        "involved_object_kind": _string(involved.get("kind")),
        "involved_object_name": _string(involved.get("name")),
    }


def _stable_mutation_id(stdout: str) -> str:
    text = stdout.strip()
    if not text:
        return "kubectl-scale"
    return f"kubectl-scale:{text}"


def _required_string(arguments: JsonMapping, key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Kubernetes argument {key} must be a non-empty string")
    return value


def _required_int(arguments: JsonMapping, key: str) -> int:
    value = arguments.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"Kubernetes argument {key} must be an integer")
    return value


def _positive_int(value: JsonValue | None, *, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("Kubernetes integer argument must be positive")
    return value


def _optional_int(value: JsonValue | None) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _optional_resource_version(value: JsonValue | None) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _optional_string(value: JsonValue | None) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _string(value: JsonValue | None) -> str:
    return value if isinstance(value, str) else ""


def _bool(value: JsonValue | None) -> bool:
    return value if isinstance(value, bool) else False


def _object(value: JsonValue | None) -> dict[str, JsonValue]:
    if isinstance(value, dict):
        return _json_object(value)
    return {}


def _json_object(value: object) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], value)


def _snake(value: str) -> str:
    result: list[str] = []
    for index, character in enumerate(value.strip()):
        if character in {"-", " ", "."}:
            result.append("_")
        elif character.isupper() and index > 0:
            result.append("_")
            result.append(character.lower())
        else:
            result.append(character.lower())
    return "".join(result).strip("_")
