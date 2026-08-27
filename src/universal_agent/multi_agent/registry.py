from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import NewType

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError

from universal_agent.core import DomainIdentity, JsonMapping, SessionId
from universal_agent.core.config_validation import ConfigPayload, pydantic_error_details
from universal_agent.multi_agent.contracts import AgentTaskRequest

AgentId = NewType("AgentId", str)


class AgentRegistryError(ValueError):
    pass


class AgentProfileNotRegisteredError(LookupError):
    pass


class AgentInstanceNotRegisteredError(LookupError):
    pass


class AgentInstanceStatus(StrEnum):
    READY = "ready"
    BUSY = "busy"
    OFFLINE = "offline"
    DRAINING = "draining"


class _DomainIdentityPayload(ConfigPayload):
    name: str
    version: str


class _AgentProfileRecordPayload(ConfigPayload):
    name: str
    version: str
    domains: list[_DomainIdentityPayload] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    description: str = ""


class _AgentInstanceRecordPayload(ConfigPayload):
    agent_id: str
    profile_name: str
    profile_version: str
    status: str = AgentInstanceStatus.READY.value
    session_id: str | None = None
    endpoint: str | None = None


class _AgentRegistrySnapshotPayload(ConfigPayload):
    profiles: list[_AgentProfileRecordPayload] = Field(default_factory=list)
    instances: list[_AgentInstanceRecordPayload] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AgentProfileRecord:
    name: str
    version: str
    domains: tuple[DomainIdentity, ...]
    permissions: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("agent profile name must not be empty")
        if not self.version.strip():
            raise ValueError("agent profile version must not be empty")
        if not self.domains:
            raise ValueError("agent profile domains must not be empty")
        _reject_empty_items(self.permissions, "permissions")
        _reject_empty_items(self.capabilities, "capabilities")

    @property
    def identity(self) -> tuple[str, str]:
        return (self.name, self.version)


@dataclass(frozen=True, slots=True)
class AgentInstanceRecord:
    agent_id: AgentId
    profile_name: str
    profile_version: str
    status: AgentInstanceStatus = AgentInstanceStatus.READY
    session_id: SessionId | None = None
    endpoint: str | None = None

    def __post_init__(self) -> None:
        if not str(self.agent_id).strip():
            raise ValueError("agent instance id must not be empty")
        if not self.profile_name.strip():
            raise ValueError("agent instance profile_name must not be empty")
        if not self.profile_version.strip():
            raise ValueError("agent instance profile_version must not be empty")
        if self.endpoint is not None and not self.endpoint.strip():
            raise ValueError("agent instance endpoint must not be empty")

    @property
    def profile_identity(self) -> tuple[str, str]:
        return (self.profile_name, self.profile_version)


@dataclass(frozen=True, slots=True)
class AgentRegistrySnapshot:
    profiles: tuple[AgentProfileRecord, ...]
    instances: tuple[AgentInstanceRecord, ...]


def agent_profile_record_payload(profile: AgentProfileRecord) -> JsonMapping:
    return MappingProxyType(
        {
            "name": profile.name,
            "version": profile.version,
            "domains": [
                {"name": domain.name, "version": domain.version} for domain in profile.domains
            ],
            "permissions": list(profile.permissions),
            "capabilities": list(profile.capabilities),
            "description": profile.description,
        }
    )


def decode_agent_profile_record(payload: JsonMapping) -> AgentProfileRecord:
    parsed = _parse_registry_payload(_AgentProfileRecordPayload, payload, prefix="profile")
    return AgentProfileRecord(
        name=parsed.name,
        version=parsed.version,
        domains=tuple(DomainIdentity(item.name, item.version) for item in parsed.domains),
        permissions=tuple(parsed.permissions),
        capabilities=tuple(parsed.capabilities),
        description=parsed.description,
    )


def agent_instance_record_payload(instance: AgentInstanceRecord) -> JsonMapping:
    return MappingProxyType(
        {
            "agent_id": str(instance.agent_id),
            "profile_name": instance.profile_name,
            "profile_version": instance.profile_version,
            "status": instance.status.value,
            "session_id": None if instance.session_id is None else str(instance.session_id),
            "endpoint": instance.endpoint,
        }
    )


def decode_agent_instance_record(payload: JsonMapping) -> AgentInstanceRecord:
    parsed = _parse_registry_payload(_AgentInstanceRecordPayload, payload, prefix="instance")
    return AgentInstanceRecord(
        agent_id=AgentId(parsed.agent_id),
        profile_name=parsed.profile_name,
        profile_version=parsed.profile_version,
        status=_instance_status(parsed.status),
        session_id=_optional_session_id(parsed.session_id),
        endpoint=parsed.endpoint,
    )


def agent_registry_snapshot_payload(snapshot: AgentRegistrySnapshot) -> JsonMapping:
    return MappingProxyType(
        {
            "profiles": [
                dict(agent_profile_record_payload(profile)) for profile in snapshot.profiles
            ],
            "instances": [
                dict(agent_instance_record_payload(instance)) for instance in snapshot.instances
            ],
        }
    )


def decode_agent_registry_snapshot(payload: JsonMapping) -> AgentRegistrySnapshot:
    parsed = _parse_registry_payload(_AgentRegistrySnapshotPayload, payload)
    return AgentRegistrySnapshot(
        profiles=tuple(
            AgentProfileRecord(
                name=item.name,
                version=item.version,
                domains=tuple(
                    DomainIdentity(domain.name, domain.version) for domain in item.domains
                ),
                permissions=tuple(item.permissions),
                capabilities=tuple(item.capabilities),
                description=item.description,
            )
            for item in parsed.profiles
        ),
        instances=tuple(
            AgentInstanceRecord(
                agent_id=AgentId(item.agent_id),
                profile_name=item.profile_name,
                profile_version=item.profile_version,
                status=_instance_status(item.status),
                session_id=_optional_session_id(item.session_id),
                endpoint=item.endpoint,
            )
            for item in parsed.instances
        ),
    )


