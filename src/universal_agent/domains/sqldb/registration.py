"""CLI contribution for the sqldb domain."""

from __future__ import annotations

import argparse
from typing import cast

from universal_agent.host_contracts import (
    CliDomainContribution,
    InitDomainOutcome,
)

_SQSQLDB_INIT_BACKENDS = ("sqlite",)


def _add_init_arguments(group: argparse._ArgumentGroup) -> None:
    group.add_argument(
        "--sqldb-path",
        help="Path to the SQLite database file the Agent may query (read-only).",
    )
    group.add_argument(
        "--sqldb-timeout-seconds",
        type=float,
        default=10.0,
        help="Per-query timeout in seconds.",
    )


def _resolve_init_domain(args: argparse.Namespace) -> InitDomainOutcome | None:
    backend = cast("str | None", getattr(args, "domain_backend", None))
    if backend not in _SQSQLDB_INIT_BACKENDS:
        return None

    from universal_agent.domains.sqldb.cli_runtime import profile_domain_config

    return InitDomainOutcome(
        domain_name="sqldb",
        domain_config=profile_domain_config(
            domain_backend=backend,
            sqldb_path=cast("str | None", getattr(args, "sqldb_path", None)),
            timeout_seconds=cast(float, getattr(args, "sqldb_timeout_seconds", 10.0)),
        ),
    )


def sqldb_cli_contribution() -> CliDomainContribution:
    return CliDomainContribution(
        domain="sqldb",
        init_backends=_SQSQLDB_INIT_BACKENDS,
        init_add_arguments=_add_init_arguments,
        init_resolve_domain=_resolve_init_domain,
    )


__all__ = ["sqldb_cli_contribution"]
