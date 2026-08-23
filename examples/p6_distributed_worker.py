from __future__ import annotations

import asyncio

from universal_agent.distributed import (
    InMemoryWorkQueue,
    WorkerId,
    WorkHandlerResult,
    WorkItemStatus,
    WorkQueueWorker,
)


async def main() -> None:
    queue = InMemoryWorkQueue()
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
    )

    results = await worker.run_until_idle(max_items=5)

    print("statuses=" + ",".join(result.status.value for result in results))
    print(f"completed={len(queue.list(status=WorkItemStatus.COMPLETED))}")
    print(f"queued={len(queue.queued())}")
    print(f"leased={len(queue.leased())}")


if __name__ == "__main__":
    asyncio.run(main())
