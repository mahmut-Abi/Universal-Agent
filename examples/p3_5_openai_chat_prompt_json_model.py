from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import cast

from universal_agent import OpenAIChatCompletionsModelAdapter
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


class LegacyOpenAICompatibleTransport:
    """Offline fixture for Chat Completions providers without response_format support."""

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
        return immutable_json(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "```json\n"
                            + json.dumps(
                                {
                                    "type": "execute",
                                    "reason": "Inspect workload health before remediation.",
                                    "capability": "inspect_workload",
                                    "target": "deployment/api",
                                    "arguments": {"name": "api", "namespace": "prod"},
                                    "expected_observations": ["healthy", "ready_replicas"],
                                    "message": None,
                                },
                                sort_keys=True,
                            )
                            + "\n```",
                        },
                    }
                ],
                "usage": {"prompt_tokens": 144, "completion_tokens": 36},
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
                required_arguments=("name",),
                argument_schema=immutable_json(
                    {
                        "required": ["name"],
                        "properties": {"name": {"type": "string", "minLength": 1}},
                    }
                ),
            ),
        ),
        goal_success_criteria=(),
        current_task_required_criteria=("healthy",),
        policy_summary=("mutation requires confirmation",),
    )


async def main() -> None:
    transport = LegacyOpenAICompatibleTransport()
    adapter = OpenAIChatCompletionsModelAdapter(
        "gpt-runtime",
        api_key="example-api-key",
        response_format="prompt_json",
        transport=transport,
    )

    decision = await adapter.decide(decision_context())
    request = transport.requests[0]
    messages = cast(list[object], request["messages"])

    print(f"decision={decision.type.value} capability={decision.capability}")
    print(f"target={decision.target} arguments={dict(decision.arguments)}")
    print(f"messages={len(messages)} response_format_sent={'response_format' in request}")


if __name__ == "__main__":
    asyncio.run(main())
