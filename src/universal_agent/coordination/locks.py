from __future__ import annotations

from dataclasses import dataclass

from universal_agent.core import ActionId, SessionId, TaskId


class ResourceConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ResourceLock:
    resource_key: str
    action_id: ActionId
    session_id: SessionId
    task_id: TaskId


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
        if not resource_key.strip():
            raise ValueError("resource_key must not be empty")
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
