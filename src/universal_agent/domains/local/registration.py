"""Local Domain contribution to the CLI surface (extension point).

Provides the Golden Path fallback: `agent init` resolves to the
domain-neutral local profile unless another domain contribution claims
the init arguments. The contribution loader sorts alphabetically
("kubernetes" < "local") and the CLI takes the first accepting
contribution, so this one acts as the last-resort default.

The CLI shell discovers this module through the
``universal_agent.cli_contributions`` entry-point group; it never names a
concrete domain.
"""

from __future__ import annotations

import argparse

from universal_agent.domains.local.cli_runtime import local_domain_config
from universal_agent.host_contracts import CliDomainContribution, InitDomainOutcome


def _resolve_init_domain(args: argparse.Namespace) -> InitDomainOutcome:
    """Unconditional fallback: the domain-neutral local profile."""

    return InitDomainOutcome(
        domain_name="local",
        domain_config=local_domain_config(),
    )


def local_cli_contribution() -> CliDomainContribution:
    """Entry-point factory for the local domain CLI contribution."""

    return CliDomainContribution(
        domain="local",
        init_resolve_domain=_resolve_init_domain,
    )


__all__ = ["local_cli_contribution"]
