from __future__ import annotations

import pytest

from universal_agent.core import SessionId, Task, TaskId, TaskStatus
from universal_agent.tasks import (
    TaskExpansionContext,
    TaskGraphSnapshot,
    TaskManager,
    TaskNodeSnapshot,
    TaskSpec,
)
from universal_agent.world import WorldSnapshot


def make_task(task_id: str, status: TaskStatus = TaskStatus.PENDING) -> Task:
    return Task(f"task {task_id}", (), TaskId(task_id), status)


def make_graph(
    *,
    nodes: tuple[tuple[str, Task, tuple[TaskId, ...]], ...],
    current: str,
) -> TaskGraphSnapshot:
    return TaskGraphSnapshot(
        tuple(TaskNodeSnapshot(key, task, deps) for key, task, deps in nodes),
        TaskId(current),
    )


class DummyExpander:
    name = "dummy"
    capability_names: tuple[str, ...] = ()

    def expand(self, context: TaskExpansionContext) -> tuple[TaskSpec, ...]:
        return ()


@pytest.mark.unit
def test_task_manager_initializes_with_root_as_current_and_all() -> None:
    root = make_task("root")
    manager = TaskManager(root)

    assert manager.current is root
    assert manager.all() == (root,)
    assert manager.has_unfinished() is True


@pytest.mark.unit
def test_from_snapshot_rejects_empty_graph() -> None:
    with pytest.raises(ValueError, match="at least one task"):
        TaskManager.from_snapshot(TaskGraphSnapshot((), TaskId("root")))


@pytest.mark.unit
def test_from_snapshot_rejects_duplicate_key() -> None:
    graph = make_graph(
        nodes=(
            ("root", make_task("root"), ()),
            ("root", make_task("child"), ()),
        ),
        current="root",
    )
    with pytest.raises(ValueError, match="duplicate task key"):
        TaskManager.from_snapshot(graph)


@pytest.mark.unit
def test_from_snapshot_rejects_duplicate_id() -> None:
    shared = make_task("shared")
    graph = TaskGraphSnapshot(
        (
            TaskNodeSnapshot("root", shared, ()),
            TaskNodeSnapshot("other", shared, ()),
        ),
        shared.id,
    )
    with pytest.raises(ValueError, match="duplicate task id"):
        TaskManager.from_snapshot(graph)


@pytest.mark.unit
def test_from_snapshot_rejects_unknown_current_task() -> None:
    graph = make_graph(nodes=(("root", make_task("root"), ()),), current="missing")
    with pytest.raises(ValueError, match="current task does not exist"):
        TaskManager.from_snapshot(graph)


@pytest.mark.unit
def test_from_snapshot_rejects_unknown_dependency() -> None:
    child = make_task("child")
    graph = make_graph(
        nodes=(
            ("root", make_task("root"), ()),
            ("child", child, (TaskId("ghost"),)),
        ),
        current="root",
    )
    with pytest.raises(ValueError, match="unknown dependencies"):
        TaskManager.from_snapshot(graph)


@pytest.mark.unit
def test_from_snapshot_rejects_dependency_cycle() -> None:
    first = make_task("first")
    second = make_task("second")
    graph = TaskGraphSnapshot(
        (
            TaskNodeSnapshot("first", first, (second.id,)),
            TaskNodeSnapshot("second", second, (first.id,)),
        ),
        first.id,
    )
    with pytest.raises(ValueError, match="dependency cycle"):
        TaskManager.from_snapshot(graph)


@pytest.mark.unit
def test_from_snapshot_round_trips_through_snapshot() -> None:
    child = make_task("child")
    graph = make_graph(
        nodes=(
            ("root", make_task("root"), ()),
            ("child", child, ()),
        ),
        current="root",
    )
    manager = TaskManager.from_snapshot(graph)
    restored = TaskManager.from_snapshot(manager.snapshot())

    assert restored.all() == manager.all()
    assert restored.current.id == manager.current.id
    assert restored.snapshot() == manager.snapshot()


@pytest.mark.unit
def test_expand_requires_key_and_description() -> None:
    manager = TaskManager(make_task("root"))
    with pytest.raises(ValueError, match="task key and description are required"):
        manager.expand((TaskSpec("", "desc"),))
    with pytest.raises(ValueError, match="task key and description are required"):
        manager.expand((TaskSpec("key", ""),))


