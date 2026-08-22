from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainLoader,
    Goal,
    RuntimeAPI,
    RuntimeBuilder,
    ScriptedModelAdapter,
    SQLiteEventStore,
    SQLiteSessionStore,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class FakePersistentBackend:
    def __init__(self) -> None:
        self.scaled = False
        self.inspect_calls: list[str] = []
        self.mutation_calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls.append(capability)
        if capability == "inspect_pod":
            return immutable_json(
                {
                    "resource": "pod/example-123",
                    "root_cause": "under_replicated",
                }
            )
        if self.scaled:
            return immutable_json(
                {
                    "resource": "deployment/example",
                    "healthy": True,
                    "desired_replicas": 3,
                    "ready_replicas": 3,
                    "verification_observed": True,
                }
            )
        return immutable_json(
            {
                "resource": "deployment/example",
                "healthy": False,
                "desired_replicas": 3,
                "ready_replicas": 1,
            }
        )

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        self.scaled = True
        self.mutation_calls += 1
        return immutable_json(
            {
                "resource": "deployment/example",
                "mutation_applied": True,
                "replicas": 3,
            }
        )


def execute(capability: str, *observations: str) -> Decision:
    arguments: dict[str, str | int] = {"name": "example"}
    target = "deployment/example"
    if capability == "inspect_pod":
        arguments["name"] = "example-123"
        target = "pod/example-123"
    if capability == "scale_workload":
        arguments.update({"namespace": "default", "replicas": 3})
    return Decision(
        DecisionType.EXECUTE,
        f"Run {capability} with sqlite-backed persistence",
        capability=capability,
        target=target,
        arguments=immutable_json(arguments),
        expected_observations=observations,
    )


def build_api(
    path: Path,
    backend: FakePersistentBackend,
    decisions: list[Decision],
) -> RuntimeAPI:
    store = SQLiteSessionStore(path)
    events = SQLiteEventStore(path)
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=RuntimeBuilder().build(
            DomainLoader().load(KubernetesRemediationDomain(backend, backend))
        ),
        event_sink=events,
        environment=immutable_json({"environment": "production"}),
    )
    return RuntimeAPI(runtime=runtime, session_store=store, event_reader=events)


async def main() -> None:
    with TemporaryDirectory(prefix="universal-agent-runtime-") as directory:
        root = Path(directory)
        db_path = root / "runtime.sqlite3"
        backend = FakePersistentBackend()
        first = build_api(
            db_path,
            backend,
            [
                execute("inspect_workload", "healthy"),
                execute("inspect_pod", "root_cause"),
                execute("scale_workload", "mutation_applied"),
            ],
        )

        waiting = await first.run_goal(
            Goal(
                "Restore workload with sqlite persisted session",
                (SuccessCriterion("healthy", True),),
            ),
            Task("Inspect workload", ()),
        )

        second = build_api(
            db_path,
            backend,
            [
                execute("inspect_workload", "verification_observed", "healthy"),
                Decision(DecisionType.FINISH, "Health verified after persisted resume"),
            ],
        )
        completed = await second.resume_session(waiting.result.session_id, confirmed=True)
        sessions = await second.list_sessions()
        events = await second.list_events(waiting.result.session_id)

        print(f"store={db_path}")
        print(f"initial_status={waiting.result.status.value}")
        print(f"pending_action={waiting.session.pending_action is not None}")
        print(f"completed_status={completed.result.status.value}")
        print(f"goal_status={completed.session.goal_status.value}")
        print(f"mutation_calls={backend.mutation_calls}")
        print(f"session_count={len(sessions)}")
        print(f"event_count={len(events)}")


if __name__ == "__main__":
    asyncio.run(main())
