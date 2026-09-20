"""Credentials module tests (Phase 1)."""

from __future__ import annotations

import pytest

from universal_agent.core import utc_now
from universal_agent.security import (
    InMemoryCredentialStore,
    PrincipalAlreadyExistsError,
    PrincipalNotFoundError,
    Role,
    RoleBinding,
    Scope,
    Tenant,
    TenantStatus,
    UserAccountStatus,
    UserPrincipal,
    hash_credential,
)


def test_hash_credential_is_deterministic_and_not_plaintext() -> None:
    h1 = hash_credential("s3cret-token")
    h2 = hash_credential("s3cret-token")
    assert h1 == h2
    assert "s3cret-token" not in h1


def test_in_memory_provisions_and_resolves_principal() -> None:
    store = InMemoryCredentialStore()
    raw, _ = store.issue(user_id="alice", tenant_id="acme", role=Role.READ_ONLY)

    principal = store.resolve(raw)
    assert principal is not None
    assert principal.user_id == "alice"
    assert principal.tenant_id == "acme"
    assert principal.role is Role.READ_ONLY
    assert principal.scope is Scope.READ_ONLY
    assert principal.is_read_only is True


def test_in_memory_read_write_role_gets_read_write_scope() -> None:
    store = InMemoryCredentialStore()
    raw, _ = store.issue(user_id="bob", tenant_id="acme", role=Role.OPERATOR)
    principal = store.resolve(raw)
    assert principal is not None
    assert principal.scope is Scope.READ_WRITE


def test_unknown_token_resolves_to_none() -> None:
    store = InMemoryCredentialStore()
    assert store.resolve("not-issued") is None


def test_revoked_credential_no_longer_resolves() -> None:
    store = InMemoryCredentialStore()
    raw, cred = store.issue(user_id="alice", tenant_id="acme")
    assert store.resolve(raw) is not None
    assert store.revoke(cred.credential_id) is True
    assert store.resolve(raw) is None


def test_disabled_user_does_not_resolve() -> None:
    store = InMemoryCredentialStore()
    store.register_user(UserPrincipal(user_id="carol", status=UserAccountStatus.DISABLED))
    raw, _ = store.issue(user_id="carol", tenant_id="acme")
    assert store.resolve(raw) is None


def test_disabled_tenant_does_not_resolve() -> None:
    store = InMemoryCredentialStore()
    store.register_tenant(Tenant(tenant_id="acme", name="Acme", status=TenantStatus.DISABLED))
    raw, _ = store.issue(user_id="alice", tenant_id="acme")
    assert store.resolve(raw) is None


def test_credential_hash_is_stored_not_raw() -> None:
    store = InMemoryCredentialStore()
    raw, cred = store.issue(user_id="alice", tenant_id="acme")
    assert cred is not None
    assert raw not in (cred.credential_id, cred.token_hash)
    assert hash_credential(raw) == cred.token_hash


def test_request_principal_rejects_blank_user() -> None:
    from universal_agent.security import RequestPrincipal

    with pytest.raises(ValueError, match="user_id"):
        RequestPrincipal(user=UserPrincipal(user_id=""), tenant=Tenant(tenant_id="a", name="A"))


def test_credential_respects_revoked_at_cutoff() -> None:
    store = InMemoryCredentialStore()
    raw, cred = store.issue(user_id="alice", tenant_id="acme")
    # Revoke with a fixed earlier timestamp, then resolve at a later now.
    store.revoke(cred.credential_id, now=utc_now())
    assert store.resolve(raw) is None


# -- admin plane (Phase 2) ---------------------------------------------------


def test_admin_provisioning_flow_resolves_end_to_end() -> None:
    store = InMemoryCredentialStore()
    store.create_tenant(tenant_id="acme", name="Acme Corp")
    store.create_user(user_id="alice", email="alice@acme.test", display_name="Alice")
    store.set_role(tenant_id="acme", user_id="alice", role=Role.READ_ONLY)

    raw, cred = store.issue_credential(user_id="alice", tenant_id="acme", role=Role.READ_ONLY)
    principal = store.resolve(raw)
    assert principal is not None
    assert principal.role is Role.READ_ONLY
    assert principal.scope is Scope.READ_ONLY
    assert principal.is_read_only is True
    assert raw not in cred.token_hash


def test_role_authority_lives_in_membership_not_credential() -> None:
    store = InMemoryCredentialStore()
    store.create_tenant(tenant_id="acme", name="Acme")
    store.create_user(user_id="alice", email="a@acme.test")
    store.issue_credential(user_id="alice", tenant_id="acme", role=Role.READ_ONLY)

    members = store.list_members("acme")
    assert members == (RoleBinding(tenant_id="acme", user_id="alice", role=Role.READ_ONLY),)

    # Promote the member: a fresh credential resolves with the new role and
    # its derived read_write scope (role authority lives in the membership).
    store.set_role(tenant_id="acme", user_id="alice", role=Role.OPERATOR)
    fresh, _ = store.issue_credential(user_id="alice", tenant_id="acme", role=Role.OPERATOR)
    principal = store.resolve(fresh)
    assert principal is not None
    assert principal.role is Role.OPERATOR
    assert principal.scope is Scope.READ_WRITE


def test_admin_duplicates_and_missing_principals_raise() -> None:
    store = InMemoryCredentialStore()
    store.create_tenant(tenant_id="acme", name="Acme")
    with pytest.raises(PrincipalAlreadyExistsError):
        store.create_tenant(tenant_id="acme", name="Acme 2")
    store.create_user(user_id="alice", email="a@acme.test")
    with pytest.raises(PrincipalAlreadyExistsError):
        store.create_user(user_id="alice", email="other@acme.test")
    with pytest.raises(PrincipalAlreadyExistsError):
        store.create_user(user_id="bob", email="a@acme.test")
    with pytest.raises(PrincipalNotFoundError):
        store.issue_credential(user_id="ghost", tenant_id="acme", role=Role.OPERATOR)
    with pytest.raises(PrincipalNotFoundError):
        store.issue_credential(user_id="alice", tenant_id="missing", role=Role.OPERATOR)
    with pytest.raises(PrincipalNotFoundError):
        store.list_members("missing")
