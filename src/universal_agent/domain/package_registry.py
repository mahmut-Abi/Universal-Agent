from __future__ import annotations

from pathlib import Path

from universal_agent.core import DomainIdentity
from universal_agent.domain.package_codec import _manifest_paths, load_domain_package
from universal_agent.domain.package_models import (
    AmbiguousDomainPackageError,
    DomainPackage,
    DomainPackageNotFoundError,
    DomainPackageValidationError,
    DomainPackageVerificationReport,
    _format_identity,
)
from universal_agent.domain.package_verification import verify_domain_package_registry


class DomainPackageRegistry:
    """P7 metadata registry for independently packaged Domain runtimes.

    The registry deliberately stores package metadata only. It does not import
    domain entrypoints or mutate Kernel runtime state; actual DomainRuntime
    activation remains owned by DomainManager/DomainLoader.
    """

    def __init__(self, packages: tuple[DomainPackage, ...] = ()) -> None:
        self._packages: dict[DomainIdentity, DomainPackage] = {}
        self._order: list[DomainIdentity] = []
        for package in packages:
            self.register(package)

    def register(self, package: DomainPackage) -> DomainPackage:
        identity = package.identity
        if identity in self._packages:
            raise DomainPackageValidationError(
                f"domain package already registered: {_format_identity(identity)}"
            )
        self._packages[identity] = package
        self._order.append(identity)
        return package

    def discover(self, root: Path) -> tuple[DomainPackage, ...]:
        packages = tuple(load_domain_package(path) for path in _manifest_paths(root))
        for package in packages:
            self.register(package)
        return packages

    def install(self, path: Path) -> DomainPackage:
        """Validate and register one package without activating its Domain code."""

        return self.register(load_domain_package(path))

    def list(self, *, tag: str | None = None) -> tuple[DomainPackage, ...]:
        packages = tuple(self._packages[identity] for identity in self._order)
        if tag is None:
            return packages
        return tuple(package for package in packages if tag in package.manifest.tags)

    def identities(self) -> tuple[DomainIdentity, ...]:
        return tuple(package.identity for package in self.list())

    def get(self, identity: DomainIdentity) -> DomainPackage:
        try:
            return self._packages[identity]
        except KeyError as exc:
            raise DomainPackageNotFoundError(
                f"domain package not registered: {_format_identity(identity)}"
            ) from exc

    def get_by_name(self, name: str) -> DomainPackage:
        matches = tuple(package for package in self.list() if package.identity.name == name)
        if not matches:
            raise DomainPackageNotFoundError(f"domain package not registered: {name}")
        if len(matches) > 1:
            versions = ", ".join(sorted(package.identity.version for package in matches))
            raise AmbiguousDomainPackageError(
                f"domain package {name} has multiple registered versions: {versions}"
            )
        return matches[0]

    def verify(self, *, verify_paths: bool = False) -> DomainPackageVerificationReport:
        return verify_domain_package_registry(self, verify_paths=verify_paths)
