from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import cast

from universal_agent import OpenAIResponsesModelAdapter
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


class LocalOpenAIResponsesTransport:
    """Offline fixture with the same response shape the adapter consumes."""

    def __init__(self) -> None:
        self.requests: list[JsonMapping] = []
        self.headers: list[Mapping[str, str]] = []

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping:
        self.requests.append(payload)
        self.headers.append(headers)
        return immutable_json(
            {
                "status": "completed",
                "output_text": json.dumps(
                    {
                        "type": "execute",
                        "reason": "Inspect the workload before evaluating health.",
                        "capability": "inspect_workload",
                        "target": "deployment/api",
                        "arguments": {"name": "api", "namespace": "prod"},
                        "expected_observations": ["healthy", "ready_replicas"],
                        "message": None,
                    },
                    sort_keys=True,
                ),
                "usage": {"input_tokens": 128, "output_tokens": 32},
            }
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
    transport = LocalOpenAIResponsesTransport()
    adapter = OpenAIResponsesModelAdapter(
        "gpt-runtime",
        api_key="example-api-key",
        transport=transport,
    )

    decision = await adapter.decide(decision_context())
    usage = model_usage(adapter)
    request = transport.requests[0]
    text = cast(Mapping[str, object], request["text"])
    text_format = cast(Mapping[str, object], text["format"])

    print(f"decision={decision.type.value} capability={decision.capability}")
    print(f"target={decision.target} arguments={dict(decision.arguments)}")
    print(f"provider_tokens={0 if usage is None else usage.total_tokens}")
    print(f"structured_output={text_format['type']}")


if __name__ == "__main__":
    asyncio.run(main())
