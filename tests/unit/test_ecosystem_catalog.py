from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from universal_agent import (
    AmbiguousEcosystemRegistryItemError,
    DomainPackageRegistry,
    EcosystemCatalog,
    EcosystemDomainPackageRef,
    EcosystemProfileRef,
    EcosystemRegistryIndex,
    EcosystemRegistryInstallError,
    EcosystemRegistryItemNotFoundError,
    EcosystemRegistryManifest,
    EcosystemRegistryStoreNotFoundError,
    EcosystemRegistryValidationError,
    FileEcosystemRegistryStore,
    decode_ecosystem_registry_manifest,
    encode_ecosystem_registry_manifest,
    install_ecosystem_domain_packages,
    load_ecosystem_catalog,
    load_ecosystem_registry_index,
    load_ecosystem_registry_manifest,
    plan_ecosystem_domain_package_install,
    write_ecosystem_registry_manifest,
)
from universal_agent.core import DomainIdentity, JsonMapping


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_domain_package(root: Path) -> None:
    write_json(
        root / "manifest.json",
        {
            "apiVersion": "agent.nantian.dev/v1alpha1",
            "kind": "DomainPackage",
            "metadata": {
                "name": "kubernetes",
                "version": "1.0.0",
                "description": "Kubernetes domain package",
                "tags": ["kubernetes"],
            },
            "capabilities": ["inspect_workload"],
            "required_tools": ["kubernetes_api"],
        },
    )


def write_evaluation_dataset(root: Path) -> None:
    write_json(
        root / "suites" / "healthy.json",
        {
            "name": "healthy suite",
            "scenarios": [
                {
                    "name": "healthy workload",
                    "goal": {
                        "description": "Evaluate workload health",
                        "success_criteria": {"healthy": True},
                    },
                    "task": {
                        "description": "Inspect workload",
                        "required_criteria": ["healthy"],
                    },
                }
            ],
        },
    )
    write_json(
        root / "dataset.json",
        {
            "apiVersion": "agent.nantian.dev/v1alpha1",
            "kind": "EvaluationDataset",
            "metadata": {
                "name": "kubernetes-remediation",
                "version": "1.0.0",
                "description": "Kubernetes remediation evaluation dataset",
            },
            "domains": [{"name": "kubernetes", "version": "1.0.0"}],
            "suites": [{"name": "healthy", "path": "suites/healthy.json"}],
        },
    )


def write_profile(path: Path) -> None:
    write_json(
        path,
        {
            "name": "kubernetes-operator",
            "version": "1.0.0",
            "description": "Kubernetes operator profile",
            "domain": {"name": "kubernetes", "version": "1.0.0"},
            "runtime": {"domain": {"name": "kubernetes", "version": "1.0.0"}},
        },
    )


def test_ecosystem_catalog_discovers_all_local_ecosystem_artifacts(tmp_path: Path) -> None:
    domain_root = tmp_path / "domains"
    dataset_root = tmp_path / "datasets"
    profile_root = tmp_path / "profiles"
    write_domain_package(domain_root / "kubernetes")
    write_evaluation_dataset(dataset_root / "kubernetes")
    write_profile(profile_root / "kubernetes.profile.json")

    catalog = load_ecosystem_catalog(
        domain_package_root=domain_root,
        evaluation_dataset_root=dataset_root,
        profile_root=profile_root,
    )

    assert catalog.summary.domain_package_count == 1
    assert catalog.summary.evaluation_dataset_count == 1
    assert catalog.summary.profile_count == 1
    assert catalog.summary.total_items == 3
    assert catalog.domain_packages[0].identity.name == "kubernetes"
    assert catalog.evaluation_datasets[0].identity.name == "kubernetes-remediation"
    assert catalog.profiles[0].profile.name == "kubernetes-operator"
    assert catalog.verify().passed is True


def test_ecosystem_catalog_can_be_empty_or_partial(tmp_path: Path) -> None:
    profile_root = tmp_path / "profiles"
    write_profile(profile_root / "profile.json")

    empty = EcosystemCatalog.discover()
    partial = EcosystemCatalog.discover(profile_root=profile_root)

    assert empty.summary.total_items == 0
    assert partial.summary.profile_count == 1
    assert partial.domain_packages == ()
    assert partial.evaluation_datasets == ()


