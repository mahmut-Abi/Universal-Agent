from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    FileEcosystemRegistryStore,
    install_ecosystem_domain_packages,
    load_ecosystem_catalog,
    plan_ecosystem_domain_package_install,
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


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        write_domain_package(root / "domains" / "kubernetes", "kubernetes")
        write_domain_package(root / "domains" / "observability", "observability")

        catalog = load_ecosystem_catalog(domain_package_root=root / "domains")
        registry_store = FileEcosystemRegistryStore(root / "registries")
        registry_store.save(
            catalog.registry_manifest(
                name="ops-ecosystem",
                version="1.0.0",
                description="Operations ecosystem registry",
            )
        )
        index = registry_store.index("ops-ecosystem", "1.0.0")
        plan = plan_ecosystem_domain_package_install(index)
        result = install_ecosystem_domain_packages(index)

        print(f"planned={len(plan.candidates)}")
        print(
            "installed=" + ",".join(package.identity.name for package in result.installed_packages)
        )
        print(f"registry_count={len(result.registry.identities())}")


if __name__ == "__main__":
    main()
