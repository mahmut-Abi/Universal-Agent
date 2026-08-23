from __future__ import annotations

import asyncio

from universal_agent.core import ActionId, SessionId, TaskId, immutable_json
from universal_agent.distributed import (
    InMemoryWorkQueue,
    WorkerId,
    WorkHandlerResult,
    WorkItemStatus,
    WorkKind,
    WorkQueueWorker,
    WorkScheduler,
)


async def main() -> None:
    queue = InMemoryWorkQueue()
    scheduler = WorkScheduler(queue)

    scheduler.schedule_session(
        SessionId("session-1"),
        payload=immutable_json({"goal": "verify workload health"}),
        priority=10,
    )
    scheduler.schedule_action(
        SessionId("session-1"),
        TaskId("task-1"),
        ActionId("action-1"),
        payload=immutable_json({"capability": "inspect_workload"}),
        priority=5,
    )

    worker = WorkQueueWorker(
        queue=queue,
        worker_id=WorkerId("agent-worker-a"),
        handlers={
            WorkKind.AGENT_SESSION.value: lambda item: WorkHandlerResult.completed(
                f"scheduled session {item.session_id}"
            ),
            WorkKind.TOOL_ACTION.value: lambda item: WorkHandlerResult.completed(
                f"scheduled action {item.action_id}"
            ),
        },
    )
    results = await worker.run_until_idle(max_items=5)

    print("statuses=" + ",".join(result.status.value for result in results))
    print(f"completed={len(queue.list(status=WorkItemStatus.COMPLETED))}")
    print(f"queued={len(queue.queued())}")
    print(f"leased={len(queue.leased())}")


if __name__ == "__main__":
    asyncio.run(main())
