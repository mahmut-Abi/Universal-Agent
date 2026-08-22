from __future__ import annotations

import pytest

from universal_agent.coordination import ResourceConflictError, ResourceLockRegistry
from universal_agent.core import ActionId, SessionId, TaskId


def test_resource_lock_registry_acquires_reenters_and_releases() -> None:
    registry = ResourceLockRegistry()

    first = registry.acquire(
        resource_key="deployment/example",
        action_id=ActionId("action-1"),
        session_id=SessionId("session-1"),
        task_id=TaskId("task-1"),
    )
    second = registry.acquire(
        resource_key="deployment/example",
        action_id=ActionId("action-1"),
        session_id=SessionId("session-1"),
        task_id=TaskId("task-1"),
    )

    assert first == second
    assert registry.active() == (first,)

    registry.release(first)
    assert registry.active() == ()


def test_resource_lock_registry_rejects_conflicting_action() -> None:
    registry = ResourceLockRegistry()
    registry.acquire(
        resource_key="deployment/example",
        action_id=ActionId("action-1"),
        session_id=SessionId("session-1"),
        task_id=TaskId("task-1"),
    )

    with pytest.raises(ResourceConflictError):
        registry.acquire(
            resource_key="deployment/example",
            action_id=ActionId("action-2"),
            session_id=SessionId("session-2"),
            task_id=TaskId("task-2"),
        )


def test_resource_lock_registry_rejects_empty_resource_key() -> None:
    registry = ResourceLockRegistry()

    with pytest.raises(ValueError):
        registry.acquire(
            resource_key=" ",
            action_id=ActionId("action-1"),
            session_id=SessionId("session-1"),
            task_id=TaskId("task-1"),
        )
