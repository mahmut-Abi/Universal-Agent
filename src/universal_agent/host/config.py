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
    SQLITE = "sqlite"


class SecretSource(StrEnum):
    ENV = "env"


class ModelProvider(StrEnum):
    SCRIPTED = "scripted"
    JSON_HTTP = "json_http"


@dataclass(frozen=True, slots=True)
class SecretRef:
    name: str
    source: SecretSource
    key: str
    required: bool = True

    @classmethod
    def env(cls, name: str, key: str, *, required: bool = True) -> SecretRef:
        return cls(name, SecretSource.ENV, key, required)

    @classmethod
    def from_mapping(cls, name: str, values: Mapping[str, JsonValue]) -> SecretRef:
        ref = cls(
            name=name,
            source=SecretSource(_string(values.get("source", SecretSource.ENV.value), "source")),
            key=_string(values.get("key"), "key"),
            required=_bool(values.get("required", True), "required"),
        )
        ref.validate()
        return ref

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("secret name must not be empty")
        if not self.key.strip():
            raise ValueError(f"secret {self.name} key must not be empty")


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
    def sqlite(cls, path: str) -> StoreConfig:
        return cls(StoreBackend.SQLITE, path)

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
        if self.backend is StoreBackend.SQLITE and not self.path:
            raise ValueError("sqlite store requires path")
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
    backend: str | None = None
    settings: JsonMapping = field(default_factory=immutable_json)

    @classmethod
    def from_mapping(cls, values: Mapping[str, JsonValue]) -> DomainConfig:
        config = cls(
            name=_optional_string(values.get("name"), "name"),
            version=_optional_string(values.get("version"), "version"),
            backend=_optional_string(values.get("backend"), "backend"),
            settings=immutable_json(_object(values.get("settings", {}), "settings")),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.name is not None and not self.name:
            raise ValueError("domain name must not be empty")
        if self.version is not None and not self.version:
            raise ValueError("domain version must not be empty")
        if self.backend is not None and not self.backend.strip():
            raise ValueError("domain backend must not be empty")

    def identity(self) -> DomainIdentity:
        if self.name is None or self.version is None:
            raise ValueError("domain identity requires name and version")
        return DomainIdentity(self.name, self.version)


@dataclass(frozen=True, slots=True)
class ModelConfig:
    provider: ModelProvider = ModelProvider.SCRIPTED
    name: str = "scripted"
    endpoint: str | None = None
    api_key_secret: str | None = None
    timeout_seconds: float = 30.0
    headers: JsonMapping = field(default_factory=immutable_json)

    @classmethod
    def scripted(cls, name: str = "scripted") -> ModelConfig:
        return cls(ModelProvider.SCRIPTED, name)

    @classmethod
    def json_http(
        cls,
        *,
        name: str,
        endpoint: str,
        api_key_secret: str | None = None,
        timeout_seconds: float = 30.0,
        headers: Mapping[str, str] | None = None,
    ) -> ModelConfig:
        return cls(
            ModelProvider.JSON_HTTP,
            name,
            endpoint,
            api_key_secret,
            timeout_seconds,
            immutable_json(dict(headers or {})),
        )

    @classmethod
    def from_mapping(cls, values: Mapping[str, JsonValue]) -> ModelConfig:
        config = cls(
            provider=ModelProvider(
                _string(values.get("provider", ModelProvider.SCRIPTED.value), "provider")
            ),
            name=_string(values.get("name", "scripted"), "name"),
            endpoint=_optional_string(values.get("endpoint"), "endpoint"),
            api_key_secret=_optional_string(values.get("api_key_secret"), "api_key_secret"),
            timeout_seconds=_float(values.get("timeout_seconds", 30.0), "timeout_seconds"),
            headers=immutable_json(_string_mapping(values.get("headers", {}), "headers")),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("model name must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("model timeout_seconds must be positive")
        _string_mapping(self.headers, "model headers")
        if self.provider is ModelProvider.SCRIPTED:
            if self.endpoint is not None:
                raise ValueError("scripted model does not accept endpoint")
            if self.api_key_secret is not None:
                raise ValueError("scripted model does not accept api_key_secret")
            return
        if self.provider is ModelProvider.JSON_HTTP:
            if self.endpoint is None or not self.endpoint.strip():
                raise ValueError("json_http model requires endpoint")
            if self.api_key_secret is not None and not self.api_key_secret.strip():
                raise ValueError("model api_key_secret must not be empty")
            return
        raise ValueError(f"unsupported model provider: {self.provider}")


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    environment: JsonMapping = field(default_factory=immutable_json)
    secrets: tuple[SecretRef, ...] = ()
    model: ModelConfig = field(default_factory=ModelConfig.scripted)
    store: StoreConfig = field(default_factory=StoreConfig.memory)
    distributed_queue: StoreConfig = field(default_factory=StoreConfig.memory)
    distributed_locks: StoreConfig = field(default_factory=StoreConfig.memory)
    distributed_workers: StoreConfig = field(default_factory=StoreConfig.memory)
    distributed_terminal_retention_seconds: float | None = None
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
            secrets=_secret_refs(values.get("secrets")),
            model=ModelConfig.from_mapping(_object(values.get("model", {}), "model")),
            store=StoreConfig.from_mapping(_object(values.get("store", {}), "store")),
            distributed_queue=StoreConfig.from_mapping(
                _object(values.get("distributed_queue", {}), "distributed_queue")
            ),
            distributed_locks=StoreConfig.from_mapping(
                _object(values.get("distributed_locks", {}), "distributed_locks")
            ),
            distributed_workers=StoreConfig.from_mapping(
                _object(values.get("distributed_workers", {}), "distributed_workers")
            ),
            distributed_terminal_retention_seconds=_optional_float(
                values.get("distributed_terminal_retention_seconds"),
                "distributed_terminal_retention_seconds",
            ),
            limits=RuntimeLimitsConfig.from_mapping(_object(values.get("limits", {}), "limits")),
            domain=domain,
            domains=domains,
        )
        config.validate()
        return config

    def validate(self) -> None:
        for secret in self.secrets:
            secret.validate()
        duplicate_secrets = _duplicates(tuple(secret.name for secret in self.secrets))
        if duplicate_secrets:
            raise ValueError("duplicate runtime secrets: " + ", ".join(duplicate_secrets))
        self.model.validate()
        if self.model.api_key_secret is not None and self.model.api_key_secret not in {
            secret.name for secret in self.secrets
        }:
            raise ValueError(f"model api_key_secret is not declared: {self.model.api_key_secret}")
        self.store.validate()
        self.distributed_queue.validate()
        self.distributed_locks.validate()
        self.distributed_workers.validate()
        if (
            self.distributed_terminal_retention_seconds is not None
            and self.distributed_terminal_retention_seconds <= 0
        ):
            raise ValueError("distributed_terminal_retention_seconds must be positive")
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


def _secret_refs(value: JsonValue) -> tuple[SecretRef, ...]:
    if value is None:
        return ()
    secrets = _object(value, "secrets")
    return tuple(
        SecretRef.from_mapping(name, _object(body, f"secrets.{name}"))
        for name, body in sorted(secrets.items())
    )


def _domain_configs(value: JsonValue) -> tuple[DomainConfig, ...]:
    if value is None:
        return ()
    return tuple(
        DomainConfig.from_mapping(_object(item, "domains[]")) for item in _list(value, "domains")
    )


def _duplicate_domain_configs(domains: tuple[DomainConfig, ...]) -> tuple[str, ...]:
    return _duplicates(tuple(f"{domain.name or ''}@{domain.version or ''}" for domain in domains))


def _duplicates(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return tuple(sorted(duplicates))


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


def _optional_float(value: JsonValue, field: str) -> float | None:
    if value is None:
        return None
    return _float(value, field)


def _float(value: JsonValue, field: str) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    raise ValueError(f"{field} must be a number")


def _bool(value: JsonValue, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be a boolean")


def _list(value: JsonValue, field: str) -> list[JsonValue]:
    if isinstance(value, list):
        return value
    raise ValueError(f"{field} must be a list")


def _string_mapping(
    value: JsonValue | Mapping[str, JsonValue],
    field: str,
) -> Mapping[str, str]:
    values = value if isinstance(value, Mapping) else _object(value, field)
    result: dict[str, str] = {}
    for key, item in values.items():
        if not isinstance(item, str):
            raise ValueError(f"{field}.{key} must be a string")
        result[key] = item
    return result
