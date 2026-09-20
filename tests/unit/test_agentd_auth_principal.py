"""Principal-aware agentd authentication (Phase 1).

Covers the agentd auth boundary upgrade: bearer tokens resolve through a
CredentialStore to a RequestPrincipal, then RBAC (role/scope) and cross-tenant
denials gate each request — while the legacy shared-token model still works
unchanged when no credential store is configured.
"""

from __future__ import annotations

from collections.abc import Mapping

from universal_agent import security as sec
from universal_agent.agentd.http import (
    AgentdAuthPolicy,
    HttpRequest,
    _authenticate,
)


def _req(method: str, authorization: str) -> HttpRequest:
    return HttpRequest(method=method, path="/v1/sessions", headers={"authorization": authorization})


def _message(response: object) -> str:
    if response is None:
        return ""
    body = response.body  # type: ignore[attr-defined]
    error = body.get("error")
    return str(error.get("message", "")) if isinstance(error, Mapping) else ""


def _policy(
    store: sec.InMemoryCredentialStore,
    tenant_id: str | None = "acme",
) -> AgentdAuthPolicy:
    return AgentdAuthPolicy(credential_store=store, tenant_id=tenant_id)


def _make_store() -> sec.InMemoryCredentialStore:
    store = sec.InMemoryCredentialStore()
    return store


def test_legacy_shared_token_read_only_still_guards_writes() -> None:
    policy = AgentdAuthPolicy(bearer_token="full", read_only_bearer_token="ro")

    get_ok = _authenticate(policy, _req("GET", "Bearer ro"), "/v1/sessions", method="GET")
    assert get_ok.response is None
    post = _authenticate(policy, _req("POST", "Bearer ro"), "/v1/sessions", method="POST")
    denied = post.response
    assert denied is not None and denied.status_code == 403


def test_legacy_shared_token_full_allows_writes() -> None:
    policy = AgentdAuthPolicy(bearer_token="full", read_only_bearer_token="ro")
    outcome = _authenticate(policy, _req("POST", "Bearer full"), "/v1/sessions", method="POST")
    assert outcome.response is None


def test_credential_read_only_allows_get() -> None:
    store = _make_store()
    token, _ = store.issue(user_id="alice", tenant_id="acme", role=sec.Role.READ_ONLY)
    policy = _policy(store)
    outcome = _authenticate(policy, _req("GET", f"Bearer {token}"), "/v1/s", method="GET")
    assert outcome.response is None


def test_credential_read_only_denies_write_with_rbac_reason() -> None:
    store = _make_store()
    token, _ = store.issue(user_id="alice", tenant_id="acme", role=sec.Role.READ_ONLY)
    policy = _policy(store)

    outcome = _authenticate(policy, _req("POST", f"Bearer {token}"), "/v1/s", method="POST")
    denied = outcome.response
    assert denied is not None
    assert denied.status_code == 403
    assert "rbac" in _message(denied)


def test_credential_operator_allows_write() -> None:
    store = _make_store()
    token, _ = store.issue(user_id="alice", tenant_id="acme", role=sec.Role.OPERATOR)
    policy = _policy(store)
    outcome = _authenticate(policy, _req("POST", f"Bearer {token}"), "/v1/s", method="POST")
    assert outcome.response is None


def test_unknown_credential_is_unauthorized() -> None:
    store = _make_store()
    policy = _policy(store)
    denied = _authenticate(policy, _req("GET", "Bearer bogus"), "/v1/s", method="GET").response
    assert denied is not None and denied.status_code == 401


def test_cross_tenant_credential_is_denied() -> None:
    store = _make_store()
    token, _ = store.issue(user_id="mallory", tenant_id="other")
    policy = _policy(store, tenant_id="acme")

    denied = _authenticate(policy, _req("GET", f"Bearer {token}"), "/v1/s", method="GET").response
    assert denied is not None
    assert denied.status_code == 403
    assert "cross_tenant" in _message(denied)


def test_public_path_bypasses_principal_auth() -> None:
    store = _make_store()
    policy = _policy(store)
    request = HttpRequest(method="GET", path="/health", headers={})
    health = _authenticate(policy, request, "/health", method="GET")
    assert health.response is None


def test_policy_enabled_with_credential_store_alone() -> None:
    store = _make_store()
    policy = AgentdAuthPolicy(credential_store=store)
    assert policy.enabled is True
