from __future__ import annotations

import asyncio

import pytest

from universal_agent.core import (
    DomainIdentity,
    ErrorCode,
    JsonMapping,
    ObservationStatus,
    ToolCall,
    ToolDefinition,
    immutable_json,
    new_action_id,
)
from universal_agent.tools import (
    DuplicateToolError,
    ToolRegistry,
    ToolRuntime,
    UncertainToolExecutionError,
)


class EchoTool:
    definition = ToolDefinition(
        name="echo",
        description="Return the provided value",
        capabilities=("echo_value",),
        required_arguments=("value",),
        timeout_seconds=0.1,
    )

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"value": arguments["value"]})


class BrokenTool:
    definition = ToolDefinition("broken", "Fail", ("break",))

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        raise RuntimeError("boom")


class SlowTool:
    definition = ToolDefinition("slow", "Timeout", ("wait",), timeout_seconds=0.001)

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        await asyncio.sleep(0.02)
        return immutable_json()


class UncertainTool:
    definition = ToolDefinition("uncertain", "Unknown outcome", ("mutate",))

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        raise UncertainToolExecutionError("connection closed after dispatch")


def call(tool: str, capability: str, arguments: JsonMapping | None = None) -> ToolCall:
    return ToolCall(new_action_id(), tool, capability, immutable_json(arguments))


def test_registry_rejects_duplicate_names() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    with pytest.raises(DuplicateToolError):
        registry.register(EchoTool())


@pytest.mark.asyncio
async def test_tool_runtime_validates_required_arguments() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    result = await ToolRuntime(registry).execute(call("echo", "echo_value"))
    assert result.status is ObservationStatus.FAILED
    assert result.error_code is ErrorCode.VALIDATION_ERROR


@pytest.mark.asyncio
async def test_tool_runtime_rejects_domain_mismatch() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool(), DomainIdentity("alpha", "1.0.0"))
    runtime = ToolRuntime(registry)
    result = await runtime.execute(
        ToolCall(
            new_action_id(),
            "echo",
            "echo_value",
            immutable_json({"value": "ok"}),
            domain_name="beta",
            domain_version="1.0.0",
        )
    )

    assert result.status is ObservationStatus.FAILED
    assert result.error_code is ErrorCode.VALIDATION_ERROR
    assert result.error is not None
    assert "domain mismatch" in result.error

    partial = await runtime.execute(
        ToolCall(
            new_action_id(),
            "echo",
            "echo_value",
            immutable_json({"value": "ok"}),
            domain_name="alpha",
        )
    )
    assert partial.error_code is ErrorCode.VALIDATION_ERROR


@pytest.mark.asyncio
async def test_tool_runtime_normalizes_unknown_failure_and_timeout() -> None:
    registry = ToolRegistry()
    registry.register(BrokenTool())
    registry.register(SlowTool())
    registry.register(UncertainTool())
    runtime = ToolRuntime(registry)
    unknown = await runtime.execute(call("missing", "missing"))
    broken = await runtime.execute(call("broken", "break"))
    slow = await runtime.execute(call("slow", "wait"))
    uncertain = await runtime.execute(call("uncertain", "mutate"))
    assert unknown.error_code is ErrorCode.UNKNOWN_TOOL
    assert broken.error_code is ErrorCode.TOOL_FAILURE
    assert slow.error_code is ErrorCode.TIMEOUT
    assert uncertain.status is ObservationStatus.UNKNOWN
    assert uncertain.error_code is ErrorCode.UNKNOWN_EXECUTION
    assert uncertain.error is not None
    assert "connection closed after dispatch" in uncertain.error
