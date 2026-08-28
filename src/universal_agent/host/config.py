from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import Field

from universal_agent.core import (
    DomainIdentity,
    JsonMapping,
    JsonValue,
    immutable_json,
    read_json_file,
)
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    duplicate_values,
    enum_before_validator,
    json_mapping,
    parse_json_object,
    parse_non_empty_string,
    parse_optional_non_empty_string,
    parse_optional_positive_float,
    parse_payload,
    parse_positive_float,
    parse_positive_int,
    string_mapping,
)


class StoreBackend(StrEnum):
    MEMORY = "memory"
    FILE = "file"
    SQLITE = "sqlite"


class SecretSource(StrEnum):
    ENV = "env"
    FILE = "file"


class ModelProvider(StrEnum):
    SCRIPTED = "scripted"
    JSON_HTTP = "json_http"
    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
    OPENAI_RESPONSES = "openai_responses"


_SecretSourcePayload = Annotated[SecretSource, enum_before_validator(SecretSource, "source")]
_StoreBackendPayload = Annotated[StoreBackend, enum_before_validator(StoreBackend, "backend")]
_ModelProviderPayload = Annotated[ModelProvider, enum_before_validator(ModelProvider, "provider")]


class _SecretRefPayload(ConfigPayload):
    source: _SecretSourcePayload = SecretSource.ENV
    key: str
    required: bool = True


class _StoreConfigPayload(ConfigPayload):
    backend: _StoreBackendPayload = StoreBackend.MEMORY
    path: str | None = None


class _RuntimeLimitsConfigPayload(ConfigPayload):
    max_iterations: int = 20
    max_recovery_steps: int = 8


class _DomainConfigPayload(ConfigPayload):
    name: str | None = None
    version: str | None = None
    backend: str | None = None
    settings: dict[str, PydanticJsonValue] = Field(default_factory=dict)


class _ModelConfigPayload(ConfigPayload):
    provider: _ModelProviderPayload = ModelProvider.SCRIPTED
    name: str = "scripted"
    endpoint: str | None = None
    api_key_secret: str | None = None
    timeout_seconds: float = 30.0
    headers: dict[str, str] = Field(default_factory=dict)
    response_format: str | None = None


class _RuntimeConfigPayload(ConfigPayload):
    environment: dict[str, PydanticJsonValue] = Field(default_factory=dict)
    secrets: dict[str, dict[str, PydanticJsonValue]] | None = None
    model: dict[str, PydanticJsonValue] = Field(default_factory=dict)
    store: dict[str, PydanticJsonValue] = Field(default_factory=dict)
    distributed_queue: dict[str, PydanticJsonValue] = Field(default_factory=dict)
    distributed_locks: dict[str, PydanticJsonValue] = Field(default_factory=dict)
    distributed_workers: dict[str, PydanticJsonValue] = Field(default_factory=dict)
    distributed_terminal_retention_seconds: float | None = None
    limits: dict[str, PydanticJsonValue] = Field(default_factory=dict)
    domain: dict[str, PydanticJsonValue] = Field(default_factory=dict)
    domains: list[dict[str, PydanticJsonValue]] | None = None


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
    def file(cls, name: str, path: str, *, required: bool = True) -> SecretRef:
        return cls(name, SecretSource.FILE, path, required)

    @classmethod
    def from_mapping(cls, name: str, values: Mapping[str, JsonValue]) -> SecretRef:
        payload = parse_payload(_SecretRefPayload, values)
        ref = cls(
            name=name,
            source=payload.source,
            key=payload.key,
            required=payload.required,
        )
        ref.validate()
        return ref

    def validate(self) -> None:
        parse_non_empty_string(self.name, "secret name")
        parse_non_empty_string(self.key, f"secret {self.name} key")


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
        payload = parse_payload(_StoreConfigPayload, values)
        config = cls(payload.backend, payload.path)
        config.validate()
        return config

    def validate(self) -> None:
        if self.backend is StoreBackend.FILE:
            if self.path is None:
                raise ValueError("file store requires path")
            parse_non_empty_string(
                self.path,
                "file store path",
                empty_template="file store requires path",
            )
        if self.backend is StoreBackend.SQLITE:
            if self.path is None:
                raise ValueError("sqlite store requires path")
            parse_non_empty_string(
                self.path,
                "sqlite store path",
                empty_template="sqlite store requires path",
            )
        if self.backend is StoreBackend.MEMORY and self.path is not None:
            raise ValueError("memory store does not accept path")


