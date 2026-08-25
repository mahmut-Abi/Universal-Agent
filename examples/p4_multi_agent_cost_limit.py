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
    AgentTaskUsage,
)


class CostReportingExecutor:
    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        await asyncio.sleep(0)
        return AgentTaskResult(
            task_id=request.task_id,
            status=AgentTaskResultStatus.COMPLETED,
            result=immutable_json({"diagnosis": "healthy"}),
            reason="child agent completed with usage",
            usage=AgentTaskUsage(
                model_call_count=2,
                input_tokens=500,
                output_tokens=120,
                estimated_cost=0.25,
            ),
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
    orchestrator = AgentOrchestrator(registry, {agent_id: CostReportingExecutor()})

    result = await orchestrator.delegate(
        AgentTaskRequest(
            goal="Diagnose workload within budget",
            input=immutable_json({"workload": "deployment/example"}),
            constraints=AgentTaskConstraints(
                read_only=True,
                allowed_profiles=("diagnoser",),
                required_permissions=("diagnose",),
                max_cost=0.10,
            ),
            expected_output=AgentExpectedOutput("diagnosis_report"),
        )
    )

    print(
        f"{result.status.value}: cost={result.usage.estimated_cost} "
        f"limit=0.10 reason={result.reason}"
    )


if __name__ == "__main__":
    asyncio.run(main())
