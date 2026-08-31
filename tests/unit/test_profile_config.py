from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent import (
    DomainConfig,
    ProfileCatalog,
    ProfileConfig,
    ProfileConfigNotFoundError,
    ProfileRegistry,
    StoreConfig,
    load_profile_catalog,
    verify_profile_catalog_entry,
)


def write_profile(path: Path, name: str, *, domain: str = "kubernetes") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""
        {{
          "name": "{name}",
          "version": "1.0.0",
          "description": "{name} profile",
          "domain": {{"name": "{domain}", "version": "0.2.0"}},
          "runtime": {{
            "domain": {{"name": "{domain}", "version": "0.2.0"}}
          }}
        }}
        """,
        encoding="utf-8",
    )


@pytest.mark.unit
def test_profile_config_from_mapping_parses_runtime_and_domain() -> None:
    config = ProfileConfig.from_mapping(
        {
            "name": "production-operator",
            "version": "1.0.0",
            "description": "Production Kubernetes operator",
            "domain": {"name": "kubernetes", "version": "0.2.0"},
            "runtime": {
                "environment": {"environment": "production"},
                "store": {"backend": "file", "path": "/tmp/universal-agent"},
                "domain": {"name": "kubernetes", "version": "0.2.0"},
            },
        }
    )

    profile = config.to_profile()

    assert profile.name == "production-operator"
    assert profile.version == "1.0.0"
    assert profile.description == "Production Kubernetes operator"
    assert profile.domain == DomainConfig("kubernetes", "0.2.0")
    assert profile.configured_domains() == (DomainConfig("kubernetes", "0.2.0"),)
    assert profile.runtime.configured_domains() == (DomainConfig("kubernetes", "0.2.0"),)
    assert profile.runtime.store == StoreConfig.file("/tmp/universal-agent")
    assert profile.runtime.environment["environment"] == "production"


@pytest.mark.unit
def test_profile_config_matches_domains_by_identity_when_runtime_has_backend() -> None:
    config = ProfileConfig.from_mapping(
        {
            "name": "kubectl-operator",
            "version": "1.0.0",
            "domain": {"name": "kubernetes", "version": "0.2.0"},
            "runtime": {
                "domain": {
                    "name": "kubernetes",
                    "version": "0.2.0",
                    "backend": "kubectl",
                    "settings": {"default_namespace": "prod"},
                }
            },
        }
    )

    profile = config.to_profile()

    assert profile.domain == DomainConfig("kubernetes", "0.2.0")
    assert profile.runtime.domain.backend == "kubectl"
    assert profile.runtime.domain.settings["default_namespace"] == "prod"


@pytest.mark.unit
def test_profile_config_from_mapping_parses_multi_domain_profile() -> None:
    config = ProfileConfig.from_mapping(
        {
            "name": "ai-infra-operator",
            "version": "1.0.0",
            "domains": [
                {"name": "kubernetes", "version": "0.2.0"},
                {"name": "observability", "version": "0.1.0"},
            ],
            "runtime": {
                "domains": [
                    {"name": "kubernetes", "version": "0.2.0"},
                    {"name": "observability", "version": "0.1.0"},
                ]
            },
        }
    )

    profile = config.to_profile()

    assert profile.domain == DomainConfig("kubernetes", "0.2.0")
    assert profile.configured_domains() == (
        DomainConfig("kubernetes", "0.2.0"),
        DomainConfig("observability", "0.1.0"),
    )
    assert profile.runtime.domain == DomainConfig("kubernetes", "0.2.0")
    assert profile.runtime.configured_domains() == profile.configured_domains()


@pytest.mark.contract
def test_profile_config_from_json_file_parses_profile(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(
        """
        {
          "name": "production-operator",
          "version": "1.0.0",
          "domain": {"name": "kubernetes", "version": "0.2.0"},
          "runtime": {
            "domain": {"name": "kubernetes", "version": "0.2.0"}
          }
        }
        """,
        encoding="utf-8",
    )

    config = ProfileConfig.from_json_file(path)

    assert config.name == "production-operator"
    assert config.domain == DomainConfig("kubernetes", "0.2.0")


@pytest.mark.unit
def test_profile_config_rejects_missing_profile_identity_and_domain() -> None:
    with pytest.raises(ValueError, match="name must be a string"):
        ProfileConfig.from_mapping(
            {"version": "1.0.0", "domain": {"name": "kubernetes", "version": "0.2.0"}}
        )

    with pytest.raises(ValueError, match="profile version must not be empty"):
        ProfileConfig.from_mapping(
            {
                "name": "production-operator",
                "version": "",
                "domain": {"name": "kubernetes", "version": "0.2.0"},
            }
        )

    with pytest.raises(ValueError, match="profile domain name must not be empty"):
        ProfileConfig.from_mapping(
            {
                "name": "production-operator",
                "version": "1.0.0",
                "domain": {"version": "0.2.0"},
            }
        )

    with pytest.raises(ValueError, match="profile domains must match runtime configured domains"):
        ProfileConfig.from_mapping(
            {
                "name": "ai-infra-operator",
                "version": "1.0.0",
                "domains": [
                    {"name": "kubernetes", "version": "0.2.0"},
                    {"name": "observability", "version": "0.1.0"},
                ],
                "runtime": {
                    "domains": [
                        {"name": "kubernetes", "version": "0.2.0"},
                    ]
                },
            }
        )


@pytest.mark.unit
def test_profile_registry_rejects_duplicate_profile_names() -> None:
    profile = ProfileConfig.from_mapping(
        {
            "name": "production-operator",
            "version": "1.0.0",
            "domain": {"name": "kubernetes", "version": "0.2.0"},
        }
    ).to_profile()

    with pytest.raises(ValueError, match="duplicate profiles: production-operator"):
        ProfileRegistry((profile, profile))


@pytest.mark.unit
def test_profile_catalog_discovers_profile_configs_in_stable_order(tmp_path: Path) -> None:
    write_profile(tmp_path / "beta" / "profile.json", "beta-profile", domain="database")
    write_profile(tmp_path / "alpha.profile.json", "alpha-profile")

    catalog = load_profile_catalog(tmp_path)
    registry = catalog.registry()

    assert [entry.profile.name for entry in catalog.all()] == ["alpha-profile", "beta-profile"]
    assert registry.has("alpha-profile") is True
    assert catalog.get("beta-profile").profile.domain == DomainConfig("database", "0.2.0")


@pytest.mark.unit
def test_profile_catalog_accepts_single_profile_file(tmp_path: Path) -> None:
    path = tmp_path / "operator.profile.json"
    write_profile(path, "operator")

    catalog = ProfileCatalog.discover(path)

    assert catalog.all()[0].path == path
    assert catalog.all()[0].profile.name == "operator"


@pytest.mark.unit
def test_profile_catalog_rejects_missing_configs_and_duplicate_names(tmp_path: Path) -> None:
    with pytest.raises(ProfileConfigNotFoundError, match="not found"):
        load_profile_catalog(tmp_path / "missing")

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ProfileConfigNotFoundError, match="files not found"):
        load_profile_catalog(empty)

    write_profile(tmp_path / "one.profile.json", "duplicate")
    write_profile(tmp_path / "two" / "profile.json", "duplicate")
    with pytest.raises(ValueError, match="duplicate profiles: duplicate"):
        load_profile_catalog(tmp_path)


@pytest.mark.unit
def test_profile_catalog_verification_checks_local_config_identity(tmp_path: Path) -> None:
    profile_path = tmp_path / "operator.profile.json"
    write_profile(profile_path, "operator")
    catalog = load_profile_catalog(tmp_path)
    entry = catalog.get("operator")

    passing = catalog.verify()
    write_profile(profile_path, "renamed")
    failing = verify_profile_catalog_entry(entry)
    failed_checks = {check.name: check.message for check in failing.failed_checks}

    assert passing.passed is True
    assert {check.name for check in passing.checks} == {
        "profile_config_exists",
        "profile_config_matches_identity",
    }
    assert failing.passed is False
    assert "profile_config_matches_identity" in failed_checks
    assert "identity mismatch" in failed_checks["profile_config_matches_identity"]
