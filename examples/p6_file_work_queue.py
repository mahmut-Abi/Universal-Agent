from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent.core import SessionId, immutable_json
from universal_agent.distributed import (
    FileWorkQueue,
    WorkerId,
    WorkHandlerResult,
    WorkItemStatus,
    WorkQueueWorker,
    WorkScheduler,
)


async def main() -> None:
    with TemporaryDirectory() as directory:
        queue_path = Path(directory) / "work-queue.json"
        scheduler = WorkScheduler(FileWorkQueue(queue_path))
        scheduled = scheduler.schedule_session(
            SessionId("session-1"),
            payload=immutable_json({"goal": "verify workload health"}),
            priority=5,
        )

        worker = WorkQueueWorker(
            queue=FileWorkQueue(queue_path),
            worker_id=WorkerId("agent-worker-a"),
            handlers={
                "agent_session": lambda item: WorkHandlerResult.completed(
                    f"processed {item.session_id}"
                )
            },
        )
        result = await worker.run_once()
        reloaded = FileWorkQueue(queue_path)

        print(f"scheduled={scheduled.work_item_id}")
        print(f"worker={result.status.value}")
        print(f"completed={len(reloaded.list(status=WorkItemStatus.COMPLETED))}")
        print(f"queued={len(reloaded.queued())}")


if __name__ == "__main__":
    asyncio.run(main())
