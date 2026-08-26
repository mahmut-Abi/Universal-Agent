from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from universal_agent.core import DomainIdentity, JsonMapping, JsonValue
from universal_agent.domain import (
    AmbiguousDomainPackageError,
    DomainPackageCompatibility,
    DomainPackageNotFoundError,
    DomainPackageRegistry,
    DomainPackageRuntimeLoadError,
    DomainPackageScaffoldSpec,
    DomainPackageValidationError,
    build_domain_package_manifest,
    decode_domain_package_manifest,
    encode_domain_package_manifest,
    load_domain_package_runtime,
    scaffold_domain_package,
    verify_domain_package,
)


def package_payload(
    name: str = "kubernetes",
    version: str = "1.0.0",
    *,
    tags: tuple[str, ...] = ("ops",),
) -> dict[str, JsonValue]:
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
        "ontology": ["Deployment"],
        "capabilities": ["inspect_workload"],
        "tools": ["kubernetes_api_get_deployment"],
        "policies": ["read_only"],
        "procedures": ["diagnose_unhealthy_workload"],
        "knowledge": ["deployment_rollout"],
        "evaluators": ["workload_health"],
        "context_providers": ["cluster_context"],
        "prompts": ["diagnostic_notes"],
        "resources": ["resources/runbook.md"],
        "dependencies": [{"name": "observability", "version": "2.0.0"}],
        "required_tools": ["kubernetes_api"],
        "compatibility": {
            "runtime_api": ">=0.1,<1",
            "domain_api": "agent.nantian.dev/v1alpha1",
        },
        "security": {
            "requires_confirmation": False,
            "side_effects": "none",
        },
    }


def write_manifest(root: Path, payload: JsonMapping) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    resources = payload.get("resources", ())
    if isinstance(resources, list | tuple):
        for resource in resources:
            if not isinstance(resource, str):
                continue
            resource_path = root / resource
            if resource_path.suffix:
                resource_path.parent.mkdir(parents=True, exist_ok=True)
                resource_path.touch()
            else:
                resource_path.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return manifest_path


def runtime_package_payload(
    *,
    name: str = "widget",
    version: str = "1.0.0",
    module_name: str = "widget_domain",
) -> dict[str, JsonValue]:
    payload = package_payload(name, version, tags=("sdk",))
    payload["entrypoint"] = f"{module_name}:build_domain"
    payload["ontology"] = ["Widget"]
    payload["capabilities"] = ["inspect_widget"]
    payload["tools"] = ["inspect_widget"]
    payload["policies"] = []
    payload["procedures"] = []
    payload["knowledge"] = []
    payload["evaluators"] = ["criteria"]
    payload["context_providers"] = []
    payload["prompts"] = []
    payload["dependencies"] = []
    payload["required_tools"] = []
    return payload


def write_runtime_module(
    root: Path,
    *,
    module_name: str = "widget_domain",
    domain_name: str = "widget",
    version: str = "1.0.0",
    capability_name: str = "inspect_widget",
) -> None:
    (root / f"{module_name}.py").write_text(
        f"""
from __future__ import annotations

from universal_agent import BaseDomainRuntime, immutable_json
from universal_agent.core import (
    CapabilityCategory,
    CapabilityDefinition,
    DomainManifest,
    DomainMetadata,
    JsonMapping,
    ToolDefinition,
)
from universal_agent.evaluation import CriteriaEvaluator, Evaluator
from universal_agent.tools import Tool


class InspectWidgetTool:
    definition = ToolDefinition("inspect_widget", "Inspect widget state", ("{capability_name}",))

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({{"healthy": True}})


class WidgetDomain(BaseDomainRuntime):
    manifest = DomainManifest(
        "agent.nantian.dev/v1alpha1",
        "Domain",
        DomainMetadata("{domain_name}", "{version}", "Widget domain"),
        ("Widget",),
        ("{capability_name}",),
        ("criteria",),
    )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                "{capability_name}",
                "Inspect widget health",
                CapabilityCategory.OBSERVATION,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (InspectWidgetTool(),)

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (CriteriaEvaluator(),)


def build_domain() -> WidgetDomain:
    return WidgetDomain()
""".lstrip(),
        encoding="utf-8",
    )


