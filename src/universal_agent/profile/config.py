from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from universal_agent.core import JsonCodecError, JsonValue, read_json_file
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    duplicate_values,
    json_mapping,
    parse_json_object,
    parse_non_empty_string,
    parse_payload,
)

if TYPE_CHECKING:
    from universal_agent.host.config import DomainConfig, RuntimeConfig

PROFILE_CONFIG_FILE = "profile.json"
PROFILE_CONFIG_SUFFIX = ".profile.json"


class _ProfileConfigPayload(ConfigPayload):
    name: str
    version: str
    description: str = ""
    domain: dict[str, PydanticJsonValue] = Field(default_factory=dict)
    runtime: dict[str, PydanticJsonValue] = Field(default_factory=dict)
    domains: list[dict[str, PydanticJsonValue]] | None = None


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """Application-level declaration of a configured Agent identity.

    A Profile is not a Kernel concept and not a Domain implementation. It
    describes the runtime configuration an application can select before
    submitting a Goal.
    """

    name: str
    version: str
    description: str
    domain: DomainConfig
    runtime: RuntimeConfig
    domains: tuple[DomainConfig, ...] = ()

    def configured_domains(self) -> tuple[DomainConfig, ...]:
        return self.domains or (self.domain,)


@dataclass(frozen=True, slots=True)
class ProfileConfig:
    name: str
    version: str
    description: str = ""
    domain: DomainConfig = field(default_factory=lambda: _domain_config_type()())
    runtime: RuntimeConfig = field(default_factory=lambda: _runtime_config_type()())
    domains: tuple[DomainConfig, ...] = ()

    @classmethod
    def from_json_file(cls, path: str | Path) -> ProfileConfig:
        loaded = read_json_file(path)
        return cls.from_mapping(parse_json_object(loaded, "profile config file"))

    @classmethod
    def from_mapping(cls, values: Mapping[str, JsonValue]) -> ProfileConfig:
        payload = parse_payload(_ProfileConfigPayload, values)
        domain_config = _domain_config_type()
        runtime_config = _runtime_config_type()
        domains = _domain_configs(payload.domains, domain_config)
        domain = domains[0] if domains else domain_config.from_mapping(json_mapping(payload.domain))
        config = cls(
            name=payload.name,
            version=payload.version,
            description=payload.description,
            domain=domain,
            runtime=runtime_config.from_mapping(json_mapping(payload.runtime)),
            domains=domains,
        )
        config.validate()
        return config

    def validate(self) -> None:
        _require_non_empty(self.name, "profile name")
        _require_non_empty(self.version, "profile version")
        _require_domain_identity(self.domain)
        for domain in self.configured_domains():
            _require_domain_identity(domain)
        duplicates = _duplicate_domain_configs(self.configured_domains())
        if duplicates:
            raise ValueError("duplicate profile domains: " + ", ".join(duplicates))
        self.runtime.validate()
        runtime_domains = _domain_identities(self.runtime.configured_domains())
        profile_domains = _domain_identities(self.configured_domains())
        if runtime_domains and runtime_domains != profile_domains:
            raise ValueError("profile domains must match runtime configured domains")

    def to_profile(self) -> AgentProfile:
        self.validate()
        return AgentProfile(
            self.name,
            self.version,
            self.description,
            self.domain,
            self.runtime,
            self.configured_domains(),
        )

    def configured_domains(self) -> tuple[DomainConfig, ...]:
        return self.domains or (self.domain,)


class ProfileNotFoundError(LookupError):
    pass


class ProfileConfigNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ProfileCatalogEntry:
    profile: AgentProfile
    config: ProfileConfig
    path: Path


