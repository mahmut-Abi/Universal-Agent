"""Backward-compatible import shim for the Runtime SDK."""

from universal_agent.service.sdk import (
    RuntimeSDKError,
    SDKGoal,
    SDKRunResult,
    SDKSuccessCriterion,
    SDKTask,
    UniversalAgentRuntime,
)

__all__ = [
    "RuntimeSDKError",
    "SDKGoal",
    "SDKRunResult",
    "SDKSuccessCriterion",
    "SDKTask",
    "UniversalAgentRuntime",
]