def test_ecosystem_catalog_verification_reports_missing_domain_references(
    tmp_path: Path,
) -> None:
    profile_root = tmp_path / "profiles"
    dataset_root = tmp_path / "datasets"
    write_profile(profile_root / "profile.json")
    write_evaluation_dataset(dataset_root / "kubernetes")

    catalog = EcosystemCatalog.discover(
        evaluation_dataset_root=dataset_root,
        profile_root=profile_root,
    )
    report = catalog.verify()
    failed = {check.name: check.message for check in report.failed_checks}

    assert report.passed is False
    assert "profile_domains_registered" in failed
    assert "dataset_domains_registered" in failed
    assert "kubernetes@1.0.0" in failed["profile_domains_registered"]


def test_ecosystem_registry_manifest_round_trips_catalog_metadata(tmp_path: Path) -> None:
    domain_root = tmp_path / "domains"
    dataset_root = tmp_path / "datasets"
    profile_root = tmp_path / "profiles"
    write_domain_package(domain_root / "kubernetes")
    write_evaluation_dataset(dataset_root / "kubernetes")
    write_profile(profile_root / "kubernetes.profile.json")
    catalog = load_ecosystem_catalog(
        domain_package_root=domain_root,
        evaluation_dataset_root=dataset_root,
        profile_root=profile_root,
    )

    manifest = catalog.registry_manifest(
        name="ops-ecosystem",
        version="1.2.3",
        description="Operations ecosystem registry",
    )
    encoded = encode_ecosystem_registry_manifest(manifest)
    decoded = decode_ecosystem_registry_manifest(cast(JsonMapping, encoded))

    assert encoded["kind"] == "EcosystemRegistry"
    assert encoded["summary"] == {
        "domain_package_count": 1,
        "evaluation_dataset_count": 1,
        "profile_count": 1,
        "total_items": 3,
    }
    assert decoded.name == "ops-ecosystem"
    assert decoded.version == "1.2.3"
    assert decoded.domain_packages[0].identity.name == "kubernetes"
    assert decoded.evaluation_datasets[0].domains[0].name == "kubernetes"
    assert decoded.profiles[0].domains[0].name == "kubernetes"

    output_path = tmp_path / "registry" / "ecosystem.json"
    result = write_ecosystem_registry_manifest(output_path, manifest)
    loaded = load_ecosystem_registry_manifest(output_path)

    assert result.overwritten is False
    assert result.path == output_path
    assert loaded.name == "ops-ecosystem"
    assert loaded.summary.total_items == 3

    with pytest.raises(EcosystemRegistryValidationError, match="already exists"):
        write_ecosystem_registry_manifest(output_path, manifest)
    overwritten = write_ecosystem_registry_manifest(output_path, manifest, overwrite=True)
    assert overwritten.overwritten is True


def test_ecosystem_registry_index_queries_exported_manifest(tmp_path: Path) -> None:
    domain_root = tmp_path / "domains"
    dataset_root = tmp_path / "datasets"
    profile_root = tmp_path / "profiles"
    write_domain_package(domain_root / "kubernetes")
    write_evaluation_dataset(dataset_root / "kubernetes")
    write_profile(profile_root / "kubernetes.profile.json")
    catalog = load_ecosystem_catalog(
        domain_package_root=domain_root,
        evaluation_dataset_root=dataset_root,
        profile_root=profile_root,
    )
    manifest = catalog.registry_manifest()
    index = EcosystemRegistryIndex(manifest)
    domain = DomainIdentity("kubernetes", "1.0.0")

    assert index.summary.total_items == 3
    assert index.domain_package("kubernetes").version == "1.0.0"
    assert index.domain_packages(tag="kubernetes")[0].name == "kubernetes"
    assert index.evaluation_dataset("kubernetes-remediation").version == "1.0.0"
    assert index.evaluation_datasets(domain=domain)[0].name == "kubernetes-remediation"
    assert index.profile("kubernetes-operator").version == "1.0.0"
    assert index.profiles(domain=domain)[0].name == "kubernetes-operator"

    output_path = tmp_path / "registry.json"
    write_ecosystem_registry_manifest(output_path, manifest)
    loaded = load_ecosystem_registry_index(output_path)
    assert loaded.domain_package("kubernetes").version == "1.0.0"
    assert index.verify().passed is True

    with pytest.raises(EcosystemRegistryItemNotFoundError, match="domain package not found"):
        index.domain_package("database")

    ambiguous = EcosystemRegistryIndex(
        EcosystemRegistryManifest(
            api_version="agent.nantian.dev/v1alpha1",
            kind="EcosystemRegistry",
            name="ambiguous",
            version="1.0.0",
            description="Ambiguous registry",
            domain_packages=(
                EcosystemDomainPackageRef("kubernetes", "1.0.0", "Kubernetes v1"),
                EcosystemDomainPackageRef("kubernetes", "2.0.0", "Kubernetes v2"),
            ),
        )
    )
    with pytest.raises(AmbiguousEcosystemRegistryItemError, match="multiple versions"):
        ambiguous.domain_package("kubernetes")


