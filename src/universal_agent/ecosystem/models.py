from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from universal_agent.core import DomainIdentity, JsonMapping, immutable_json
from universal_agent.domain import DomainPackage, DomainPackageCompatibility, DomainPackageRegistry
from universal_agent.ecosystem.validation import (
    _dataset_identities,
    _format_domain_identity,
    _profile_identities,
    _reject_duplicates,
    _require_non_empty,
    _validate_optional_sha256,
    _validate_strings,
)
from universal_agent.evaluation.dataset import EvaluationDataset, EvaluationDatasetRegistry
from universal_agent.profile import ProfileCatalogEntry, ProfileRegistry

ECOSYSTEM_REGISTRY_KIND = "EcosystemRegistry"


class _DatasetSuiteLike(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def path(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def tags(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class EcosystemCatalogSummary:
    domain_package_count: int
    evaluation_dataset_count: int
    profile_count: int

    @property
    def total_items(self) -> int:
        return self.domain_package_count + self.evaluation_dataset_count + self.profile_count


@dataclass(frozen=True, slots=True)
class EcosystemCatalogCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class EcosystemCatalogVerificationReport:
    checks: tuple[EcosystemCatalogCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_checks(self) -> tuple[EcosystemCatalogCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)


class EcosystemRegistryValidationError(ValueError):
    pass


class EcosystemRegistryNotFoundError(LookupError):
    pass


class EcosystemRegistryItemNotFoundError(LookupError):
    pass


class EcosystemRegistryStoreNotFoundError(LookupError):
    pass


class EcosystemRegistryInstallError(ValueError):
    pass


class AmbiguousEcosystemRegistryItemError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EcosystemRegistryTrustPolicy:
    """Trust policy for local registry install planning.

    The local P7 registry foundation has no built-in cryptographic verifier. A
    registry that declares signature metadata is rejected by default unless a
    caller supplies an explicit signature verifier or opts into unverified local
    trust for trusted registries.
    """

    allow_unsigned: bool = True
    allow_unverified_signatures: bool = False


@dataclass(frozen=True, slots=True)
class EcosystemRegistrySignatureVerification:
    passed: bool
    verifier: str
    reason: str
    signer: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.verifier, "signature_verification.verifier")
        _require_non_empty(self.reason, "signature_verification.reason")
        if self.signer is not None:
            _require_non_empty(self.signer, "signature_verification.signer")


class EcosystemRegistrySignatureVerifier(Protocol):
    """Verifies registry signature metadata before local install planning."""

    def verify_registry(
        self,
        manifest: EcosystemRegistryManifest,
    ) -> EcosystemRegistrySignatureVerification: ...


@dataclass(frozen=True, slots=True)
class EcosystemDomainPackageRef:
    name: str
    version: str
    description: str
    author: str | None = None
    tags: tuple[str, ...] = ()
    capability_names: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    dependencies: tuple[DomainIdentity, ...] = ()
    compatibility: DomainPackageCompatibility = field(default_factory=DomainPackageCompatibility)
    security: JsonMapping = field(default_factory=immutable_json)
    root_path: str = ""
    manifest_path: str = ""
    manifest_sha256: str = ""
    entrypoint: str | None = None
    resources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "domain_packages[].name")
        _require_non_empty(self.version, "domain_packages[].version")
        _require_non_empty(self.description, "domain_packages[].description")
        if self.entrypoint is not None:
            _require_non_empty(self.entrypoint, "domain_packages[].entrypoint")
        _validate_strings("domain_packages[].tags", self.tags)
        _validate_strings("domain_packages[].capability_names", self.capability_names)
        _validate_strings("domain_packages[].required_tools", self.required_tools)
        _validate_strings("domain_packages[].resources", self.resources)
        _validate_optional_sha256("domain_packages[].manifest_sha256", self.manifest_sha256)
        for index, dependency in enumerate(self.dependencies):
            _require_non_empty(
                dependency.name,
                f"domain_packages[].dependencies[{index}].name",
            )
            _require_non_empty(
                dependency.version,
                f"domain_packages[].dependencies[{index}].version",
            )

    @property
    def identity(self) -> DomainIdentity:
        return DomainIdentity(self.name, self.version)


@dataclass(frozen=True, slots=True)
class EcosystemEvaluationDatasetSuiteRef:
    name: str
    path: str
    description: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "evaluation_datasets[].suites[].name")
        _require_non_empty(self.path, "evaluation_datasets[].suites[].path")
        _validate_strings("evaluation_datasets[].suites[].tags", self.tags)


@dataclass(frozen=True, slots=True)
class EcosystemEvaluationDatasetRef:
    name: str
    version: str
    description: str
    author: str | None = None
    tags: tuple[str, ...] = ()
    domains: tuple[DomainIdentity, ...] = ()
    suites: tuple[EcosystemEvaluationDatasetSuiteRef, ...] = ()
    root_path: str = ""
    manifest_path: str = ""
    manifest_sha256: str = ""

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "evaluation_datasets[].name")
        _require_non_empty(self.version, "evaluation_datasets[].version")
        _require_non_empty(self.description, "evaluation_datasets[].description")
        _validate_strings("evaluation_datasets[].tags", self.tags)
        _validate_optional_sha256(
            "evaluation_datasets[].manifest_sha256",
            self.manifest_sha256,
        )
        if not self.suites:
            raise EcosystemRegistryValidationError(
                "evaluation_datasets[] must include at least one suite"
            )
        for index, domain in enumerate(self.domains):
            _require_non_empty(domain.name, f"evaluation_datasets[].domains[{index}].name")
            _require_non_empty(domain.version, f"evaluation_datasets[].domains[{index}].version")

    @property
    def identity(self) -> tuple[str, str]:
        return (self.name, self.version)


