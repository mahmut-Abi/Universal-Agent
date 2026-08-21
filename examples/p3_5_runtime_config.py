from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    Decision,
    DecisionType,
    Goal,
    RuntimeConfig,
    RuntimeHost,
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.agentd import AgentdApp, HttpRequest
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class FakeConfiguredBackend:
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
        f"Run {capability} through configured RuntimeHost",
        capability=capability,
        target=target,
        arguments=immutable_json(arguments),
        expected_observations=observations,
    )


def build_host(
    config: RuntimeConfig,
    backend: FakeConfiguredBackend,
    decisions: list[Decision],
) -> RuntimeHost:
    return RuntimeHost.build(
        config=config,
        model=ScriptedModelAdapter(decisions),
        domain=KubernetesRemediationDomain(backend, backend),
    )


async def main() -> None:
    with TemporaryDirectory(prefix="universal-agent-config-") as directory:
        root = Path(directory)
        config_path = root / "runtime-config.json"
        config_path.write_text(
            json.dumps(
                {
                    "environment": {"environment": "production"},
                    "store": {"backend": "file", "path": str(root / "store")},
                    "limits": {"max_iterations": 8, "max_recovery_steps": 4},
                    "domain": {"name": "kubernetes", "version": "0.2.0"},
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        config = RuntimeConfig.from_json_file(config_path)
        backend = FakeConfiguredBackend()
        first = build_host(
            config,
            backend,
            [
                execute("inspect_workload", "healthy"),
                execute("inspect_pod", "root_cause"),
                execute("scale_workload", "mutation_applied"),
            ],
        )
        app = AgentdApp(first.service)

        ready = await app.handle(HttpRequest("GET", "/ready"))
        waiting = await first.service.run_goal(
            Goal(
                "Restore workload through configured RuntimeHost",
                (SuccessCriterion("healthy", True),),
            ),
            Task("Inspect workload", ()),
        )

        second = build_host(
            config,
            backend,
            [
                execute("inspect_workload", "verification_observed", "healthy"),
                Decision(DecisionType.FINISH, "Health verified after configured resume"),
            ],
        )
        completed = await second.runtime_api.resume_session(
            waiting.result.session_id, confirmed=True
        )
        events = await second.service.list_events(waiting.result.session_id)

        print(f"config={config_path}")
        print(f"store_backend={config.store.backend.value}")
        print(f"domain={second.domain_identity.name}@{second.domain_identity.version}")
        print(f"ready={ready.body['ready']}")
        print(f"initial_status={waiting.result.status.value}")
        print(f"pending_action={waiting.session.pending_action is not None}")
        print(f"completed_status={completed.result.status.value}")
        print(f"mutation_calls={backend.mutation_calls}")
        print(f"event_count={len(events)}")


if __name__ == "__main__":
    asyncio.run(main())
