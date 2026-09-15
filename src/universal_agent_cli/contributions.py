"""CLI contribution contracts (compatibility re-export).

The contracts live in the kernel (``universal_agent.domain.host_contracts``)
so domain packages can contribute CLI surfaces without importing a client
package; the CLI shell re-exports them here for its own modules and any
external importers.
"""

from __future__ import annotations

from universal_agent.host_contracts import (
    CLI_CONTRIBUTIONS_ENTRY_POINT_GROUP,
    CliDomainContribution,
    CommandOutcome,
    InitDomainOutcome,
    RemoteAgentdClient,
    add_secret,
    load_cli_contributions,
    single_secret_source,
)

__all__ = [
    "CLI_CONTRIBUTIONS_ENTRY_POINT_GROUP",
    "CliDomainContribution",
    "CommandOutcome",
    "InitDomainOutcome",
    "RemoteAgentdClient",
    "add_secret",
    "load_cli_contributions",
    "single_secret_source",
]
