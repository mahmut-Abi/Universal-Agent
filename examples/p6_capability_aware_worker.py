from __future__ import annotations

import asyncio

from universal_agent.distributed import (
    InMemoryWorkerRegistry,
    InMemoryWorkQueue,
    WorkerId,
    WorkHandlerResult,
    WorkItem,
    WorkItemStatus,
    WorkQueueWorker,
)


async def main() -> None:
    queue = InMemoryWorkQueue()
    registry = InMemoryWorkerRegistry()
    unsupported = queue.enqueue(kind="tool_action", priority=10)
    supported = queue.enqueue(kind="agent_session", priority=1)

    async def handle_session_work(item: WorkItem) -> WorkHandlerResult:
        await asyncio.sleep(0.05)
        return WorkHandlerResult.completed(f"processed {item.work_item_id}")

    worker = WorkQueueWorker(
        queue=queue,
        worker_id=WorkerId("agent-worker-a"),
        handlers={"agent_session": handle_session_work},
        worker_registry=registry,
        lease_ttl_seconds=0.1,
        heartbeat_interval_seconds=0.02,
    )

    result = await worker.run_once()

    print(f"processed={result.work_item.work_item_id if result.work_item else None}")
    print(f"supported_status={queue.get(supported.work_item_id).status.value}")
    print(f"unsupported_status={queue.get(unsupported.work_item_id).status.value}")
    print(f"completed={len(queue.list(status=WorkItemStatus.COMPLETED))}")
    print(f"queued={len(queue.queued())}")
    print(f"worker_heartbeat={registry.get(WorkerId('agent-worker-a')).heartbeat_at.isoformat()}")


if __name__ == "__main__":
    asyncio.run(main())
