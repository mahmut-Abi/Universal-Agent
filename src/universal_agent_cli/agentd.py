"""`agentd` thin-client compatibility shim.

The remote command implementations live under
:mod:`universal_agent_cli.remote`, split by command family. This module
re-exports the client setup and routing seams so existing imports
(``universal_agent_cli.agentd``) keep working while ``agentd.py`` itself stays
small.
"""

from __future__ import annotations

from universal_agent_cli.remote import (
    _agentd_api_token,
    command_supports_agentd,
    dispatch_agentd_cli,
    dispatch_agentd_commands,
)
from universal_agent_cli.remote.client import _client_timeout_seconds

__all__ = [
    "_agentd_api_token",
    "_client_timeout_seconds",
    "command_supports_agentd",
    "dispatch_agentd_cli",
    "dispatch_agentd_commands",
]
