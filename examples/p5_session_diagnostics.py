from __future__ import annotations

import asyncio

from universal_agent import Goal, SuccessCriterion, Task
from universal_agent_cli import build_default_service


async def main() -> None:
    service = build_default_service()
    run = await service.run_goal(
        Goal("Inspect session diagnostics", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    explorer = await service.session_explorer(run.result.session_id)

    print(f"session={explorer.session.session_id}")
    print(f"evidence={len(explorer.evidence)}")
    print(f"world_facts={len(explorer.world_facts)}")
    for fact in explorer.world_facts:
        print(f"{fact.subject}.{fact.claim}={fact.value}")


if __name__ == "__main__":
    asyncio.run(main())
