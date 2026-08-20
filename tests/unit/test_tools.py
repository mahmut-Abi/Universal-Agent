from __future__ import annotations

import asyncio

import pytest

from universal_agent.core import (
    ErrorCode,
    ObservationStatus,
    ToolCall,
    ToolDefinition,
    immutable_json,
    new_action_id,
)
from universal_agent.tools import DuplicateToolError, ToolRegistry, ToolRuntime


class EchoTool:
    definition = ToolDefinition(
        name="echo",
        description="Return the provided value",
        capabilities=("echo_value",),
        required_arguments=("value",),
        timeout_seconds=0.1,
    )

    async def execute(self, arguments):  # type: ignore[no-untyped-def]
        return immutable_json({"value": arguments["value"]})


class BrokenTool:
    definition = ToolDefinition("broken", "Fail", ("break",))

    async def execute(self, arguments):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")


class SlowTool:
    definition = ToolDefinition("slow", "Timeout", ("wait",), timeout_seconds=0.001)

    async def execute(self, arguments):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0.02)
        return immutable_json()


def call(tool: str, capability: str, arguments=None):  # type: ignore[no-untyped-def]
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
async def test_tool_runtime_normalizes_unknown_failure_and_timeout() -> None:
    registry = ToolRegistry()
    registry.register(BrokenTool())
    registry.register(SlowTool())
    runtime = ToolRuntime(registry)
    unknown = await runtime.execute(call("missing", "missing"))
    broken = await runtime.execute(call("broken", "break"))
    slow = await runtime.execute(call("slow", "wait"))
    assert unknown.error_code is ErrorCode.UNKNOWN_TOOL
    assert broken.error_code is ErrorCode.TOOL_FAILURE
    assert slow.error_code is ErrorCode.TIMEOUT