@dataclass(frozen=True, slots=True)
class EcosystemProfileRef:
    name: str
    version: str
    description: str
    domains: tuple[DomainIdentity, ...]
    path: str
    config_sha256: str = ""

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "profiles[].name")
        _require_non_empty(self.version, "profiles[].version")
        if not self.domains:
            raise EcosystemRegistryValidationError("profiles[] must include at least one domain")
        _validate_optional_sha256("profiles[].config_sha256", self.config_sha256)
        for index, domain in enumerate(self.domains):
            _require_non_empty(domain.name, f"profiles[].domains[{index}].name")
            _require_non_empty(domain.version, f"profiles[].domains[{index}].version")

    @property
    def identity(self) -> tuple[str, str]:
        return (self.name, self.version)


@dataclass(frozen=True, slots=True)
class EcosystemRegistryManifest:
    api_version: str
    kind: str
    name: str
    version: str
    description: str
    domain_packages: tuple[EcosystemDomainPackageRef, ...] = ()
    evaluation_datasets: tuple[EcosystemEvaluationDatasetRef, ...] = ()
    profiles: tuple[EcosystemProfileRef, ...] = ()
    metadata: JsonMapping = field(default_factory=immutable_json)

    def __post_init__(self) -> None:
        _require_non_empty(self.api_version, "apiVersion")
        _require_non_empty(self.kind, "kind")
        _require_non_empty(self.name, "metadata.name")
        _require_non_empty(self.version, "metadata.version")
        _require_non_empty(self.description, "metadata.description")
        if self.kind != ECOSYSTEM_REGISTRY_KIND:
            raise EcosystemRegistryValidationError(f"kind must be {ECOSYSTEM_REGISTRY_KIND}")
        _reject_duplicates(
            "domain package",
            tuple(_format_domain_identity(item.identity) for item in self.domain_packages),
        )
        _reject_duplicates(
            "evaluation dataset",
            tuple(f"{name}@{version}" for name, version in _dataset_identities(self)),
        )
        _reject_duplicates(
            "profile",
            tuple(f"{name}@{version}" for name, version in _profile_identities(self)),
        )

    @property
    def summary(self) -> EcosystemCatalogSummary:
        return EcosystemCatalogSummary(
            domain_package_count=len(self.domain_packages),
            evaluation_dataset_count=len(self.evaluation_datasets),
            profile_count=len(self.profiles),
        )


@dataclass(frozen=True, slots=True)
class EcosystemRegistryWriteResult:
    manifest: EcosystemRegistryManifest
    path: Path
    overwritten: bool


@dataclass(frozen=True, slots=True)
class EcosystemDomainPackageInstallCandidate:
    reference: EcosystemDomainPackageRef
    package: DomainPackage


@dataclass(frozen=True, slots=True)
class EcosystemDomainPackageInstallPlan:
    candidates: tuple[EcosystemDomainPackageInstallCandidate, ...]

    @property
    def packages(self) -> tuple[DomainPackage, ...]:
        return tuple(candidate.package for candidate in self.candidates)

    @property
    def identities(self) -> tuple[DomainIdentity, ...]:
        return tuple(package.identity for package in self.packages)


@dataclass(frozen=True, slots=True)
class EcosystemDomainPackageInstallResult:
    registry: DomainPackageRegistry
    installed_packages: tuple[DomainPackage, ...]


@dataclass(frozen=True, slots=True)
class EcosystemEvaluationDatasetInstallCandidate:
    reference: EcosystemEvaluationDatasetRef
    dataset: EvaluationDataset


@dataclass(frozen=True, slots=True)
class EcosystemProfileInstallCandidate:
    reference: EcosystemProfileRef
    entry: ProfileCatalogEntry


@dataclass(frozen=True, slots=True)
class EcosystemInstallPlan:
    domain_packages: EcosystemDomainPackageInstallPlan
    evaluation_datasets: tuple[EcosystemEvaluationDatasetInstallCandidate, ...] = ()
    profiles: tuple[EcosystemProfileInstallCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class EcosystemInstallResult:
    domain_packages: DomainPackageRegistry
    evaluation_datasets: EvaluationDatasetRegistry
    profiles: ProfileRegistry
    installed_domain_packages: tuple[DomainPackage, ...]
    installed_evaluation_datasets: tuple[EvaluationDataset, ...]
    installed_profiles: tuple[ProfileCatalogEntry, ...]
