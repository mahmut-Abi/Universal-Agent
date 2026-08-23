from __future__ import annotations

from tempfile import TemporaryDirectory

from universal_agent import (
    EcosystemDomainPackageRef,
    EcosystemRegistryManifest,
    FileEcosystemRegistryStore,
)


def registry_manifest(name: str, version: str) -> EcosystemRegistryManifest:
    return EcosystemRegistryManifest(
        api_version="agent.nantian.dev/v1alpha1",
        kind="EcosystemRegistry",
        name=name,
        version=version,
        description=f"{name} registry {version}",
        domain_packages=(
            EcosystemDomainPackageRef(
                "kubernetes",
                version,
                "Kubernetes domain package",
                tags=("kubernetes", "ops"),
                capability_names=("inspect_workload",),
            ),
        ),
    )


def main() -> None:
    with TemporaryDirectory() as directory:
        store = FileEcosystemRegistryStore(directory)
        store.save(registry_manifest("ops-ecosystem", "1.0.0"))
        store.save(registry_manifest("ops-ecosystem", "2.0.0"))

        manifests = store.list_manifests()
        latest = store.index("ops-ecosystem", "2.0.0")

        print(f"registry_count={len(manifests)}")
        print(f"latest_version={latest.manifest.version}")
        print(f"package={latest.domain_package('kubernetes').name}")
        print(f"verified={latest.verify().passed}")


if __name__ == "__main__":
    main()
