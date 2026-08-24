from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import NewType, cast

from universal_agent.core import DomainIdentity, JsonMapping, SessionId
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
    return AgentProfileRecord(
        name=_string(payload.get("name"), "profile.name"),
        version=_string(payload.get("version"), "profile.version"),
        domains=tuple(
            _decode_domain_identity(item)
            for item in _mapping_list(payload.get("domains"), "profile.domains")
        ),
        permissions=_string_tuple(payload.get("permissions"), "profile.permissions"),
        capabilities=_string_tuple(payload.get("capabilities"), "profile.capabilities"),
        description=_string(payload.get("description"), "profile.description", ""),
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
    return AgentInstanceRecord(
        agent_id=AgentId(_string(payload.get("agent_id"), "instance.agent_id")),
        profile_name=_string(payload.get("profile_name"), "instance.profile_name"),
        profile_version=_string(payload.get("profile_version"), "instance.profile_version"),
        status=_instance_status(payload.get("status")),
        session_id=_optional_session_id(payload.get("session_id")),
        endpoint=_optional_string(payload.get("endpoint"), "instance.endpoint"),
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
    return AgentRegistrySnapshot(
        profiles=tuple(
            decode_agent_profile_record(item)
            for item in _mapping_list(payload.get("profiles"), "profiles")
        ),
        instances=tuple(
            decode_agent_instance_record(item)
            for item in _mapping_list(payload.get("instances"), "instances")
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


def _decode_domain_identity(payload: JsonMapping) -> DomainIdentity:
    return DomainIdentity(
        _string(payload.get("name"), "domain.name"),
        _string(payload.get("version"), "domain.version"),
    )


def _mapping(value: object, field_name: str) -> JsonMapping:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} keys must be strings")
    return cast(JsonMapping, value)


def _mapping_list(value: object, field_name: str) -> tuple[JsonMapping, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return tuple(_mapping(item, f"{field_name}[{index}]") for index, item in enumerate(value))


def _string(value: object, field_name: str, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name)


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    strings: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{field_name}[{index}] must be a string")
        strings.append(item)
    return tuple(strings)


def _optional_session_id(value: object) -> SessionId | None:
    raw = _optional_string(value, "instance.session_id")
    if raw is None:
        return None
    return SessionId(raw)


def _instance_status(value: object) -> AgentInstanceStatus:
    raw = _string(value, "instance.status", AgentInstanceStatus.READY.value)
    try:
        return AgentInstanceStatus(raw)
    except ValueError as exc:
        raise ValueError(f"unsupported agent instance status: {raw}") from exc
