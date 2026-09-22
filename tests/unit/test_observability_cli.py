"""Observability domain CLI contribution tests (UA-LIVE-2026-09-21 R6-1)."""

from __future__ import annotations

import argparse
import pathlib

import pytest

from universal_agent.domains.observability.cli_runtime import (
    build_observability_profile_service,
    profile_domain_config,
)
from universal_agent.domains.observability.registration import (
    observability_cli_contribution,
)


def test_init_resolve_domain_builds_prometheus_config() -> None:
    args = argparse.Namespace(
        domain_backend="prometheus",
        observability_endpoint="http://victoria-metrics.test",
        observability_token_env=None,
        observability_token_secret="observability_api_token",
        observability_timeout_seconds=30.0,
    )

    contribution = observability_cli_contribution()
    resolve = contribution.init_resolve_domain
    assert resolve is not None
    outcome = resolve(args)
    assert outcome is not None

    assert outcome.domain_name == "observability"
    assert outcome.domain_config["backend"] == "prometheus"
    settings = outcome.domain_config["settings"]
    assert isinstance(settings, dict)
    assert settings["base_url"] == "http://victoria-metrics.test"
    assert settings["timeout_seconds"] == 30.0
    assert "bearer_token_secret" not in settings


def test_init_resolve_domain_ignores_other_backends() -> None:
    args = argparse.Namespace(domain_backend="kubectl")
    resolve = observability_cli_contribution().init_resolve_domain
    assert resolve is not None
    assert resolve(args) is None


def test_profile_domain_config_requires_endpoint() -> None:
    with pytest.raises(ValueError, match="requires --observability-endpoint"):
        profile_domain_config(
            domain_backend="prometheus",
            endpoint=None,
            bearer_token_secret=None,
            timeout_seconds=15.0,
        )


def test_profile_service_builds_observability_runtime(tmp_path: pathlib.Path) -> None:
    profile_path = tmp_path / "obs-profile.json"
    profile_path.write_text(
        """{
            "name": "obs-test",
            "version": "0.1.0",
            "description": "observability profile",
            "domain": {
                "name": "observability",
                "version": "0.2.0",
                "backend": "prometheus",
                "settings": {"base_url": "http://victoria-metrics.test", "timeout_seconds": 15.0}
            },
            "runtime": {
                "environment": {"environment": "staging"},
                "model": {"provider": "scripted", "name": "scripted", "timeout_seconds": 30.0},
                "store": {"backend": "file", "path": "STORE_PLACEHOLDER"},
                "domain": {
                    "name": "observability",
                    "version": "0.2.0",
                    "backend": "prometheus",
                    "settings": {
                        "base_url": "http://victoria-metrics.test",
                        "timeout_seconds": 15.0
                    }
                }
            }
        }""".replace("STORE_PLACEHOLDER", str(tmp_path / "store")),
        encoding="utf-8",
    )

    service = build_observability_profile_service(profile_path)

    capabilities = {item.name for item in service.capabilities()}
    assert {"query_metrics", "query_metric_ranges", "inspect_alert_rules"} <= capabilities
    assert service.accepts_profile("obs-test")


def test_profile_service_rejects_missing_base_url(tmp_path: pathlib.Path) -> None:
    profile_path = tmp_path / "obs-profile.json"
    payload = """{
        "name": "obs-test",
        "version": "0.1.0",
        "domain": {"name": "observability", "version": "0.2.0", "backend": "prometheus"},
        "runtime": {
            "environment": {"environment": "staging"},
            "model": {"provider": "scripted", "name": "scripted", "timeout_seconds": 30.0},
            "store": {"backend": "memory"},
            "domain": {"name": "observability", "version": "0.2.0", "backend": "prometheus"}
        }
    }"""
    profile_path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match="base_url"):
        build_observability_profile_service(profile_path)