def test_ecosystem_registry_index_verification_reports_missing_references() -> None:
    index = EcosystemRegistryIndex(
        EcosystemRegistryManifest(
            api_version="agent.nantian.dev/v1alpha1",
            kind="EcosystemRegistry",
            name="missing-references",
            version="1.0.0",
            description="Registry with missing references",
            domain_packages=(
                EcosystemDomainPackageRef(
                    "kubernetes",
                    "1.0.0",
                    "Kubernetes",
                    dependencies=(DomainIdentity("observability", "1.0.0"),),
                ),
            ),
            profiles=(
                EcosystemProfileRef(
                    "ops-profile",
                    "1.0.0",
                    "Ops profile",
                    (DomainIdentity("database", "1.0.0"),),
                    "profiles/ops.profile.json",
                ),
            ),
        )
    )

    report = index.verify()
    failed = {check.name: check.message for check in report.failed_checks}

    assert report.passed is False
    assert "profile_domains_registered" in failed
    assert "package_dependencies_registered" in failed
    assert "database@1.0.0" in failed["profile_domains_registered"]
    assert "observability@1.0.0" in failed["package_dependencies_registered"]


def test_file_ecosystem_registry_store_persists_and_lists_manifests(tmp_path: Path) -> None:
    first = EcosystemRegistryManifest(
        api_version="agent.nantian.dev/v1alpha1",
        kind="EcosystemRegistry",
        name="ops-ecosystem",
        version="1.0.0",
        description="Operations ecosystem registry",
        domain_packages=(EcosystemDomainPackageRef("kubernetes", "1.0.0", "Kubernetes"),),
    )
    second = EcosystemRegistryManifest(
        api_version="agent.nantian.dev/v1alpha1",
        kind="EcosystemRegistry",
        name="ops-ecosystem",
        version="2.0.0",
        description="Operations ecosystem registry v2",
        domain_packages=(EcosystemDomainPackageRef("kubernetes", "2.0.0", "Kubernetes"),),
    )
    store = FileEcosystemRegistryStore(tmp_path / "registries")

    first_result = store.save(first)
    second_result = store.save(second)
    listed = store.list_manifests()

    assert first_result.overwritten is False
    assert first_result.path.name == "ops-ecosystem@1.0.0.json"
    assert second_result.path.name == "ops-ecosystem@2.0.0.json"
    assert [manifest.version for manifest in listed] == ["1.0.0", "2.0.0"]
    assert store.load("ops-ecosystem", "1.0.0").description == "Operations ecosystem registry"
    assert store.index("ops-ecosystem", "2.0.0").domain_package("kubernetes").version == "2.0.0"

    overwritten = store.save(first)
    assert overwritten.overwritten is True

    with pytest.raises(EcosystemRegistryStoreNotFoundError, match=r"missing@1\.0\.0"):
        store.load("missing", "1.0.0")

    with pytest.raises(EcosystemRegistryValidationError, match="already exists"):
        store.save(first, overwrite=False)


