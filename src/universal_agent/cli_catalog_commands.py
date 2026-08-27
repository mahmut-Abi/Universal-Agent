from __future__ import annotations

import argparse
from pathlib import Path
from typing import TextIO, cast

from universal_agent.agentd.representations import domain_package_body, profile_body
from universal_agent.cli_io import _parse_domain_identity, _write_json
from universal_agent.core import immutable_json
from universal_agent.domain import (
    DomainPackageCompatibility,
    DomainPackageRuntimeActivation,
    DomainPackageScaffoldResult,
    DomainPackageScaffoldSpec,
    DomainPackageVerificationReport,
    load_domain_package_runtime,
    scaffold_domain_package,
)
from universal_agent.profile import (
    ProfileCatalogVerificationReport,
    load_profile_catalog,
)
from universal_agent.service import RuntimeService


def _dispatch_profile(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    command = cast(str, args.profile_command)
    if command == "list":
        _write_json(out, {"profiles": [profile_body(item) for item in service.profiles()]})
        return
    if command == "show":
        profile = cast(str, args.profile)
        if not service.accepts_profile(profile):
            raise ValueError(f"unknown profile: {profile}")
        _write_json(out, profile_body(service.profile(profile)))
        return
    if command == "verify":
        catalog = load_profile_catalog(cast(str, args.profile_dir))
        _write_json(out, profile_catalog_verification_body(catalog.verify()))
        return
    raise ValueError(f"unknown profile command: {command}")

def _dispatch_domain_packages(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    command = cast(str, args.domain_packages_command)
    if command == "list":
        tag = cast(str | None, args.tag)
        _write_json(
            out,
            {
                "domain_packages": [
                    domain_package_body(item) for item in service.domain_packages(tag=tag)
                ]
            },
        )
        return
    if command == "show":
        _write_json(
            out,
            domain_package_body(
                service.domain_package(cast(str, args.name), cast(str | None, args.version))
            ),
        )
        return
    if command == "verify":
        _write_json(
            out,
            domain_package_verification_body(
                service.domain_package_verification(
                    verify_paths=cast(bool, args.local_paths),
                )
            ),
        )
        return
    if command == "load-runtime":
        activation = load_domain_package_runtime(
            Path(cast(str, args.path)),
            verify_paths=not cast(bool, args.skip_local_paths),
        )
        _write_json(out, domain_package_runtime_activation_body(activation))
        return
    if command == "scaffold":
        result = scaffold_domain_package(
            Path(cast(str, args.output)),
            _domain_package_scaffold_spec(args),
            overwrite=cast(bool, args.force),
        )
        _write_json(out, domain_package_scaffold_body(result))
        return
    raise ValueError(f"unknown domain package command: {command}")

def _domain_package_scaffold_spec(args: argparse.Namespace) -> DomainPackageScaffoldSpec:
    return DomainPackageScaffoldSpec(
        name=cast(str, args.name),
        version=cast(str, args.version),
        description=cast(str, args.description),
        api_version=cast(str, args.api_version),
        author=cast(str | None, args.author),
        entrypoint=cast(str | None, args.entrypoint),
        ontology=tuple(cast(list[str], args.ontology)),
        capabilities=tuple(cast(list[str], args.capability)),
        tools=tuple(cast(list[str], args.tool)),
        policies=tuple(cast(list[str], args.policy)),
        procedures=tuple(cast(list[str], args.procedure)),
        knowledge=tuple(cast(list[str], args.knowledge)),
        evaluators=tuple(cast(list[str], args.evaluator)),
        context_providers=tuple(cast(list[str], args.context_provider)),
        prompts=tuple(cast(list[str], args.prompt)),
        resources=tuple(cast(list[str], args.resource)),
        dependencies=tuple(
            _parse_domain_identity(item) for item in cast(list[str], args.dependency)
        ),
        required_tools=tuple(cast(list[str], args.required_tool)),
        compatibility=DomainPackageCompatibility(
            runtime_api=cast(str | None, args.runtime_api),
            domain_api=cast(str | None, args.domain_api),
        ),
        security=immutable_json(
            {
                "side_effects": cast(str, args.side_effects),
                "requires_confirmation": cast(bool, args.requires_confirmation),
            }
        ),
        tags=tuple(cast(list[str], args.tag)),
        runtime_stub=cast(bool, args.runtime_stub),
    )

def domain_package_scaffold_body(result: DomainPackageScaffoldResult) -> dict[str, object]:
    package = result.package
    return {
        "status": "updated" if result.overwritten else "created",
        "name": package.identity.name,
        "version": package.identity.version,
        "root_path": str(package.root_path),
        "manifest_path": str(package.manifest_path),
        "created_paths": [str(path) for path in result.created_paths],
        "written_paths": [str(path) for path in result.written_paths],
        "runtime_stub_paths": [str(path) for path in result.runtime_stub_paths],
    }

def domain_package_runtime_activation_body(
    activation: DomainPackageRuntimeActivation,
) -> dict[str, object]:
    package = activation.package
    active = activation.active_domain
    return {
        "status": "loaded",
        "metadata_verified": True,
        "package": {
            "name": package.identity.name,
            "version": package.identity.version,
            "entrypoint": package.manifest.entrypoint,
            "root_path": str(package.root_path),
            "manifest_path": str(package.manifest_path),
        },
        "active_domain": {
            "name": active.identity.name,
            "version": active.identity.version,
            "description": active.manifest.metadata.description,
            "capability_names": [capability.name for capability in active.capabilities],
            "tool_names": [tool.definition.name for tool in active.tools],
            "policy_names": [policy.name for policy in active.policies],
            "evaluator_names": [evaluator.name for evaluator in active.evaluators],
            "context_provider_names": [provider.name for provider in active.context_providers],
            "evidence_extractor_count": len(active.evidence_extractors),
            "world_updater_count": len(active.world_updaters),
            "task_expander_count": len(active.task_expanders),
            "recovery_rule_count": len(active.recovery_rules),
            "memory_count": len(active.memories),
        },
    }

def domain_package_verification_body(
    report: DomainPackageVerificationReport,
) -> dict[str, object]:
    return {
        "passed": report.passed,
        "failed_check_count": len(report.failed_checks),
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "message": check.message,
            }
            for check in report.checks
        ],
    }

def profile_catalog_verification_body(
    report: ProfileCatalogVerificationReport,
) -> dict[str, object]:
    return {
        "passed": report.passed,
        "failed_check_count": len(report.failed_checks),
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "message": check.message,
            }
            for check in report.checks
        ],
    }
