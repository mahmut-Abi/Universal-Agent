from __future__ import annotations

from universal_agent.evidence import EvidenceId
from universal_agent.multi_agent import (
    AgentResultMergePolicy,
    AgentResultMerger,
    AgentTaskId,
    AgentTaskResult,
    AgentTaskResultStatus,
    MultiAgentEvaluationExpectations,
    MultiAgentMergeEvaluator,
    decode_multi_agent_evaluation_report,
    multi_agent_evaluation_report_payload,
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
        ),
        policy=AgentResultMergePolicy(expected_task_ids=(AgentTaskId("security-audit"),)),
    )
    report = MultiAgentMergeEvaluator().evaluate(
        merge,
        MultiAgentEvaluationExpectations(
            required_evidence_ids=(EvidenceId("evidence-security-audit"),),
            required_completed_task_ids=(AgentTaskId("security-audit"),),
            min_completed_task_count=1,
        ),
    )
    decoded = decode_multi_agent_evaluation_report(multi_agent_evaluation_report_payload(report))
    print(f"passed={decoded.passed} checks={len(decoded.checks)}")


if __name__ == "__main__":
    main()
