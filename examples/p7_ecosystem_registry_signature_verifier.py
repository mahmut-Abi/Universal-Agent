from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    EcosystemRegistryInstallError,
    EcosystemRegistryManifest,
    EcosystemRegistrySignatureVerification,
    load_ecosystem_catalog,
    plan_ecosystem_install,
)


class LocalSignatureVerifier:
    """Example verifier seam; real deployments should use cryptographic verification."""

    def __init__(self, expected_value: str) -> None:
        self._expected_value = expected_value

    def verify_registry(
        self,
        manifest: EcosystemRegistryManifest,
    ) -> EcosystemRegistrySignatureVerification:
        signature = manifest.metadata.get("signature")
        value = ""
        signer = None
        if isinstance(signature, dict):
            candidate = signature.get("value")
            signed_by = signature.get("signed_by")
            if isinstance(candidate, str):
                value = candidate
            if isinstance(signed_by, str):
                signer = signed_by
        passed = value == self._expected_value
        reason = "signature metadata matched expected value" if passed else "signature mismatch"
        return EcosystemRegistrySignatureVerification(
            passed=passed,
            verifier="local-example-verifier",
            reason=reason,
            signer=signer,
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

        manifest = load_ecosystem_catalog(domain_package_root=root / "domains").registry_manifest(
            name="ops-ecosystem",
            version="1.0.0",
            description="Operations ecosystem registry",
        )
        signed = replace(
            manifest,
            metadata={
                "signature": {
                    "algorithm": "local-example",
                    "value": "expected-signature",
                    "signed_by": "platform-team",
                }
            },
        )

        plan = plan_ecosystem_install(
            signed,
            signature_verifier=LocalSignatureVerifier("expected-signature"),
        )
        print(f"verified_packages={plan.domain_packages.identities[0].name}")

        try:
            plan_ecosystem_install(
                signed,
                signature_verifier=LocalSignatureVerifier("different-signature"),
            )
        except EcosystemRegistryInstallError as exc:
            print(f"rejected={exc.__class__.__name__}")


if __name__ == "__main__":
    main()
