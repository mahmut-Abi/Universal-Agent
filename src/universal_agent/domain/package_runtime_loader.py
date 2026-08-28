from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any, cast

from universal_agent.domain.package_codec import load_domain_package
from universal_agent.domain.package_models import (
    DomainPackage,
    DomainPackageRuntimeActivation,
    DomainPackageRuntimeLoadError,
    DomainPackageVerificationReport,
    _format_identity,
)
from universal_agent.domain.package_verification import verify_domain_package
from universal_agent.domain.runtime import ActiveDomain, DomainLoader, DomainRuntime


def load_domain_package_runtime(
    package_or_path: DomainPackage | Path,
    *,
    loader: DomainLoader | None = None,
    verify_paths: bool = True,
) -> DomainPackageRuntimeActivation:
    """Load and validate DomainRuntime code from an explicit package entrypoint.

    This is intentionally separate from registry install/discovery. Installing a
    package records metadata; this function is the SDK seam that imports local
    package code, validates it through DomainLoader, and checks that the loaded
    Domain identity matches the package manifest identity.
    """

    package = (
        package_or_path
        if isinstance(package_or_path, DomainPackage)
        else load_domain_package(package_or_path)
    )
    if verify_paths:
        _raise_for_failed_verification(verify_domain_package(package))
    entrypoint = package.manifest.entrypoint
    if entrypoint is None:
        raise DomainPackageRuntimeLoadError(
            f"domain package {_format_identity(package.identity)} has no entrypoint"
        )

    runtime = _load_domain_runtime_entrypoint(package.root_path, entrypoint)
    domain_loader = loader or DomainLoader()
    active = domain_loader.load(runtime)
    if active.identity != package.identity:
        raise DomainPackageRuntimeLoadError(
            "domain package entrypoint identity mismatch: "
            f"package {_format_identity(package.identity)}, "
            f"runtime {_format_identity(active.identity)}"
        )
    _validate_package_runtime_metadata(package, active)
    return DomainPackageRuntimeActivation(package, runtime, active)


def _load_domain_runtime_entrypoint(root_path: Path, entrypoint: str) -> DomainRuntime:
    parsed = _entrypoint(entrypoint)
    with _package_import_path(root_path):
        try:
            target = parsed.load()
        except ImportError as exc:
            raise DomainPackageRuntimeLoadError(
                f"domain package entrypoint module could not be imported: {parsed.module}"
            ) from exc
        except AttributeError as exc:
            raise DomainPackageRuntimeLoadError(
                f"domain package entrypoint attribute not found: {parsed.module}.{parsed.attr}"
            ) from exc
        return _coerce_domain_runtime(target, entrypoint)


def _parse_entrypoint(entrypoint: str) -> tuple[str, tuple[str, ...]]:
    parsed = _entrypoint(entrypoint)
    return parsed.module, tuple(parsed.attr.split("."))


def _entrypoint(entrypoint: str) -> EntryPoint:
    try:
        parsed = EntryPoint(
            name="domain_runtime",
            value=entrypoint,
            group="universal_agent.domains",
        )
        module_name = parsed.module
        attribute_name = parsed.attr
        extras = parsed.extras
    except (AssertionError, ValueError) as exc:
        raise DomainPackageRuntimeLoadError(
            "domain package entrypoint must use 'module:attribute' format"
        ) from exc
    if attribute_name is None:
        raise DomainPackageRuntimeLoadError(
            "domain package entrypoint must use 'module:attribute' format"
        )
    if not module_name.strip() or not attribute_name.strip():
        raise DomainPackageRuntimeLoadError(
            "domain package entrypoint must include module and attribute"
        )
    if extras:
        raise DomainPackageRuntimeLoadError("domain package entrypoint extras are not supported")
    if not all(part.strip() for part in attribute_name.split(".")):
        raise DomainPackageRuntimeLoadError("domain package entrypoint attribute is invalid")
    return parsed


@contextmanager
def _package_import_path(root_path: Path) -> Iterator[None]:
    root = str(root_path)
    inserted = False
    if root not in sys.path:
        sys.path.insert(0, root)
        inserted = True
    try:
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(root)
            except ValueError:  # pragma: no cover - defensive against external sys.path mutation
                pass


def _coerce_domain_runtime(target: Any, entrypoint: str) -> DomainRuntime:
    if _looks_like_domain_runtime(target):
        return cast(DomainRuntime, target)
    if isinstance(target, type):
        try:
            instance = target()
        except TypeError as exc:
            raise DomainPackageRuntimeLoadError(
                f"domain package entrypoint class requires unsupported arguments: {entrypoint}"
            ) from exc
        if _looks_like_domain_runtime(instance):
            return cast(DomainRuntime, instance)
    if callable(target):
        try:
            result = target()
        except TypeError as exc:
            raise DomainPackageRuntimeLoadError(
                f"domain package entrypoint callable requires unsupported arguments: {entrypoint}"
            ) from exc
        if _looks_like_domain_runtime(result):
            return cast(DomainRuntime, result)
        if isinstance(result, type):
            return _coerce_domain_runtime(result, entrypoint)
    raise DomainPackageRuntimeLoadError(
        f"domain package entrypoint did not produce a DomainRuntime: {entrypoint}"
    )


def _looks_like_domain_runtime(candidate: object) -> bool:
    return (
        hasattr(candidate, "manifest")
        and callable(getattr(candidate, "capabilities", None))
        and callable(getattr(candidate, "tools", None))
        and callable(getattr(candidate, "evaluators", None))
    )


def _raise_for_failed_verification(report: DomainPackageVerificationReport) -> None:
    if report.passed:
        return
    details = "; ".join(f"{check.name}: {check.message}" for check in report.failed_checks)
    raise DomainPackageRuntimeLoadError(f"domain package verification failed: {details}")


def _validate_package_runtime_metadata(package: DomainPackage, active: ActiveDomain) -> None:
    _validate_declared_runtime_names(
        "capabilities",
        package.manifest.capabilities,
        tuple(capability.name for capability in active.capabilities),
    )
    _validate_declared_runtime_names(
        "tools",
        package.manifest.tools,
        tuple(tool.definition.name for tool in active.tools),
    )
    _validate_declared_runtime_names(
        "evaluators",
        package.manifest.evaluators,
        tuple(evaluator.name for evaluator in active.evaluators),
    )


def _validate_declared_runtime_names(
    field_name: str,
    declared: tuple[str, ...],
    actual: tuple[str, ...],
) -> None:
    if set(declared) == set(actual):
        return
    raise DomainPackageRuntimeLoadError(
        f"domain package entrypoint {field_name} mismatch: "
        f"manifest [{', '.join(sorted(declared))}], "
        f"runtime [{', '.join(sorted(actual))}]"
    )
