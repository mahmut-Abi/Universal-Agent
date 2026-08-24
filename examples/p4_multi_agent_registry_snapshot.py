from __future__ import annotations

from universal_agent.core import DomainIdentity, SessionId
from universal_agent.multi_agent import (
    AgentId,
    AgentInstanceRecord,
    AgentInstanceStatus,
    AgentProfileRecord,
    AgentRegistry,
    agent_registry_from_snapshot,
    agent_registry_snapshot_payload,
    decode_agent_registry_snapshot,
)


def main() -> None:
    registry = AgentRegistry(
        profiles=(
            AgentProfileRecord(
                name="security-auditor",
                version="1.0.0",
                domains=(DomainIdentity("kubernetes", "0.2.0"),),
                permissions=("read_only", "security_review"),
                capabilities=("inspect_workload", "inspect_policy"),
                description="Read-only security checks",
            ),
        ),
        instances=(
            AgentInstanceRecord(
                AgentId("security-auditor-1"),
                "security-auditor",
                "1.0.0",
                status=AgentInstanceStatus.READY,
                session_id=SessionId("session-security-auditor-1"),
                endpoint="http://localhost:9000",
            ),
        ),
    )

    snapshot = decode_agent_registry_snapshot(agent_registry_snapshot_payload(registry.snapshot()))
    restored = agent_registry_from_snapshot(snapshot)
    instance = restored.instance(AgentId("security-auditor-1"))
    profile = restored.profile(instance.profile_name, instance.profile_version)

    print(f"{instance.agent_id}: {instance.status.value} {profile.name}@{profile.version}")


if __name__ == "__main__":
    main()
