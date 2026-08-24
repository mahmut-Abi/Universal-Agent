from __future__ import annotations

import asyncio

from universal_agent import immutable_json
from universal_agent.core import DomainIdentity
from universal_agent.multi_agent import (
    AgentExpectedOutput,
    AgentId,
    AgentInstanceRecord,
    AgentInstanceStatus,
    AgentOrchestrator,
    AgentProfileRecord,
    AgentRegistry,
    AgentTaskConstraints,
    AgentTaskRequest,
    AgentTaskResult,
    AgentTaskResultStatus,
)


class LifecycleExecutor:
    def __init__(
        self,
        registry: AgentRegistry,
        agent_id: AgentId,
        observed: list[AgentInstanceStatus],
    ) -> None:
        self.registry = registry
        self.agent_id = agent_id
        self.observed = observed

    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        self.observed.append(self.registry.instance(self.agent_id).status)
        await asyncio.sleep(0)
        return AgentTaskResult(
            task_id=request.task_id,
            status=AgentTaskResultStatus.COMPLETED,
            result=immutable_json({"observed_status": self.observed[-1].value}),
            reason="observed delegated agent lifecycle",
        )


async def main() -> None:
    agent_id = AgentId("diagnoser-1")
    observed: list[AgentInstanceStatus] = []
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
    orchestrator = AgentOrchestrator(
        registry,
        {agent_id: LifecycleExecutor(registry, agent_id, observed)},
    )

    before = registry.instance(agent_id).status
    result = await orchestrator.delegate(
        AgentTaskRequest(
            goal="Inspect lifecycle transition",
            input=immutable_json({"workload": "deployment/example"}),
            constraints=AgentTaskConstraints(
                read_only=True,
                allowed_profiles=("diagnoser",),
                required_permissions=("diagnose",),
            ),
            expected_output=AgentExpectedOutput("lifecycle_report"),
        )
    )
    after = registry.instance(agent_id).status
    during = observed[0]

    print(f"{result.status.value}: before={before.value} during={during.value} after={after.value}")


if __name__ == "__main__":
    asyncio.run(main())
