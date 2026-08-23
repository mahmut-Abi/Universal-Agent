from __future__ import annotations

import asyncio

from universal_agent.distributed import (
    InMemoryWorkerRegistry,
    InMemoryWorkQueue,
    WorkerId,
    WorkerRunStatus,
    WorkHandlerResult,
    WorkItemStatus,
    WorkQueueWorker,
)


async def main() -> None:
    queue = InMemoryWorkQueue()
    registry = InMemoryWorkerRegistry()
    queue.enqueue(kind="agent_session", priority=10)
    queue.enqueue(kind="agent_session", priority=5)

    worker = WorkQueueWorker(
        queue=queue,
        worker_id=WorkerId("agent-worker-a"),
        handlers={
            "agent_session": lambda item: WorkHandlerResult.completed(
                f"processed {item.work_item_id}"
            )
        },
        worker_registry=registry,
    )

    results = await worker.run_until_idle(max_items=5)
    registry.drain(WorkerId("agent-worker-a"), reason="maintenance")
    queue.enqueue(kind="agent_session")
    inactive = await worker.run_once()

    print("statuses=" + ",".join(result.status.value for result in results))
    print(f"inactive={inactive.status.value}")
    print(f"completed={len(queue.list(status=WorkItemStatus.COMPLETED))}")
    print(f"queued={len(queue.queued())}")
    print(f"leased={len(queue.leased())}")
    print(f"worker_status={registry.get(WorkerId('agent-worker-a')).status.value}")
    assert inactive.status is WorkerRunStatus.WORKER_INACTIVE


if __name__ == "__main__":
    asyncio.run(main())
