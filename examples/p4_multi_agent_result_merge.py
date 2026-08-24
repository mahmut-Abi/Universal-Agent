from __future__ import annotations

from typing import cast

from universal_agent.evidence import EvidenceId
from universal_agent.multi_agent import (
    AgentResultMergePolicy,
    AgentResultMerger,
    AgentTaskId,
    AgentTaskResult,
    AgentTaskResultStatus,
    agent_result_merge_payload,
    decode_agent_result_merge,
)


def main() -> None:
    merge = AgentResultMerger().merge(
        (
            AgentTaskResult(
                AgentTaskId("security-audit"),
                AgentTaskResultStatus.COMPLETED,
                result={"risk_level": "low"},
                evidence_ids=(EvidenceId("evidence-security-audit"),),
                reason="security audit completed",
            ),
            AgentTaskResult(
                AgentTaskId("runtime-audit"),
                AgentTaskResultStatus.COMPLETED,
                result={"runtime_ready": True},
                evidence_ids=(EvidenceId("evidence-runtime-audit"),),
                reason="runtime audit completed",
            ),
        ),
        policy=AgentResultMergePolicy(
            expected_task_ids=(AgentTaskId("security-audit"), AgentTaskId("runtime-audit"))
        ),
    )
    payload = agent_result_merge_payload(merge)
    decoded = decode_agent_result_merge(payload)
    evidence_ids = cast(list[str], payload["evidence"])
    print(f"{decoded.status.value}: {','.join(evidence_ids)}")


if __name__ == "__main__":
    main()
