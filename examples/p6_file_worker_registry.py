from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    DomainConfig,
    RuntimeConfig,
    RuntimeHost,
    ScriptedModelAdapter,
    StoreConfig,
    WorkerId,
    immutable_json,
)
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class Backend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def build_host(workers_path: Path) -> RuntimeHost:
    backend = Backend()
    return RuntimeHost.build(
        config=RuntimeConfig(
            environment=immutable_json({"environment": "local"}),
            distributed_workers=StoreConfig.file(str(workers_path)),
            domain=DomainConfig("kubernetes", "0.2.0"),
        ),
        model=ScriptedModelAdapter([]),
        domain=KubernetesRemediationDomain(backend, backend),
    )


def main() -> None:
    with TemporaryDirectory(prefix="universal-agent-workers-") as directory:
        workers_path = Path(directory) / "workers.json"
        first = build_host(workers_path)
        registered = first.distributed_coordinator.register_worker(
            WorkerId("worker-a"),
            capabilities=("agent_session", "tool_action"),
        )

        second = build_host(workers_path)
        snapshot = second.service.distributed_snapshot()
        assert snapshot is not None

        print(f"worker={registered.worker.worker_id}")
        print(f"workers_file={workers_path.exists()}")
        print(f"reloaded_workers={snapshot.workers.total_count}")
        print(f"reloaded_capabilities={','.join(snapshot.workers.workers[0].capabilities)}")


if __name__ == "__main__":
    main()
