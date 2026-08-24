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


class SchemaTool:
    def __init__(self) -> None:
        self.calls = 0
        self.definition = ToolDefinition(
            name="schema",
            description="Validate structured arguments",
            capabilities=("schema_value",),
            argument_schema=immutable_json(
                {
                    "required": ["name", "count", "mode"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "count": {"type": "integer", "minimum": 1, "maximum": 3},
                        "mode": {"type": "string", "enum": ["safe", "fast"]},
                    },
                    "additionalProperties": False,
                }
            ),
        )

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        return immutable_json({"accepted": True})


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
async def test_tool_runtime_validates_argument_schema_before_execution() -> None:
    registry = ToolRegistry()
    tool = SchemaTool()
    registry.register(tool)
    runtime = ToolRuntime(registry)

    missing = await runtime.execute(call("schema", "schema_value", {"name": "example"}))
    wrong_type = await runtime.execute(
        call("schema", "schema_value", {"name": "example", "count": "2", "mode": "safe"})
    )
    out_of_range = await runtime.execute(
        call("schema", "schema_value", {"name": "example", "count": 0, "mode": "safe"})
    )
    bad_enum = await runtime.execute(
        call("schema", "schema_value", {"name": "example", "count": 1, "mode": "unsafe"})
    )
    unexpected = await runtime.execute(
        call(
            "schema",
            "schema_value",
            {"name": "example", "count": 1, "mode": "safe", "extra": True},
        )
    )
    accepted = await runtime.execute(
        call("schema", "schema_value", {"name": "example", "count": 2, "mode": "safe"})
    )

    assert missing.error == "missing required arguments: count, mode"
    assert wrong_type.error == "argument count must be integer"
    assert out_of_range.error == "argument count must be >= 1"
    assert bad_enum.error == "argument mode must be one of 'safe', 'fast'"
    assert unexpected.error == "unexpected arguments: extra"
    assert accepted.status is ObservationStatus.SUCCEEDED
    assert tool.calls == 1


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
