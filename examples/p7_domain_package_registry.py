from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import DomainPackageRegistry
from universal_agent.core import JsonMapping, JsonValue


def package_manifest(name: str, version: str, tags: tuple[str, ...]) -> dict[str, JsonValue]:
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
        "ontology": ["Workload"],
        "capabilities": ["inspect_workload"],
        "tools": ["domain_backend_inspect"],
        "policies": ["read_only"],
        "procedures": ["diagnose_workload"],
        "knowledge": ["workload_health"],
        "evaluators": ["criteria"],
        "context_providers": ["domain_context"],
        "required_tools": ["domain_backend"],
        "compatibility": {
            "runtime_api": ">=0.1,<1",
            "domain_api": "agent.nantian.dev/v1alpha1",
        },
        "security": {"side_effects": "none"},
    }


def write_manifest(root: Path, payload: JsonMapping) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    with TemporaryDirectory() as directory:
        package_root = Path(directory)
        write_manifest(
            package_root / "kubernetes-domain",
            package_manifest("kubernetes", "1.0.0", ("ops", "kubernetes")),
        )
        write_manifest(
            package_root / "database-domain",
            package_manifest("database", "1.0.0", ("ops", "database")),
        )

        registry = DomainPackageRegistry()
        registry.discover(package_root)
        kubernetes = registry.get_by_name("kubernetes")

        print(f"packages={','.join(identity.name for identity in registry.identities())}")
        print(f"kubernetes_entrypoint={kubernetes.manifest.entrypoint}")
        print(
            "ops_packages="
            + ",".join(package.identity.name for package in registry.list(tag="ops"))
        )


if __name__ == "__main__":
    main()