def test_decode_domain_package_manifest_accepts_structured_ecosystem_metadata() -> None:
    manifest = decode_domain_package_manifest(package_payload())

    assert manifest.api_version == "agent.nantian.dev/v1alpha1"
    assert manifest.kind == "DomainPackage"
    assert manifest.identity == DomainIdentity("kubernetes", "1.0.0")
    assert manifest.author == "Runtime Team"
    assert manifest.entrypoint == "kubernetes.domain:build_domain"
    assert manifest.capabilities == ("inspect_workload",)
    assert manifest.procedures == ("diagnose_unhealthy_workload",)
    assert manifest.dependencies == (DomainIdentity("observability", "2.0.0"),)
    assert manifest.required_tools == ("kubernetes_api",)
    assert manifest.compatibility.runtime_api == ">=0.1,<1"
    assert manifest.compatibility.domain_api == "agent.nantian.dev/v1alpha1"
    assert manifest.resources == ("resources/runbook.md",)
    assert manifest.security["side_effects"] == "none"
    assert manifest.tags == ("ops",)


def test_decode_domain_package_manifest_accepts_legacy_snake_case_api_version() -> None:
    payload = package_payload()
    payload["api_version"] = payload.pop("apiVersion")

    manifest = decode_domain_package_manifest(payload)

    assert manifest.api_version == "agent.nantian.dev/v1alpha1"


def test_decode_domain_package_manifest_rejects_conflicting_api_version_keys() -> None:
    payload = package_payload()
    payload["api_version"] = "agent.nantian.dev/v2"

    with pytest.raises(DomainPackageValidationError, match="apiVersion and api_version"):
        decode_domain_package_manifest(payload)


def test_decode_domain_package_manifest_rejects_resources_outside_package_root() -> None:
    payload = package_payload()
    payload["resources"] = ["../outside.md"]

    with pytest.raises(DomainPackageValidationError, match="must stay inside package root"):
        decode_domain_package_manifest(payload)


def test_domain_package_registry_installs_validated_package(tmp_path: Path) -> None:
    root = tmp_path / "kubernetes-domain"
    manifest_path = write_manifest(root, package_payload())

    registry = DomainPackageRegistry()
    package = registry.install(root)

    assert package.root_path == root
    assert package.manifest_path == manifest_path
    assert registry.identities() == (DomainIdentity("kubernetes", "1.0.0"),)
    assert registry.get_by_name("kubernetes") is package
    assert registry.list(tag="ops") == (package,)
    assert registry.list(tag="database") == ()


def test_domain_package_registry_discovers_manifests_in_stable_order(tmp_path: Path) -> None:
    write_manifest(tmp_path / "beta-domain", package_payload("beta", tags=("database",)))
    write_manifest(tmp_path / "alpha-domain", package_payload("alpha", tags=("ops",)))

    registry = DomainPackageRegistry()
    packages = registry.discover(tmp_path)

    assert [package.identity.name for package in packages] == ["alpha", "beta"]
    assert registry.identities() == (
        DomainIdentity("alpha", "1.0.0"),
        DomainIdentity("beta", "1.0.0"),
    )


def test_domain_package_registry_reports_duplicate_missing_and_ambiguous_packages(
    tmp_path: Path,
) -> None:
    registry = DomainPackageRegistry()
    package = registry.install(write_manifest(tmp_path / "alpha-v1", package_payload("alpha")))

    with pytest.raises(DomainPackageValidationError, match="already registered"):
        registry.register(package)

    with pytest.raises(DomainPackageNotFoundError, match="beta"):
        registry.get_by_name("beta")

    registry.install(write_manifest(tmp_path / "alpha-v2", package_payload("alpha", "2.0.0")))

    with pytest.raises(AmbiguousDomainPackageError, match="multiple registered versions"):
        registry.get_by_name("alpha")


def test_domain_package_registry_refuses_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(DomainPackageNotFoundError, match="manifest not found"):
        DomainPackageRegistry().install(tmp_path / "missing-domain")


def test_domain_package_verification_checks_local_package_paths_and_manifest_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "kubernetes-domain"
    write_manifest(root, package_payload())
    package = DomainPackageRegistry().install(root)

    passing = verify_domain_package(package)
    write_manifest(root, package_payload("database"))
    failing = verify_domain_package(package)
    failed_checks = {check.name: check.message for check in failing.failed_checks}

    assert passing.passed is True
    assert {check.name for check in passing.checks} == {
        "package_root_exists",
        "package_manifest_exists",
        "package_manifest_matches_identity",
        "package_resources_exist",
    }
    assert failing.passed is False
    assert "package_manifest_matches_identity" in failed_checks
    assert "identity mismatch" in failed_checks["package_manifest_matches_identity"]


