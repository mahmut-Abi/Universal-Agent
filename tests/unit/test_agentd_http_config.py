from __future__ import annotations

from typing import cast

import pytest

from universal_agent.agentd import AgentdAuthPolicy, AgentdServerConfig


def test_agentd_server_config_validates_boundary_inputs() -> None:
    assert AgentdServerConfig(port=0).port == 0

    with pytest.raises(ValueError, match="agentd host must not be empty"):
        AgentdServerConfig(host=" ")
    with pytest.raises(ValueError, match="agentd port must be an integer"):
        AgentdServerConfig(port=cast(int, "8765"))
    with pytest.raises(ValueError, match="agentd max_body_bytes must be non-negative"):
        AgentdServerConfig(max_body_bytes=-1)


def test_agentd_auth_policy_validates_tokens_and_public_paths() -> None:
    assert AgentdAuthPolicy(public_paths=("/health",)).public_paths == ("/health",)

    with pytest.raises(ValueError, match="agentd bearer token must not be empty"):
        AgentdAuthPolicy(bearer_token=" ")
    with pytest.raises(ValueError, match="agentd read-only bearer token must be a string"):
        AgentdAuthPolicy(read_only_bearer_token=cast(str, 1))
    with pytest.raises(ValueError, match="agentd public paths must be absolute non-empty paths"):
        AgentdAuthPolicy(public_paths=(" ",))
    with pytest.raises(ValueError, match="agentd public paths must be absolute non-empty paths"):
        AgentdAuthPolicy(public_paths=("health",))
    with pytest.raises(ValueError, match="agentd public paths must be absolute non-empty paths"):
        AgentdAuthPolicy(public_paths=cast(tuple[str, ...], (1,)))
