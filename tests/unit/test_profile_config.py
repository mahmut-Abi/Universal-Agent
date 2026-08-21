from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent import DomainConfig, ProfileConfig, ProfileRegistry, StoreConfig


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
    assert profile.runtime.store == StoreConfig.file("/tmp/universal-agent")
    assert profile.runtime.environment["environment"] == "production"


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
