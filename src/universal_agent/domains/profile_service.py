"""Profile -> RuntimeService composition across the built-in domains.

This module lives in the domains package (not the kernel) so no kernel or
runtime module needs to import a concrete domain: it is the single place that
resolves which built-in domain implementation a profile config maps to.

Dispatch contract (shared by the SDK facade, the CLI and agentd):

1. profiles with ``domain_package_paths`` load their packaged domains through
   the generic ``RuntimeHost.from_configured_domain_packages`` boundary;
2. profiles whose first configured domain is ``local`` use the domain-neutral
   Local workspace service (the Golden Path default created by `agent init`);
3. every other profile uses the Kubernetes profile service.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path

from universal_agent.host import RuntimeHost, build_configured_model_adapter
from universal_agent.profile import ProfileConfig
from universal_agent.security import EnvSecretProvider
from universal_agent.service import RuntimeService

__all__ = [
    "build_configured_service",
    "build_default_domain_service",
    "build_default_service",
    "build_probe_service",
]

DEFAULT_DOMAIN_ENTRY_POINT_GROUP = "universal_agent.default_domains"


def build_configured_service(config_path: str | Path) -> RuntimeService:
    """Assemble a RuntimeService from one profile config file."""

    profile = ProfileConfig.from_json_file(config_path).to_profile()
    secret_provider = EnvSecretProvider()
    if profile.runtime.domain_package_paths:
        return RuntimeHost.from_configured_domain_packages(
            config=profile.runtime,
            model=build_configured_model_adapter(profile.runtime, secret_provider=secret_provider),
            profile=profile,
            secret_provider=secret_provider,
        ).service
    configured_domains = profile.runtime.configured_domains()
    if configured_domains and configured_domains[0].name == "local":
        from universal_agent.domains.local.cli_runtime import build_local_profile_service

        return build_local_profile_service(config_path)
    from universal_agent.domains.kubernetes.cli_runtime import (
        build_configured_service as build_kubernetes_service,
    )

    return build_kubernetes_service(config_path)


def build_default_service(*, kubernetes_default: bool = False) -> RuntimeService:
    """Assemble the no-profile-config default service.

    ``kubernetes_default=True`` keeps the historical agentd
    ``--kubernetes-default`` behavior; the default is the domain-neutral Local
    workspace service used by the Golden Path.
    """

    return build_default_domain_service("kubernetes" if kubernetes_default else "local")


def build_default_domain_service(name: str) -> RuntimeService:
    """Assemble the default service of one registered built-in domain.

    Default-domain builders are discovered through the
    ``universal_agent.default_domains`` entry-point group, so this
    composition module never imports a concrete domain.
    """

    for entry_point in entry_points(group=DEFAULT_DOMAIN_ENTRY_POINT_GROUP):
        if entry_point.name == name:
            factory = entry_point.load()
            service = factory()
            if isinstance(service, RuntimeService):
                return service
    raise ValueError(
        f"no default service is registered for domain {name!r} "
        f"(entry-point group {DEFAULT_DOMAIN_ENTRY_POINT_GROUP!r})"
    )


def build_probe_service(config_path: str | Path) -> RuntimeService:
    """Assemble a probe-style RuntimeService metadata without model connect.

    The probe surface is a Kubernetes operator command; profiles for other
    domains must use :func:`build_configured_service`.
    """

    from universal_agent.domains.kubernetes.cli_runtime import build_configured_probe_service

    return build_configured_probe_service(config_path)
