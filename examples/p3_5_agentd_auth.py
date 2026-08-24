from __future__ import annotations

import asyncio

from universal_agent import (
    AgentRuntime,
    DomainLoader,
    InMemoryEventSink,
    InMemoryStateStore,
    RuntimeAPI,
    RuntimeBuilder,
    RuntimeService,
    ScriptedModelAdapter,
    immutable_json,
)
from universal_agent.agentd import AgentdApp, AgentdAuthPolicy, HttpRequest
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class AuthExampleBackend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def build_service() -> RuntimeService:
    backend = AuthExampleBackend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter([]),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "local"}),
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )


async def main() -> None:
    app = AgentdApp(build_service(), auth=AgentdAuthPolicy("local-token"))

    public = await app.handle(HttpRequest("GET", "/health"))
    denied = await app.handle(HttpRequest("GET", "/v1/config"))
    allowed = await app.handle(
        HttpRequest(
            "GET",
            "/v1/config",
            headers={"authorization": "Bearer local-token"},
        )
    )

    print(f"health_status={public.status_code}")
    print(f"unauthorized_status={denied.status_code}")
    print(f"authorized_status={allowed.status_code}")
    print(f"auth_header={denied.headers['www-authenticate']}")


if __name__ == "__main__":
    asyncio.run(main())
