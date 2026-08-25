from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    EcosystemRegistryInstallError,
    EcosystemRegistryTrustPolicy,
    load_ecosystem_catalog,
    plan_ecosystem_install,
)


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
                "version": "0.2.0",
                "description": "Kubernetes domain package",
                "tags": ["kubernetes"],
            },
            "capabilities": ["inspect_workload", "scale_workload"],
            "required_tools": ["kubernetes_api"],
            "compatibility": {
                "runtime_api": ">=0.1,<1",
                "domain_api": "agent.nantian.dev/v1alpha1",
            },
            "security": {"side_effects": "reversible"},
        },
    )


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        write_domain_package(root / "domains" / "kubernetes")

        catalog = load_ecosystem_catalog(domain_package_root=root / "domains")
        unsigned = catalog.registry_manifest(
            name="ops-ecosystem",
            version="1.0.0",
            description="Operations ecosystem registry",
        )
        signed = replace(
            unsigned,
            metadata={
                "signature": {
                    "algorithm": "ed25519",
                    "value": "local-test-signature",
                }
            },
        )

        try:
            plan_ecosystem_install(signed)
        except EcosystemRegistryInstallError as exc:
            print(f"default_policy=rejected:{exc.__class__.__name__}")

        allowed = plan_ecosystem_install(
            signed,
            trust_policy=EcosystemRegistryTrustPolicy(allow_unverified_signatures=True),
        )
        signed_only_policy = EcosystemRegistryTrustPolicy(allow_unsigned=False)

        print(f"allowed_packages={allowed.domain_packages.identities[0].name}")
        try:
            plan_ecosystem_install(unsigned, trust_policy=signed_only_policy)
        except EcosystemRegistryInstallError as exc:
            print(f"signed_only_policy=rejected:{exc.__class__.__name__}")


if __name__ == "__main__":
    main()
