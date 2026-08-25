from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    encode_ecosystem_registry_manifest,
    load_ecosystem_catalog,
    load_ecosystem_registry_manifest,
    write_ecosystem_registry_manifest,
)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_domain_package(root: Path) -> None:
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
                "name": "kubernetes",
                "version": "0.2.0",
                "description": "Kubernetes domain package",
                "tags": ["kubernetes"],
            },
            "entrypoint": "kubernetes.domain:build_domain",
            "capabilities": ["inspect_workload", "scale_workload"],
            "resources": ["resources/runbook.md", "schemas/workload.json"],
            "required_tools": ["kubernetes_api"],
            "compatibility": {
                "runtime_api": ">=0.1,<1",
                "domain_api": "agent.nantian.dev/v1alpha1",
            },
            "security": {"side_effects": "reversible"},
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
            "domains": [{"name": "kubernetes", "version": "0.2.0"}],
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
            "domain": {"name": "kubernetes", "version": "0.2.0"},
            "runtime": {"domain": {"name": "kubernetes", "version": "0.2.0"}},
        },
    )


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        write_domain_package(root / "domains" / "kubernetes")
        write_dataset(root / "datasets" / "kubernetes")
        write_profile(root / "profiles" / "kubernetes.profile.json")

        catalog = load_ecosystem_catalog(
            domain_package_root=root / "domains",
            evaluation_dataset_root=root / "datasets",
            profile_root=root / "profiles",
        )
        manifest = catalog.registry_manifest(
            name="ops-ecosystem",
            version="1.0.0",
            description="Operations ecosystem registry",
        )
        output = root / "ecosystem-registry.json"
        write_ecosystem_registry_manifest(output, manifest)
        loaded = load_ecosystem_registry_manifest(output)
        encoded = encode_ecosystem_registry_manifest(loaded)

        print(f"kind={loaded.kind}")
        print(f"name={loaded.name}")
        print(f"total_items={loaded.summary.total_items}")
        print(f"domain_packages={encoded['summary']['domain_package_count']}")
        print(f"package_entrypoint={loaded.domain_packages[0].entrypoint}")
        print(f"package_resources={len(loaded.domain_packages[0].resources)}")
        print(f"package_sha256={loaded.domain_packages[0].manifest_sha256[:12]}")
        print(f"profile_sha256={loaded.profiles[0].config_sha256[:12]}")
        print(f"path={output}")


if __name__ == "__main__":
    main()
