from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from universal_agent.core import JsonValue

if TYPE_CHECKING:
    from universal_agent.host.config import DomainConfig, RuntimeConfig


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


@dataclass(frozen=True, slots=True)
class ProfileConfig:
    name: str
    version: str
    description: str = ""
    domain: DomainConfig = field(default_factory=lambda: _domain_config_type()())
    runtime: RuntimeConfig = field(default_factory=lambda: _runtime_config_type()())

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
        config = cls(
            name=_string(values.get("name"), "name"),
            version=_string(values.get("version"), "version"),
            description=_string(values.get("description", ""), "description"),
            domain=domain_config.from_mapping(_object(values.get("domain", {}), "domain")),
            runtime=runtime_config.from_mapping(_object(values.get("runtime", {}), "runtime")),
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
        self.runtime.validate()

    def to_profile(self) -> AgentProfile:
        self.validate()
        return AgentProfile(
            self.name,
            self.version,
            self.description,
            self.domain,
            self.runtime,
        )


class ProfileNotFoundError(LookupError):
    pass


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


def _object(value: JsonValue, field: str) -> Mapping[str, JsonValue]:
    if isinstance(value, dict):
        return value
    raise ValueError(f"{field} must be an object")


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


def _domain_config_type() -> type[DomainConfig]:
    from universal_agent.host.config import DomainConfig

    return DomainConfig


def _runtime_config_type() -> type[RuntimeConfig]:
    from universal_agent.host.config import RuntimeConfig

    return RuntimeConfig
