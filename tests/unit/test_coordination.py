from __future__ import annotations

import pytest

from universal_agent.coordination import (
    ResourceConflictError,
    ResourceLockRegistry,
    ResourceVersionConflictError,
    ResourceVersionRegistry,
)
from universal_agent.core import ActionId, SessionId, TaskId


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
def test_resource_lock_registry_rejects_same_action_from_different_session() -> None:
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
            action_id=ActionId("action-1"),
            session_id=SessionId("session-2"),
            task_id=TaskId("task-2"),
        )


@pytest.mark.unit
def test_resource_lock_registry_rejects_empty_resource_key() -> None:
    registry = ResourceLockRegistry()

    with pytest.raises(ValueError):
        registry.acquire(
            resource_key=" ",
            action_id=ActionId("action-1"),
            session_id=SessionId("session-1"),
            task_id=TaskId("task-1"),
        )


@pytest.mark.unit
def test_resource_version_registry_allows_unknown_current_version() -> None:
    registry = ResourceVersionRegistry()

    check = registry.verify(
        resource_key="deployment/example",
        expected_version="rv-1",
    )

    assert check.matched
    assert check.current_version is None
    assert check.reason == "current resource version is unknown"


@pytest.mark.unit
def test_resource_version_registry_detects_matching_current_version() -> None:
    registry = ResourceVersionRegistry({"deployment/example": "rv-1"})

    check = registry.verify(
        resource_key="deployment/example",
        expected_version="rv-1",
    )

    assert check.matched
    assert check.current_version == "rv-1"
    assert check.reason == "resource version matched"


@pytest.mark.unit
def test_resource_version_registry_rejects_stale_expected_version() -> None:
    registry = ResourceVersionRegistry({"deployment/example": "rv-2"})

    with pytest.raises(ResourceVersionConflictError, match="expected rv-1, current rv-2"):
        registry.verify(
            resource_key="deployment/example",
            expected_version="rv-1",
        )
