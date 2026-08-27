from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from universal_agent.core import JsonMapping, JsonValue


@dataclass(frozen=True, slots=True)
class KubernetesResourceRef:
    kind: str
    name: str
    namespace: str

    @property
    def resource(self) -> str:
        return f"{self.kind}/{self.name}"


def resource_ref(
    arguments: JsonMapping,
    *,
    default_kind: str,
    default_namespace: str,
) -> KubernetesResourceRef:
    raw_name = required_string(arguments, "name")
    kind = optional_string(arguments.get("kind")) or default_kind
    name = raw_name
    if "/" in raw_name:
        raw_kind, raw_resource_name = raw_name.split("/", 1)
        kind = raw_kind
        name = raw_resource_name
    if not name.strip():
        raise ValueError("Kubernetes resource name must not be empty")
    namespace = optional_string(arguments.get("namespace")) or default_namespace
    return KubernetesResourceRef(normal_kind(kind), name, namespace)


def normal_kind(value: str) -> str:
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


def items(payload: dict[str, JsonValue]) -> tuple[dict[str, JsonValue], ...]:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return ()
    return tuple(json_object(item) for item in raw_items if isinstance(item, dict))


def node_ready(node: dict[str, JsonValue]) -> bool:
    status = object_value(node.get("status"))
    for condition in conditions(status.get("conditions")):
        if condition.get("type") == "Ready":
            return condition.get("status") == "True"
    return False


def workload_root_cause(
    desired: int,
    ready: int,
    raw_conditions: JsonValue,
) -> str:
    for condition in conditions(raw_conditions):
        if condition.get("status") == "False":
            reason = optional_string(condition.get("reason"))
            if reason:
                return snake_case(reason)
    if ready < desired:
        return "under_replicated"
    return "unhealthy_workload"


def pod_root_cause(
    phase: JsonValue,
    ready: bool,
    container_statuses: tuple[dict[str, JsonValue], ...],
) -> str:
    for status in container_statuses:
        state = object_value(status.get("state"))
        waiting = object_value(state.get("waiting"))
        reason = optional_string(waiting.get("reason"))
        if reason:
            return snake_case(reason)
        terminated = object_value(state.get("terminated"))
        terminated_reason = optional_string(terminated.get("reason"))
        if terminated_reason:
            return snake_case(terminated_reason)
    if phase == "Pending":
        return "pending"
    if not ready:
        return "containers_not_ready"
    return ""


def conditions(value: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        return []
    parsed: list[dict[str, JsonValue]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        raw = json_object(item)
        parsed.append(
            {
                "type": string_value(raw.get("type")),
                "status": string_value(raw.get("status")),
                "reason": string_value(raw.get("reason")),
                "message": string_value(raw.get("message")),
            }
        )
    return parsed


def condition_list(value: JsonValue) -> list[JsonValue]:
    return [dict(condition) for condition in conditions(value)]


def container_statuses(value: JsonValue) -> tuple[dict[str, JsonValue], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(json_object(item) for item in value if isinstance(item, dict))


def container_summary(status: dict[str, JsonValue]) -> JsonValue:
    state = object_value(status.get("state"))
    return {
        "name": string_value(status.get("name")),
        "ready": bool_value(status.get("ready")),
        "restart_count": optional_int(status.get("restartCount")) or 0,
        "state": container_state_name(state),
        "reason": container_state_reason(state),
    }


def container_state_name(state: dict[str, JsonValue]) -> str:
    for name in ("waiting", "running", "terminated"):
        if isinstance(state.get(name), dict):
            return name
    return "unknown"


def container_state_reason(state: dict[str, JsonValue]) -> str:
    for name in ("waiting", "terminated"):
        details = object_value(state.get(name))
        reason = optional_string(details.get("reason"))
        if reason:
            return reason
    return ""


def event_summary(item: dict[str, JsonValue]) -> JsonValue:
    involved = object_value(item.get("involvedObject"))
    metadata = object_value(item.get("metadata"))
    return {
        "type": string_value(item.get("type")),
        "reason": string_value(item.get("reason")),
        "message": string_value(item.get("message")),
        "count": optional_int(item.get("count")) or 0,
        "first_timestamp": string_value(item.get("firstTimestamp")),
        "last_timestamp": string_value(item.get("lastTimestamp")),
        "event_time": string_value(item.get("eventTime")),
        "creation_timestamp": string_value(metadata.get("creationTimestamp")),
        "involved_object_kind": string_value(involved.get("kind")),
        "involved_object_name": string_value(involved.get("name")),
    }


def stable_mutation_id(stdout: str) -> str:
    text = stdout.strip()
    if not text:
        return "kubectl-scale"
    return f"kubectl-scale:{text}"


def required_string(arguments: JsonMapping, key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Kubernetes argument {key} must be a non-empty string")
    return value


def required_int(arguments: JsonMapping, key: str) -> int:
    value = arguments.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"Kubernetes argument {key} must be an integer")
    return value


def positive_int(value: JsonValue | None, *, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("Kubernetes integer argument must be positive")
    return value


def optional_int(value: JsonValue | None) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def optional_resource_version(value: JsonValue | None) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def optional_string(value: JsonValue | None) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def string_value(value: JsonValue | None) -> str:
    return value if isinstance(value, str) else ""


def bool_value(value: JsonValue | None) -> bool:
    return value if isinstance(value, bool) else False


def object_value(value: JsonValue | None) -> dict[str, JsonValue]:
    if isinstance(value, dict):
        return json_object(value)
    return {}


def json_object(value: object) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], value)


def snake_case(value: str) -> str:
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
