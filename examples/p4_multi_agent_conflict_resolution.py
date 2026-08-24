from __future__ import annotations

from universal_agent.core import SideEffect, immutable_json
from universal_agent.multi_agent import (
    AgentActionProposal,
    AgentConflictResolver,
    AgentId,
    AgentProposalId,
    AgentTaskId,
)


def main() -> None:
    resolver = AgentConflictResolver()
    resolution = resolver.resolve(
        (
            AgentActionProposal(
                proposal_id=AgentProposalId("proposal-restart"),
                agent_id=AgentId("agent-a"),
                task_id=AgentTaskId("agent-task-a"),
                capability="restart_workload",
                resource_key="deployment/example",
                target="deployment/example",
                side_effect=SideEffect.DESTRUCTIVE,
            ),
            AgentActionProposal(
                proposal_id=AgentProposalId("proposal-scale"),
                agent_id=AgentId("agent-b"),
                task_id=AgentTaskId("agent-task-b"),
                capability="scale_workload",
                resource_key="deployment/example",
                target="deployment/example",
                arguments=immutable_json({"replicas": 3}),
                side_effect=SideEffect.REVERSIBLE,
            ),
        )
    )
    print(f"{resolution.status.value}: {resolution.selected_proposal_id}")


if __name__ == "__main__":
    main()
