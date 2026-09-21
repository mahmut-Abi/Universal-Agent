"""Tests for transient-failure retry in the runtime decision boundary."""

from __future__ import annotations

from typing import cast

import pytest

from universal_agent import (
    Decision,
    DecisionType,
    DomainLoader,
    ExecutionStatus,
    Goal,
    RuntimeBuilder,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import DecisionContext
from universal_agent.domains.kubernetes import KubernetesDomain
from universal_agent.model import ModelAdapter, ModelUsage
from universal_agent.model.errors import JsonHttpModelError
from universal_agent.runtime import AgentRuntime, InMemoryEventSink
from universal_agent.state import InMemoryStateStore
from universal_agent_api.types import JsonMapping as JsonMappingType


class FlakyDecisionModel:
    """Model adapter that raises transient timeouts before finishing."""

    def __init__(self, transient_failures: int, permanent: bool = False) -> None:
        self._remaining_transient = transient_failures
        self._permanent = permanent
        self.calls = 0

    async def decide(self, context: DecisionContext) -> Decision:
        self.calls += 1
        if self._permanent:
            raise JsonHttpModelError("model provider returned HTTP 401: unauthorized")
        if self._remaining_transient > 0:
            self._remaining_transient -= 1
            raise JsonHttpModelError(
                "model provider request timed out: Request timed out.",
                transient=True,
            )
        if context.latest_observation is None:
            return Decision(
                DecisionType.EXECUTE,
                "inspect first",
                capability="inspect_workload",
                target="deployment/api",
                arguments=immutable_json({"name": "api", "namespace": "default"}),
                expected_observations=("resource", "healthy"),
            )
        return Decision(
            DecisionType.FINISH,
            "diagnosis complete",
        )

    def model_usage(self) -> ModelUsage | None:
        return None


class FakeKubernetesBackend:
    async def inspect(self, capability: str, arguments: JsonMappingType) -> JsonMappingType:
        return cast(
            JsonMappingType,
            {
                "resource": "deployment/api",
                "healthy": True,
                "desired_replicas": 1,
                "ready_replicas": 1,
            },
        )


def build_runtime(model: ModelAdapter) -> AgentRuntime:
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesDomain(FakeKubernetesBackend()))
    )
    return AgentRuntime(
        model=model,
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=InMemoryEventSink(),
    )


@pytest.mark.asyncio
async def test_runtime_retries_transient_model_timeout_then_succeeds() -> None:
    model = FlakyDecisionModel(transient_failures=1)
    runtime = build_runtime(cast(ModelAdapter, model))

    result = await runtime.run(
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )

    assert model.calls == 3
    assert result.status is ExecutionStatus.COMPLETED


@pytest.mark.asyncio
async def test_runtime_retries_each_transient_failure_up_to_limit() -> None:
    model = FlakyDecisionModel(transient_failures=2)
    runtime = build_runtime(cast(ModelAdapter, model))

    result = await runtime.run(
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )

    assert model.calls == 4
    assert result.status is ExecutionStatus.COMPLETED


@pytest.mark.asyncio
async def test_runtime_does_not_retry_permanent_model_errors() -> None:
    model = FlakyDecisionModel(transient_failures=0, permanent=True)
    runtime = build_runtime(cast(ModelAdapter, model))

    result = await runtime.run(
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )

    assert model.calls == 1
    assert result.status is ExecutionStatus.FAILED
    assert "model failed" in (result.reason or "")


@pytest.mark.asyncio
async def test_runtime_stops_retrying_after_transient_limit() -> None:
    class AlwaysTransient(FlakyDecisionModel):
        def __init__(self) -> None:
            super().__init__(transient_failures=10**6)

        async def decide(self, context: DecisionContext) -> Decision:
            self.calls += 1
            raise JsonHttpModelError(
                "model provider request timed out: Request timed out.",
                transient=True,
            )

    model = AlwaysTransient()
    runtime = build_runtime(cast(ModelAdapter, model))

    result = await runtime.run(
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )

    assert model.calls == 3
    assert result.status is ExecutionStatus.FAILED


def test_openai_transport_marks_http_500_transient() -> None:
    """UA-LIVE-2026-09-21 R6-3: the OpenAI SDK transport must preserve the
    transient classification for 408/429/5xx so the runtime retries provider
    hiccups instead of failing the goal on the first attempt."""
    from unittest.mock import MagicMock

    from universal_agent.model.openai_transport import _openai_status_error

    error = MagicMock()
    error.status_code = 500
    error.response.text = '{"error":{"message":"Internal server error"}}'

    raised = _openai_status_error(error)
    assert raised.transient is True
    assert "HTTP 500" in str(raised)

    error.status_code = 429
    assert _openai_status_error(error).transient is True

    error.status_code = 400
    assert _openai_status_error(error).transient is False
