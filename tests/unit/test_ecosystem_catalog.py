from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from universal_agent import (
    EcosystemCatalog,
    EcosystemRegistryValidationError,
    decode_ecosystem_registry_manifest,
    encode_ecosystem_registry_manifest,
    load_ecosystem_catalog,
    load_ecosystem_registry_manifest,
    write_ecosystem_registry_manifest,
)
from universal_agent.core import JsonMapping


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
