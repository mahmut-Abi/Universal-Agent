from __future__ import annotations

from dataclasses import dataclass

from universal_agent.core import Task, TaskId
from universal_agent.evidence import Evidence
from universal_agent.world import WorldSnapshot


@dataclass(frozen=True, slots=True)
class TaskSpec:
    key: str
    description: str
    required_criteria: tuple[str, ...] = ()
    depends_on: tuple[TaskId, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskNodeSnapshot:
    key: str
    task: Task
    depends_on: tuple[TaskId, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskGraphSnapshot:
    nodes: tuple[TaskNodeSnapshot, ...]
    current_task_id: TaskId


@dataclass(frozen=True, slots=True)
class TaskExpansionContext:
    task: Task
    evidence: tuple[Evidence, ...]
    world: WorldSnapshot


class TaskExpander:
    @property
    def name(self) -> str: ...

    @property
    def capability_names(self) -> tuple[str, ...]: ...

    def expand(self, context: TaskExpansionContext) -> tuple[TaskSpec, ...]: ...
