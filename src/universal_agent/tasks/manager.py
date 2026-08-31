from __future__ import annotations

from graphlib import CycleError, TopologicalSorter

from universal_agent.core import Task, TaskId, TaskStatus
from universal_agent.tasks.models import (
    TaskGraphSnapshot,
    TaskNodeSnapshot,
    TaskSpec,
)


class TaskManager:
    def __init__(self, root: Task) -> None:
        self._tasks: dict[TaskId, Task] = {root.id: root}
        self._keys: dict[str, TaskId] = {"root": root.id}
        self._dependencies: dict[TaskId, tuple[TaskId, ...]] = {root.id: ()}
        self._order: list[TaskId] = [root.id]
        self._current_id = root.id

    @classmethod
    def from_specs(cls, specs: tuple[TaskSpec, ...]) -> TaskManager:
        if not specs:
            raise ValueError("task specs must contain at least one task")
        root_spec = specs[0]
        root = _task_from_spec(root_spec)
        manager = cls(root)
        if root_spec.key != "root":
            manager._keys = {root_spec.key: root.id}
        manager.expand(specs[1:])
        return manager

    @classmethod
    def from_snapshot(cls, snapshot: TaskGraphSnapshot) -> TaskManager:
        if not snapshot.nodes:
            raise ValueError("task graph must contain at least one task")
        manager = cls(snapshot.nodes[0].task)
        manager._tasks = {}
        manager._keys = {}
        manager._dependencies = {}
        manager._order = []
        for node in snapshot.nodes:
            if node.key in manager._keys:
                raise ValueError(f"duplicate task key: {node.key}")
            if node.task.id in manager._tasks:
                raise ValueError(f"duplicate task id: {node.task.id}")
            manager._keys[node.key] = node.task.id
            manager._tasks[node.task.id] = node.task
            manager._dependencies[node.task.id] = node.depends_on
            manager._order.append(node.task.id)
        if snapshot.current_task_id not in manager._tasks:
            raise ValueError("task graph current task does not exist")
        for task_id, dependencies in manager._dependencies.items():
            if set(dependencies) - manager._tasks.keys():
                raise ValueError(f"task {task_id} references unknown dependencies")
        manager._validate_acyclic()
        manager._current_id = snapshot.current_task_id
        return manager

    @property
    def current(self) -> Task:
        return self._tasks[self._current_id]

    def all(self) -> tuple[Task, ...]:
        return tuple(self._tasks[task_id] for task_id in self._order)

    def snapshot(self) -> TaskGraphSnapshot:
        keys_by_id = {task_id: key for key, task_id in self._keys.items()}
        return TaskGraphSnapshot(
            tuple(
                TaskNodeSnapshot(
                    keys_by_id[task_id],
                    self._tasks[task_id],
                    self._dependencies[task_id],
                )
                for task_id in self._order
            ),
            self._current_id,
        )

    def expand(self, specs: tuple[TaskSpec, ...]) -> tuple[Task, ...]:
        created: list[Task] = []
        for spec in specs:
            if not spec.key or not spec.description:
                raise ValueError("task key and description are required")
            if spec.key in self._keys:
                continue
            unknown = set(spec.depends_on) - self._tasks.keys()
            if unknown:
                raise ValueError("task dependencies must reference existing tasks")
            task = Task(
                spec.description,
                spec.required_criteria,
                id=spec.task_id if spec.task_id is not None else TaskId(f"task:{spec.key}"),
            )
            if task.id in self._tasks:
                raise ValueError(f"duplicate task id: {task.id}")
            self._tasks[task.id] = task
            self._keys[spec.key] = task.id
            self._dependencies[task.id] = spec.depends_on
            self._order.append(task.id)
            created.append(task)
        self._validate_acyclic()
        return tuple(created)

    def complete_current(self) -> None:
        self.current.status = TaskStatus.COMPLETED

    def start_next(self) -> Task | None:
        for task_id in self._order:
            task = self._tasks[task_id]
            dependencies = self._dependencies[task_id]
            if task.status is TaskStatus.PENDING and all(
                self._tasks[item].status is TaskStatus.COMPLETED for item in dependencies
            ):
                self._current_id = task_id
                task.status = TaskStatus.RUNNING
                return task
        return None

    def has_unfinished(self) -> bool:
        return any(
            task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
            for task in self._tasks.values()
        )

    def _validate_acyclic(self) -> None:
        try:
            TopologicalSorter(self._dependencies).prepare()
        except CycleError as exc:
            raise ValueError("task graph contains a dependency cycle") from exc


def _task_from_spec(spec: TaskSpec) -> Task:
    if not spec.key or not spec.description:
        raise ValueError("task key and description are required")
    return Task(
        spec.description,
        spec.required_criteria,
        id=spec.task_id if spec.task_id is not None else TaskId(f"task:{spec.key}"),
    )
