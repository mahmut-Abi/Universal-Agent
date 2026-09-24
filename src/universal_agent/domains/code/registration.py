"""CLI contribution for the code domain."""

from __future__ import annotations

import argparse
from typing import cast

from universal_agent.host_contracts import (
    CliDomainContribution,
    InitDomainOutcome,
)

_CODE_INIT_BACKENDS = ("shell",)


def _add_init_arguments(group: argparse._ArgumentGroup) -> None:
    group.add_argument(
        "--code-workspace",
        help="Path to the codebase directory the Agent operates on.",
    )
    group.add_argument(
        "--code-timeout-seconds",
        type=float,
        default=30.0,
        help="Per-command timeout in seconds.",
    )


def _resolve_init_domain(args: argparse.Namespace) -> InitDomainOutcome | None:
    backend = cast("str | None", getattr(args, "domain_backend", None))
    if backend not in _CODE_INIT_BACKENDS:
        return None

    from universal_agent.domains.code.cli_runtime import profile_domain_config

    return InitDomainOutcome(
        domain_name="code",
        domain_config=profile_domain_config(
            domain_backend=backend,
            code_workspace=cast("str | None", getattr(args, "code_workspace", None)),
            code_timeout_seconds=cast(float, getattr(args, "code_timeout_seconds", 30.0)),
        ),
    )


def code_cli_contribution() -> CliDomainContribution:
    return CliDomainContribution(
        domain="code",
        init_backends=_CODE_INIT_BACKENDS,
        init_add_arguments=_add_init_arguments,
        init_resolve_domain=_resolve_init_domain,
    )


__all__ = ["code_cli_contribution"]
