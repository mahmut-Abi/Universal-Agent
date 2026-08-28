from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from universal_agent.core import ActionId, SessionId, TaskId
from universal_agent.core.config_validation import parse_non_empty_string


class ResourceConflictError(RuntimeError):
    pass


class ResourceVersionConflictError(ResourceConflictError):
    pass


@dataclass(frozen=True, slots=True)
class ResourceLock:
    resource_key: str
    action_id: ActionId
    session_id: SessionId
    task_id: TaskId


@dataclass(frozen=True, slots=True)
class ResourceVersionCheck:
    resource_key: str
    expected_version: str | None
    current_version: str | None
    matched: bool

    @property
    def reason(self) -> str:
        if self.matched:
            if self.expected_version is None:
                return "no expected resource version supplied"
            if self.current_version is None:
                return "current resource version is unknown"
            return "resource version matched"
        return (
            "resource version conflict: "
            f"{self.resource_key} expected {self.expected_version}, "
            f"current {self.current_version}"
        )


class ResourceLockRegistry:
    """Runtime-owned resource lock table for side-effecting actions."""

    def __init__(self) -> None:
        self._locks: dict[str, ResourceLock] = {}

    def acquire(
        self,
        *,
        resource_key: str,
        action_id: ActionId,
        session_id: SessionId,
        task_id: TaskId,
    ) -> ResourceLock:
        parse_non_empty_string(resource_key, "resource_key")
        requested = ResourceLock(resource_key, action_id, session_id, task_id)
        existing = self._locks.get(resource_key)
        if existing is None:
            self._locks[resource_key] = requested
            return requested
        if existing == requested:
            return existing
        raise ResourceConflictError(
            "resource is locked: "
            f"{resource_key} by action {existing.action_id} in session {existing.session_id}"
        )

    def is_owned_by(
        self,
        *,
        resource_key: str,
        action_id: ActionId,
        session_id: SessionId,
        task_id: TaskId,
    ) -> bool:
        return self._locks.get(resource_key) == ResourceLock(
            resource_key,
            action_id,
            session_id,
            task_id,
        )

    def release(self, lock: ResourceLock) -> None:
        existing = self._locks.get(lock.resource_key)
        if existing == lock:
            del self._locks[lock.resource_key]

    def active(self) -> tuple[ResourceLock, ...]:
        return tuple(self._locks[key] for key in sorted(self._locks))


class ResourceVersionRegistry:
    """Runtime-owned optimistic concurrency view for side-effecting resources.

    The registry only blocks when both an expected version and a known current
    version exist and they differ. Domains can populate it from observations or
    backend adapters without putting version logic into the Kernel.
    """

    def __init__(self, versions: Mapping[str, str] | None = None) -> None:
        self._versions: dict[str, str] = dict(versions or {})

    def set_current(self, resource_key: str, version: str) -> None:
        resource_key = parse_non_empty_string(resource_key, "resource_key").strip()
        version = parse_non_empty_string(version, "resource version").strip()
        self._versions[resource_key] = version

    def current(self, resource_key: str) -> str | None:
        return self._versions.get(resource_key)

    def forget(self, resource_key: str) -> None:
        self._versions.pop(resource_key, None)

    def verify(
        self,
        *,
        resource_key: str,
        expected_version: str | None,
    ) -> ResourceVersionCheck:
        parse_non_empty_string(resource_key, "resource_key")
        current = self.current(resource_key)
        matched = expected_version is None or current is None or expected_version == current
        check = ResourceVersionCheck(resource_key, expected_version, current, matched)
        if not matched:
            raise ResourceVersionConflictError(check.reason)
        return check
