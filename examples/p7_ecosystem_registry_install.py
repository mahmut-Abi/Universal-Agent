from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    FileEcosystemRegistryStore,
    install_ecosystem,
    load_ecosystem_catalog,
    plan_ecosystem_install,
)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_domain_package(root: Path, name: str) -> None:
    write_json(
        root / "manifest.json",
        {
            "apiVersion": "agent.nantian.dev/v1alpha1",
            "kind": "DomainPackage",
            "metadata": {
                "name": name,
                "version": "1.0.0",
                "description": f"{name} domain package",
                "tags": ["ops"],
            },
            "entrypoint": f"{name}.domain:build_domain",
            "capabilities": ["inspect_workload"],
            "required_tools": [f"{name}_api"],
            "compatibility": {
                "runtime_api": ">=0.1,<1",
                "domain_api": "agent.nantian.dev/v1alpha1",
            },
            "security": {"side_effects": "none"},
        },
    )


def write_dataset(root: Path) -> None:
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


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        write_domain_package(root / "domains" / "kubernetes", "kubernetes")
        write_dataset(root / "datasets" / "kubernetes")
        write_profile(root / "profiles" / "kubernetes.profile.json")

        catalog = load_ecosystem_catalog(
            domain_package_root=root / "domains",
            evaluation_dataset_root=root / "datasets",
            profile_root=root / "profiles",
        )
        registry_store = FileEcosystemRegistryStore(root / "registries")
        registry_store.save(
            catalog.registry_manifest(
                name="ops-ecosystem",
                version="1.0.0",
                description="Operations ecosystem registry",
            )
        )
        index = registry_store.index("ops-ecosystem", "1.0.0")
        plan = plan_ecosystem_install(index)
        result = install_ecosystem(index)

        print(f"planned_packages={len(plan.domain_packages.candidates)}")
        print(f"planned_datasets={len(plan.evaluation_datasets)}")
        print(f"planned_profiles={len(plan.profiles)}")
        print(f"installed_packages={len(result.installed_domain_packages)}")
        print(f"installed_datasets={len(result.installed_evaluation_datasets)}")
        print(f"installed_profiles={len(result.installed_profiles)}")


if __name__ == "__main__":
    main()
