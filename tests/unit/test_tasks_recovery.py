import pytest

from universal_agent.core import ErrorCode, Task, TaskStatus, immutable_json
from universal_agent.recovery import (
    Failure,
    FailureCategory,
    RecoveryManager,
    RecoveryRule,
    RecoveryStrategy,
    classify_failure,
)
from universal_agent.tasks import TaskManager, TaskSpec


@pytest.mark.behavior
def test_task_manager_expands_idempotently_and_honors_dependencies() -> None:
    root = Task("Inspect", ("inspected",))
    root.status = TaskStatus.RUNNING
    manager = TaskManager(root)
    spec = TaskSpec("diagnose", "Diagnose", ("diagnosed",), (root.id,))

    created = manager.expand((spec, spec))
    assert len(created) == 1
    assert manager.start_next() is None

    manager.complete_current()
    next_task = manager.start_next()
    assert next_task is created[0]
    assert next_task is not None
    assert next_task.status is TaskStatus.RUNNING


@pytest.mark.unit
def test_task_manager_rejects_unknown_dependency() -> None:
    manager = TaskManager(Task("Inspect", ()))
    try:
        manager.expand((TaskSpec("bad", "Bad", depends_on=(Task("Other", ()).id,)),))
    except ValueError as exc:
        assert "dependencies" in str(exc)
    else:
        raise AssertionError("unknown dependency was accepted")


@pytest.mark.behavior
def test_recovery_manager_enforces_attempt_budget() -> None:
    task = Task("Inspect", ())
    failure = Failure(
        task.id,
        ErrorCode.TIMEOUT,
        classify_failure(ErrorCode.TIMEOUT),
        "timed out",
        "inspect",
        immutable_json({"name": "example"}),
    )
    manager = RecoveryManager(
        (
            RecoveryRule(
                "retry-timeout",
                (FailureCategory.TIMEOUT,),
                RecoveryStrategy.RETRY_ACTION,
                max_attempts=1,
            ),
        )
    )

    first, key = manager.decide(failure, {})
    assert first.strategy is RecoveryStrategy.RETRY_ACTION
    assert not first.exhausted

    second, _ = manager.decide(failure, {key: first.attempt})
    assert second.strategy is RecoveryStrategy.STOP
    assert second.exhausted


@pytest.mark.unit
def test_unknown_execution_classifies_as_unknown_failure() -> None:
    assert classify_failure(ErrorCode.UNKNOWN_EXECUTION) is FailureCategory.UNKNOWN


@pytest.mark.behavior
def test_recovery_rules_can_match_specific_capabilities() -> None:
    task = Task("Execute", ())
    manager = RecoveryManager(
        (
            RecoveryRule(
                "inspection-timeout",
                (FailureCategory.TIMEOUT,),
                RecoveryStrategy.RETRY_ACTION,
                max_attempts=1,
                match_capabilities=("inspect_workload",),
            ),
        )
    )

    inspection_failure = Failure(
        task.id,
        ErrorCode.TIMEOUT,
        FailureCategory.TIMEOUT,
        "inspection timed out",
        "inspect_workload",
    )
    retry, key = manager.decide(inspection_failure, {})
    assert retry.strategy is RecoveryStrategy.RETRY_ACTION
    assert key

    mutation_failure = Failure(
        task.id,
        ErrorCode.TIMEOUT,
        FailureCategory.TIMEOUT,
        "mutation timed out",
        "scale_workload",
    )
    stop, stop_key = manager.decide(mutation_failure, {})
    assert stop.strategy is RecoveryStrategy.STOP
    assert stop_key == ""

    task = Task("Inspect", ())
    failure = Failure(
        task.id,
        ErrorCode.MODEL_FAILURE,
        FailureCategory.UNKNOWN,
        "unknown",
    )
    decision, key = RecoveryManager(()).decide(failure, {})
    assert decision.strategy is RecoveryStrategy.STOP
    assert key == ""
