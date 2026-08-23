from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.core import DomainIdentity, JsonMapping, JsonValue
from universal_agent.domain import (
    AmbiguousDomainPackageError,
    DomainPackageCompatibility,
    DomainPackageNotFoundError,
    DomainPackageRegistry,
    DomainPackageScaffoldSpec,
    DomainPackageValidationError,
    build_domain_package_manifest,
    decode_domain_package_manifest,
    encode_domain_package_manifest,
    scaffold_domain_package,
    verify_domain_package,
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


def test_domain_package_verification_checks_local_package_paths_and_manifest_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "kubernetes-domain"
    write_manifest(root, package_payload())
    package = DomainPackageRegistry().install(root)

    passing = verify_domain_package(package)
    write_manifest(root, package_payload("database"))
    failing = verify_domain_package(package)
    failed_checks = {check.name: check.message for check in failing.failed_checks}

    assert passing.passed is True
    assert {check.name for check in passing.checks} == {
        "package_root_exists",
        "package_manifest_exists",
        "package_manifest_matches_identity",
    }
    assert failing.passed is False
    assert "package_manifest_matches_identity" in failed_checks
    assert "identity mismatch" in failed_checks["package_manifest_matches_identity"]


def test_domain_package_registry_verification_checks_dependency_closure(
    tmp_path: Path,
) -> None:
    registry = DomainPackageRegistry()
    registry.install(write_manifest(tmp_path / "kubernetes-domain", package_payload()))
    missing = registry.verify()

    observability = package_payload("observability", "2.0.0", tags=("observability",))
    observability["dependencies"] = []
    registry.install(write_manifest(tmp_path / "observability-domain", observability))
    passing = registry.verify()

    assert missing.passed is False
    assert missing.failed_checks[0].name == "package_dependencies_registered"
    assert "observability@2.0.0" in missing.failed_checks[0].message
    assert passing.passed is True


def test_build_domain_package_manifest_encodes_sdk_spec_with_default_entrypoint() -> None:
    manifest = build_domain_package_manifest(
        DomainPackageScaffoldSpec(
            name="ai-ops",
            description="AI operations domain package",
            author="Runtime Team",
            ontology=("Incident",),
            capabilities=("inspect_incident",),
            tools=("incident_api_get",),
            policies=("read_only",),
            evaluators=("incident_status",),
            context_providers=("incident_context",),
            dependencies=(DomainIdentity("observability", "1.0.0"),),
            required_tools=("incident_api",),
            compatibility=DomainPackageCompatibility(
                runtime_api=">=0.1,<1",
                domain_api="agent.nantian.dev/v1alpha1",
            ),
            tags=("ops", "ai"),
        )
    )

    payload = encode_domain_package_manifest(manifest)
    decoded = decode_domain_package_manifest(payload)

    assert manifest.entrypoint == "ai_ops.domain:build_domain"
    assert decoded.identity == DomainIdentity("ai-ops", "0.1.0")
    assert decoded.capabilities == ("inspect_incident",)
    assert decoded.dependencies == (DomainIdentity("observability", "1.0.0"),)
    assert decoded.tags == ("ops", "ai")


def test_scaffold_domain_package_creates_registry_loadable_package(tmp_path: Path) -> None:
    package_root = tmp_path / "ai-ops-domain"

    result = scaffold_domain_package(
        package_root,
        DomainPackageScaffoldSpec(
            name="ai-ops",
            version="1.0.0",
            description="AI operations domain package",
            capabilities=("inspect_incident", "resolve_incident"),
            tools=("incident_api_get", "incident_api_resolve"),
            policies=("incident_safety",),
            required_tools=("incident_api",),
            security={"side_effects": "reversible"},
            tags=("ops",),
        ),
    )
    installed = DomainPackageRegistry().install(package_root)
    payload = json.loads((package_root / "manifest.json").read_text(encoding="utf-8"))

    assert result.package.identity == DomainIdentity("ai-ops", "1.0.0")
    assert installed.manifest.capabilities == ("inspect_incident", "resolve_incident")
    assert installed.manifest.security["side_effects"] == "reversible"
    assert payload["entrypoint"] == "ai_ops.domain:build_domain"
    assert (package_root / "ontology").is_dir()
    assert (package_root / "context_providers").is_dir()
    assert result.written_paths == (package_root / "manifest.json",)


def test_scaffold_domain_package_requires_force_to_overwrite_manifest(tmp_path: Path) -> None:
    package_root = tmp_path / "database-domain"
    scaffold_domain_package(
        package_root,
        DomainPackageScaffoldSpec(name="database", description="Database domain package"),
    )

    with pytest.raises(DomainPackageValidationError, match="already exists"):
        scaffold_domain_package(
            package_root,
            DomainPackageScaffoldSpec(
                name="database",
                version="2.0.0",
                description="Updated database domain package",
            ),
        )

    result = scaffold_domain_package(
        package_root,
        DomainPackageScaffoldSpec(
            name="database",
            version="2.0.0",
            description="Updated database domain package",
        ),
        overwrite=True,
    )

    assert result.overwritten is True
    assert result.package.identity == DomainIdentity("database", "2.0.0")
