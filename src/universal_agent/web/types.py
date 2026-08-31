from __future__ import annotations

from enum import StrEnum

from universal_agent.core import SessionId
from universal_agent.service import RuntimeService
from universal_agent.terminal.console import RuntimeConsoleSnapshot, build_runtime_console_snapshot

WebConsoleSnapshot = RuntimeConsoleSnapshot


class WebCatalogPage(StrEnum):
    DOMAINS = "domains"
    DOMAIN_PACKAGES = "domain-packages"
    CAPABILITIES = "capabilities"
    TOOLS = "tools"
    POLICIES = "policies"
    EVALUATORS = "evaluators"
    MEMORY = "memory"


async def build_web_console_snapshot(
    service: RuntimeService,
    *,
    session_id: SessionId | None = None,
    session_limit: int = 10,
    event_limit: int = 20,
    world_entity_id: str | None = None,
    world_relation: str | None = None,
) -> WebConsoleSnapshot:
    """Build a read-only Web Console snapshot from RuntimeService projections."""

    return await build_runtime_console_snapshot(
        service,
        session_id=session_id,
        session_limit=session_limit,
        event_limit=event_limit,
        world_entity_id=world_entity_id,
        world_relation=world_relation,
    )


__all__ = [
    "WebCatalogPage",
    "WebConsoleSnapshot",
    "build_web_console_snapshot",
]
