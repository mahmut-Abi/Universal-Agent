from __future__ import annotations

from dataclasses import dataclass

from universal_agent.core import DomainIdentity
from universal_agent.domain.runtime import (
    ActiveDomain,
    DomainComposition,
    DomainLoader,
    DomainRuntime,
    DomainValidationError,
)


class DomainNotFoundError(LookupError):
    pass


class AmbiguousDomainError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DomainActivation:
    """Result of activating one ordered Domain composition."""

    composition: DomainComposition

    @property
    def primary(self) -> ActiveDomain:
        return self.composition.primary

    @property
    def identities(self) -> tuple[DomainIdentity, ...]:
        return self.composition.identities


class DomainManager:
    """Register and activate validated Domain runtimes.

    The manager is deliberately small: it owns discovery-time validation and
    deterministic composition selection, but it never executes actions. Runtime
    components are still built from the resulting DomainComposition.
    """

    def __init__(
        self,
        domains: tuple[DomainRuntime, ...] = (),
        *,
        loader: DomainLoader | None = None,
    ) -> None:
        self._loader = loader or DomainLoader()
        self._domains: dict[DomainIdentity, ActiveDomain] = {}
        self._order: list[DomainIdentity] = []
        for domain in domains:
            self.register(domain)

    def register(self, domain: DomainRuntime) -> ActiveDomain:
        active = self._loader.load(domain)
        identity = active.identity
        if identity in self._domains:
            raise DomainValidationError(f"domain already registered: {_format_identity(identity)}")
        self._domains[identity] = active
        self._order.append(identity)
        return active

    def registered(self) -> tuple[ActiveDomain, ...]:
        return tuple(self._domains[identity] for identity in self._order)

    def identities(self) -> tuple[DomainIdentity, ...]:
        return tuple(domain.identity for domain in self.registered())

    def get(self, identity: DomainIdentity) -> ActiveDomain:
        try:
            return self._domains[identity]
        except KeyError as exc:
            raise DomainNotFoundError(
                f"domain not registered: {_format_identity(identity)}"
            ) from exc

    def activate(
        self,
        identities: tuple[DomainIdentity, ...] | None = None,
    ) -> DomainActivation:
        selected = (
            self.registered()
            if identities is None
            else tuple(self.get(item) for item in identities)
        )
        return DomainActivation(DomainComposition(selected))

    def activate_by_name(self, names: tuple[str, ...]) -> DomainActivation:
        return self.activate(tuple(self._identity_for_name(name) for name in names))

    def _identity_for_name(self, name: str) -> DomainIdentity:
        matches = [identity for identity in self._domains if identity.name == name]
        if not matches:
            raise DomainNotFoundError(f"domain not registered: {name}")
        if len(matches) > 1:
            versions = ", ".join(sorted(identity.version for identity in matches))
            raise AmbiguousDomainError(
                f"domain {name} has multiple registered versions: {versions}"
            )
        return matches[0]


def _format_identity(identity: DomainIdentity) -> str:
    return f"{identity.name}@{identity.version}"
