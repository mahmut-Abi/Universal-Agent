from __future__ import annotations

from universal_agent.web.pages import (
    render_web_catalog,
    render_web_console,
    render_web_distributed,
    render_web_doctor,
    render_web_domain_detail,
    render_web_domain_package_detail,
    render_web_evidence_explorer,
    render_web_multi_agent,
    render_web_profile_catalog,
    render_web_session_detail,
    render_web_sessions,
    render_web_settings,
    render_web_world_model_explorer,
)
from universal_agent.web.types import (
    WebCatalogPage,
    WebConsoleSnapshot,
    build_web_console_snapshot,
)

__all__ = [
    "WebCatalogPage",
    "WebConsoleSnapshot",
    "build_web_console_snapshot",
    "render_web_catalog",
    "render_web_console",
    "render_web_distributed",
    "render_web_doctor",
    "render_web_domain_detail",
    "render_web_domain_package_detail",
    "render_web_evidence_explorer",
    "render_web_multi_agent",
    "render_web_profile_catalog",
    "render_web_session_detail",
    "render_web_sessions",
    "render_web_settings",
    "render_web_world_model_explorer",
]
