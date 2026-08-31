from __future__ import annotations

from universal_agent.multi_agent.contracts import (
    AgentExpectedOutput,
    AgentTaskId,
    AgentTaskRequest,
    AgentTaskResult,
    AgentTaskResultStatus,
)
from universal_agent.multi_agent.delegation import (
    DelegationManager,
    DelegationStatus,
    InvalidDelegationTransition,
)
from universal_agent.multi_agent.registry import AgentId


def _request(task_id: str = "agent-task-x") -> AgentTaskRequest:
    return AgentTaskRequest(
        goal="investigate unhealthy deployment",
        expected_output=AgentExpectedOutput("report", {"type": "object"}),
        task_id=AgentTaskId(task_id),
    )


def _manager() -> DelegationManager:
    counter = {"n": 0}

    def factory(prefix: str) -> str:
        counter["n"] += 1
        return f"{prefix}-{counter['n']}"

    return DelegationManager(id_factory=factory)


def test_create_then_running_then_completed() -> None:
    manager = _manager()
    delegation = manager.create_delegation(
        AgentId("agent-a"),
        AgentId("agent-b"),
        _request(),
    )
    assert delegation.status is DelegationStatus.PENDING
    started = manager.start(delegation.delegation_id)
    assert started.status is DelegationStatus.RUNNING
    result = AgentTaskResult(
        started.task_id,
        AgentTaskResultStatus.COMPLETED,
        result={"ok": True},
    )
    completed = manager.complete(delegation.delegation_id, result)
    assert completed.status is DelegationStatus.COMPLETED
    assert completed.result is result
    active = {d.delegation_id for d in manager.active_delegations()}
    assert delegation.delegation_id not in active


def test_revoked_from_pending() -> None:
    manager = _manager()
    delegation = manager.create_delegation(AgentId("a"), AgentId("b"), _request())
    revoked = manager.revoke(delegation.delegation_id)
    assert revoked.status is DelegationStatus.REVOKED
    assert revoked.to_agent == AgentId("b")


def test_revoked_from_running() -> None:
    manager = _manager()
    delegation = manager.create_delegation(AgentId("a"), AgentId("b"), _request())
    manager.start(delegation.delegation_id)
    revoked = manager.revoke(delegation.delegation_id)
    assert revoked.status is DelegationStatus.REVOKED


def test_fail_triggers_fallback() -> None:
    manager = _manager()
    delegation = manager.create_delegation(
        AgentId("a"),
        AgentId("b"),
        _request(),
        fallback_agent=AgentId("c"),
    )
    manager.start(delegation.delegation_id)
    failed = manager.fail(delegation.delegation_id, "boom")
    assert failed.status is DelegationStatus.PENDING
    assert failed.to_agent == AgentId("c")
    assert failed.fallback_agent == AgentId("c")
    assert failed.result is not None
    assert failed.result.status is AgentTaskResultStatus.FAILED


def test_fail_without_fallback_marks_failed() -> None:
    manager = _manager()
    delegation = manager.create_delegation(AgentId("a"), AgentId("b"), _request())
    manager.start(delegation.delegation_id)
    failed = manager.fail(delegation.delegation_id, "boom")
    assert failed.status is DelegationStatus.FAILED
    assert failed.to_agent == AgentId("b")
    assert failed.result is not None
    assert failed.result.status is AgentTaskResultStatus.FAILED


def test_invalid_transition_from_terminal() -> None:
    manager = _manager()
    delegation = manager.create_delegation(AgentId("a"), AgentId("b"), _request())
    manager.start(delegation.delegation_id)
    manager.complete(
        delegation.delegation_id,
        AgentTaskResult(delegation.task_id, AgentTaskResultStatus.COMPLETED),
    )
    for bad in (manager.start, manager.revoke):
        raised = False
        try:
            bad(delegation.delegation_id)
        except InvalidDelegationTransition:
            raised = True
        assert raised


def test_invalid_start_from_pending_to_completed() -> None:
    manager = _manager()
    delegation = manager.create_delegation(AgentId("a"), AgentId("b"), _request())
    raised = False
    try:
        manager.complete(
            delegation.delegation_id,
            AgentTaskResult(delegation.task_id, AgentTaskResultStatus.COMPLETED),
        )
    except InvalidDelegationTransition:
        raised = True
    assert raised


def test_by_task_query() -> None:
    manager = _manager()
    delegation = manager.create_delegation(AgentId("a"), AgentId("b"), _request("agent-task-q"))
    manager.create_delegation(AgentId("a"), AgentId("b"), _request("agent-task-other"))
    matches = manager.by_task(AgentTaskId("agent-task-q"))
    assert matches == (delegation,)


def test_active_delegations() -> None:
    manager = _manager()
    pending = manager.create_delegation(AgentId("a"), AgentId("b"), _request("t1"))
    manager.create_delegation(AgentId("a"), AgentId("b"), _request("t2"))
    manager.start(pending.delegation_id)
    manager.revoke(
        manager.create_delegation(AgentId("a"), AgentId("b"), _request("t3")).delegation_id
    )
    active = manager.active_delegations()
    active_ids = {d.delegation_id for d in active}
    assert pending.delegation_id in active_ids
    assert len(active_ids) == 2
