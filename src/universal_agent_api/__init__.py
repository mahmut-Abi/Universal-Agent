"""Client SDK for the Universal Agent Runtime API.

Clients (CLI, TUI, Web) depend only on this SDK — never on the
``universal_agent`` kernel — so the packages can be extracted to their own
repository while talking to the runtime purely over its HTTP API.

The SDK owns wire concerns only: URL normalization, auth headers,
request/response JSON, SSE event streaming and HTTP error mapping.
"""

from __future__ import annotations

from universal_agent_api.client import (
    AgentdClient,
    AgentdClientError,
    AgentdClientResponse,
    AgentdTextResponse,
    quote_path_segment,
)
from universal_agent_api.types import JsonMapping, JsonValue, SessionId

__all__ = [
    "AgentdClient",
    "AgentdClientError",
    "AgentdClientResponse",
    "AgentdTextResponse",
    "JsonMapping",
    "JsonValue",
    "SessionId",
    "quote_path_segment",
]
