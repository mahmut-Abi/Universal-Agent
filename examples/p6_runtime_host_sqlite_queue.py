from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    DomainConfig,
    RuntimeConfig,
    RuntimeHost,
    ScriptedModelAdapter,
    StoreConfig,
    immutable_json,
)
from universal_agent.core import JsonMapping, SessionId
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class Backend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def build_host(queue_path: Path) -> RuntimeHost:
    backend = Backend()
    return RuntimeHost.build(
        config=RuntimeConfig(
            environment=immutable_json({"environment": "local"}),
            distributed_queue=StoreConfig.sqlite(str(queue_path)),
            domain=DomainConfig("kubernetes", "0.2.0"),
        ),
        model=ScriptedModelAdapter([]),
        domain=KubernetesRemediationDomain(backend, backend),
    )


def main() -> None:
    with TemporaryDirectory(prefix="universal-agent-sqlite-queue-") as directory:
        queue_path = Path(directory) / "runtime.sqlite3"
        first = build_host(queue_path)
        scheduled = first.service.distributed_schedule_session(
            SessionId("session-1"),
            priority=5,
        )

        second = build_host(queue_path)
        snapshot = second.service.distributed_snapshot()
        assert scheduled is not None
        assert snapshot is not None

        print(f"scheduled={scheduled.scheduled_work_item.work_item_id}")
        print(f"queue_db={queue_path.exists()}")
        print(f"reloaded_queued={snapshot.work_queue.queued_count}")
        print(f"reloaded_session={snapshot.work_queue.items[0].session_id}")


if __name__ == "__main__":
    main()
