from __future__ import annotations

from universal_agent.domains.kubernetes.cli_parser import (
    LOCAL_PROFILE_NAME,
    add_kubernetes_command,
    is_kubernetes_probe_service_command,
)
from universal_agent.domains.kubernetes.cli_reports import dispatch_kubernetes
from universal_agent.domains.kubernetes.cli_runtime import (
    build_configured_probe_service,
    build_configured_service,
    build_default_service,
    profile_domain_config,
)

__all__ = [
    "LOCAL_PROFILE_NAME",
    "add_kubernetes_command",
    "build_configured_probe_service",
    "build_configured_service",
    "build_default_service",
    "dispatch_kubernetes",
    "is_kubernetes_probe_service_command",
    "profile_domain_config",
]