@dataclass(frozen=True, slots=True)
class RuntimeLimitsConfig:
    max_iterations: int = 20
    max_recovery_steps: int = 8

    @classmethod
    def from_mapping(cls, values: Mapping[str, JsonValue]) -> RuntimeLimitsConfig:
        payload = parse_payload(_RuntimeLimitsConfigPayload, values)
        config = cls(
            max_iterations=payload.max_iterations,
            max_recovery_steps=payload.max_recovery_steps,
        )
        config.validate()
        return config

    def validate(self) -> None:
        parse_positive_int(self.max_iterations, "max_iterations")
        parse_positive_int(self.max_recovery_steps, "max_recovery_steps")


@dataclass(frozen=True, slots=True)
class DomainConfig:
    name: str | None = None
    version: str | None = None
    backend: str | None = None
    settings: JsonMapping = field(default_factory=immutable_json)

    @classmethod
    def from_mapping(cls, values: Mapping[str, JsonValue]) -> DomainConfig:
        payload = parse_payload(_DomainConfigPayload, values)
        config = cls(
            name=payload.name,
            version=payload.version,
            backend=payload.backend,
            settings=immutable_json(json_mapping(payload.settings)),
        )
        config.validate()
        return config

    def validate(self) -> None:
        parse_optional_non_empty_string(self.name, "domain name")
        parse_optional_non_empty_string(self.version, "domain version")
        parse_optional_non_empty_string(self.backend, "domain backend")

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
    response_format: str | None = None

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
            None,
        )

    @classmethod
    def openai_responses(
        cls,
        *,
        name: str,
        api_key_secret: str,
        endpoint: str | None = None,
        timeout_seconds: float = 30.0,
        headers: Mapping[str, str] | None = None,
    ) -> ModelConfig:
        return cls(
            ModelProvider.OPENAI_RESPONSES,
            name,
            endpoint,
            api_key_secret,
            timeout_seconds,
            immutable_json(dict(headers or {})),
            None,
        )

    @classmethod
    def openai_chat_completions(
        cls,
        *,
        name: str,
        api_key_secret: str,
        endpoint: str | None = None,
        timeout_seconds: float = 30.0,
        headers: Mapping[str, str] | None = None,
        response_format: str | None = None,
    ) -> ModelConfig:
        return cls(
            ModelProvider.OPENAI_CHAT_COMPLETIONS,
            name,
            endpoint,
            api_key_secret,
            timeout_seconds,
            immutable_json(dict(headers or {})),
            response_format,
        )

    @classmethod
    def from_mapping(cls, values: Mapping[str, JsonValue]) -> ModelConfig:
        payload = parse_payload(_ModelConfigPayload, values)
        config = cls(
            provider=payload.provider,
            name=payload.name,
            endpoint=payload.endpoint,
            api_key_secret=payload.api_key_secret,
            timeout_seconds=payload.timeout_seconds,
            headers=immutable_json(payload.headers),
            response_format=payload.response_format,
        )
        config.validate()
        return config

    def validate(self) -> None:
        parse_non_empty_string(self.name, "model name")
        parse_positive_float(self.timeout_seconds, "model timeout_seconds")
        string_mapping(self.headers, "model headers")
        if self.provider is ModelProvider.SCRIPTED:
            if self.endpoint is not None:
                raise ValueError("scripted model does not accept endpoint")
            if self.api_key_secret is not None:
                raise ValueError("scripted model does not accept api_key_secret")
            if self.response_format is not None:
                raise ValueError("scripted model does not accept response_format")
            return
        if self.provider is ModelProvider.JSON_HTTP:
            if self.endpoint is None:
                raise ValueError("json_http model requires endpoint")
            parse_non_empty_string(
                self.endpoint,
                "json_http model endpoint",
                empty_template="json_http model requires endpoint",
            )
            parse_optional_non_empty_string(self.api_key_secret, "model api_key_secret")
            if self.response_format is not None:
                raise ValueError("json_http model does not accept response_format")
            return
        if self.provider is ModelProvider.OPENAI_RESPONSES:
            parse_optional_non_empty_string(
                self.endpoint,
                "openai_responses model endpoint",
            )
            if self.api_key_secret is None:
                raise ValueError("openai_responses model requires api_key_secret")
            parse_non_empty_string(
                self.api_key_secret,
                "openai_responses model api_key_secret",
                empty_template="openai_responses model requires api_key_secret",
            )
            if self.response_format is not None:
                raise ValueError("openai_responses model does not accept response_format")
            return
        if self.provider is ModelProvider.OPENAI_CHAT_COMPLETIONS:
            parse_optional_non_empty_string(
                self.endpoint,
                "openai_chat_completions model endpoint",
            )
            if self.api_key_secret is None:
                raise ValueError("openai_chat_completions model requires api_key_secret")
            parse_non_empty_string(
                self.api_key_secret,
                "openai_chat_completions model api_key_secret",
                empty_template="openai_chat_completions model requires api_key_secret",
            )
            if self.response_format not in {None, "json_schema", "json_object", "prompt_json"}:
                raise ValueError(
                    "openai_chat_completions response_format must be "
                    "json_schema, json_object, or prompt_json"
                )
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
        loaded = read_json_file(path)
        return cls.from_mapping(parse_json_object(loaded, "runtime config file"))

    @classmethod
    def from_mapping(cls, values: Mapping[str, JsonValue]) -> RuntimeConfig:
        payload = parse_payload(_RuntimeConfigPayload, values)
        domains = _domain_configs(payload.domains)
        domain = domains[0] if domains else DomainConfig.from_mapping(json_mapping(payload.domain))
        config = cls(
            environment=immutable_json(json_mapping(payload.environment)),
            secrets=_secret_refs(payload.secrets),
            model=ModelConfig.from_mapping(json_mapping(payload.model)),
            store=StoreConfig.from_mapping(json_mapping(payload.store)),
            distributed_queue=StoreConfig.from_mapping(json_mapping(payload.distributed_queue)),
            distributed_locks=StoreConfig.from_mapping(json_mapping(payload.distributed_locks)),
            distributed_workers=StoreConfig.from_mapping(json_mapping(payload.distributed_workers)),
            distributed_terminal_retention_seconds=payload.distributed_terminal_retention_seconds,
            limits=RuntimeLimitsConfig.from_mapping(json_mapping(payload.limits)),
            domain=domain,
            domains=domains,
        )
        config.validate()
        return config

    def validate(self) -> None:
        for secret in self.secrets:
            secret.validate()
        duplicate_secrets = duplicate_values(secret.name for secret in self.secrets)
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
        parse_optional_positive_float(
            self.distributed_terminal_retention_seconds,
            "distributed_terminal_retention_seconds",
        )
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


def _secret_refs(
    value: Mapping[str, Mapping[str, PydanticJsonValue]] | None,
) -> tuple[SecretRef, ...]:
    if value is None:
        return ()
    return tuple(
        SecretRef.from_mapping(name, json_mapping(body)) for name, body in sorted(value.items())
    )


def _domain_configs(value: list[dict[str, PydanticJsonValue]] | None) -> tuple[DomainConfig, ...]:
    if value is None:
        return ()
    return tuple(DomainConfig.from_mapping(json_mapping(item)) for item in value)


def _duplicate_domain_configs(domains: tuple[DomainConfig, ...]) -> tuple[str, ...]:
    return duplicate_values(f"{domain.name or ''}@{domain.version or ''}" for domain in domains)