def test_domain_package_registry_verification_checks_dependency_closure(
    tmp_path: Path,
) -> None:
    registry = DomainPackageRegistry()
    registry.install(write_manifest(tmp_path / "kubernetes-domain", package_payload()))
    missing = registry.verify()

    observability = package_payload("observability", "2.0.0", tags=("observability",))
    observability["dependencies"] = []
    registry.install(write_manifest(tmp_path / "observability-domain", observability))
    passing = registry.verify()

    assert missing.passed is False
    assert missing.failed_checks[0].name == "package_dependencies_registered"
    assert "observability@2.0.0" in missing.failed_checks[0].message
    assert passing.passed is True


def test_domain_package_registry_verification_reports_dependency_cycles(
    tmp_path: Path,
) -> None:
    alpha = package_payload("alpha")
    alpha["dependencies"] = [{"name": "beta", "version": "1.0.0"}]
    beta = package_payload("beta")
    beta["dependencies"] = [{"name": "alpha", "version": "1.0.0"}]
    registry = DomainPackageRegistry()
    registry.install(write_manifest(tmp_path / "alpha-domain", alpha))
    registry.install(write_manifest(tmp_path / "beta-domain", beta))

    report = registry.verify()
    failed = {check.name: check.message for check in report.failed_checks}

    assert report.passed is False
    assert "package_dependencies_acyclic" in failed
    assert "alpha@1.0.0" in failed["package_dependencies_acyclic"]
    assert "beta@1.0.0" in failed["package_dependencies_acyclic"]


def test_domain_package_registry_can_verify_local_package_path_integrity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "kubernetes-domain"
    write_manifest(root, package_payload())
    registry = DomainPackageRegistry()
    registry.install(root)

    metadata_only = registry.verify()
    write_manifest(root, package_payload("database"))
    local_paths = registry.verify(verify_paths=True)
    failed = {check.name: check.message for check in local_paths.failed_checks}

    assert metadata_only.passed is False
    assert metadata_only.failed_checks[0].name == "package_dependencies_registered"
    assert "package_manifest_matches_identity:kubernetes@1.0.0" in failed
    assert "identity mismatch" in failed["package_manifest_matches_identity:kubernetes@1.0.0"]


def test_build_domain_package_manifest_encodes_sdk_spec_with_default_entrypoint() -> None:
    manifest = build_domain_package_manifest(
        DomainPackageScaffoldSpec(
            name="ai-ops",
            description="AI operations domain package",
            author="Runtime Team",
            ontology=("Incident",),
            capabilities=("inspect_incident",),
            tools=("incident_api_get",),
            policies=("read_only",),
            evaluators=("incident_status",),
            context_providers=("incident_context",),
            resources=("resources/runbook.md", "assets/schema.json"),
            dependencies=(DomainIdentity("observability", "1.0.0"),),
            required_tools=("incident_api",),
            compatibility=DomainPackageCompatibility(
                runtime_api=">=0.1,<1",
                domain_api="agent.nantian.dev/v1alpha1",
            ),
            tags=("ops", "ai"),
        )
    )

    payload = encode_domain_package_manifest(manifest)
    decoded = decode_domain_package_manifest(payload)

    assert manifest.entrypoint == "ai_ops.domain:build_domain"
    assert decoded.identity == DomainIdentity("ai-ops", "0.1.0")
    assert decoded.capabilities == ("inspect_incident",)
    assert decoded.resources == ("resources/runbook.md", "assets/schema.json")
    assert decoded.dependencies == (DomainIdentity("observability", "1.0.0"),)
    assert decoded.tags == ("ops", "ai")


def test_scaffold_domain_package_creates_registry_loadable_package(tmp_path: Path) -> None:
    package_root = tmp_path / "ai-ops-domain"

    result = scaffold_domain_package(
        package_root,
        DomainPackageScaffoldSpec(
            name="ai-ops",
            version="1.0.0",
            description="AI operations domain package",
            capabilities=("inspect_incident", "resolve_incident"),
            tools=("incident_api_get", "incident_api_resolve"),
            policies=("incident_safety",),
            resources=("resources/runbook.md", "templates/remediation/"),
            required_tools=("incident_api",),
            security={"side_effects": "reversible"},
            tags=("ops",),
        ),
    )
    installed = DomainPackageRegistry().install(package_root)
    payload = json.loads((package_root / "manifest.json").read_text(encoding="utf-8"))

    assert result.package.identity == DomainIdentity("ai-ops", "1.0.0")
    assert installed.manifest.capabilities == ("inspect_incident", "resolve_incident")
    assert installed.manifest.security["side_effects"] == "reversible"
    assert payload["entrypoint"] == "ai_ops.domain:build_domain"
    assert payload["resources"] == ["resources/runbook.md", "templates/remediation/"]
    assert (package_root / "ontology").is_dir()
    assert (package_root / "context_providers").is_dir()
    assert (package_root / "resources").is_dir()
    assert (package_root / "tests").is_dir()
    assert (package_root / "resources" / "runbook.md").is_file()
    assert (package_root / "templates" / "remediation").is_dir()
    assert result.written_paths == (package_root / "manifest.json",)


