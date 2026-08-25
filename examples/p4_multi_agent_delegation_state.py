from __future__ import annotations

import asyncio

from universal_agent import immutable_json
from universal_agent.core import DomainIdentity
from universal_agent.multi_agent import (
    AgentDelegationLimitError,
    AgentExpectedOutput,
    AgentId,
    AgentInstanceRecord,
    AgentOrchestrator,
    AgentProfileRecord,
    AgentRegistry,
    AgentTaskConstraints,
    AgentTaskId,
    AgentTaskRequest,
    AgentTaskResult,
    AgentTaskResultStatus,
    agent_delegation_state_payload,
    decode_agent_delegation_state,
)


class RecordingExecutor:
    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        await asyncio.sleep(0)
        return AgentTaskResult(
            task_id=request.task_id,
            status=AgentTaskResultStatus.COMPLETED,
            result=immutable_json({"goal": request.goal}),
            reason="delegated task completed",
        )


def task(
    task_id: str,
    goal: str,
    *,
    parent_task_id: AgentTaskId | None = None,
    delegation_depth: int = 0,
) -> AgentTaskRequest:
    return AgentTaskRequest(
        goal=goal,
        input=immutable_json({"workload": "deployment/example"}),
        constraints=AgentTaskConstraints(read_only=True, max_children=1, max_depth=3),
        expected_output=AgentExpectedOutput("delegation_report"),
        task_id=AgentTaskId(task_id),
        parent_task_id=parent_task_id,
        delegation_depth=delegation_depth,
    )


async def main() -> None:
    agent_id = AgentId("diagnoser-1")
    registry = AgentRegistry(
        profiles=(
            AgentProfileRecord(
                name="diagnoser",
                version="1.0.0",
                domains=(DomainIdentity("kubernetes", "0.2.0"),),
                permissions=("read_only", "diagnose"),
                capabilities=("inspect_workload",),
            ),
        ),
        instances=(AgentInstanceRecord(agent_id, "diagnoser", "1.0.0"),),
    )
    executors = {agent_id: RecordingExecutor()}
    orchestrator = AgentOrchestrator(registry, executors)

    parent_id = AgentTaskId("agent-task-parent")
    child_id = AgentTaskId("agent-task-child")
    await orchestrator.delegate(task(str(parent_id), "Inspect parent"))
    await orchestrator.delegate(
        task(str(child_id), "Inspect child", parent_task_id=parent_id, delegation_depth=1)
    )

    restored_state = decode_agent_delegation_state(
        agent_delegation_state_payload(orchestrator.snapshot())
    )
    restored = AgentOrchestrator(registry, executors, delegation_state=restored_state)

    try:
        await restored.delegate(
            task(
                "agent-task-second-child",
                "Inspect second child",
                parent_task_id=parent_id,
                delegation_depth=1,
            )
        )
    except AgentDelegationLimitError as exc:
        print(f"restored limit enforced: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
