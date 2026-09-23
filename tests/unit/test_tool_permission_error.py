"""Tool executor maps ToolPermissionError to PERMISSION_DENIED
(UA-LIVE-2026-09-21 baseline S3 finding)."""

from __future__ import annotations

import pytest

from universal_agent.core import (
    ErrorCode,
    RiskLevel,
    SideEffect,
    ToolDefinition,
)
from universal_agent.tools.runtime import ToolPermissionError, ToolRuntime

pytestmark = pytest.mark.asyncio


class DenyingTool:
    definition = ToolDefinition(
        "denying_tool",
        "Always denied by the remote system",
        ("denying_tool",),
        required_arguments=(),
        side_effect=SideEffect.NONE,
        risk=RiskLevel.LOW,
        argument_schema={},
    )

    async def execute(self, arguments: dict[str, object]) -> dict[str, object]:
        raise ToolPermissionError("403 forbidden by remote system")


def _runtime_with_denying_tool() -> ToolRuntime:
    from universal_agent.tools.runtime import ToolRegistry

    registry = ToolRegistry()
    registry.register(DenyingTool())
    return ToolRuntime(registry)


@pytest.mark.unit
async def test_tool_permission_error_maps_to_permission_denied() -> None:
    from universal_agent.core import ActionId, ToolCall

    runtime = _runtime_with_denying_tool()
    result = await runtime.execute(
        ToolCall(
            ActionId("action-1"),
            "denying_tool",
            "denying_tool",
            {},
        )
    )

    assert result.status.value == "failed"
    assert result.error_code is ErrorCode.PERMISSION_DENIED
    assert "403" in (result.error or "")
