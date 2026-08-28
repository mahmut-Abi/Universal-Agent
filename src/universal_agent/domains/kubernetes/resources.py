from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated

from pydantic import BeforeValidator, Field, TypeAdapter
from pydantic.alias_generators import to_snake

from universal_agent.core import JsonMapping, JsonValue
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    parse_int,
    parse_json_object,
    parse_non_empty_string,
    parse_optional_int,
    parse_positive_int,
)


def _text_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _bool_or_false(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _int_or_zero(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _object_or_empty(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _object_items_or_empty(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


_JsonText = Annotated[str, BeforeValidator(_text_or_empty)]
_JsonBool = Annotated[bool, BeforeValidator(_bool_or_false)]
_JsonInt = Annotated[int, BeforeValidator(_int_or_zero)]
_JsonObject = Annotated[dict[str, PydanticJsonValue], BeforeValidator(_object_or_empty)]
_JsonObjects = Annotated[
    list[dict[str, PydanticJsonValue]],
    BeforeValidator(_object_items_or_empty),
]


class _ObjectListPayload(ConfigPayload):
    items: _JsonObjects = Field(default_factory=list)


class _MetadataPayload(ConfigPayload):
    name: _JsonText = ""
    namespace: _JsonText = ""
    resource_version: _JsonText = Field(default="", alias="resourceVersion")
    creation_timestamp: _JsonText = Field(default="", alias="creationTimestamp")
    generation: _JsonInt = 0


class _ConditionPayload(ConfigPayload):
    type: _JsonText = ""
    status: _JsonText = ""
    reason: _JsonText = ""
    message: _JsonText = ""


_Conditions = Annotated[
    list[_ConditionPayload],
    BeforeValidator(_object_items_or_empty),
]


class _ConditionsPayload(ConfigPayload):
    conditions: _Conditions = Field(default_factory=list)


_ConditionStatus = Annotated[_ConditionsPayload, BeforeValidator(_object_or_empty)]


class _SelectorPayload(ConfigPayload):
    match_labels: _JsonObject = Field(default_factory=dict, alias="matchLabels")


_Selector = Annotated[_SelectorPayload, BeforeValidator(_object_or_empty)]


class _WorkloadSpecPayload(ConfigPayload):
    selector: _Selector = Field(default_factory=_SelectorPayload)


class _ContainerStatePayload(ConfigPayload):
    waiting: _JsonObject = Field(default_factory=dict)
    running: _JsonObject = Field(default_factory=dict)
    terminated: _JsonObject = Field(default_factory=dict)


_ContainerState = Annotated[_ContainerStatePayload, BeforeValidator(_object_or_empty)]


class _ContainerStatusPayload(ConfigPayload):
    name: _JsonText = ""
    ready: _JsonBool = False
    restart_count: _JsonInt = Field(default=0, alias="restartCount")
    state: _ContainerState = Field(default_factory=_ContainerStatePayload)


_ContainerStatuses = Annotated[
    list[_ContainerStatusPayload],
    BeforeValidator(_object_items_or_empty),
]


class _PodStatusPayload(ConfigPayload):
    phase: _JsonText = ""
    container_statuses: _ContainerStatuses = Field(default_factory=list, alias="containerStatuses")


_Metadata = Annotated[_MetadataPayload, BeforeValidator(_object_or_empty)]
_PodStatus = Annotated[_PodStatusPayload, BeforeValidator(_object_or_empty)]


class _PodPayload(ConfigPayload):
    metadata: _Metadata = Field(default_factory=_MetadataPayload)
    status: _PodStatus = Field(default_factory=_PodStatusPayload)


class _NodePayload(ConfigPayload):
    status: _ConditionStatus = Field(default_factory=_ConditionsPayload)


class _EventObjectPayload(ConfigPayload):
    kind: _JsonText = ""
    name: _JsonText = ""


_EventObject = Annotated[_EventObjectPayload, BeforeValidator(_object_or_empty)]


class _EventPayload(ConfigPayload):
    type: _JsonText = ""
    reason: _JsonText = ""
    message: _JsonText = ""
    count: _JsonInt = 0
    first_timestamp: _JsonText = Field(default="", alias="firstTimestamp")
    last_timestamp: _JsonText = Field(default="", alias="lastTimestamp")
    event_time: _JsonText = Field(default="", alias="eventTime")
    metadata: _Metadata = Field(default_factory=_MetadataPayload)
    involved_object: _EventObject = Field(
        default_factory=_EventObjectPayload,
        alias="involvedObject",
    )


_OBJECT_LIST_ADAPTER: TypeAdapter[_ObjectListPayload] = TypeAdapter(_ObjectListPayload)
_CONDITIONS_ADAPTER: TypeAdapter[list[_ConditionPayload]] = TypeAdapter(list[_ConditionPayload])


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
    parsed = _OBJECT_LIST_ADAPTER.validate_python(payload, strict=True)
    return tuple(json_object(item) for item in parsed.items)


def node_ready(node: dict[str, JsonValue]) -> bool:
    payload = _NodePayload.model_validate(node)
    for condition in payload.status.conditions:
        if condition.type == "Ready":
            return condition.status == "True"
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


def selector_labels(spec: dict[str, JsonValue]) -> dict[str, str]:
    payload = _WorkloadSpecPayload.model_validate(spec)
    labels: dict[str, str] = {}
    for key, value in payload.selector.match_labels.items():
        if isinstance(value, str) and value.strip():
            labels[key] = value
    return labels


def label_selector(labels: Mapping[str, str]) -> str:
    return ",".join(f"{key}={value}" for key, value in sorted(labels.items()))


def pod_summaries(payload: dict[str, JsonValue]) -> list[JsonValue]:
    return [pod_summary(item) for item in items(payload)]


def pod_summary(pod: dict[str, JsonValue]) -> JsonValue:
    payload = _PodPayload.model_validate(pod)
    metadata = payload.metadata
    status = payload.status
    container_items = tuple(status.container_statuses)
    ready = bool(container_items) and all(item.ready for item in container_items)
    restart_count = sum(item.restart_count for item in container_items)
    summary: dict[str, JsonValue] = {
        "resource": f"pod/{metadata.name}",
        "namespace": metadata.namespace,
        "name": metadata.name,
        "phase": status.phase,
        "ready": ready,
        "restart_count": restart_count,
        "resource_version": metadata.resource_version,
        "containers": [container_summary(item) for item in container_items],
    }
    root_cause = pod_root_cause(summary["phase"], ready, container_items)
    if root_cause:
        summary["root_cause"] = root_cause
    return summary


def ready_pod_count(pods: list[JsonValue]) -> int:
    return sum(1 for pod in pods if isinstance(pod, dict) and pod.get("ready") is True)


def pod_related_root_cause(pods: list[JsonValue]) -> str | None:
    for pod in pods:
        if not isinstance(pod, dict):
            continue
        root_cause = optional_string(pod.get("root_cause"))
        if root_cause:
            return root_cause
    return None


def pod_root_cause(
    phase: JsonValue,
    ready: bool,
    container_statuses: tuple[_ContainerStatusPayload, ...],
) -> str:
    for status in container_statuses:
        state = status.state
        reason = optional_string(state.waiting.get("reason"))
        if reason:
            return snake_case(reason)
        terminated_reason = optional_string(state.terminated.get("reason"))
        if terminated_reason:
            return snake_case(terminated_reason)
    if phase == "Pending":
        return "pending"
    if not ready:
        return "containers_not_ready"
    return ""


def conditions(value: JsonValue) -> list[dict[str, JsonValue]]:
    parsed = _CONDITIONS_ADAPTER.validate_python(_object_items_or_empty(value), strict=True)
    return [
        {
            "type": condition.type,
            "status": condition.status,
            "reason": condition.reason,
            "message": condition.message,
        }
        for condition in parsed
    ]


def condition_list(value: JsonValue) -> list[JsonValue]:
    return [dict(condition) for condition in conditions(value)]


def container_statuses(value: JsonValue) -> tuple[_ContainerStatusPayload, ...]:
    payload = _PodStatusPayload.model_validate({"containerStatuses": value})
    return tuple(payload.container_statuses)


def container_summary(status: _ContainerStatusPayload) -> JsonValue:
    state = status.state
    return {
        "name": status.name,
        "ready": status.ready,
        "restart_count": status.restart_count,
        "state": container_state_name(state),
        "reason": container_state_reason(state),
    }


def container_state_name(state: _ContainerStatePayload) -> str:
    for name in ("waiting", "running", "terminated"):
        if getattr(state, name):
            return name
    return "unknown"


def container_state_reason(state: _ContainerStatePayload) -> str:
    for name in ("waiting", "terminated"):
        details = getattr(state, name)
        reason = optional_string(details.get("reason"))
        if reason:
            return reason
    return ""


def event_summary(item: dict[str, JsonValue]) -> JsonValue:
    event = _EventPayload.model_validate(item)
    return {
        "type": event.type,
        "reason": event.reason,
        "message": event.message,
        "count": event.count,
        "first_timestamp": event.first_timestamp,
        "last_timestamp": event.last_timestamp,
        "event_time": event.event_time,
        "creation_timestamp": event.metadata.creation_timestamp,
        "involved_object_kind": event.involved_object.kind,
        "involved_object_name": event.involved_object.name,
    }


def stable_mutation_id(stdout: str) -> str:
    text = stdout.strip()
    if not text:
        return "kubectl-scale"
    return f"kubectl-scale:{text}"


def required_string(arguments: JsonMapping, key: str) -> str:
    try:
        return parse_non_empty_string(
            arguments.get(key),
            f"Kubernetes argument {key}",
            empty_template=f"Kubernetes argument {key} must be a non-empty string",
        )
    except ValueError as exc:
        raise ValueError(f"Kubernetes argument {key} must be a non-empty string") from exc


def required_int(arguments: JsonMapping, key: str) -> int:
    try:
        return parse_int(arguments.get(key), f"Kubernetes argument {key}")
    except ValueError as exc:
        raise ValueError(f"Kubernetes argument {key} must be an integer") from exc


def positive_int(value: JsonValue | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        return parse_positive_int(value, "Kubernetes integer argument")
    except ValueError as exc:
        raise ValueError("Kubernetes integer argument must be positive") from exc


def optional_int(value: JsonValue | None) -> int | None:
    try:
        return parse_optional_int(value, "Kubernetes optional integer")
    except ValueError:
        return None


def optional_resource_version(value: JsonValue | None) -> str | None:
    text = optional_string(value)
    if text is not None:
        return text
    parsed_int = optional_int(value)
    return None if parsed_int is None else str(parsed_int)


def optional_string(value: JsonValue | None) -> str | None:
    try:
        return parse_non_empty_string(value, "Kubernetes optional string")
    except ValueError:
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
    return dict(parse_json_object(value, "Kubernetes JSON object"))


def snake_case(value: str) -> str:
    normalized = value.strip().replace("-", "_").replace(" ", "_").replace(".", "_")
    return to_snake(normalized).strip("_")
