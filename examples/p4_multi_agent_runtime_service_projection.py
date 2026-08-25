from __future__ import annotations

from universal_agent import (
    AgentDelegationState,
    AgentDelegationTaskState,
    AgentId,
    AgentInstanceRecord,
    AgentInstanceStatus,
    AgentProfileRecord,
    AgentRegistry,
    AgentRuntime,
    AgentTaskId,
    DomainLoader,
    InMemoryEventSink,
    InMemoryStateStore,
    RuntimeAPI,
    RuntimeBuilder,
    RuntimeService,
    ScriptedModelAdapter,
    immutable_json,
)
from universal_agent.core import DomainIdentity, JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class FakeBackend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"capability": capability, "healthy": True, **arguments})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"capability": capability, "mutation_applied": True, **arguments})


def build_service() -> RuntimeService:
    backend = FakeBackend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(()),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "staging"}),
    )
    registry = AgentRegistry(
        profiles=(
            AgentProfileRecord(
                name="security-auditor",
                version="1.0.0",
                domains=(DomainIdentity("kubernetes", "0.2.0"),),
                permissions=("read_only", "security_review"),
                capabilities=("inspect_workload",),
                description="Read-only delegated security checks",
            ),
        ),
        instances=(
            AgentInstanceRecord(
                AgentId("security-auditor-1"),
                "security-auditor",
                "1.0.0",
                status=AgentInstanceStatus.READY,
            ),
        ),
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
        agent_registry=registry,
        agent_delegation_state=AgentDelegationState(
            (AgentDelegationTaskState(AgentTaskId("parent-remediation"), 1, 0),)
        ),
    )


def main() -> None:
    multi_agent = build_service().multi_agent()

    print(f"enabled={multi_agent.enabled}")
    print(
        "instances="
        + ",".join(
            f"{instance.agent_id}:{instance.status.value}" for instance in multi_agent.instances
        )
    )
    print(
        "delegation_tasks="
        + ",".join(
            f"{task.task_id}:children={task.child_count}" for task in multi_agent.delegation_tasks
        )
    )


if __name__ == "__main__":
    main()
