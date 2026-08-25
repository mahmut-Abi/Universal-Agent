from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import cast

from universal_agent import JsonHttpModelAdapter
from universal_agent.core import (
    CapabilityCategory,
    CapabilitySummary,
    DecisionContext,
    GoalId,
    JsonMapping,
    RiskLevel,
    SessionId,
    TaskId,
    immutable_json,
)
from universal_agent.model import model_usage


class LocalProviderBridge:
    """Example provider bridge that behaves like a JSON HTTP model endpoint."""

    def __init__(self) -> None:
        self.requests: list[JsonMapping] = []

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping:
        self.requests.append(payload)
        return cast(
            JsonMapping,
            {
                "decision": {
                    "type": "execute",
                    "reason": "Inspect the workload before evaluating health.",
                    "capability": "inspect_workload",
                    "target": "deployment/api",
                    "arguments": {"name": "api", "namespace": "prod"},
                    "expected_observations": ["healthy", "ready_replicas"],
                },
                "usage": {"input_tokens": 96, "output_tokens": 24},
            },
        )


def decision_context() -> DecisionContext:
    return DecisionContext(
        session_id=SessionId("session-example"),
        goal_id=GoalId("goal-example"),
        goal_description="Verify production API workload health",
        task_id=TaskId("task-example"),
        task_description="Inspect workload state",
        iteration=1,
        satisfied_criteria=immutable_json(),
        latest_observation=None,
        capabilities=(
            CapabilitySummary(
                "inspect_workload",
                "Inspect a Kubernetes workload",
                CapabilityCategory.OBSERVATION,
                RiskLevel.LOW,
            ),
        ),
        policy_summary=("mutation requires confirmation",),
    )


async def main() -> None:
    bridge = LocalProviderBridge()
    adapter = JsonHttpModelAdapter(
        "https://model-bridge.local/decide",
        "runtime-decider",
        provider="local-bridge",
        transport=bridge,
    )

    decision = await adapter.decide(decision_context())
    usage = model_usage(adapter)

    print(f"decision={decision.type.value} capability={decision.capability}")
    print(f"target={decision.target} arguments={dict(decision.arguments)}")
    print(f"requests={len(bridge.requests)} tokens={0 if usage is None else usage.total_tokens}")


if __name__ == "__main__":
    asyncio.run(main())
