from __future__ import annotations

import json
from pathlib import Path

from universal_agent import EcosystemCatalog, load_ecosystem_catalog


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


def test_ecosystem_catalog_can_be_empty_or_partial(tmp_path: Path) -> None:
    profile_root = tmp_path / "profiles"
    write_profile(profile_root / "profile.json")

    empty = EcosystemCatalog.discover()
    partial = EcosystemCatalog.discover(profile_root=profile_root)

    assert empty.summary.total_items == 0
    assert partial.summary.profile_count == 1
    assert partial.domain_packages == ()
    assert partial.evaluation_datasets == ()
