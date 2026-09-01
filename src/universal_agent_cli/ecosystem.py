from __future__ import annotations

import argparse
from typing import TextIO, cast

from universal_agent.domain import DomainPackage
from universal_agent.ecosystem import (
    EcosystemCatalog,
    EcosystemCatalogVerificationReport,
    EcosystemInstallPlan,
    EcosystemInstallResult,
    EcosystemRegistryIndex,
    EcosystemRegistryManifest,
    EcosystemRegistryTrustPolicy,
    FileEcosystemRegistryStore,
    encode_ecosystem_registry_manifest,
    install_ecosystem,
    load_ecosystem_catalog,
    load_ecosystem_registry_index,
    load_ecosystem_registry_manifest,
    plan_ecosystem_install,
    write_ecosystem_registry_manifest,
)
from universal_agent.profile import ProfileCatalogEntry
from universal_agent_cli.evaluation import _evaluation_dataset_body
from universal_agent_cli.io import _write_json


def _dispatch_ecosystem(args: argparse.Namespace, out: TextIO) -> None:
    command = cast(str, args.ecosystem_command)
    if command == "catalog":
        catalog = _load_ecosystem_catalog_from_args(args)
        _write_json(out, _ecosystem_catalog_body(catalog))
        return
    if command == "verify":
        catalog = _load_ecosystem_catalog_from_args(args)
        _write_json(out, _ecosystem_verification_body(catalog))
        return
    if command == "export":
        catalog = _load_ecosystem_catalog_from_args(args)
        manifest = catalog.registry_manifest(
            name=cast(str, args.name),
            version=cast(str, args.version),
            description=cast(str, args.description),
        )
        output = cast(str | None, args.output)
        if output is None:
            _write_json(out, encode_ecosystem_registry_manifest(manifest))
            return
        write_result = write_ecosystem_registry_manifest(
            output,
            manifest,
            overwrite=cast(bool, args.force),
        )
        _write_json(
            out,
            {
                "status": "updated" if write_result.overwritten else "created",
                "path": str(write_result.path),
                "manifest": encode_ecosystem_registry_manifest(write_result.manifest),
            },
        )
        return
    if command == "registry":
        index = load_ecosystem_registry_index(cast(str, args.manifest))
        if cast(bool, args.verify):
            _write_json(out, _ecosystem_verification_report_body(index.verify()))
            return
        _write_json(out, encode_ecosystem_registry_manifest(index.manifest))
        return
    if command == "install":
        index = load_ecosystem_registry_index(cast(str, args.manifest))
        base_path = cast(str | None, args.base_path)
        verify = not cast(bool, args.no_verify)
        trust_policy = EcosystemRegistryTrustPolicy(
            allow_unverified_signatures=cast(bool, args.allow_unverified_signatures)
        )
        if cast(bool, args.plan_only):
            plan = plan_ecosystem_install(
                index,
                base_path=base_path,
                verify=verify,
                trust_policy=trust_policy,
            )
            _write_json(out, _ecosystem_install_plan_body(plan))
            return
        install_result = install_ecosystem(
            index,
            base_path=base_path,
            verify=verify,
            trust_policy=trust_policy,
        )
        _write_json(out, _ecosystem_install_result_body(install_result))
        return
    if command == "store":
        _dispatch_ecosystem_store(args, out)
        return
    raise ValueError(f"unknown ecosystem command: {command}")


def _dispatch_ecosystem_store(args: argparse.Namespace, out: TextIO) -> None:
    store = FileEcosystemRegistryStore(cast(str, args.store_dir))
    command = cast(str, args.ecosystem_store_command)
    if command == "save":
        manifest = load_ecosystem_registry_manifest(cast(str, args.manifest))
        write_result = store.save(manifest, overwrite=cast(bool, args.force))
        _write_json(
            out,
            {
                "status": "updated" if write_result.overwritten else "created",
                "path": str(write_result.path),
                "manifest": _ecosystem_registry_summary_body(write_result.manifest),
            },
        )
        return
    if command == "list":
        manifests = store.list_manifests()
        _write_json(
            out,
            {
                "registry_count": len(manifests),
                "registries": [_ecosystem_registry_summary_body(item) for item in manifests],
            },
        )
        return
    if command == "show":
        manifest = store.load(cast(str, args.name), cast(str, args.version))
        if cast(bool, args.verify):
            _write_json(
                out, _ecosystem_verification_report_body(EcosystemRegistryIndex(manifest).verify())
            )
            return
        _write_json(out, encode_ecosystem_registry_manifest(manifest))
        return
    raise ValueError(f"unknown ecosystem store command: {command}")


def _load_ecosystem_catalog_from_args(args: argparse.Namespace) -> EcosystemCatalog:
    return load_ecosystem_catalog(
        domain_package_root=cast(str | None, args.domain_package_dir),
        evaluation_dataset_root=cast(str | None, args.dataset_dir),
        profile_root=cast(str | None, args.profile_dir),
    )


