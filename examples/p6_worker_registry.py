from __future__ import annotations

from datetime import UTC, datetime, timedelta

from universal_agent.distributed import InMemoryWorkerRegistry, WorkerId, WorkerStatus


def main() -> None:
    registry = InMemoryWorkerRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    registered = registry.register(
        WorkerId("agent-worker-a"),
        capabilities=("agent_session", "tool_action"),
        ttl_seconds=30,
        now=now,
    )
    heartbeat = registry.heartbeat(
        registered.worker_id,
        ttl_seconds=30,
        now=now + timedelta(seconds=10),
    )
    registry.register(WorkerId("agent-worker-b"), ttl_seconds=5, now=now)
    expired = registry.expire(now=now + timedelta(seconds=6))

    print(f"worker={heartbeat.worker_id}")
    print(f"status={heartbeat.status.value}")
    print(f"active={len(registry.active())}")
    print(f"lost={len(registry.list(status=WorkerStatus.LOST))}")
    print("expired=" + ",".join(str(item.worker_id) for item in expired))


if __name__ == "__main__":
    main()
