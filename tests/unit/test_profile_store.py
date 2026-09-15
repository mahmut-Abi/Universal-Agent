"""ProfileStore unit tests (config-management write surface, UA-CS-005).

Covers CRUD, RFC 7386 patch semantics, validation-before-persist, domain
binding rules, and the config audit log.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.profile.store import (
    ProfileAlreadyExistsError,
    ProfileNotFoundError,
    ProfileStore,
    ProfileStoreValidationError,
)

pytestmark = pytest.mark.unit

_VALID_PROFILE = {
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


@pytest.fixture(name="store")
def store_fixture(tmp_path: Path) -> ProfileStore:
    return ProfileStore(tmp_path / "profiles")


def test_create_writes_validated_config(store: ProfileStore) -> None:
    payload = store.create(dict(_VALID_PROFILE))

    assert payload["name"] == "checkout-sre"
    assert store.names() == ("checkout-sre",)
    stored = json.loads((store.root / "checkout-sre.json").read_text(encoding="utf-8"))
    assert stored["description"] == "Checkout SRE profile"


def test_create_rejects_invalid_profile_without_persisting(store: ProfileStore) -> None:
    invalid = {
        **_VALID_PROFILE,
        "runtime": {"model": {"provider": "scripted"}, "store": {"backend": "bogus"}},
    }

    with pytest.raises(ProfileStoreValidationError) as excinfo:
        store.create(dict(invalid))

    assert excinfo.value.errors, "structured errors must be present"
    assert store.names() == (), "invalid profile must not be persisted"


def test_create_rejects_duplicate(store: ProfileStore) -> None:
    store.create(dict(_VALID_PROFILE))

    with pytest.raises(ProfileAlreadyExistsError):
        store.create(dict(_VALID_PROFILE))


def test_load_missing_raises(store: ProfileStore) -> None:
    with pytest.raises(ProfileNotFoundError):
        store.load("missing")


def test_patch_applies_rfc7386_merge(store: ProfileStore) -> None:
    store.create(dict(_VALID_PROFILE))

    patched = store.patch(
        "checkout-sre",
        {"description": "Updated", "runtime": {"limits": {"max_iterations": 7}}},
    )

    assert patched["description"] == "Updated"
    runtime = patched["runtime"]
    assert isinstance(runtime, dict)
    model = runtime.get("model")
    assert isinstance(model, dict)
    assert model.get("provider") == "scripted"  # nested merge keeps siblings
    limits = runtime.get("limits")
    assert isinstance(limits, dict) and limits.get("max_iterations") == 7
    assert patched["name"] == "checkout-sre"


def test_patch_null_deletes_key(store: ProfileStore) -> None:
    store.create(dict(_VALID_PROFILE))

    patched = store.patch("checkout-sre", {"description": None})

    assert "description" not in patched


def test_patch_rejects_invalid_merge_result(store: ProfileStore) -> None:
    store.create(dict(_VALID_PROFILE))

    with pytest.raises(ValueError, match="name cannot be changed"):
        store.patch("checkout-sre", {"name": "renamed"})


def test_patch_missing_raises(store: ProfileStore) -> None:
    with pytest.raises(ProfileNotFoundError):
        store.patch("missing", {"description": "x"})


def test_delete_removes_config(store: ProfileStore) -> None:
    store.create(dict(_VALID_PROFILE))

    store.delete("checkout-sre")

    assert store.names() == ()
    with pytest.raises(ProfileNotFoundError):
        store.load("checkout-sre")


def test_domain_binding_round_trip(store: ProfileStore) -> None:
    store.create(dict(_VALID_PROFILE))

    store.set_domain_binding("checkout-sre", "kubernetes", bound=True, version="0.2.0")

    bound = store.load("checkout-sre")
    domains = bound.get("domains")
    assert isinstance(domains, list) and domains == [
        {"name": "local", "version": "0.1.0"},
        {"name": "kubernetes", "version": "0.2.0"},
    ]

    store.set_domain_binding("checkout-sre", "kubernetes", bound=False)
    unbound = store.load("checkout-sre")
    assert unbound.get("domains") == [{"name": "local", "version": "0.1.0"}]


def test_domain_unbind_refuses_primary_domain(store: ProfileStore) -> None:
    store.create(dict(_VALID_PROFILE))

    with pytest.raises(ValueError, match="primary domain"):
        store.set_domain_binding("checkout-sre", "local", bound=False)


def test_audit_records_every_mutation(store: ProfileStore) -> None:
    store.create(dict(_VALID_PROFILE), actor="alice")
    store.patch("checkout-sre", {"description": "v2"}, actor="bob")
    store.delete("checkout-sre", actor="alice")

    records = store.audit_records()

    assert [(r["actor"], r["action"]) for r in records] == [
        ("alice", "deleted"),
        ("bob", "updated"),
        ("alice", "created"),
    ]


def test_invalid_profile_names_are_rejected(store: ProfileStore) -> None:
    with pytest.raises(ValueError, match="profile name"):
        store.create({**_VALID_PROFILE, "name": "../escape"})


def test_load_rejects_invalid_json(store: ProfileStore) -> None:
    store.create(dict(_VALID_PROFILE))
    (store.root / "checkout-sre.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(ProfileStoreValidationError, match="invalid JSON"):
        store.load("checkout-sre")
