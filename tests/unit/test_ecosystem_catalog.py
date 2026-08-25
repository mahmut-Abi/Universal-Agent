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
    EcosystemEvaluationDatasetRef,
    EcosystemEvaluationDatasetSuiteRef,
    EcosystemProfileRef,
    EcosystemRegistryIndex,
    EcosystemRegistryInstallError,
    EcosystemRegistryItemNotFoundError,
    EcosystemRegistryManifest,
    EcosystemRegistrySignatureVerification,
    EcosystemRegistryStoreNotFoundError,
    EcosystemRegistryTrustPolicy,
    EcosystemRegistryValidationError,
    FileEcosystemRegistryStore,
    decode_ecosystem_registry_manifest,
    encode_ecosystem_registry_manifest,
    install_ecosystem,
    install_ecosystem_domain_packages,
    load_ecosystem_catalog,
    load_ecosystem_registry_index,
    load_ecosystem_registry_manifest,
    plan_ecosystem_domain_package_install,
    plan_ecosystem_install,
    write_ecosystem_registry_manifest,
)
from universal_agent.core import DomainIdentity, JsonMapping


class StaticRegistrySignatureVerifier:
    def __init__(self, passed: bool, reason: str = "static registry signature check") -> None:
        self._passed = passed
        self._reason = reason

    def verify_registry(
        self,
        manifest: EcosystemRegistryManifest,
    ) -> EcosystemRegistrySignatureVerification:
        signature = manifest.metadata.get("signature")
        signer = None
        if isinstance(signature, dict):
            signed_by = signature.get("signed_by")
            if isinstance(signed_by, str):
                signer = signed_by
        return EcosystemRegistrySignatureVerification(
            passed=self._passed,
            verifier="static-test-verifier",
            reason=self._reason,
            signer=signer,
        )


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_domain_package(
    root: Path,
    *,
    name: str = "kubernetes",
    dependencies: tuple[DomainIdentity, ...] = (),
) -> None:
    (root / "resources").mkdir(parents=True, exist_ok=True)
    (root / "resources" / "runbook.md").touch()
    (root / "schemas").mkdir(parents=True, exist_ok=True)
    (root / "schemas" / "workload.json").touch()
    write_json(
        root / "manifest.json",
        {
            "apiVersion": "agent.nantian.dev/v1alpha1",
            "kind": "DomainPackage",
            "metadata": {
                "name": name,
                "version": "1.0.0",
                "description": f"{name} domain package",
                "tags": [name],
            },
            "entrypoint": f"{name}.domain:build_domain",
            "capabilities": ["inspect_workload"],
            "resources": ["resources/runbook.md", "schemas/workload.json"],
            "required_tools": ["kubernetes_api"],
            "dependencies": [
                {"name": dependency.name, "version": dependency.version}
                for dependency in dependencies
            ],
            "compatibility": {
                "runtime_api": ">=0.1,<1",
                "domain_api": "agent.nantian.dev/v1alpha1",
            },
            "security": {
                "requires_confirmation": False,
                "side_effects": "none",
            },
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
    assert decoded.domain_packages[0].entrypoint == "kubernetes.domain:build_domain"
    assert decoded.domain_packages[0].resources == (
        "resources/runbook.md",
        "schemas/workload.json",
    )
    assert decoded.domain_packages[0].compatibility.runtime_api == ">=0.1,<1"
    assert decoded.domain_packages[0].compatibility.domain_api == "agent.nantian.dev/v1alpha1"
    assert decoded.domain_packages[0].security["side_effects"] == "none"
    assert len(decoded.domain_packages[0].manifest_sha256) == 64
    assert decoded.domain_packages[0].manifest_sha256 == cast(
        str,
        encoded["domain_packages"][0]["manifest_sha256"],
    )
    assert encoded["domain_packages"][0]["compatibility"] == {
        "domain_api": "agent.nantian.dev/v1alpha1",
        "runtime_api": ">=0.1,<1",
    }
    assert encoded["domain_packages"][0]["entrypoint"] == "kubernetes.domain:build_domain"
    assert encoded["domain_packages"][0]["resources"] == [
        "resources/runbook.md",
        "schemas/workload.json",
    ]
    assert encoded["domain_packages"][0]["security"] == {
        "requires_confirmation": False,
        "side_effects": "none",
    }
    assert decoded.evaluation_datasets[0].domains[0].name == "kubernetes"
    assert len(decoded.evaluation_datasets[0].manifest_sha256) == 64
    assert decoded.profiles[0].domains[0].name == "kubernetes"
    assert len(decoded.profiles[0].config_sha256) == 64

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


def test_ecosystem_registry_manifest_accepts_legacy_package_refs_without_optional_metadata() -> (
    None
):
    decoded = decode_ecosystem_registry_manifest(
        {
            "apiVersion": "agent.nantian.dev/v1alpha1",
            "kind": "EcosystemRegistry",
            "metadata": {
                "name": "legacy-registry",
                "version": "1.0.0",
                "description": "Legacy registry",
            },
            "domain_packages": [
                {
                    "name": "kubernetes",
                    "version": "1.0.0",
                    "description": "Kubernetes",
                }
            ],
        }
    )

    assert decoded.domain_packages[0].compatibility.runtime_api is None
    assert decoded.domain_packages[0].compatibility.domain_api is None
    assert decoded.domain_packages[0].entrypoint is None
    assert decoded.domain_packages[0].resources == ()
    assert decoded.domain_packages[0].security == {}
    assert decoded.domain_packages[0].manifest_sha256 == ""


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


def test_ecosystem_registry_index_verification_reports_dependency_cycles() -> None:
    index = EcosystemRegistryIndex(
        EcosystemRegistryManifest(
            api_version="agent.nantian.dev/v1alpha1",
            kind="EcosystemRegistry",
            name="cyclic-registry",
            version="1.0.0",
            description="Registry with cyclic package references",
            domain_packages=(
                EcosystemDomainPackageRef(
                    "alpha",
                    "1.0.0",
                    "Alpha",
                    dependencies=(DomainIdentity("beta", "1.0.0"),),
                ),
                EcosystemDomainPackageRef(
                    "beta",
                    "1.0.0",
                    "Beta",
                    dependencies=(DomainIdentity("alpha", "1.0.0"),),
                ),
            ),
        )
    )

    report = index.verify()
    failed = {check.name: check.message for check in report.failed_checks}

    assert report.passed is False
    assert "package_dependencies_acyclic" in failed
    assert "alpha@1.0.0" in failed["package_dependencies_acyclic"]
    assert "beta@1.0.0" in failed["package_dependencies_acyclic"]


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


def test_ecosystem_registry_domain_package_install_plan_sorts_dependencies_first(
    tmp_path: Path,
) -> None:
    domain_root = tmp_path / "domains"
    write_domain_package(
        domain_root / "kubernetes",
        dependencies=(DomainIdentity("observability", "1.0.0"),),
    )
    write_domain_package(domain_root / "observability", name="observability")
    catalog = load_ecosystem_catalog(domain_package_root=domain_root)
    manifest = catalog.registry_manifest()

    plan = plan_ecosystem_domain_package_install(manifest)

    assert tuple(reference.identity for reference in manifest.domain_packages) == (
        DomainIdentity("kubernetes", "1.0.0"),
        DomainIdentity("observability", "1.0.0"),
    )
    assert plan.identities == (
        DomainIdentity("observability", "1.0.0"),
        DomainIdentity("kubernetes", "1.0.0"),
    )


def test_ecosystem_registry_domain_package_install_checks_loaded_manifest_dependencies(
    tmp_path: Path,
) -> None:
    domain_root = tmp_path / "domains"
    write_domain_package(
        domain_root / "kubernetes",
        dependencies=(DomainIdentity("observability", "1.0.0"),),
    )
    manifest = EcosystemRegistryManifest(
        api_version="agent.nantian.dev/v1alpha1",
        kind="EcosystemRegistry",
        name="legacy-dependency-gap",
        version="1.0.0",
        description="Legacy registry with missing loaded dependency metadata",
        domain_packages=(
            EcosystemDomainPackageRef(
                "kubernetes",
                "1.0.0",
                "Kubernetes",
                manifest_path=str(domain_root / "kubernetes" / "manifest.json"),
            ),
        ),
    )

    with pytest.raises(EcosystemRegistryInstallError, match="missing dependencies"):
        plan_ecosystem_domain_package_install(manifest)


def test_ecosystem_registry_plans_and_installs_full_ecosystem_artifacts(
    tmp_path: Path,
) -> None:
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

    plan = plan_ecosystem_install(manifest)
    result = install_ecosystem(manifest)

    assert plan.domain_packages.identities == (DomainIdentity("kubernetes", "1.0.0"),)
    assert plan.evaluation_datasets[0].dataset.identity.name == "kubernetes-remediation"
    assert plan.profiles[0].entry.profile.name == "kubernetes-operator"
    assert result.domain_packages.get_by_name("kubernetes").identity == DomainIdentity(
        "kubernetes",
        "1.0.0",
    )
    assert result.evaluation_datasets.get_by_name("kubernetes-remediation").identity.name == (
        "kubernetes-remediation"
    )
    assert result.profiles.get("kubernetes-operator").version == "1.0.0"
    assert result.installed_domain_packages[0].identity.name == "kubernetes"
    assert result.installed_evaluation_datasets[0].identity.name == "kubernetes-remediation"
    assert result.installed_profiles[0].path.name == "kubernetes.profile.json"


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


def test_ecosystem_registry_install_refuses_unverified_signature_metadata_by_default(
    tmp_path: Path,
) -> None:
    domain_root = tmp_path / "domains"
    write_domain_package(domain_root / "kubernetes")
    catalog = load_ecosystem_catalog(domain_package_root=domain_root)
    exported = catalog.registry_manifest()
    signed = EcosystemRegistryManifest(
        api_version=exported.api_version,
        kind=exported.kind,
        name=exported.name,
        version=exported.version,
        description=exported.description,
        domain_packages=exported.domain_packages,
        evaluation_datasets=exported.evaluation_datasets,
        profiles=exported.profiles,
        metadata={
            "signature": {
                "algorithm": "ed25519",
                "value": "local-test-signature",
            }
        },
    )

    with pytest.raises(EcosystemRegistryInstallError, match="pass a signature verifier"):
        plan_ecosystem_install(signed)

    allowed = plan_ecosystem_install(
        signed,
        trust_policy=EcosystemRegistryTrustPolicy(allow_unverified_signatures=True),
    )

    assert allowed.domain_packages.identities == (DomainIdentity("kubernetes", "1.0.0"),)


def test_ecosystem_registry_install_accepts_verified_signature_metadata(
    tmp_path: Path,
) -> None:
    domain_root = tmp_path / "domains"
    write_domain_package(domain_root / "kubernetes")
    exported = load_ecosystem_catalog(domain_package_root=domain_root).registry_manifest()
    signed = EcosystemRegistryManifest(
        api_version=exported.api_version,
        kind=exported.kind,
        name=exported.name,
        version=exported.version,
        description=exported.description,
        domain_packages=exported.domain_packages,
        metadata={
            "signature": {
                "algorithm": "local-static",
                "value": "valid",
                "signed_by": "platform-team",
            }
        },
    )

    plan = plan_ecosystem_install(
        signed,
        signature_verifier=StaticRegistrySignatureVerifier(True),
    )

    assert plan.domain_packages.identities == (DomainIdentity("kubernetes", "1.0.0"),)


def test_ecosystem_registry_install_rejects_failed_signature_verification(
    tmp_path: Path,
) -> None:
    domain_root = tmp_path / "domains"
    write_domain_package(domain_root / "kubernetes")
    exported = load_ecosystem_catalog(domain_package_root=domain_root).registry_manifest()
    signed = EcosystemRegistryManifest(
        api_version=exported.api_version,
        kind=exported.kind,
        name=exported.name,
        version=exported.version,
        description=exported.description,
        domain_packages=exported.domain_packages,
        metadata={"signature": {"algorithm": "local-static", "value": "invalid"}},
    )

    with pytest.raises(EcosystemRegistryInstallError, match="bad signature"):
        install_ecosystem_domain_packages(
            signed,
            signature_verifier=StaticRegistrySignatureVerifier(False, "bad signature"),
        )


def test_ecosystem_registry_install_can_require_signed_registry(
    tmp_path: Path,
) -> None:
    domain_root = tmp_path / "domains"
    write_domain_package(domain_root / "kubernetes")
    unsigned = load_ecosystem_catalog(domain_package_root=domain_root).registry_manifest()

    with pytest.raises(EcosystemRegistryInstallError, match="unsigned ecosystem registry"):
        install_ecosystem_domain_packages(
            unsigned,
            trust_policy=EcosystemRegistryTrustPolicy(allow_unsigned=False),
        )


def test_ecosystem_registry_install_refuses_paths_outside_registry_base(
    tmp_path: Path,
) -> None:
    base_root = tmp_path / "registry"
    outside_root = tmp_path / "outside"
    write_domain_package(outside_root / "domains" / "kubernetes")
    manifest = EcosystemRegistryManifest(
        api_version="agent.nantian.dev/v1alpha1",
        kind="EcosystemRegistry",
        name="escaping-registry",
        version="1.0.0",
        description="Escaping path registry",
        domain_packages=(
            EcosystemDomainPackageRef(
                "kubernetes",
                "1.0.0",
                "Kubernetes",
                manifest_path="../outside/domains/kubernetes/manifest.json",
            ),
        ),
    )

    with pytest.raises(EcosystemRegistryInstallError, match="escapes registry base path"):
        install_ecosystem_domain_packages(manifest, base_path=base_root)


def test_ecosystem_registry_install_refuses_missing_domain_package_resources(
    tmp_path: Path,
) -> None:
    domain_root = tmp_path / "domains"
    write_domain_package(domain_root / "kubernetes")
    catalog = load_ecosystem_catalog(domain_package_root=domain_root)
    manifest = catalog.registry_manifest()
    (domain_root / "kubernetes" / "resources" / "runbook.md").unlink()

    with pytest.raises(EcosystemRegistryInstallError, match="local verification failed"):
        install_ecosystem_domain_packages(manifest)


def test_ecosystem_registry_install_refuses_tampered_domain_manifest(
    tmp_path: Path,
) -> None:
    domain_root = tmp_path / "domains"
    write_domain_package(domain_root / "kubernetes")
    catalog = load_ecosystem_catalog(domain_package_root=domain_root)
    manifest = catalog.registry_manifest()
    write_json(
        domain_root / "kubernetes" / "manifest.json",
        {
            "apiVersion": "agent.nantian.dev/v1alpha1",
            "kind": "DomainPackage",
            "metadata": {
                "name": "kubernetes",
                "version": "1.0.0",
                "description": "Tampered Kubernetes domain package",
                "tags": ["kubernetes"],
            },
            "capabilities": ["inspect_workload"],
            "required_tools": ["kubernetes_api"],
            "compatibility": {
                "runtime_api": ">=0.1,<1",
                "domain_api": "agent.nantian.dev/v1alpha1",
            },
            "security": {
                "requires_confirmation": False,
                "side_effects": "none",
            },
        },
    )

    with pytest.raises(EcosystemRegistryInstallError, match="sha256 mismatch"):
        install_ecosystem_domain_packages(manifest)


def test_ecosystem_registry_install_refuses_tampered_dataset_manifest(
    tmp_path: Path,
) -> None:
    domain_root = tmp_path / "domains"
    dataset_root = tmp_path / "datasets"
    write_domain_package(domain_root / "kubernetes")
    write_evaluation_dataset(dataset_root / "kubernetes")
    catalog = load_ecosystem_catalog(
        domain_package_root=domain_root,
        evaluation_dataset_root=dataset_root,
    )
    manifest = catalog.registry_manifest()
    write_json(
        dataset_root / "kubernetes" / "dataset.json",
        {
            "apiVersion": "agent.nantian.dev/v1alpha1",
            "kind": "EvaluationDataset",
            "metadata": {
                "name": "kubernetes-remediation",
                "version": "1.0.0",
                "description": "Tampered Kubernetes remediation evaluation dataset",
            },
            "domains": [{"name": "kubernetes", "version": "1.0.0"}],
            "suites": [{"name": "healthy", "path": "suites/healthy.json"}],
        },
    )

    with pytest.raises(EcosystemRegistryInstallError, match="sha256 mismatch"):
        plan_ecosystem_install(manifest)


def test_ecosystem_registry_install_refuses_tampered_profile_config(
    tmp_path: Path,
) -> None:
    domain_root = tmp_path / "domains"
    profile_root = tmp_path / "profiles"
    profile_path = profile_root / "kubernetes.profile.json"
    write_domain_package(domain_root / "kubernetes")
    write_profile(profile_path)
    catalog = load_ecosystem_catalog(
        domain_package_root=domain_root,
        profile_root=profile_root,
    )
    manifest = catalog.registry_manifest()
    write_profile(profile_path)
    loaded = json.loads(profile_path.read_text(encoding="utf-8"))
    loaded["description"] = "Tampered Kubernetes operator profile"
    profile_path.write_text(json.dumps(loaded, indent=2), encoding="utf-8")

    with pytest.raises(EcosystemRegistryInstallError, match="sha256 mismatch"):
        plan_ecosystem_install(manifest)


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


def test_ecosystem_registry_install_refuses_domain_package_metadata_mismatch(
    tmp_path: Path,
) -> None:
    write_domain_package(tmp_path / "domains" / "kubernetes")
    manifest = EcosystemRegistryManifest(
        api_version="agent.nantian.dev/v1alpha1",
        kind="EcosystemRegistry",
        name="metadata-mismatch-registry",
        version="1.0.0",
        description="Metadata mismatch registry",
        domain_packages=(
            EcosystemDomainPackageRef(
                "kubernetes",
                "1.0.0",
                "Kubernetes",
                capability_names=("restart_workload",),
                entrypoint="different.domain:build_domain",
                resources=("resources/different.md",),
                manifest_path=str(tmp_path / "domains" / "kubernetes" / "manifest.json"),
            ),
        ),
    )

    with pytest.raises(
        EcosystemRegistryInstallError,
        match=r"metadata mismatch:.*entrypoint.*capability_names.*resources",
    ):
        install_ecosystem_domain_packages(manifest)


def test_ecosystem_registry_install_refuses_evaluation_dataset_metadata_mismatch(
    tmp_path: Path,
) -> None:
    domain_root = tmp_path / "domains"
    dataset_root = tmp_path / "datasets"
    write_domain_package(domain_root / "kubernetes")
    write_evaluation_dataset(dataset_root / "kubernetes")
    manifest = EcosystemRegistryManifest(
        api_version="agent.nantian.dev/v1alpha1",
        kind="EcosystemRegistry",
        name="dataset-metadata-mismatch-registry",
        version="1.0.0",
        description="Dataset metadata mismatch registry",
        domain_packages=(
            EcosystemDomainPackageRef(
                "kubernetes",
                "1.0.0",
                "Kubernetes",
                manifest_path=str(domain_root / "kubernetes" / "manifest.json"),
            ),
        ),
        evaluation_datasets=(
            EcosystemEvaluationDatasetRef(
                "kubernetes-remediation",
                "1.0.0",
                "Kubernetes remediation evaluation dataset",
                domains=(DomainIdentity("kubernetes", "1.0.0"),),
                suites=(
                    EcosystemEvaluationDatasetSuiteRef(
                        "healthy",
                        "suites/renamed.json",
                    ),
                ),
                manifest_path=str(dataset_root / "kubernetes" / "dataset.json"),
            ),
        ),
    )

    with pytest.raises(EcosystemRegistryInstallError, match=r"metadata mismatch:.*suites"):
        plan_ecosystem_install(manifest)


def test_ecosystem_registry_install_refuses_profile_metadata_mismatch(
    tmp_path: Path,
) -> None:
    domain_root = tmp_path / "domains"
    profile_path = tmp_path / "profiles" / "kubernetes.profile.json"
    write_domain_package(domain_root / "database", name="database")
    write_domain_package(domain_root / "kubernetes")
    write_profile(profile_path)
    manifest = EcosystemRegistryManifest(
        api_version="agent.nantian.dev/v1alpha1",
        kind="EcosystemRegistry",
        name="profile-metadata-mismatch-registry",
        version="1.0.0",
        description="Profile metadata mismatch registry",
        domain_packages=(
            EcosystemDomainPackageRef(
                "database",
                "1.0.0",
                "Database",
                manifest_path=str(domain_root / "database" / "manifest.json"),
            ),
            EcosystemDomainPackageRef(
                "kubernetes",
                "1.0.0",
                "Kubernetes",
                manifest_path=str(domain_root / "kubernetes" / "manifest.json"),
            ),
        ),
        profiles=(
            EcosystemProfileRef(
                "kubernetes-operator",
                "1.0.0",
                "Kubernetes operator profile",
                (DomainIdentity("database", "1.0.0"),),
                str(profile_path),
            ),
        ),
    )

    with pytest.raises(EcosystemRegistryInstallError, match=r"metadata mismatch:.*domains"):
        plan_ecosystem_install(manifest)


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