def agent_registry_from_snapshot(snapshot: AgentRegistrySnapshot) -> AgentRegistry:
    return AgentRegistry(snapshot.profiles, snapshot.instances)


class AgentRegistry:
    """Registry for optional Multi-Agent profile templates and running instances."""

    def __init__(
        self,
        profiles: tuple[AgentProfileRecord, ...] = (),
        instances: tuple[AgentInstanceRecord, ...] = (),
    ) -> None:
        self._profiles: dict[tuple[str, str], AgentProfileRecord] = {}
        self._instances: dict[AgentId, AgentInstanceRecord] = {}
        for profile in profiles:
            self.register_profile(profile)
        for instance in instances:
            self.register_instance(instance)

    def register_profile(self, profile: AgentProfileRecord) -> None:
        if profile.identity in self._profiles:
            name, version = profile.identity
            raise AgentRegistryError(f"duplicate agent profile: {name}@{version}")
        self._profiles[profile.identity] = profile

    def register_instance(self, instance: AgentInstanceRecord) -> None:
        if instance.agent_id in self._instances:
            raise AgentRegistryError(f"duplicate agent instance: {instance.agent_id}")
        if instance.profile_identity not in self._profiles:
            name, version = instance.profile_identity
            raise AgentProfileNotRegisteredError(
                f"agent instance profile not registered: {name}@{version}"
            )
        self._instances[instance.agent_id] = instance

    def profile(self, name: str, version: str) -> AgentProfileRecord:
        try:
            return self._profiles[(name, version)]
        except KeyError as exc:
            raise AgentProfileNotRegisteredError(
                f"agent profile not registered: {name}@{version}"
            ) from exc

    def instance(self, agent_id: AgentId) -> AgentInstanceRecord:
        try:
            return self._instances[agent_id]
        except KeyError as exc:
            raise AgentInstanceNotRegisteredError(
                f"agent instance not registered: {agent_id}"
            ) from exc

    def update_instance_status(
        self,
        agent_id: AgentId,
        status: AgentInstanceStatus,
    ) -> AgentInstanceRecord:
        instance = self.instance(agent_id)
        updated = replace(instance, status=status)
        self._instances[agent_id] = updated
        return updated

    def all_profiles(self) -> tuple[AgentProfileRecord, ...]:
        return tuple(sorted(self._profiles.values(), key=lambda item: item.identity))

    def all_instances(self) -> tuple[AgentInstanceRecord, ...]:
        return tuple(sorted(self._instances.values(), key=lambda item: str(item.agent_id)))

    def eligible_instances(self, request: AgentTaskRequest) -> tuple[AgentInstanceRecord, ...]:
        return tuple(
            instance
            for instance in self.all_instances()
            if instance.status is AgentInstanceStatus.READY
            and self.profile_eligible(
                self.profile(instance.profile_name, instance.profile_version), request
            )
        )

    def profile_eligible(self, profile: AgentProfileRecord, request: AgentTaskRequest) -> bool:
        constraints = request.constraints
        if constraints.allowed_profiles and profile.name not in constraints.allowed_profiles:
            return False
        permissions = set(profile.permissions)
        if constraints.read_only and "read_only" not in permissions:
            return False
        return set(constraints.required_permissions).issubset(permissions)

    def snapshot(self) -> AgentRegistrySnapshot:
        return AgentRegistrySnapshot(self.all_profiles(), self.all_instances())


def _reject_empty_items(values: tuple[str, ...], field_name: str) -> None:
    for index, value in enumerate(values):
        if not value.strip():
            raise ValueError(f"agent profile {field_name}[{index}] must not be empty")


def _optional_session_id(value: str | None) -> SessionId | None:
    if value is None:
        return None
    return SessionId(value)


def _instance_status(raw: str) -> AgentInstanceStatus:
    try:
        return AgentInstanceStatus(raw)
    except ValueError as exc:
        raise ValueError(f"unsupported agent instance status: {raw}") from exc


def _parse_registry_payload[T: ConfigPayload](
    payload_type: type[T],
    payload: Mapping[str, object],
    *,
    prefix: str | None = None,
) -> T:
    try:
        return payload_type.model_validate(dict(payload))
    except PydanticValidationError as exc:
        raise ValueError(_registry_payload_error_message(exc, prefix=prefix)) from exc


def _registry_payload_error_message(
    error: PydanticValidationError,
    *,
    prefix: str | None,
) -> str:
    details = pydantic_error_details(error, prefix)
    path = details.path
    error_type = details.error_type
    if not error_type:
        return details.message
    expected = _expected_registry_type(error_type, path)
    if expected is not None:
        return f"{path} must be {expected}"
    if details.message:
        return details.message.removeprefix("Value error, ")
    return str(error)


def _expected_registry_type(error_type: str, path: str) -> str | None:
    if error_type == "missing":
        return _missing_registry_field_type(path)
    return {
        "dict_type": "an object",
        "list_type": "a list",
        "model_attributes_type": "an object",
        "model_type": "an object",
        "string_type": "a string",
    }.get(error_type)


def _missing_registry_field_type(path: str) -> str:
    if path in {
        "profile.domains",
        "profile.permissions",
        "profile.capabilities",
        "profiles",
        "instances",
    }:
        return "a list"
    return "a string"
