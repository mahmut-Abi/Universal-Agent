from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from universal_agent.core import Task, TaskId
from universal_agent.evidence import Evidence
from universal_agent.world import WorldSnapshot


@dataclass(frozen=True, slots=True)
class TaskSpec:
    key: str
    description: str
    required_criteria: tuple[str, ...] = ()
    depends_on: tuple[TaskId, ...] = ()
    # Optional stable id used when a compiler has already assigned identities.
    # Dynamic expanders may omit it and let TaskManager generate one.
    task_id: TaskId | None = None


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


class TaskExpander(Protocol):
    """Proposes new Tasks from Evidence a Domain considers significant.

    A Protocol rather than a base class, matching the other Domain extension
    points: expansion is recognised by shape, not by inheritance.
    """

    @property
    def name(self) -> str: ...

    @property
    def capability_names(self) -> tuple[str, ...]: ...

    def expand(self, context: TaskExpansionContext) -> tuple[TaskSpec, ...]: ...
