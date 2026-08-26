from __future__ import annotations

import asyncio

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainLoader,
    Goal,
    InMemoryEventSink,
    InMemoryStateStore,
    RuntimeBuilder,
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesDomain


class FakeKubernetesBackend:
    def __init__(self) -> None:
        self.calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        return immutable_json({"healthy": True, "capability": capability})


async def main() -> None:
    backend = FakeKubernetesBackend()
    components = RuntimeBuilder().build(DomainLoader().load(KubernetesDomain(backend)))
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                Decision(
                    DecisionType.EXECUTE,
                    "Model forgot the required workload name.",
                    capability="inspect_workload",
                    arguments=immutable_json({}),
                    expected_observations=("healthy",),
                )
            ]
        ),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=events,
    )

    result = await runtime.run(
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )

    event_types = [event.type for event in events.events]
    error_code = "none" if result.error_code is None else result.error_code.value
    print(f"status={result.status.value} error_code={error_code}")
    print(f"reason={result.reason}")
    print(f"backend_calls={backend.calls}")
    print(f"policy_checked={'PolicyChecked' in event_types}")
    print(f"action_started={'ActionStarted' in event_types}")


if __name__ == "__main__":
    asyncio.run(main())
