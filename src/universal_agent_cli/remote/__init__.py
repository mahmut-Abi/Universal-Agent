"""Remote (agentd thin-client) command package.

Split by command family so each surface stays small and testable:

- ``client``: bearer-token resolution, top-level router, capability matrix
- ``catalog``: domain/profile/domain-packages list+show
- ``config``: bare ``agent config`` / ``config show``
- ``distributed``: local distributed primitives (advanced/experimental)
- ``eval_ecosystem``: eval harness and ecosystem registry commands
- ``kubernetes``: Kubernetes operator commands
- ``observability``: metrics, traces, repair
- ``run``: goal submission
- ``session``: session lifecycle and projections
"""

from __future__ import annotations

from universal_agent_cli.remote.client import (
    _agentd_api_token,
    command_supports_agentd,
    dispatch_agentd_cli,
    dispatch_agentd_commands,
)

__all__ = [
    "_agentd_api_token",
    "command_supports_agentd",
    "dispatch_agentd_cli",
    "dispatch_agentd_commands",
]
