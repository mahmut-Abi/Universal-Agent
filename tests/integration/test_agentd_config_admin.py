"""Config-management write surface over agentd HTTP (UA-CS-005).

Covers the profile CRUD routes, config validation dry-run, domain binding,
and the config audit log through the agentd request handler with a real
ProfileStore rooted in a temp directory.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from universal_agent.agentd import AgentdApp
from universal_agent.agentd.http import HttpRequest
from universal_agent.core import JsonMapping, JsonValue, immutable_json
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.model import ScriptedModelAdapter
from universal_agent.profile.store import ProfileStore
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore

pytestmark = pytest.mark.integration

_VALID_PROFILE: dict[str, JsonValue] = {
    "name": "checkout-sre",
    "version": "1.0.0",
    "description": "Checkout SRE profile",
    "domain": {"name": "local", "version": "0.1.0"},
    "runtime": {
        "model": {"provider": "scripted", "name": "scripted"},
        "store": {"backend": "memory"},
        "domain": {"name": "local", "version": "0.1.0"},
    },
}


class _Backend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "scaled": True})


def build_app(tmp_path: Any, *, with_store: bool = True) -> AgentdApp:
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(_Backend(), _Backend()))
    )
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(()),
        state_store=store,
        components=components,
        event_sink=events,
    )
    service = RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )
    profile_store = ProfileStore(tmp_path / "profiles") if with_store else None
    return AgentdApp(service, profile_store=profile_store)


def _request(method: str, path: str, body: Mapping[str, JsonValue] | None = None) -> HttpRequest:
    return HttpRequest(
        method=method,
        path=path,
        body={} if body is None else dict(body),
        headers={"x-acting-principal": "tester"},
    )


def _as_dict(value: object) -> dict[str, JsonValue]:
    assert isinstance(value, Mapping)
    return dict(value)


def _as_list(value: object) -> list[JsonValue]:
    assert isinstance(value, list)
    return list(value)


@pytest.fixture(name="app")
def app_fixture(tmp_path: Any) -> AgentdApp:
    return build_app(tmp_path)


@pytest.mark.asyncio
async def test_profile_crud_round_trip(app: AgentdApp) -> None:
    created = await app.handle(_request("POST", "/v1/profiles", dict(_VALID_PROFILE)))
    assert created is not None and created.status_code == 201

    duplicate = await app.handle(_request("POST", "/v1/profiles", dict(_VALID_PROFILE)))
    assert duplicate is not None and duplicate.status_code == 409

    patched = await app.handle(
        _request(
            "PATCH",
            "/v1/profiles/checkout-sre",
            {"description": "Updated description"},
        )
    )
    assert patched is not None and patched.status_code == 200

    deleted = await app.handle(_request("DELETE", "/v1/profiles/checkout-sre"))
    assert deleted is not None and deleted.status_code == 204

    missing = await app.handle(_request("DELETE", "/v1/profiles/checkout-sre"))
    assert missing is not None and missing.status_code == 404


@pytest.mark.asyncio
async def test_config_validate_reports_field_errors(app: AgentdApp) -> None:
    ok = await app.handle(
        _request(
            "POST",
            "/v1/config/validate",
            {"kind": "profile", "payload": dict(_VALID_PROFILE)},
        )
    )
    assert ok is not None and ok.status_code == 200

    bad = await app.handle(
        _request(
            "POST",
            "/v1/config/validate",
            {
                "kind": "profile",
                "payload": {
                    **_VALID_PROFILE,
                    "runtime": {
                        "model": {"provider": "scripted"},
                        "store": {"backend": "bogus"},
                    },
                },
            },
        )
    )
    assert bad is not None and bad.status_code == 400

    unsupported = await app.handle(
        _request("POST", "/v1/config/validate", {"kind": "policy", "payload": {}})
    )
    assert unsupported is not None and unsupported.status_code == 400


@pytest.mark.asyncio
async def test_domain_binding_updates_stored_profile(app: AgentdApp) -> None:
    await app.handle(_request("POST", "/v1/profiles", dict(_VALID_PROFILE)))

    bound = await app.handle(
        _request(
            "PUT",
            "/v1/domains/kubernetes/profiles",
            {"add": ["checkout-sre"], "remove": []},
        )
    )
    assert bound is not None and bound.status_code == 200

    store = app._profile_store
    assert store is not None
    payload = store.load("checkout-sre")
    domains = _as_list(payload.get("domains"))
    assert any(_as_dict(item).get("name") == "kubernetes" for item in domains)


@pytest.mark.asyncio
async def test_config_audit_log_records_actor_and_action(app: AgentdApp) -> None:
    await app.handle(_request("POST", "/v1/profiles", dict(_VALID_PROFILE)))
    await app.handle(_request("DELETE", "/v1/profiles/checkout-sre"))

    audit = await app.handle(_request("GET", "/v1/config/audit"))
    assert audit is not None and audit.status_code == 200
    records = _as_list(_as_dict(audit.body).get("records"))
    assert len(records) == 2
    first = _as_dict(records[0])
    second = _as_dict(records[1])
    assert first["actor"] == "tester"
    assert first["action"] == "deleted"
    assert second["action"] == "created"


@pytest.mark.asyncio
async def test_config_admin_without_store_falls_through(tmp_path: Any) -> None:
    app = build_app(tmp_path, with_store=False)
    response = await app.handle(_request("POST", "/v1/profiles", dict(_VALID_PROFILE)))
    # No store wired: the config-admin plane falls through (405/404 from the
    # runtime read models) instead of writing anywhere.
    assert response is not None and response.status_code in {404, 405}


@pytest.mark.asyncio
async def test_validation_error_carries_field_errors(app: AgentdApp) -> None:
    response = await app.handle(_request("POST", "/v1/profiles", {"name": "broken"}))
    assert response is not None and response.status_code == 422
    assert _as_dict(response.body).get("status") == "error"
    errors = _as_list(_as_dict(response.body).get("errors"))
    assert errors
    assert all("path" in _as_dict(e) and "message" in _as_dict(e) for e in errors)


@pytest.mark.asyncio
async def test_builtin_profile_delete_returns_409(app: AgentdApp) -> None:
    builtin = {**_VALID_PROFILE, "builtin": True}
    await app.handle(_request("POST", "/v1/profiles", dict(builtin)))

    response = await app.handle(_request("DELETE", "/v1/profiles/checkout-sre"))
    assert response is not None and response.status_code == 409
    error = _as_dict(response.body.get("error"))
    assert error.get("code") == "builtin_profile"

    # PATCH is also refused
    patched = await app.handle(_request("PATCH", "/v1/profiles/checkout-sre", {"description": "x"}))
    assert patched is not None and patched.status_code == 409
