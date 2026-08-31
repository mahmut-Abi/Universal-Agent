from __future__ import annotations

from universal_agent.core import Goal, SuccessCriterion
from universal_agent.goals import DefaultGoalCompiler, GoalCompilation
from universal_agent.goals.compiler import GoalCompiler


def _make_goal(description: str, criteria: tuple[SuccessCriterion, ...]) -> Goal:
    return Goal(description=description, success_criteria=criteria)


async def test_single_goal_single_root_with_criteria() -> None:
    goal = _make_goal(
        "Make the deployment healthy",
        (SuccessCriterion("health", "ok"), SuccessCriterion("available", 3)),
    )
    compiler: GoalCompiler = DefaultGoalCompiler(split_steps=False)
    result = await compiler.compile(goal)

    assert isinstance(result, GoalCompilation)
    assert len(result.initial_tasks) == 1
    root = result.root_task
    assert root.key == "root"
    assert root.description == goal.description
    assert root.depends_on == ()
    assert set(root.required_criteria) == {"health", "available"}
    assert result.constraints == goal.success_criteria


async def test_multiline_description_splits_into_dependent_steps() -> None:
    goal = _make_goal(
        "Fix the service\n1. Inspect the pod\n2. Restart the deployment",
        (SuccessCriterion("health", "ok"),),
    )
    compiler: GoalCompiler = DefaultGoalCompiler()
    result = await compiler.compile(goal)

    assert len(result.initial_tasks) == 4
    root_id = f"goal:{goal.id}:root"
    children = result.initial_tasks[1:]
    assert [c.key for c in children] == ["root:step:0", "root:step:1", "root:step:2"]
    assert children[0].description == "Fix the service"
    assert children[1].description == "1. Inspect the pod"
    assert children[2].description == "2. Restart the deployment"
    for child in children:
        assert child.depends_on == (root_id,)
        assert child.required_criteria == ()


async def test_keys_unique_and_no_dangling_dependencies() -> None:
    goal = _make_goal(
        "Deploy\nstep a\nstep b\nstep c",
        (SuccessCriterion("done", True),),
    )
    compiler: GoalCompiler = DefaultGoalCompiler()
    result = await compiler.compile(goal)

    keys = [task.key for task in result.initial_tasks]
    assert len(keys) == len(set(keys))

    root_id = f"goal:{goal.id}:root"
    known_ids = {root_id}
    for task in result.initial_tasks:
        for dep in task.depends_on:
            assert dep in known_ids


async def test_deterministic_output() -> None:
    goal = _make_goal(
        "Recover the cluster\n- check nodes\n- check pods",
        (SuccessCriterion("stable", True),),
    )
    compiler_a: GoalCompiler = DefaultGoalCompiler()
    compiler_b: GoalCompiler = DefaultGoalCompiler()
    result_a = await compiler_a.compile(goal)
    result_b = await compiler_b.compile(goal)

    assert [t.key for t in result_a.initial_tasks] == [t.key for t in result_b.initial_tasks]
    assert [t.description for t in result_a.initial_tasks] == [
        t.description for t in result_b.initial_tasks
    ]
    assert [t.depends_on for t in result_a.initial_tasks] == [
        t.depends_on for t in result_b.initial_tasks
    ]
    assert result_a.root_task.depends_on == ()
