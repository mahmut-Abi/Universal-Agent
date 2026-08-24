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
    agent_task_request_payload,
    agent_task_result_payload,
    decode_agent_task_request,
    decode_agent_task_result,
)


class ReadOnlySecurityExecutor:
    async def execute_agent_task(self, request: AgentTaskRequest) -> AgentTaskResult:
        return AgentTaskResult(
            task_id=request.task_id,
            status=AgentTaskResultStatus.COMPLETED,
            result=immutable_json({"risk_level": "low", "checked": request.input["resource"]}),
            reason="read-only audit completed",
        )


async def main() -> None:
    registry = AgentRegistry(
        profiles=(
            AgentProfileRecord(
                name="security-auditor",
                version="1.0.0",
                domains=(DomainIdentity("kubernetes", "0.2.0"),),
                permissions=("read_only", "security_review"),
                capabilities=("inspect_workload",),
            ),
        ),
        instances=(
            AgentInstanceRecord(AgentId("security-auditor-1"), "security-auditor", "1.0.0"),
        ),
    )
    orchestrator = AgentOrchestrator(
        registry,
        {AgentId("security-auditor-1"): ReadOnlySecurityExecutor()},
    )
    request_payload = agent_task_request_payload(
        AgentTaskRequest(
            goal="Audit deployment security",
            input=immutable_json({"resource": "deployment/example"}),
            constraints=AgentTaskConstraints(
                read_only=True,
                allowed_profiles=("security-auditor",),
                required_permissions=("security_review",),
            ),
            expected_output=AgentExpectedOutput("security_report"),
        )
    )
    request = decode_agent_task_request(request_payload)
    result = await orchestrator.delegate(request)
    decoded_result = decode_agent_task_result(agent_task_result_payload(result))
    print(f"{decoded_result.status.value}: {decoded_result.result['risk_level']}")


if __name__ == "__main__":
    asyncio.run(main())