@pytest.mark.unit
def test_expand_skips_duplicate_key() -> None:
    manager = TaskManager(make_task("root"))
    created = manager.expand(
        (
            TaskSpec("probe", "probe target"),
            TaskSpec("probe", "probe target again"),
        )
    )
    assert len(created) == 1
    assert manager.all() == (manager.current, created[0])


@pytest.mark.unit
def test_expand_rejects_unknown_dependency() -> None:
    manager = TaskManager(make_task("root"))
    with pytest.raises(ValueError, match="existing tasks"):
        manager.expand((TaskSpec("probe", "probe", depends_on=(TaskId("ghost"),)),))


@pytest.mark.unit
def test_expand_adds_new_tasks_and_records_dependencies() -> None:
    root = make_task("root")
    manager = TaskManager(root)
    created = manager.expand(
        (
            TaskSpec("probe", "probe target"),
            TaskSpec("verify", "verify target", depends_on=(root.id,)),
        )
    )

    assert len(created) == 2
    assert {task.description for task in created} == {"probe target", "verify target"}
    assert manager.all()[-2:] == created
    dependencies_by_key = {node.key: node.depends_on for node in manager.snapshot().nodes}
    assert dependencies_by_key["verify"] == (root.id,)


@pytest.mark.unit
def test_complete_current_marks_current_completed() -> None:
    manager = TaskManager(make_task("root"))
    manager.complete_current()
    assert manager.current.status is TaskStatus.COMPLETED


@pytest.mark.unit
def test_start_next_selects_dependency_ready_pending_task() -> None:
    root = make_task("root")
    manager = TaskManager(root)
    created = manager.expand(
        (
            TaskSpec("probe", "probe target", depends_on=(root.id,)),
            TaskSpec("verify", "verify target", depends_on=(root.id,)),
        )
    )
    manager.complete_current()

    first = manager.start_next()
    assert first is not None
    assert first.status is TaskStatus.RUNNING
    assert manager.current is first
    assert first.id in {task.id for task in created}


@pytest.mark.unit
def test_start_next_returns_none_when_dependencies_unmet() -> None:
    manager = TaskManager(make_task("root", TaskStatus.RUNNING))
    manager.expand((TaskSpec("probe", "probe target", depends_on=(TaskId("root"),)),))
    assert manager.start_next() is None


@pytest.mark.unit
def test_start_next_returns_none_when_all_finished() -> None:
    root = make_task("root")
    manager = TaskManager(root)
    manager.complete_current()
    created = manager.expand((TaskSpec("probe", "probe target", depends_on=(root.id,)),))
    manager.start_next()
    created[0].status = TaskStatus.COMPLETED
    assert manager.start_next() is None


@pytest.mark.unit
def test_has_unfinished_reflects_states() -> None:
    manager = TaskManager(make_task("root"))
    assert manager.has_unfinished() is True
    manager.complete_current()
    assert manager.has_unfinished() is False
    manager.expand((TaskSpec("probe", "probe target", depends_on=(manager.current.id,)),))
    assert manager.has_unfinished() is True


@pytest.mark.unit
def test_task_spec_is_frozen_and_task_expander_is_constructible() -> None:
    spec = TaskSpec("key", "desc", required_criteria=("ok",), depends_on=(TaskId("t"),))
    assert spec.key == "key"
    assert spec.required_criteria == ("ok",)

    expander = DummyExpander()
    assert expander.name == "dummy"
    context = TaskExpansionContext(
        Task("task", ()),
        (),
        WorldSnapshot(SessionId("session-test")),
    )
    assert expander.expand(context) == ()


@pytest.mark.unit
def test_expand_detects_cycle_over_corrupted_graph() -> None:
    root = make_task("root")
    manager = TaskManager(root)
    other = make_task("other")
    manager._tasks[other.id] = other
    manager._keys["other"] = other.id
    manager._order.append(other.id)
    manager._dependencies[other.id] = (root.id,)
    manager._dependencies[root.id] = (other.id,)
    with pytest.raises(ValueError, match="dependency cycle"):
        manager.expand((TaskSpec("probe", "probe target"),))
