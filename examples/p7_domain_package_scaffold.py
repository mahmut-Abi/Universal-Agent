from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    DomainPackageCompatibility,
    DomainPackageRegistry,
    DomainPackageScaffoldSpec,
    load_domain_package_runtime,
    scaffold_domain_package,
)
from universal_agent.core import DomainIdentity, immutable_json


def main() -> None:
    with TemporaryDirectory() as directory:
        package_root = Path(directory) / "ai-ops-domain"
        result = scaffold_domain_package(
            package_root,
            DomainPackageScaffoldSpec(
                name="ai-ops",
                version="1.0.0",
                description="AI operations domain package",
                author="Runtime Team",
                ontology=("Incident",),
                capabilities=("inspect_incident", "resolve_incident"),
                tools=("incident_api_get", "incident_api_resolve"),
                policies=("incident_safety",),
                procedures=("diagnose_incident",),
                knowledge=("incident lifecycle",),
                evaluators=("incident_status",),
                context_providers=("incident_context",),
                resources=("resources/runbook.md", "schemas/incident.json"),
                dependencies=(DomainIdentity("observability", "1.0.0"),),
                required_tools=("incident_api",),
                compatibility=DomainPackageCompatibility(
                    runtime_api=">=0.1,<1",
                    domain_api="agent.nantian.dev/v1alpha1",
                ),
                security=immutable_json({"side_effects": "reversible"}),
                tags=("ops", "ai"),
                runtime_stub=True,
            ),
        )
        package = DomainPackageRegistry().install(package_root)
        activation = load_domain_package_runtime(package)

        print(f"created={result.package.identity.name}@{result.package.identity.version}")
        print(f"manifest={result.package.manifest_path}")
        print(f"registered={package.identity.name}@{package.identity.version}")
        loaded = activation.active_domain.identity
        print(f"loaded={loaded.name}@{loaded.version}")
        print(f"directories={len(result.created_paths) - 1}")
        print(f"resources={','.join(package.manifest.resources)}")


if __name__ == "__main__":
    main()
