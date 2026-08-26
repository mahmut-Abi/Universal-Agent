from __future__ import annotations

from collections.abc import Callable, Sequence
from urllib.parse import parse_qs, urlsplit

from universal_agent.core import ActionId, EventId, JsonMapping, SessionId, TaskId
from universal_agent.distributed import (
    DistributedLockLeaseId,
    DistributedLockOwnerId,
    WorkerId,
    WorkItemId,
)
from universal_agent.service import DomainPackageView, DomainView
from universal_agent.web import (
    WebCatalogPage,
    WebConsoleSnapshot,
    render_web_evidence_explorer,
    render_web_session_detail,
    render_web_world_model_explorer,
)


def _normalize_path(path: str) -> str:
    normalized = urlsplit(path).path
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    normalized = normalized.rstrip("/")
    return normalized or "/"


def _optional_event_cursor(path: str) -> EventId | None:
    value = _optional_query_value(path, "after")
    if value is None:
        return None
    return EventId(value)


def _optional_session_cursor(path: str) -> SessionId | None:
    value = _optional_query_value(path, "after")
    if value is None:
        return None
    return SessionId(value)


def _optional_session_id_query(path: str) -> SessionId | None:
    value = _optional_query_value(path, "session_id")
    if value is None:
        return None
    return SessionId(value)


def _optional_bool_query(path: str, key: str) -> bool | None:
    value = _optional_query_value(path, key)
    if value is None:
        return None
    normalized = value.lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"{key} must be a boolean")


def _optional_float_query(
    path: str,
    key: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = _optional_query_value(path, key)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{key} must be between {minimum:g} and {maximum:g}")
    return parsed


def _optional_positive_int_query(path: str, key: str) -> int | None:
    value = _optional_query_value(path, key)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{key} must be a positive integer")
    return parsed


def _optional_query_value(path: str, key: str) -> str | None:
    values = parse_qs(urlsplit(path).query, keep_blank_values=True).get(key)
    if values is None:
        return None
    if len(values) != 1:
        raise ValueError(f"{key} must be specified once")
    value = values[0]
    if not value.strip():
        raise ValueError(f"{key} must not be empty")
    return value


def _session_route(path: str) -> tuple[SessionId | None, str]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if len(segments) == 3 and segments[:2] == ("v1", "sessions"):
        return SessionId(segments[2]), ""
    if len(segments) == 4 and segments[:2] == ("v1", "sessions"):
        return SessionId(segments[2]), segments[3]
    if len(segments) == 5 and segments[:2] == ("v1", "sessions"):
        return SessionId(segments[2]), f"{segments[3]}/{segments[4]}"
    return None, ""


def _console_session_route(path: str) -> tuple[SessionId | None, str]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if len(segments) == 3 and segments[:2] == ("console", "sessions") and segments[2].strip():
        return SessionId(segments[2]), ""
    if len(segments) == 4 and segments[:2] == ("console", "sessions") and segments[2].strip():
        return SessionId(segments[2]), segments[3]
    return None, ""


def _console_catalog_route(path: str) -> WebCatalogPage | None:
    segments = tuple(segment for segment in path.split("/") if segment)
    if len(segments) != 2 or segments[0] != "console":
        return None
    try:
        return WebCatalogPage(segments[1])
    except ValueError:
        return None


def _console_domain_route(path: str) -> tuple[str | None, str | None]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if len(segments) == 3 and segments[:2] == ("console", "domains") and segments[2].strip():
        return segments[2], None
    if (
        len(segments) == 4
        and segments[:2] == ("console", "domains")
        and segments[2].strip()
        and segments[3].strip()
    ):
        return segments[2], segments[3]
    return None, None


def _console_domain_package_route(path: str) -> tuple[str | None, str | None]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if (
        len(segments) == 3
        and segments[:2] == ("console", "domain-packages")
        and segments[2].strip()
    ):
        return segments[2], None
    if (
        len(segments) == 4
        and segments[:2] == ("console", "domain-packages")
        and segments[2].strip()
        and segments[3].strip()
    ):
        return segments[2], segments[3]
    return None, None


def _distributed_lock_lease_route(path: str) -> tuple[DistributedLockLeaseId | None, str]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if (
        len(segments) == 5
        and segments[:3] == ("v1", "distributed", "lock-leases")
        and segments[3].strip()
        and segments[4].strip()
    ):
        return DistributedLockLeaseId(segments[3]), segments[4]
    return None, ""


def _distributed_lock_owner_id(body: JsonMapping) -> DistributedLockOwnerId:
    return DistributedLockOwnerId(
        _distributed_required_string(
            body,
            key="owner_id",
            field_name="distributed lock owner_id",
        )
    )


def _distributed_lock_ttl_seconds(body: JsonMapping) -> float:
    value = body.get("ttl_seconds", 30.0)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("distributed lock ttl_seconds must be a positive number")
    ttl_seconds = float(value)
    if ttl_seconds <= 0:
        raise ValueError("distributed lock ttl_seconds must be a positive number")
    return ttl_seconds


def _distributed_required_string(
    body: JsonMapping,
    *,
    key: str,
    field_name: str,
) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _distributed_worker_action_route(path: str) -> tuple[WorkerId | None, str]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if (
        len(segments) == 5
        and segments[:3] == ("v1", "distributed", "workers")
        and segments[3].strip()
        and segments[4].strip()
    ):
        return WorkerId(segments[3]), segments[4]
    return None, ""


def _distributed_worker_capabilities(body: JsonMapping) -> tuple[str, ...]:
    value = body.get("capabilities", ())
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError("distributed worker capabilities must be a list of strings")
    capabilities: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"distributed worker capabilities[{index}] must be a non-empty string")
        capabilities.append(item)
    return tuple(capabilities)


