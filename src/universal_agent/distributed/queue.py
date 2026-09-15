"""Work queue backends: in-memory, file-locked and SQLite.

The implementations live in ``queue_memory`` / ``queue_file`` /
``queue_sqlite``; this module is the stable import surface.
"""

from __future__ import annotations

__all__ = [
    "FencingToken",
    "FileWorkQueue",
    "InMemoryWorkQueue",
    "LeaseId",
    "LeaseLostError",
    "NoWorkAvailable",
    "SQLiteWorkQueue",
    "WorkItem",
    "WorkItemId",
    "WorkItemNotFoundError",
    "WorkItemStatus",
    "WorkerId",
    "WorkerLease",
]


from universal_agent.distributed.queue_file import FileWorkQueue
from universal_agent.distributed.queue_memory import InMemoryWorkQueue
from universal_agent.distributed.queue_models import (
    FencingToken,
    LeaseId,
    LeaseLostError,
    NoWorkAvailable,
    WorkerId,
    WorkerLease,
    WorkItem,
    WorkItemId,
    WorkItemNotFoundError,
    WorkItemStatus,
)
from universal_agent.distributed.queue_sqlite import SQLiteWorkQueue
