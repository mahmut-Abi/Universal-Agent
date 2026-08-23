from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    DistributedLockOwnerId,
    DomainConfig,
    RuntimeConfig,
    RuntimeHost,
    ScriptedModelAdapter,
    StoreConfig,
    immutable_json,
)
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class Backend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def build_host(locks_path: Path) -> RuntimeHost:
    backend = Backend()
    return RuntimeHost.build(
        config=RuntimeConfig(
            environment=immutable_json({"environment": "local"}),
            distributed_locks=StoreConfig.file(str(locks_path)),
            domain=DomainConfig("kubernetes", "0.2.0"),
        ),
        model=ScriptedModelAdapter([]),
        domain=KubernetesRemediationDomain(backend, backend),
    )


def main() -> None:
    with TemporaryDirectory(prefix="universal-agent-locks-") as directory:
        locks_path = Path(directory) / "distributed-locks.json"
        first = build_host(locks_path)
        acquired = first.distributed_coordinator.acquire_lock(
            lock_key="session/session-1",
            owner_id=DistributedLockOwnerId("worker-a"),
        )

        second = build_host(locks_path)
        snapshot = second.service.distributed_snapshot()
        assert snapshot is not None

        print(f"lease_id={acquired.lock.lease_id}")
        print(f"locks_file={locks_path.exists()}")
        print(f"reloaded_locks={len(snapshot.locks)}")
        print(f"reloaded_owner={snapshot.locks[0].owner_id}")


if __name__ == "__main__":
    main()
