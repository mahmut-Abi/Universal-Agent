"""Workspace Domain contribution to the CLI surface (extension point).

Provides `agent init --domain-backend workspace`: the init arguments add a
sandboxed workspace path option, and the resolver returns the workspace
domain config. The contribution loader sorts alphabetically and the CLI
resolves exact backend claims before unconditional fallbacks, so this
contribution is reachable even though 'workspace' sorts after 'local'.

The CLI shell discovers this module through the
``universal_agent.cli_contributions`` entry-point group; it never names a
concrete domain.
"""

from __future__ import annotations

import argparse

from universal_agent.domains.workspace.cli_runtime import (
    workspace_domain_config,
)
from universal_agent.host_contracts import CliDomainContribution, InitDomainOutcome

WORKSPACE_INIT_BACKEND = "workspace"


def _add_init_arguments(group: argparse._ArgumentGroup) -> None:
    group.add_argument(
        "--workspace-path",
        default=".",
        help="Sandboxed directory the workspace domain operates on.",
    )


def _resolve_init_domain(args: argparse.Namespace) -> InitDomainOutcome | None:
    backend = getattr(args, "domain_backend", None)
    if backend != WORKSPACE_INIT_BACKEND:
        return None
    workspace_path = str(getattr(args, "workspace_path", ".") or ".")
    return InitDomainOutcome(
        domain_name="workspace",
        domain_config=workspace_domain_config(workspace_path),
    )


def workspace_cli_contribution() -> CliDomainContribution:
    """Entry-point factory for the workspace domain CLI contribution."""

    return CliDomainContribution(
        domain="workspace",
        init_backends=(WORKSPACE_INIT_BACKEND,),
        init_add_arguments=_add_init_arguments,
        init_resolve_domain=_resolve_init_domain,
    )


__all__ = ["WORKSPACE_INIT_BACKEND", "workspace_cli_contribution"]