def _distributed_ttl_seconds(body: JsonMapping) -> float:
    value = body.get("ttl_seconds", 30.0)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("distributed worker ttl_seconds must be a positive number")
    ttl_seconds = float(value)
    if ttl_seconds <= 0:
        raise ValueError("distributed worker ttl_seconds must be a positive number")
    return ttl_seconds


def _distributed_worker_run_seconds(
    body: JsonMapping,
    *,
    field_name: str,
    default: float,
) -> float:
    value = body.get(field_name, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"distributed worker {field_name} must be a positive number")
    seconds = float(value)
    if seconds <= 0:
        raise ValueError(f"distributed worker {field_name} must be a positive number")
    return seconds


def _distributed_worker_run_optional_seconds(
    body: JsonMapping,
    *,
    field_name: str,
) -> float | None:
    if field_name not in body:
        return None
    return _distributed_worker_run_seconds(body, field_name=field_name, default=30.0)


def _distributed_worker_run_max_items(body: JsonMapping) -> int:
    value = body.get("max_items", 1)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("distributed worker max_items must be a positive integer")
    if value < 1:
        raise ValueError("distributed worker max_items must be a positive integer")
    return value


def _distributed_reason(
    body: JsonMapping,
    *,
    default: str,
    field_name: str,
) -> str:
    value = body.get("reason", default)
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _distributed_schedule_session_route(path: str) -> SessionId | None:
    segments = tuple(segment for segment in path.split("/") if segment)
    if (
        len(segments) == 5
        and segments[:3] == ("v1", "distributed", "sessions")
        and segments[3].strip()
        and segments[4] == "schedule"
    ):
        return SessionId(segments[3])
    return None


def _distributed_schedule_task_route(path: str) -> tuple[SessionId | None, TaskId | None]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if (
        len(segments) == 7
        and segments[:3] == ("v1", "distributed", "sessions")
        and segments[3].strip()
        and segments[4] == "tasks"
        and segments[5].strip()
        and segments[6] == "schedule"
    ):
        return SessionId(segments[3]), TaskId(segments[5])
    return None, None


def _distributed_schedule_action_route(
    path: str,
) -> tuple[SessionId | None, TaskId | None, ActionId | None]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if (
        len(segments) == 9
        and segments[:3] == ("v1", "distributed", "sessions")
        and segments[3].strip()
        and segments[4] == "tasks"
        and segments[5].strip()
        and segments[6] == "actions"
        and segments[7].strip()
        and segments[8] == "schedule"
    ):
        return SessionId(segments[3]), TaskId(segments[5]), ActionId(segments[7])
    return None, None, None


def _distributed_cancel_route(path: str) -> WorkItemId | None:
    segments = tuple(segment for segment in path.split("/") if segment)
    if (
        len(segments) == 5
        and segments[:3] == ("v1", "distributed", "work-items")
        and segments[3].strip()
        and segments[4] == "cancel"
    ):
        return WorkItemId(segments[3])
    return None


def _console_domain_view(
    snapshot: WebConsoleSnapshot,
    name: str,
    version: str | None,
) -> DomainView | None:
    matches = tuple(
        domain
        for domain in snapshot.domains
        if domain.name == name and (version is None or domain.version == version)
    )
    if len(matches) != 1:
        return None
    return matches[0]


def _console_domain_package_view(
    snapshot: WebConsoleSnapshot,
    name: str,
    version: str | None,
) -> DomainPackageView | None:
    matches = tuple(
        package
        for package in snapshot.domain_packages
        if package.name == name and (version is None or package.version == version)
    )
    if len(matches) != 1:
        return None
    return matches[0]


def _domain_not_found_message(name: str, version: str | None) -> str:
    if version is None:
        return f"domain not found or ambiguous: {name}"
    return f"domain not found: {name}@{version}"


def _domain_package_not_found_message(name: str, version: str | None) -> str:
    if version is None:
        return f"domain package not found or ambiguous: {name}"
    return f"domain package not found: {name}@{version}"


def _console_explorer_renderer(
    path: str,
) -> Callable[[WebConsoleSnapshot], str] | None:
    if path == "/console/evidence":
        return render_web_evidence_explorer
    if path == "/console/world":
        return render_web_world_model_explorer
    return None


def _console_session_renderer(
    suffix: str,
) -> Callable[[WebConsoleSnapshot], str] | None:
    if suffix == "":
        return render_web_session_detail
    if suffix == "evidence":
        return render_web_evidence_explorer
    if suffix == "world":
        return render_web_world_model_explorer
    return None


def _profile_route(path: str) -> str | None:
    segments = tuple(segment for segment in path.split("/") if segment)
    if len(segments) == 3 and segments[:2] == ("v1", "profiles") and segments[2].strip():
        return segments[2]
    return None


def _domain_package_route(path: str) -> tuple[str | None, str | None]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if len(segments) == 3 and segments[:2] == ("v1", "domain-packages") and segments[2].strip():
        return segments[2], None
    if (
        len(segments) == 4
        and segments[:2] == ("v1", "domain-packages")
        and segments[2].strip()
        and segments[3].strip()
    ):
        return segments[2], segments[3]
    return None, None