def _ecosystem_catalog_body(catalog: EcosystemCatalog) -> dict[str, object]:
    summary = catalog.summary
    return {
        "summary": {
            "domain_package_count": summary.domain_package_count,
            "evaluation_dataset_count": summary.evaluation_dataset_count,
            "profile_count": summary.profile_count,
            "total_items": summary.total_items,
        },
        "domain_packages": [
            _ecosystem_domain_package_body(package) for package in catalog.domain_packages
        ],
        "evaluation_datasets": [
            _evaluation_dataset_body(dataset) for dataset in catalog.evaluation_datasets
        ],
        "profiles": [_ecosystem_profile_body(entry) for entry in catalog.profiles],
    }


def _ecosystem_verification_body(catalog: EcosystemCatalog) -> dict[str, object]:
    return _ecosystem_verification_report_body(catalog.verify())


def _ecosystem_verification_report_body(
    report: EcosystemCatalogVerificationReport,
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


def _ecosystem_registry_summary_body(manifest: EcosystemRegistryManifest) -> dict[str, object]:
    return {
        "name": manifest.name,
        "version": manifest.version,
        "description": manifest.description,
        "summary": {
            "domain_package_count": manifest.summary.domain_package_count,
            "evaluation_dataset_count": manifest.summary.evaluation_dataset_count,
            "profile_count": manifest.summary.profile_count,
            "total_items": manifest.summary.total_items,
        },
    }


def _ecosystem_install_plan_body(plan: EcosystemInstallPlan) -> dict[str, object]:
    return {
        "status": "planned",
        "domain_package_count": len(plan.domain_packages.candidates),
        "evaluation_dataset_count": len(plan.evaluation_datasets),
        "profile_count": len(plan.profiles),
        "domain_packages": [
            _ecosystem_domain_package_body(candidate.package)
            for candidate in plan.domain_packages.candidates
        ],
        "evaluation_datasets": [
            _evaluation_dataset_body(candidate.dataset) for candidate in plan.evaluation_datasets
        ],
        "profiles": [_ecosystem_profile_body(candidate.entry) for candidate in plan.profiles],
    }


def _ecosystem_install_result_body(result: EcosystemInstallResult) -> dict[str, object]:
    domain_package_registry_count = len(result.domain_packages.identities())
    return {
        "status": "installed",
        "domain_package_count": len(result.installed_domain_packages),
        "evaluation_dataset_count": len(result.installed_evaluation_datasets),
        "profile_count": len(result.installed_profiles),
        "registry_count": domain_package_registry_count,
        "domain_package_registry_count": domain_package_registry_count,
        "evaluation_dataset_registry_count": len(result.evaluation_datasets.identities()),
        "profile_registry_count": len(result.profiles.all()),
        "domain_packages": [
            _ecosystem_domain_package_body(package) for package in result.installed_domain_packages
        ],
        "evaluation_datasets": [
            _evaluation_dataset_body(dataset) for dataset in result.installed_evaluation_datasets
        ],
        "profiles": [_ecosystem_profile_body(entry) for entry in result.installed_profiles],
    }


def _ecosystem_domain_package_body(package: DomainPackage) -> dict[str, object]:
    manifest = package.manifest
    return {
        "name": package.identity.name,
        "version": package.identity.version,
        "description": manifest.description,
        "author": manifest.author,
        "entrypoint": manifest.entrypoint,
        "tags": list(manifest.tags),
        "ontology": list(manifest.ontology),
        "capability_names": list(manifest.capabilities),
        "tool_names": list(manifest.tools),
        "policy_names": list(manifest.policies),
        "procedure_names": list(manifest.procedures),
        "knowledge_names": list(manifest.knowledge),
        "evaluator_names": list(manifest.evaluators),
        "context_provider_names": list(manifest.context_providers),
        "prompt_names": list(manifest.prompts),
        "resource_names": list(manifest.resources),
        "dependencies": [
            {"name": dependency.name, "version": dependency.version}
            for dependency in manifest.dependencies
        ],
        "required_tools": list(manifest.required_tools),
        "compatibility": {
            "runtime_api": manifest.compatibility.runtime_api,
            "domain_api": manifest.compatibility.domain_api,
        },
        "security": dict(manifest.security),
        "root_path": str(package.root_path),
        "manifest_path": str(package.manifest_path),
    }


def _ecosystem_profile_body(entry: ProfileCatalogEntry) -> dict[str, object]:
    profile = entry.profile
    return {
        "name": profile.name,
        "version": profile.version,
        "description": profile.description,
        "domains": [
            {"name": domain.name, "version": domain.version}
            for domain in profile.configured_domains()
        ],
        "path": str(entry.path),
    }
