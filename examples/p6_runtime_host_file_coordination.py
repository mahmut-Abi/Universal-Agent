from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    Decision,
    DecisionType,
    DomainConfig,
    Goal,
    RuntimeConfig,
    RuntimeHost,
    ScriptedModelAdapter,
    StoreConfig,
    SuccessCriterion,
    Task,
    WorkerId,
    immutable_json,
)
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class Backend:
    def __init__(self) -> None:
        self.healthy = False

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "healthy": self.healthy})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def build_host(
    *,
    root: Path,
    backend: Backend,
    decisions: list[Decision],
) -> RuntimeHost:
    return RuntimeHost.build(
        config=RuntimeConfig(
            environment=immutable_json({"environment": "local"}),
            store=StoreConfig.file(str(root / "runtime-store")),
            distributed_queue=StoreConfig.file(str(root / "coordination" / "work-queue.json")),
            distributed_locks=StoreConfig.file(
                str(root / "coordination" / "distributed-locks.json")
            ),
            distributed_workers=StoreConfig.file(str(root / "coordination" / "workers.json")),
            domain=DomainConfig("kubernetes", "0.2.0"),
        ),
        model=ScriptedModelAdapter(decisions),
        domain=KubernetesRemediationDomain(backend, backend),
    )


async def main() -> None:
    backend = Backend()
    with TemporaryDirectory(prefix="universal-agent-file-coordination-") as directory:
        root = Path(directory)
        first = build_host(
            root=root,
            backend=backend,
            decisions=[Decision(DecisionType.WAIT, "pause for file-backed worker")],
        )
        waiting = await first.service.run_goal(
            Goal("Verify workload through file coordination", (SuccessCriterion("healthy", True),)),
            Task("Inspect workload", ("healthy",)),
        )
        scheduled = first.service.distributed_schedule_session(waiting.result.session_id)

        backend.healthy = True
        second = build_host(
            root=root,
            backend=backend,
            decisions=[
                Decision(
                    DecisionType.EXECUTE,
                    "Inspect after file-backed worker resume",
                    capability="inspect_workload",
                    target="deployment/example",
                    arguments=immutable_json({"name": "example"}),
                    expected_observations=("healthy",),
                ),
                Decision(DecisionType.FINISH, "Health evidence is present"),
            ],
        )
        worker = await second.service.distributed_run_worker_once(WorkerId("worker-a"))

        third = build_host(root=root, backend=backend, decisions=[])
        snapshot = third.service.distributed_snapshot()
        completed = await third.service.get_session(waiting.result.session_id)
        assert scheduled is not None
        assert worker is not None
        assert snapshot is not None

        print(f"session={completed.goal_status.value}")
        print(f"work_item={worker.work_item.status.value if worker.work_item else None}")
        print(f"queue_completed={snapshot.work_queue.completed_count}")
        print(f"locks_active={len(snapshot.locks)}")
        print(f"workers={snapshot.workers.total_count}")


if __name__ == "__main__":
    asyncio.run(main())
