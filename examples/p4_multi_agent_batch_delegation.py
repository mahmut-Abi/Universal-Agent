from __future__ import annotations

import asyncio

from universal_agent import immutable_json
from universal_agent.core import DomainIdentity
from universal_agent.multi_agent import (
    AgentDelegationSpec,
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
)


class RecordingExecutor:
    def __init__(self, label: str, events: list[str]) -> None:
        self.label = label
        self.events = events

    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        self.events.append(f"start:{self.label}:{request.task_id}")
        await asyncio.sleep(0)
        self.events.append(f"finish:{self.label}:{request.task_id}")
        return AgentTaskResult(
            task_id=request.task_id,
            status=AgentTaskResultStatus.COMPLETED,
            result=immutable_json({"executor": self.label, "goal": request.goal}),
            reason="delegated task completed",
        )


def request(
    task_id: str,
    goal: str,
    *,
    profile: str,
    permission: str,
) -> AgentTaskRequest:
    return AgentTaskRequest(
        goal=goal,
        input=immutable_json({"workload": "deployment/example"}),
        constraints=AgentTaskConstraints(
            read_only=True,
            max_children=3,
            allowed_profiles=(profile,),
            required_permissions=(permission,),
        ),
        expected_output=AgentExpectedOutput("agent_report"),
        task_id=AgentTaskId(task_id),
    )


async def main() -> None:
    events: list[str] = []
    registry = AgentRegistry(
        profiles=(
            AgentProfileRecord(
                name="reliability-diagnoser",
                version="1.0.0",
                domains=(DomainIdentity("kubernetes", "0.2.0"),),
                permissions=("read_only", "diagnose"),
                capabilities=("inspect_workload",),
            ),
            AgentProfileRecord(
                name="security-auditor",
                version="1.0.0",
                domains=(DomainIdentity("kubernetes", "0.2.0"),),
                permissions=("read_only", "security_review"),
                capabilities=("inspect_workload",),
            ),
        ),
        instances=(
            AgentInstanceRecord(
                AgentId("reliability-1"),
                "reliability-diagnoser",
                "1.0.0",
            ),
            AgentInstanceRecord(AgentId("security-1"), "security-auditor", "1.0.0"),
        ),
    )
    orchestrator = AgentOrchestrator(
        registry,
        {
            AgentId("reliability-1"): RecordingExecutor("reliability", events),
            AgentId("security-1"): RecordingExecutor("security", events),
        },
    )

    batch = await orchestrator.delegate_many(
        (
            AgentDelegationSpec(
                request(
                    "inspect-health",
                    "Inspect workload health",
                    profile="reliability-diagnoser",
                    permission="diagnose",
                ),
                agent_id=AgentId("reliability-1"),
            ),
            AgentDelegationSpec(
                request(
                    "inspect-risk",
                    "Inspect workload security risk",
                    profile="security-auditor",
                    permission="security_review",
                ),
                agent_id=AgentId("security-1"),
            ),
            AgentDelegationSpec(
                request(
                    "summarize-remediation",
                    "Summarize safe remediation options",
                    profile="reliability-diagnoser",
                    permission="diagnose",
                ),
                agent_id=AgentId("reliability-1"),
                depends_on=(AgentTaskId("inspect-health"), AgentTaskId("inspect-risk")),
            ),
        )
    )

    print(f"batch={batch.status.value}: {batch.reason}")
    print("results=" + ",".join(result.status.value for result in batch.results))
    print("events=" + " > ".join(events))


if __name__ == "__main__":
    asyncio.run(main())
