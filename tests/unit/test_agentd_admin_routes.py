"""Admin-plane route tests (Phase 2).

Exercises ``handle_admin_route`` directly with an ``InMemoryCredentialStore``:
route dispatch, the admin gate, the provisioning loop, and error mapping.
"""

from __future__ import annotations

import pytest

from universal_agent.agentd.admin_routes import handle_admin_route
from universal_agent.agentd.http import HttpRequest
from universal_agent.core import JsonValue
from universal_agent.security import (
    InMemoryCredentialStore,
    RequestPrincipal,
    Role,
    Scope,
    Tenant,
    TenantStatus,
    UserAccountStatus,
    UserPrincipal,
)


def _request(
    method: str,
    path: str,
    body: dict[str, JsonValue] | None = None,
) -> HttpRequest:
    return HttpRequest(method=method, path=path, body={**(body or {})})


def _principal(role: Role, *, tenant_id: str = "acme") -> RequestPrincipal:
    return RequestPrincipal(
        user=UserPrincipal(user_id="boss"),
        tenant=Tenant(tenant_id=tenant_id, name="Acme"),
        role=role,
        scope=Scope.READ_WRITE,
    )


def _provisioned_store() -> InMemoryCredentialStore:
    store = InMemoryCredentialStore()
    store.create_tenant(tenant_id="acme", name="Acme")
    store.create_user(user_id="alice", email="alice@acme.test")
    return store


def test_falls_through_without_admin_store() -> None:
    outcome = handle_admin_route(
        None, None, _request("POST", "/v1/admin/tenants"), "POST", "/v1/admin/tenants"
    )
    assert outcome is None


def test_unmatched_path_falls_through() -> None:
    store = _provisioned_store()
    outcome = handle_admin_route(store, None, _request("GET", "/v1/health"), "GET", "/v1/health")
    assert outcome is None


def test_non_admin_principal_is_denied_with_rbac_reason() -> None:
    store = _provisioned_store()
    response = handle_admin_route(
        store,
        _principal(Role.OPERATOR),
        _request("POST", "/v1/admin/tenants", {"tenant_id": "t", "name": "T"}),
        "POST",
        "/v1/admin/tenants",
    )
    assert response is not None
    assert response is not None
    assert response.status_code == 403
    error = response.body["error"]
    assert isinstance(error, dict) and "rbac" in str(error.get("message"))


def test_legacy_shared_token_bootstrap_is_allowed() -> None:
    store = _provisioned_store()
    response = handle_admin_route(
        store,
        None,
        _request("POST", "/v1/admin/tenants", {"tenant_id": "beta", "name": "Beta"}),
        "POST",
        "/v1/admin/tenants",
    )
    assert response is not None and response.status_code == 201


def test_admin_can_provision_tenant_user_role() -> None:
    store = _provisioned_store()
    admin = _principal(Role.ADMIN)

    created_tenant = handle_admin_route(
        store,
        admin,
        _request("POST", "/v1/admin/tenants", {"tenant_id": "beta", "name": "Beta"}),
        "POST",
        "/v1/admin/tenants",
    )
    assert created_tenant is not None and created_tenant.status_code == 201

    created_user = handle_admin_route(
        store,
        admin,
        _request("POST", "/v1/admin/users", {"user_id": "bob", "email": "bob@beta.test"}),
        "POST",
        "/v1/admin/users",
    )
    assert created_user is not None and created_user.status_code == 201

    role = handle_admin_route(
        store,
        admin,
        _request("PUT", "/v1/admin/tenants/beta/members/bob", {"role": "operator"}),
        "PUT",
        "/v1/admin/tenants/beta/members/bob",
    )
    assert role is not None and role.status_code == 200

    members = handle_admin_route(
        store,
        admin,
        _request("GET", "/v1/admin/tenants/beta/members"),
        "GET",
        "/v1/admin/tenants/beta/members",
    )
    assert members is not None
    assert members.body["members"] == [{"user_id": "bob", "role": "operator"}]


def test_admin_credential_issue_returns_token_once_and_revokes() -> None:
    store = _provisioned_store()
    admin = _principal(Role.ADMIN)

    issued = handle_admin_route(
        store,
        admin,
        _request(
            "POST",
            "/v1/admin/credentials",
            {"user_id": "alice", "tenant_id": "acme", "role": "read_only"},
        ),
        "POST",
        "/v1/admin/credentials",
    )
    assert issued is not None and issued.status_code == 201
    token = str(issued.body["token"])
    assert issued.body["scope"] == "read_only"

    # The issued token authenticates as a read-only principal.
    principal = store.resolve(token)
    assert principal is not None
    assert principal.role is Role.READ_ONLY
    assert principal.is_read_only is True

    # Revocation kills the credential end-to-end.
    credential_id = str(issued.body["credential_id"])
    revoked = handle_admin_route(
        store,
        admin,
        _request("DELETE", f"/v1/admin/credentials/{credential_id}"),
        "DELETE",
        f"/v1/admin/credentials/{credential_id}",
    )
    assert revoked is not None
    assert revoked.body["revoked"] is True
    assert store.resolve(token) is None


def test_admin_error_mapping() -> None:
    store = _provisioned_store()
    admin = _principal(Role.ADMIN)

    duplicate = handle_admin_route(
        store,
        admin,
        _request("POST", "/v1/admin/tenants", {"tenant_id": "acme", "name": "Dup"}),
        "POST",
        "/v1/admin/tenants",
    )
    assert duplicate is not None and duplicate.status_code == 409

    missing_user = handle_admin_route(
        store,
        admin,
        _request(
            "POST",
            "/v1/admin/credentials",
            {"user_id": "ghost", "tenant_id": "acme", "role": "operator"},
        ),
        "POST",
        "/v1/admin/credentials",
    )
    assert missing_user is not None and missing_user.status_code == 404

    bad_role = handle_admin_route(
        store,
        admin,
        _request(
            "POST",
            "/v1/admin/credentials",
            {"user_id": "alice", "tenant_id": "acme", "role": "emperor"},
        ),
        "POST",
        "/v1/admin/credentials",
    )
    assert bad_role is not None and bad_role.status_code == 400


def test_disabled_principals_stop_authenticating() -> None:
    store = _provisioned_store()
    raw, _ = store.issue(user_id="alice", tenant_id="acme", role=Role.OPERATOR)
    assert store.resolve(raw) is not None
    store.register_user(UserPrincipal(user_id="alice", status=UserAccountStatus.DISABLED))
    assert store.resolve(raw) is None
    # A disabled tenant also blocks resolution.
    raw2, _ = store.issue(user_id="alice", tenant_id="acme", role=Role.OPERATOR)
    store.register_tenant(Tenant(tenant_id="acme", name="Acme", status=TenantStatus.DISABLED))
    assert store.resolve(raw2) is None


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"name": "NoId"}, 400),
        ({}, 400),
        ({"tenant_id": "", "name": "X"}, 400),
    ],
)
def test_admin_validation_errors(body: dict[str, JsonValue], status: int) -> None:
    store = _provisioned_store()
    response = handle_admin_route(
        store,
        _principal(Role.ADMIN),
        _request("POST", "/v1/admin/tenants", body),
        "POST",
        "/v1/admin/tenants",
    )
    assert response is not None
    assert response.status_code == status
