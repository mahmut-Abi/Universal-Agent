from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from universal_agent.core import DomainIdentity, JsonMapping, JsonValue, immutable_json


class StoreBackend(StrEnum):
    MEMORY = "memory"
    FILE = "file"


@dataclass(frozen=True, slots=True)
class StoreConfig:
    backend: StoreBackend = StoreBackend.MEMORY
    path: str | None = None

    @classmethod
    def memory(cls) -> StoreConfig:
        return cls(StoreBackend.MEMORY)

    @classmethod
    def file(cls, path: str) -> StoreConfig:
        return cls(StoreBackend.FILE, path)

    @classmethod
    def from_mapping(cls, values: Mapping[str, JsonValue]) -> StoreConfig:
        backend = StoreBackend(_string(values.get("backend", StoreBackend.MEMORY.value), "backend"))
        path = _optional_string(values.get("path"), "path")
        config = cls(backend, path)
        config.validate()
        return config

    def validate(self) -> None:
        if self.backend is StoreBackend.FILE and not self.path:
            raise ValueError("file store requires path")
        if self.backend is StoreBackend.MEMORY and self.path is not None:
            raise ValueError("memory store does not accept path")


@dataclass(frozen=True, slots=True)
class RuntimeLimitsConfig:
    max_iterations: int = 20
    max_recovery_steps: int = 8

    @classmethod
    def from_mapping(cls, values: Mapping[str, JsonValue]) -> RuntimeLimitsConfig:
        config = cls(
            max_iterations=_int(values.get("max_iterations", 20), "max_iterations"),
            max_recovery_steps=_int(values.get("max_recovery_steps", 8), "max_recovery_steps"),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        if self.max_recovery_steps < 1:
            raise ValueError("max_recovery_steps must be positive")


@dataclass(frozen=True, slots=True)
class DomainConfig:
    name: str | None = None
    version: str | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, JsonValue]) -> DomainConfig:
        config = cls(
            name=_optional_string(values.get("name"), "name"),
            version=_optional_string(values.get("version"), "version"),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.name is not None and not self.name:
            raise ValueError("domain name must not be empty")
        if self.version is not None and not self.version:
            raise ValueError("domain version must not be empty")

    def identity(self) -> DomainIdentity:
        if self.name is None or self.version is None:
            raise ValueError("domain identity requires name and version")
        return DomainIdentity(self.name, self.version)


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    environment: JsonMapping = field(default_factory=immutable_json)
    store: StoreConfig = field(default_factory=StoreConfig.memory)
    limits: RuntimeLimitsConfig = field(default_factory=RuntimeLimitsConfig)
    domain: DomainConfig = field(default_factory=DomainConfig)
    domains: tuple[DomainConfig, ...] = ()

    @classmethod
    def from_json_file(cls, path: str | Path) -> RuntimeConfig:
        with Path(path).open("r", encoding="utf-8") as handle:
            loaded: object = json.load(handle)
        payload = _object(_json_value(loaded, "runtime config file"), "runtime config file")
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, values: Mapping[str, JsonValue]) -> RuntimeConfig:
        domains = _domain_configs(values.get("domains"))
        domain = (
            domains[0]
            if domains
            else DomainConfig.from_mapping(_object(values.get("domain", {}), "domain"))
        )
        config = cls(
            environment=immutable_json(_object(values.get("environment", {}), "environment")),
            store=StoreConfig.from_mapping(_object(values.get("store", {}), "store")),
            limits=RuntimeLimitsConfig.from_mapping(_object(values.get("limits", {}), "limits")),
            domain=domain,
            domains=domains,
        )
        config.validate()
        return config

    def validate(self) -> None:
        self.store.validate()
        self.limits.validate()
        self.domain.validate()
        for domain in self.domains:
            domain.validate()
            if domain.name is None or domain.version is None:
                raise ValueError("configured domains require name and version")
        if self.domains and self.domain != self.domains[0]:
            raise ValueError("primary domain must match first configured domain")
        duplicates = _duplicate_domain_configs(self.configured_domains())
        if duplicates:
            raise ValueError("duplicate configured domains: " + ", ".join(duplicates))

    def configured_domains(self) -> tuple[DomainConfig, ...]:
        if self.domains:
            return self.domains
        if self.domain.name is None and self.domain.version is None:
            return ()
        return (self.domain,)


def _object(value: JsonValue, field: str) -> Mapping[str, JsonValue]:
    if isinstance(value, dict):
        return value
    raise ValueError(f"{field} must be an object")


def _domain_configs(value: JsonValue) -> tuple[DomainConfig, ...]:
    if value is None:
        return ()
    return tuple(
        DomainConfig.from_mapping(_object(item, "domains[]")) for item in _list(value, "domains")
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


def _optional_string(value: JsonValue, field: str) -> str | None:
    if value is None:
        return None
    return _string(value, field)


def _int(value: JsonValue, field: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be an integer")


def _list(value: JsonValue, field: str) -> list[JsonValue]:
    if isinstance(value, list):
        return value
    raise ValueError(f"{field} must be a list")
