"""Client-side server target resolution (client/server separation).

A client machine points at a remote agentd Runtime by adding a ``server``
section to its user config (``universal-agent/config.json`` in the project
or ``~/.universal-agent/config.json``):

.. code-block:: json

    {
      "server": {
        "url": "http://agentd.internal:8765",
        "auth_token_env": "AGENTD_AUTH_TOKEN"
      }
    }

Resolution order for the API target:

1. the explicit ``--api-url`` CLI flag (handled by the caller);
2. the ``AGENT_API_URL`` environment variable;
3. the config file ``server.url``.

Bearer-token resolution follows the same pattern: explicit ``--api-token``
/ ``--api-token-env`` (caller), then ``AGENT_API_TOKEN``, then the
environment variable named by ``server.auth_token_env``. Token *values*
never live in the config file — only the environment-variable name.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

SERVER_ENV_URL = "AGENT_API_URL"
SERVER_ENV_TOKEN = "AGENT_API_TOKEN"
CONFIG_FILE_NAME = "config.json"

__all__ = [
    "SERVER_ENV_TOKEN",
    "SERVER_ENV_URL",
    "ClientServerTarget",
    "load_client_server_target",
    "resolve_client_server_target",
    "resolve_client_token",
    "user_config_candidates",
]


@dataclass(frozen=True, slots=True)
class ClientServerTarget:
    """The remote agentd target a thin client should talk to."""

    url: str
    auth_token_env: str | None = None


def user_config_candidates(environ: Mapping[str, str]) -> tuple[Path, ...]:
    """Config-file locations, most specific first."""

    candidates: list[Path] = []
    config_dir = environ.get("AGENT_CONFIG_DIR")
    if config_dir:
        candidates.append(Path(config_dir) / CONFIG_FILE_NAME)
    candidates.append(Path.cwd() / "universal-agent" / CONFIG_FILE_NAME)
    candidates.append(Path.home() / ".universal-agent" / CONFIG_FILE_NAME)
    return tuple(candidates)


def load_client_server_target(
    environ: Mapping[str, str] | None = None,
) -> ClientServerTarget | None:
    """Load the configured remote server target, or ``None`` when absent."""

    environ = os.environ if environ is None else environ
    for path in user_config_candidates(environ):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        server = payload.get("server")
        if not isinstance(server, dict):
            continue
        url = server.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        auth_token_env = server.get("auth_token_env")
        return ClientServerTarget(
            url.strip(),
            auth_token_env if isinstance(auth_token_env, str) and auth_token_env else None,
        )
    return None


def resolve_client_server_target(
    explicit_url: str | None,
    environ: Mapping[str, str] | None = None,
) -> ClientServerTarget | None:
    """Resolve the effective server target: flag > env > config file."""

    if explicit_url and explicit_url.strip():
        return ClientServerTarget(explicit_url.strip())
    environ = os.environ if environ is None else environ
    env_url = environ.get(SERVER_ENV_URL)
    if env_url and env_url.strip():
        return ClientServerTarget(env_url.strip())
    return load_client_server_target(environ)


def resolve_client_token(
    target: ClientServerTarget | None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve the bearer token for a configured target from the environment."""

    if target is None or target.auth_token_env is None:
        return None
    environ = os.environ if environ is None else environ
    return environ.get(target.auth_token_env)
