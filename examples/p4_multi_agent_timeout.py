from __future__ import annotations

import asyncio

from universal_agent import immutable_json
from universal_agent.core import DomainIdentity
from universal_agent.multi_agent import (
    AgentExpectedOutput,
    AgentId,
    AgentInstanceRecord,
    AgentOrchestrator,
    AgentProfileRecord,
    AgentRegistry,
    AgentTaskConstraints,
    AgentTaskRequest,
    AgentTaskResult,
    AgentTaskResultStatus,
)


class SlowExecutor:
    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        await asyncio.sleep(1)
        return AgentTaskResult(
            task_id=request.task_id,
            status=AgentTaskResultStatus.COMPLETED,
            result=immutable_json({"completed": True}),
            reason="completed after delay",
        )


async def main() -> None:
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
        instances=(AgentInstanceRecord(AgentId("diagnoser-1"), "diagnoser", "1.0.0"),),
    )
    orchestrator = AgentOrchestrator(
        registry,
        {AgentId("diagnoser-1"): SlowExecutor()},
    )
    result = await orchestrator.delegate(
        AgentTaskRequest(
            goal="Inspect workload within a strict deadline",
            input=immutable_json({"workload": "deployment/example"}),
            constraints=AgentTaskConstraints(
                read_only=True,
                max_duration_seconds=0.001,
                allowed_profiles=("diagnoser",),
                required_permissions=("diagnose",),
            ),
            expected_output=AgentExpectedOutput("diagnostic_report"),
        )
    )
    print(f"{result.status.value}: {result.error_code.value if result.error_code else 'ok'}")
    print(result.reason)


if __name__ == "__main__":
    asyncio.run(main())