def test_domain_package_verification_reports_missing_resources(tmp_path: Path) -> None:
    root = tmp_path / "kubernetes-domain"
    write_manifest(root, package_payload())
    (root / "resources" / "runbook.md").unlink()
    package = DomainPackageRegistry().install(root)

    report = verify_domain_package(package)
    failed = {check.name: check.message for check in report.failed_checks}

    assert report.passed is False
    assert "package_resources_exist" in failed
    assert "resources/runbook.md" in failed["package_resources_exist"]


def test_scaffold_domain_package_rejects_resources_outside_package_root(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "unsafe-domain"

    with pytest.raises(DomainPackageValidationError, match="must stay inside package root"):
        scaffold_domain_package(
            package_root,
            DomainPackageScaffoldSpec(
                name="unsafe",
                description="Unsafe domain package",
                resources=("../outside.md",),
            ),
        )

    with pytest.raises(DomainPackageValidationError, match="must stay inside package root"):
        scaffold_domain_package(
            package_root,
            DomainPackageScaffoldSpec(
                name="unsafe",
                description="Unsafe domain package",
                resources=("/tmp/outside.md",),
            ),
        )


def test_scaffold_domain_package_requires_force_to_overwrite_manifest(tmp_path: Path) -> None:
    package_root = tmp_path / "database-domain"
    scaffold_domain_package(
        package_root,
        DomainPackageScaffoldSpec(name="database", description="Database domain package"),
    )

    with pytest.raises(DomainPackageValidationError, match="already exists"):
        scaffold_domain_package(
            package_root,
            DomainPackageScaffoldSpec(
                name="database",
                version="2.0.0",
                description="Updated database domain package",
            ),
        )

    result = scaffold_domain_package(
        package_root,
        DomainPackageScaffoldSpec(
            name="database",
            version="2.0.0",
            description="Updated database domain package",
        ),
        overwrite=True,
    )

    assert result.overwritten is True
    assert result.package.identity == DomainIdentity("database", "2.0.0")


def test_domain_package_runtime_loader_imports_explicit_entrypoint(tmp_path: Path) -> None:
    root = tmp_path / "widget-domain"
    module_name = "widget_domain_runtime_loader"
    write_manifest(root, runtime_package_payload(module_name=module_name))
    write_runtime_module(root, module_name=module_name)
    package = DomainPackageRegistry().install(root)
    sys_path_before = tuple(sys.path)

    activation = load_domain_package_runtime(package)

    assert activation.package is package
    assert activation.active_domain.identity == DomainIdentity("widget", "1.0.0")
    assert activation.active_domain.manifest.capability_names == ("inspect_widget",)
    assert activation.active_domain.tools[0].definition.name == "inspect_widget"
    assert module_name in sys.modules
    assert tuple(sys.path) == sys_path_before


def test_domain_package_runtime_loader_requires_explicit_entrypoint(tmp_path: Path) -> None:
    root = tmp_path / "metadata-only-domain"
    payload = runtime_package_payload()
    payload["entrypoint"] = None
    write_manifest(root, payload)
    package = DomainPackageRegistry().install(root)

    with pytest.raises(DomainPackageRuntimeLoadError, match="has no entrypoint"):
        load_domain_package_runtime(package)


def test_domain_package_runtime_loader_rejects_identity_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "widget-domain"
    module_name = "widget_domain_identity_mismatch"
    write_manifest(root, runtime_package_payload(module_name=module_name))
    write_runtime_module(root, module_name=module_name, domain_name="other-widget")
    package = DomainPackageRegistry().install(root)

    with pytest.raises(DomainPackageRuntimeLoadError, match="identity mismatch"):
        load_domain_package_runtime(package)


def test_domain_package_runtime_loader_rejects_declared_metadata_mismatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "widget-domain"
    module_name = "widget_domain_metadata_mismatch"
    write_manifest(root, runtime_package_payload(module_name=module_name))
    write_runtime_module(root, module_name=module_name, capability_name="observe_widget")
    package = DomainPackageRegistry().install(root)

    with pytest.raises(DomainPackageRuntimeLoadError, match="capabilities mismatch"):
        load_domain_package_runtime(package)
