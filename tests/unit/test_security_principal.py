"""Unit tests for the principal model (Phase 0).

See docs/phase0-principal-implementation.md §4.
"""

from __future__ import annotations

import pytest

from universal_agent.core import DEFAULT_TENANT_ID
from universal_agent.security import (
    Tenant,
    TenantStatus,
    UserAccountStatus,
    UserPrincipal,
    default_tenant,
)


def test_tenant_constructs_with_defaults() -> None:
    tenant = Tenant(tenant_id="acme", name="Acme Corp")
    assert tenant.tenant_id == "acme"
    assert tenant.name == "Acme Corp"
    assert tenant.status is TenantStatus.ACTIVE


def test_tenant_rejects_blank_identifier() -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        Tenant(tenant_id="", name="Acme")


def test_tenant_rejects_blank_name() -> None:
    with pytest.raises(ValueError, match="tenant name"):
        Tenant(tenant_id="acme", name="")


def test_tenant_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    tenant = Tenant(tenant_id="acme", name="Acme")
    with pytest.raises(FrozenInstanceError):
        tenant.tenant_id = "other"  # type: ignore[misc]


def test_user_principal_constructs() -> None:
    user = UserPrincipal(user_id="u-1", display_name="Alice")
    assert user.user_id == "u-1"
    assert user.display_name == "Alice"
    assert user.status is UserAccountStatus.ACTIVE
    assert user.subject == "u-1"


def test_user_principal_defaults_display_name_to_none() -> None:
    user = UserPrincipal(user_id="u-1")
    assert user.display_name is None


def test_user_principal_rejects_blank_identifier() -> None:
    with pytest.raises(ValueError, match="user_id"):
        UserPrincipal(user_id="")


def test_default_tenant_matches_canonical_identifier() -> None:
    tenant = default_tenant()
    assert tenant.tenant_id == DEFAULT_TENANT_ID


def test_principal_layer_has_no_upward_dependency() -> None:
    # security is a kernel service layer (rank 1); principal must only depend
    # on core (rank 0), never on persistence/agentd/etc. Guard the seam isn't
    # accidentally widened when RBAC lands later.
    from pathlib import Path

    module = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "universal_agent"
        / "security"
        / "principal.py"
    )
    text = module.read_text(encoding="utf-8")
    forbidden = (
        "universal_agent.persistence",
        "universal_agent.agentd",
        "universal_agent.host",
    )
    assert not any(token in text for token in forbidden)