def test_ecosystem_registry_plans_and_installs_domain_packages(
    tmp_path: Path,
) -> None:
    domain_root = tmp_path / "domains"
    write_domain_package(domain_root / "kubernetes")
    catalog = load_ecosystem_catalog(domain_package_root=domain_root)
    manifest = catalog.registry_manifest()

    plan = plan_ecosystem_domain_package_install(manifest)
    result = install_ecosystem_domain_packages(manifest)

    assert plan.identities == (DomainIdentity("kubernetes", "1.0.0"),)
    assert plan.candidates[0].reference.name == "kubernetes"
    assert result.installed_packages[0].identity == DomainIdentity("kubernetes", "1.0.0")
    assert result.registry.get_by_name("kubernetes").manifest.capabilities == ("inspect_workload",)


def test_ecosystem_registry_installs_domain_packages_from_relative_paths(
    tmp_path: Path,
) -> None:
    write_domain_package(tmp_path / "domains" / "kubernetes")
    manifest = EcosystemRegistryManifest(
        api_version="agent.nantian.dev/v1alpha1",
        kind="EcosystemRegistry",
        name="relative-registry",
        version="1.0.0",
        description="Relative path registry",
        domain_packages=(
            EcosystemDomainPackageRef(
                "kubernetes",
                "1.0.0",
                "Kubernetes",
                root_path="domains/kubernetes",
                manifest_path="domains/kubernetes/manifest.json",
            ),
        ),
    )

    result = install_ecosystem_domain_packages(manifest, base_path=tmp_path)

    assert result.registry.identities() == (DomainIdentity("kubernetes", "1.0.0"),)


def test_ecosystem_registry_install_refuses_identity_mismatch(
    tmp_path: Path,
) -> None:
    write_domain_package(tmp_path / "domains" / "kubernetes")
    manifest = EcosystemRegistryManifest(
        api_version="agent.nantian.dev/v1alpha1",
        kind="EcosystemRegistry",
        name="mismatched-registry",
        version="1.0.0",
        description="Mismatched registry",
        domain_packages=(
            EcosystemDomainPackageRef(
                "database",
                "1.0.0",
                "Database",
                manifest_path=str(tmp_path / "domains" / "kubernetes" / "manifest.json"),
            ),
        ),
    )

    with pytest.raises(EcosystemRegistryInstallError, match="identity mismatch"):
        install_ecosystem_domain_packages(manifest)


def test_ecosystem_registry_install_refuses_missing_paths_and_duplicates(
    tmp_path: Path,
) -> None:
    write_domain_package(tmp_path / "domains" / "kubernetes")
    catalog = load_ecosystem_catalog(domain_package_root=tmp_path / "domains")
    manifest = catalog.registry_manifest()
    missing_path_manifest = EcosystemRegistryManifest(
        api_version="agent.nantian.dev/v1alpha1",
        kind="EcosystemRegistry",
        name="missing-path-registry",
        version="1.0.0",
        description="Missing path registry",
        domain_packages=(EcosystemDomainPackageRef("kubernetes", "1.0.0", "Kubernetes"),),
    )
    registry = DomainPackageRegistry()

    install_ecosystem_domain_packages(manifest, registry=registry)

    with pytest.raises(EcosystemRegistryInstallError, match="no local path"):
        install_ecosystem_domain_packages(missing_path_manifest)
    with pytest.raises(EcosystemRegistryInstallError, match="already registered"):
        install_ecosystem_domain_packages(manifest, registry=registry)
    assert registry.identities() == (DomainIdentity("kubernetes", "1.0.0"),)


def test_ecosystem_registry_install_requires_verified_references_by_default() -> None:
    manifest = EcosystemRegistryManifest(
        api_version="agent.nantian.dev/v1alpha1",
        kind="EcosystemRegistry",
        name="broken-registry",
        version="1.0.0",
        description="Broken registry",
        domain_packages=(
            EcosystemDomainPackageRef(
                "kubernetes",
                "1.0.0",
                "Kubernetes",
                dependencies=(DomainIdentity("observability", "1.0.0"),),
            ),
        ),
    )

    with pytest.raises(EcosystemRegistryInstallError, match="verification failed"):
        install_ecosystem_domain_packages(manifest)
