from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from universal_agent.core import Goal, SuccessCriterion, TaskId
from universal_agent.tasks.models import TaskSpec


@dataclass(frozen=True, slots=True)
class GoalCompilation:
    initial_tasks: tuple[TaskSpec, ...]
    constraints: tuple[SuccessCriterion, ...]
    notes: str = ""

    @property
    def root_task(self) -> TaskSpec:
        return self.initial_tasks[0]


class GoalCompiler(Protocol):
    async def compile(self, goal: Goal) -> GoalCompilation: ...


class DefaultGoalCompiler:
    def __init__(self, *, split_steps: bool = True) -> None:
        self._split_steps = split_steps

    async def compile(self, goal: Goal) -> GoalCompilation:
        root_id = TaskId(f"goal:{goal.id}:root")
        root = TaskSpec(
            key="root",
            description=goal.description,
            # When split steps are present, the root is the first executable
            # task. Goal criteria belong to the Goal and are evaluated across
            # all observations; keeping them on the root would gate every
            # child behind criteria that the children are meant to produce.
            required_criteria=(
                ()
                if self._split_steps
                else tuple(criterion.key for criterion in goal.success_criteria)
            ),
            depends_on=(),
            task_id=root_id,
        )
        tasks: list[TaskSpec] = [root]
        if self._split_steps:
            for index, line in enumerate(self._split_lines(goal.description)):
                tasks.append(
                    TaskSpec(
                        key=f"root:step:{index}",
                        description=line,
                        required_criteria=(),
                        depends_on=(root_id,),
                    )
                )
        return GoalCompilation(
            initial_tasks=tuple(tasks),
            constraints=tuple(goal.success_criteria),
            notes="",
        )

    @staticmethod
    def _split_lines(description: str) -> list[str]:
        lines: list[str] = []
        for raw in description.splitlines():
            line = raw.strip()
            if not line:
                continue
            lines.append(line)
        return lines
