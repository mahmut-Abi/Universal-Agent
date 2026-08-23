from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from universal_agent.core import JsonValue

if TYPE_CHECKING:
    from universal_agent.host.config import DomainConfig, RuntimeConfig

PROFILE_CONFIG_FILE = "profile.json"
PROFILE_CONFIG_SUFFIX = ".profile.json"


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
        with Path(path).open("r", encoding="utf-8") as handle:
            loaded: object = json.load(handle)
        payload = _object(_json_value(loaded, "profile config file"), "profile config file")
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, values: Mapping[str, JsonValue]) -> ProfileConfig:
        domain_config = _domain_config_type()
        runtime_config = _runtime_config_type()
        domains = _domain_configs(values.get("domains"), domain_config)
        domain = (
            domains[0]
            if domains
            else domain_config.from_mapping(_object(values.get("domain", {}), "domain"))
        )
        config = cls(
            name=_string(values.get("name"), "name"),
            version=_string(values.get("version"), "version"),
            description=_string(values.get("description", ""), "description"),
            domain=domain,
            runtime=runtime_config.from_mapping(_object(values.get("runtime", {}), "runtime")),
            domains=domains,
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("profile name must not be empty")
        if not self.version.strip():
            raise ValueError("profile version must not be empty")
        if self.domain.name is None or not self.domain.name.strip():
            raise ValueError("profile domain name must not be empty")
        if self.domain.version is None or not self.domain.version.strip():
            raise ValueError("profile domain version must not be empty")
        for domain in self.configured_domains():
            if domain.name is None or not domain.name.strip():
                raise ValueError("profile domain name must not be empty")
            if domain.version is None or not domain.version.strip():
                raise ValueError("profile domain version must not be empty")
        duplicates = _duplicate_domain_configs(self.configured_domains())
        if duplicates:
            raise ValueError("duplicate profile domains: " + ", ".join(duplicates))
        self.runtime.validate()
        runtime_domains = self.runtime.configured_domains()
        if runtime_domains and runtime_domains != self.configured_domains():
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

    def get(self, name: str) -> ProfileCatalogEntry:
        for entry in self.all():
            if entry.profile.name == name:
                return entry
        raise ProfileNotFoundError(f"profile not found: {name}")


@dataclass(frozen=True, slots=True)
class ProfileRegistry:
    profiles: tuple[AgentProfile, ...] = ()

    def __post_init__(self) -> None:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for profile in self.profiles:
            if profile.name in seen:
                duplicates.add(profile.name)
            seen.add(profile.name)
        if duplicates:
            raise ValueError("duplicate profiles: " + ", ".join(sorted(duplicates)))

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


def _load_profile_entry(path: Path) -> ProfileCatalogEntry:
    config = ProfileConfig.from_json_file(path)
    return ProfileCatalogEntry(config.to_profile(), config, path)


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


def _object(value: JsonValue, field: str) -> Mapping[str, JsonValue]:
    if isinstance(value, dict):
        return value
    raise ValueError(f"{field} must be an object")


def _domain_configs(
    value: JsonValue,
    domain_config: type[DomainConfig],
) -> tuple[DomainConfig, ...]:
    if value is None:
        return ()
    return tuple(
        domain_config.from_mapping(_object(item, "domains[]")) for item in _list(value, "domains")
    )


def _duplicate_domain_configs(domains: tuple[DomainConfig, ...]) -> tuple[str, ...]:
    seen: set[tuple[str | None, str | None]] = set()
    duplicates: set[tuple[str | None, str | None]] = set()
    for domain in domains:
        key = (domain.name, domain.version)
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    return tuple(f"{name or ''}@{version or ''}" for name, version in sorted(duplicates))


def _json_value(value: object, field: str) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [_json_value(item, f"{field}[]") for item in value]
    if isinstance(value, dict):
        payload: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field} keys must be strings")
            payload[key] = _json_value(item, f"{field}.{key}")
        return payload
    raise ValueError(f"{field} must be JSON-compatible")


def _string(value: JsonValue, field: str) -> str:
    if isinstance(value, str):
        return value
    raise ValueError(f"{field} must be a string")


def _list(value: JsonValue, field: str) -> list[JsonValue]:
    if isinstance(value, list):
        return value
    raise ValueError(f"{field} must be a list")


def _domain_config_type() -> type[DomainConfig]:
    from universal_agent.host.config import DomainConfig

    return DomainConfig


def _runtime_config_type() -> type[RuntimeConfig]:
    from universal_agent.host.config import RuntimeConfig

    return RuntimeConfig