@dataclass(frozen=True, slots=True)
class ProfileCatalogCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class ProfileCatalogVerificationReport:
    checks: tuple[ProfileCatalogCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_checks(self) -> tuple[ProfileCatalogCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)


@dataclass(frozen=True, slots=True)
class ProfileCatalog:
    entries: tuple[ProfileCatalogEntry, ...] = ()

    def __post_init__(self) -> None:
        ProfileRegistry(tuple(entry.profile for entry in self.entries))

    @classmethod
    def discover(cls, root: str | Path) -> ProfileCatalog:
        entries = tuple(_load_profile_entry(path) for path in _profile_config_paths(Path(root)))
        return cls(entries)

    def all(self) -> tuple[ProfileCatalogEntry, ...]:
        return tuple(sorted(self.entries, key=lambda item: (item.profile.name, str(item.path))))

    def registry(self) -> ProfileRegistry:
        return ProfileRegistry(tuple(entry.profile for entry in self.all()))

    def verify(self) -> ProfileCatalogVerificationReport:
        return verify_profile_catalog(self)

    def get(self, name: str) -> ProfileCatalogEntry:
        for entry in self.all():
            if entry.profile.name == name:
                return entry
        raise ProfileNotFoundError(f"profile not found: {name}")


@dataclass(frozen=True, slots=True)
class ProfileRegistry:
    profiles: tuple[AgentProfile, ...] = ()

    def __post_init__(self) -> None:
        duplicates = duplicate_values(profile.name for profile in self.profiles)
        if duplicates:
            raise ValueError("duplicate profiles: " + ", ".join(duplicates))

    def all(self) -> tuple[AgentProfile, ...]:
        return tuple(sorted(self.profiles, key=lambda item: item.name))

    def has(self, name: str) -> bool:
        return any(profile.name == name for profile in self.profiles)

    def get(self, name: str) -> AgentProfile:
        for profile in self.profiles:
            if profile.name == name:
                return profile
        raise ProfileNotFoundError(f"profile not found: {name}")


def load_profile_catalog(root: str | Path) -> ProfileCatalog:
    return ProfileCatalog.discover(root)


def verify_profile_catalog_entry(entry: ProfileCatalogEntry) -> ProfileCatalogVerificationReport:
    return ProfileCatalogVerificationReport(
        (
            _profile_config_exists(entry),
            _profile_config_matches_identity(entry),
        )
    )


def verify_profile_catalog(catalog: ProfileCatalog) -> ProfileCatalogVerificationReport:
    checks = tuple(
        check for entry in catalog.all() for check in verify_profile_catalog_entry(entry).checks
    )
    return ProfileCatalogVerificationReport(checks)


def _load_profile_entry(path: Path) -> ProfileCatalogEntry:
    config = ProfileConfig.from_json_file(path)
    return ProfileCatalogEntry(config.to_profile(), config, path)


def _profile_config_exists(entry: ProfileCatalogEntry) -> ProfileCatalogCheck:
    if entry.path.is_file():
        return ProfileCatalogCheck(
            "profile_config_exists",
            True,
            f"profile config exists: {_profile_identity(entry.profile)}",
        )
    return ProfileCatalogCheck(
        "profile_config_exists",
        False,
        f"profile config missing or not a file: {entry.path}",
    )


def _profile_config_matches_identity(entry: ProfileCatalogEntry) -> ProfileCatalogCheck:
    try:
        loaded = ProfileConfig.from_json_file(entry.path).to_profile()
    except (OSError, JsonCodecError, ValueError) as exc:
        return ProfileCatalogCheck(
            "profile_config_matches_identity",
            False,
            f"profile config could not be loaded: {exc}",
        )
    if (loaded.name, loaded.version) == (entry.profile.name, entry.profile.version):
        return ProfileCatalogCheck(
            "profile_config_matches_identity",
            True,
            f"profile config identity matches: {_profile_identity(entry.profile)}",
        )
    return ProfileCatalogCheck(
        "profile_config_matches_identity",
        False,
        "profile config identity mismatch: "
        f"expected {_profile_identity(entry.profile)}, loaded {_profile_identity(loaded)}",
    )


def _profile_config_paths(root: Path) -> tuple[Path, ...]:
    if root.is_file():
        return (root,)
    if not root.exists():
        raise ProfileConfigNotFoundError(f"profile config root not found: {root}")
    profile_json = tuple(root.rglob(PROFILE_CONFIG_FILE))
    suffixed = tuple(root.rglob(f"*{PROFILE_CONFIG_SUFFIX}"))
    paths = tuple(sorted(set(profile_json + suffixed)))
    if not paths:
        raise ProfileConfigNotFoundError(f"profile config files not found: {root}")
    return paths


def _domain_configs(
    value: list[dict[str, PydanticJsonValue]] | None,
    domain_config: type[DomainConfig],
) -> tuple[DomainConfig, ...]:
    if value is None:
        return ()
    return tuple(domain_config.from_mapping(json_mapping(item)) for item in value)


def _duplicate_domain_configs(domains: tuple[DomainConfig, ...]) -> tuple[str, ...]:
    return duplicate_values(f"{domain.name or ''}@{domain.version or ''}" for domain in domains)


def _require_domain_identity(domain: DomainConfig) -> None:
    _require_non_empty(domain.name or "", "profile domain name")
    _require_non_empty(domain.version or "", "profile domain version")


def _require_non_empty(value: str, field: str) -> None:
    parse_non_empty_string(value, field)


def _domain_identities(
    domains: tuple[DomainConfig, ...],
) -> tuple[tuple[str | None, str | None], ...]:
    return tuple((domain.name, domain.version) for domain in domains)


def _profile_identity(profile: AgentProfile) -> str:
    return f"{profile.name}@{profile.version}"


def _domain_config_type() -> type[DomainConfig]:
    from universal_agent.host.config import DomainConfig

    return DomainConfig


def _runtime_config_type() -> type[RuntimeConfig]:
    from universal_agent.host.config import RuntimeConfig

    return RuntimeConfig
