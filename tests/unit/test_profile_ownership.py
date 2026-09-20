"""Profile ownership metadata tests (Phase 3)."""

from __future__ import annotations

from universal_agent.core import DEFAULT_TENANT_ID, JsonValue, immutable_json
from universal_agent.profile.config import ProfileConfig
from universal_agent.profile.store import ProfileStore


def _payload(
    name: str,
    tenant_id: str | None = None,
    owner_id: str | None = None,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "name": name,
        "version": "1.0.0",
        "description": f"{name} profile",
        "domain": {"name": "local", "version": "0.1.0"},
        "runtime": {"environment": {"environment": "local"}},
    }
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id
    if owner_id is not None:
        payload["owner_id"] = owner_id
    return payload


def test_profile_config_parses_ownership_fields() -> None:
    config = ProfileConfig.from_mapping(_payload("p1", tenant_id="acme", owner_id="alice"))
    assert config.tenant_id == "acme"
    assert config.owner_id == "alice"
    profile = config.to_profile()
    assert profile.tenant_id == "acme"
    assert profile.owner_id == "alice"


def test_profile_config_defaults_ownership_to_none() -> None:
    config = ProfileConfig.from_mapping(_payload("p1"))
    assert config.tenant_id is None
    assert config.owner_id is None


def test_store_survives_ownership_metadata_round_trip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = ProfileStore(tmp_path)
    created = store.create(_payload("owned", tenant_id="acme", owner_id="alice"))
    assert created["tenant_id"] == "acme"
    assert created["owner_id"] == "alice"
    assert store.load("owned")["tenant_id"] == "acme"


def test_names_filters_by_tenant(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = ProfileStore(tmp_path)
    store.create(_payload("shared-default"))
    store.create(_payload("acme-ops", tenant_id="acme"))
    store.create(_payload("beta-ops", tenant_id="beta"))

    # Unscoped listing (admin view) is unchanged.
    assert store.names() == ("acme-ops", "beta-ops", "shared-default")
    # Tenant-scoped listing isolates profiles; ownership-less profiles count
    # as the implicit default tenant.
    assert store.names(tenant_id="acme") == ("acme-ops",)
    assert store.names(tenant_id="beta") == ("beta-ops",)
    assert store.names(tenant_id=DEFAULT_TENANT_ID) == ("shared-default",)
    assert store.names(tenant_id="unknown") == ()


def test_unscoped_deployments_are_unaffected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = ProfileStore(tmp_path)
    store.create(_payload("plain"))
    assert store.names() == ("plain",)
    config = ProfileConfig.from_mapping(store.load("plain"))
    assert config.tenant_id is None
    assert config.runtime.environment == immutable_json({"environment": "local"})