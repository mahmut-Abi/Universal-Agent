from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    Decision,
    DecisionType,
    ProfileConfig,
    RuntimeHost,
    ScriptedModelAdapter,
    immutable_json,
)
from universal_agent.agentd import AgentdApp, HttpRequest
from universal_agent.core import JsonMapping, JsonValue, SessionId
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


def session_request_body(*, profile: str) -> JsonMapping:
    success_criterion: dict[str, JsonValue] = {"key": "healthy", "expected": True}
    goal_payload: dict[str, JsonValue] = {
        "description": "Restore workload through configured RuntimeHost",
        "success_criteria": [success_criterion],
    }
    task_payload: dict[str, JsonValue] = {
        "description": "Inspect workload",
        "required_criteria": [],
    }
    return immutable_json({"profile": profile, "goal": goal_payload, "task": task_payload})


def build_host(
    profile: ProfileConfig,
    backend: FakeConfiguredBackend,
    decisions: list[Decision],
) -> RuntimeHost:
    return RuntimeHost.from_profile(
        profile=profile.to_profile(),
        model=ScriptedModelAdapter(decisions),
        domain=KubernetesRemediationDomain(backend, backend),
    )


async def main() -> None:
    with TemporaryDirectory(prefix="universal-agent-config-") as directory:
        root = Path(directory)
        profile_path = root / "profile.json"
        profile_path.write_text(
            json.dumps(
                {
                    "name": "production-operator",
                    "version": "1.0.0",
                    "description": "Production Kubernetes operator",
                    "domain": {"name": "kubernetes", "version": "0.2.0"},
                    "runtime": {
                        "environment": {"environment": "production"},
                        "secrets": {
                            "openai_api_key": {
                                "source": "env",
                                "key": "OPENAI_API_KEY",
                                "required": True,
                            }
                        },
                        "store": {"backend": "file", "path": str(root / "store")},
                        "limits": {"max_iterations": 8, "max_recovery_steps": 4},
                        "domain": {"name": "kubernetes", "version": "0.2.0"},
                    },
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        profile = ProfileConfig.from_json_file(profile_path)
        backend = FakeConfiguredBackend()
        first = build_host(
            profile,
            backend,
            [
                execute("inspect_workload", "healthy"),
                execute("inspect_pod", "root_cause"),
                execute("scale_workload", "mutation_applied"),
            ],
        )
        app = AgentdApp(first.service)

        ready = await app.handle(HttpRequest("GET", "/ready"))
        profiles = await app.handle(HttpRequest("GET", "/v1/profiles"))
        created = await app.handle(
            HttpRequest(
                "POST",
                "/v1/sessions",
                session_request_body(profile="production-operator"),
            )
        )
        created_result = created.body["result"]
        assert isinstance(created_result, dict)
        session_id = created_result["session_id"]
        assert isinstance(session_id, str)

        second = build_host(
            profile,
            backend,
            [
                execute("inspect_workload", "verification_observed", "healthy"),
                Decision(DecisionType.FINISH, "Health verified after configured resume"),
            ],
        )
        completed = await AgentdApp(second.service).handle(
            HttpRequest(
                "POST",
                f"/v1/sessions/{session_id}/resume",
                immutable_json({"confirmed": True}),
            )
        )
        completed_result = completed.body["result"]
        assert isinstance(completed_result, dict)
        events = await second.service.list_events(SessionId(session_id))
        profile_items = profiles.body["profiles"]
        assert isinstance(profile_items, list)

        print(f"profile={profile_path}")
        print(f"store_backend={profile.runtime.store.backend.value}")
        print(f"secret_ref_count={len(first.service.config().secrets)}")
        print(f"domain={second.domain_identity.name}@{second.domain_identity.version}")
        print(f"profile_count={len(profile_items)}")
        print(f"ready={ready.body['ready']}")
        print(f"initial_status={created_result['status']}")
        print(f"completed_status={completed_result['status']}")
        print(f"mutation_calls={backend.mutation_calls}")
        print(f"event_count={len(events)}")


if __name__ == "__main__":
    asyncio.run(main())
