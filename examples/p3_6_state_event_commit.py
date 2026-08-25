from __future__ import annotations

import asyncio
from tempfile import TemporaryDirectory

from universal_agent import (
    AgentRuntime,
    DomainLoader,
    FileRuntimeStore,
    InMemoryEventSink,
    InMemoryStateStore,
    RuntimeAPI,
    RuntimeBuilder,
    ScriptedModelAdapter,
    SQLiteRuntimeStore,
)
from universal_agent.core import JsonMapping, immutable_json
from universal_agent.domains.kubernetes import KubernetesDomain
from universal_agent.runtime import EventReader, EventSink
from universal_agent.state import SessionStore


class FakeKubernetesBackend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json(
            {"healthy": True, "capability": capability, "arguments": dict(arguments)}
        )


def build_api(
    state_store: SessionStore,
    event_sink: EventSink,
    event_reader: EventReader,
) -> RuntimeAPI:
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesDomain(FakeKubernetesBackend()))
    )
    runtime = AgentRuntime(
        model=ScriptedModelAdapter([]),
        state_store=state_store,
        components=components,
        event_sink=event_sink,
    )
    return RuntimeAPI(
        runtime=runtime,
        session_store=state_store,
        event_reader=event_reader,
    )


async def main() -> None:
    with TemporaryDirectory() as tmp:
        memory_store = InMemoryStateStore()
        memory_events = InMemoryEventSink()
        file_store = FileRuntimeStore(f"{tmp}/runtime-file")
        sqlite_store = SQLiteRuntimeStore(f"{tmp}/runtime.sqlite3")

        projections = {
            "memory": build_api(memory_store, memory_events, memory_events).state_event_commit(),
            "file": build_api(file_store, file_store, file_store).state_event_commit(),
            "sqlite": build_api(sqlite_store, sqlite_store, sqlite_store).state_event_commit(),
        }

        for name, projection in projections.items():
            print(
                f"{name}: supported={projection.supported} "
                f"strategy={projection.strategy} shared_store={projection.shared_store}"
            )


if __name__ == "__main__":
    asyncio.run(main())
