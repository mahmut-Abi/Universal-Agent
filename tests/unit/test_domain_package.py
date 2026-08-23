from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.core import DomainIdentity, JsonMapping, JsonValue
from universal_agent.domain import (
    AmbiguousDomainPackageError,
    DomainPackageNotFoundError,
    DomainPackageRegistry,
    DomainPackageValidationError,
    decode_domain_package_manifest,
)


def package_payload(
    name: str = "kubernetes",
    version: str = "1.0.0",
    *,
    tags: tuple[str, ...] = ("ops",),
) -> dict[str, JsonValue]:
    return {
        "apiVersion": "agent.nantian.dev/v1alpha1",
        "kind": "DomainPackage",
        "metadata": {
            "name": name,
            "version": version,
            "description": f"{name} domain package",
            "author": "Runtime Team",
            "tags": list(tags),
        },
        "entrypoint": f"{name}.domain:build_domain",
        "ontology": ["Deployment"],
        "capabilities": ["inspect_workload"],
        "tools": ["kubernetes_api_get_deployment"],
        "policies": ["read_only"],
        "procedures": ["diagnose_unhealthy_workload"],
        "knowledge": ["deployment_rollout"],
        "evaluators": ["workload_health"],
        "context_providers": ["cluster_context"],
        "prompts": ["diagnostic_notes"],
        "dependencies": [{"name": "observability", "version": "2.0.0"}],
        "required_tools": ["kubernetes_api"],
        "compatibility": {
            "runtime_api": ">=0.1,<1",
            "domain_api": "agent.nantian.dev/v1alpha1",
        },
        "security": {
            "requires_confirmation": False,
            "side_effects": "none",
        },
    }


def write_manifest(root: Path, payload: JsonMapping) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return manifest_path


def test_decode_domain_package_manifest_accepts_structured_ecosystem_metadata() -> None:
    manifest = decode_domain_package_manifest(package_payload())

    assert manifest.api_version == "agent.nantian.dev/v1alpha1"
    assert manifest.kind == "DomainPackage"
    assert manifest.identity == DomainIdentity("kubernetes", "1.0.0")
    assert manifest.author == "Runtime Team"
    assert manifest.entrypoint == "kubernetes.domain:build_domain"
    assert manifest.capabilities == ("inspect_workload",)
    assert manifest.procedures == ("diagnose_unhealthy_workload",)
    assert manifest.dependencies == (DomainIdentity("observability", "2.0.0"),)
    assert manifest.required_tools == ("kubernetes_api",)
    assert manifest.compatibility.runtime_api == ">=0.1,<1"
    assert manifest.compatibility.domain_api == "agent.nantian.dev/v1alpha1"
    assert manifest.security["side_effects"] == "none"
    assert manifest.tags == ("ops",)


def test_decode_domain_package_manifest_accepts_legacy_snake_case_api_version() -> None:
    payload = package_payload()
    payload["api_version"] = payload.pop("apiVersion")

    manifest = decode_domain_package_manifest(payload)

    assert manifest.api_version == "agent.nantian.dev/v1alpha1"


def test_decode_domain_package_manifest_rejects_conflicting_api_version_keys() -> None:
    payload = package_payload()
    payload["api_version"] = "agent.nantian.dev/v2"

    with pytest.raises(DomainPackageValidationError, match="apiVersion and api_version"):
        decode_domain_package_manifest(payload)


def test_domain_package_registry_installs_validated_package(tmp_path: Path) -> None:
    root = tmp_path / "kubernetes-domain"
    manifest_path = write_manifest(root, package_payload())

    registry = DomainPackageRegistry()
    package = registry.install(root)

    assert package.root_path == root
    assert package.manifest_path == manifest_path
    assert registry.identities() == (DomainIdentity("kubernetes", "1.0.0"),)
    assert registry.get_by_name("kubernetes") is package
    assert registry.list(tag="ops") == (package,)
    assert registry.list(tag="database") == ()


def test_domain_package_registry_discovers_manifests_in_stable_order(tmp_path: Path) -> None:
    write_manifest(tmp_path / "beta-domain", package_payload("beta", tags=("database",)))
    write_manifest(tmp_path / "alpha-domain", package_payload("alpha", tags=("ops",)))

    registry = DomainPackageRegistry()
    packages = registry.discover(tmp_path)

    assert [package.identity.name for package in packages] == ["alpha", "beta"]
    assert registry.identities() == (
        DomainIdentity("alpha", "1.0.0"),
        DomainIdentity("beta", "1.0.0"),
    )


def test_domain_package_registry_reports_duplicate_missing_and_ambiguous_packages(
    tmp_path: Path,
) -> None:
    registry = DomainPackageRegistry()
    package = registry.install(write_manifest(tmp_path / "alpha-v1", package_payload("alpha")))

    with pytest.raises(DomainPackageValidationError, match="already registered"):
        registry.register(package)

    with pytest.raises(DomainPackageNotFoundError, match="beta"):
        registry.get_by_name("beta")

    registry.install(write_manifest(tmp_path / "alpha-v2", package_payload("alpha", "2.0.0")))

    with pytest.raises(AmbiguousDomainPackageError, match="multiple registered versions"):
        registry.get_by_name("alpha")


def test_domain_package_registry_refuses_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(DomainPackageNotFoundError, match="manifest not found"):
        DomainPackageRegistry().install(tmp_path / "missing-domain")
