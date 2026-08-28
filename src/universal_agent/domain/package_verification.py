from __future__ import annotations

from graphlib import CycleError, TopologicalSorter
from typing import TYPE_CHECKING, cast

from universal_agent.core import DomainIdentity
from universal_agent.domain.package_codec import load_domain_package
from universal_agent.domain.package_models import (
    DomainPackage,
    DomainPackageCheck,
    DomainPackageNotFoundError,
    DomainPackageValidationError,
    DomainPackageVerificationReport,
    _format_identity,
    _package_resource_path,
)

if TYPE_CHECKING:
    from universal_agent.domain.package_registry import DomainPackageRegistry


def verify_domain_package(package: DomainPackage) -> DomainPackageVerificationReport:
    return DomainPackageVerificationReport(
        (
            _package_root_exists(package),
            _package_manifest_exists(package),
            _package_manifest_matches_identity(package),
            _package_resources_exist(package),
        )
    )


def verify_domain_package_registry(
    registry: DomainPackageRegistry,
    *,
    verify_paths: bool = False,
) -> DomainPackageVerificationReport:
    packages = registry.list()
    registered_domains = frozenset(package.identity for package in packages)
    checks = [
        _package_dependencies_registered(packages, registered_domains),
        _package_dependencies_acyclic(packages),
    ]
    if verify_paths:
        checks.extend(_package_registry_local_checks(packages))
    return DomainPackageVerificationReport(tuple(checks))


def _package_root_exists(package: DomainPackage) -> DomainPackageCheck:
    if package.root_path.is_dir():
        return DomainPackageCheck(
            "package_root_exists",
            True,
            "domain package root exists",
        )
    return DomainPackageCheck(
        "package_root_exists",
        False,
        f"domain package root missing or not a directory: {package.root_path}",
    )


def _package_manifest_exists(package: DomainPackage) -> DomainPackageCheck:
    if package.manifest_path.is_file():
        return DomainPackageCheck(
            "package_manifest_exists",
            True,
            "domain package manifest exists",
        )
    return DomainPackageCheck(
        "package_manifest_exists",
        False,
        f"domain package manifest missing or not a file: {package.manifest_path}",
    )


def _package_manifest_matches_identity(package: DomainPackage) -> DomainPackageCheck:
    try:
        loaded = load_domain_package(package.manifest_path)
    except (DomainPackageNotFoundError, DomainPackageValidationError) as exc:
        return DomainPackageCheck(
            "package_manifest_matches_identity",
            False,
            f"domain package manifest could not be loaded: {exc}",
        )
    if loaded.identity == package.identity:
        return DomainPackageCheck(
            "package_manifest_matches_identity",
            True,
            "domain package manifest identity matches loaded package",
        )
    return DomainPackageCheck(
        "package_manifest_matches_identity",
        False,
        "domain package identity mismatch: "
        f"expected {_format_identity(package.identity)}, "
        f"loaded {_format_identity(loaded.identity)}",
    )


def _package_resources_exist(package: DomainPackage) -> DomainPackageCheck:
    invalid: list[str] = []
    missing: list[str] = []
    for resource in package.manifest.resources:
        try:
            resource_path = _package_resource_path(package.root_path, resource)
        except DomainPackageValidationError:
            invalid.append(resource)
            continue
        if not resource_path.exists():
            missing.append(resource)

    if invalid:
        return DomainPackageCheck(
            "package_resources_exist",
            False,
            "domain package resources must stay inside package root: " + ", ".join(invalid),
        )
    if missing:
        return DomainPackageCheck(
            "package_resources_exist",
            False,
            "domain package declares missing resources: " + ", ".join(missing),
        )
    return DomainPackageCheck(
        "package_resources_exist",
        True,
        "domain package declared resources exist",
    )


def _package_dependencies_registered(
    packages: tuple[DomainPackage, ...],
    registered_domains: frozenset[DomainIdentity],
) -> DomainPackageCheck:
    missing = tuple(
        f"{package.identity.name}:{dependency.name}@{dependency.version}"
        for package in packages
        for dependency in package.manifest.dependencies
        if dependency not in registered_domains
    )
    if not missing:
        return DomainPackageCheck(
            "package_dependencies_registered",
            True,
            "all Domain package dependencies are registered",
        )
    return DomainPackageCheck(
        "package_dependencies_registered",
        False,
        "Domain packages reference missing dependencies: " + ", ".join(missing),
    )


def _package_dependencies_acyclic(packages: tuple[DomainPackage, ...]) -> DomainPackageCheck:
    dependency_map = {package.identity: package.manifest.dependencies for package in packages}
    cycles = _dependency_cycles(dependency_map)
    if not cycles:
        return DomainPackageCheck(
            "package_dependencies_acyclic",
            True,
            "Domain package dependencies are acyclic",
        )
    return DomainPackageCheck(
        "package_dependencies_acyclic",
        False,
        "Domain package dependencies contain cycles: " + ", ".join(cycles),
    )


def _package_registry_local_checks(
    packages: tuple[DomainPackage, ...],
) -> tuple[DomainPackageCheck, ...]:
    checks: list[DomainPackageCheck] = []
    for package in packages:
        identity = _format_identity(package.identity)
        for check in verify_domain_package(package).checks:
            checks.append(
                DomainPackageCheck(
                    f"{check.name}:{identity}",
                    check.passed,
                    f"{identity}: {check.message}",
                )
            )
    return tuple(checks)


def _dependency_cycles(
    dependency_map: dict[DomainIdentity, tuple[DomainIdentity, ...]],
) -> tuple[str, ...]:
    known = frozenset(dependency_map)
    sorter: TopologicalSorter[DomainIdentity] = TopologicalSorter()
    for identity in dependency_map:
        sorter.add(
            identity,
            *(dependency for dependency in dependency_map[identity] if dependency in known),
        )
    try:
        tuple(sorter.static_order())
    except CycleError as exc:
        cycle = cast(list[DomainIdentity], exc.args[1])
        return (" -> ".join(_format_identity(item) for item in cycle),)
    return ()
