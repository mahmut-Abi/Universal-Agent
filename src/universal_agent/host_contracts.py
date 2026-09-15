"""Host-surface contribution contracts (extension points).

Domains contribute CLI commands, agentd HTTP routes, default services and
evaluation suites without hosts importing them and without domains importing
host adapters: the contracts live in the kernel and are discovered through
entry-point groups (AGENTS.md §11). Dependency direction:

    domain -> kernel contracts <- host (CLI / agentd)
"""

from __future__ import annotations

import argparse
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from universal_agent.core import JsonMapping, JsonValue, immutable_json

if TYPE_CHECKING:
    # Type-only: importing the runtime service stack at module scope would
    # weight down the CLI import graph (see test_cli_startup_weight.py).
    from universal_agent.service import RuntimeService

CLI_CONTRIBUTIONS_ENTRY_POINT_GROUP = "universal_agent.cli_contributions"
AGENTD_ROUTES_ENTRY_POINT_GROUP = "universal_agent.agentd_routes"


# ---------------------------------------------------------------------------
# agentd HTTP route contributions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DomainRouteDefinition:
    """One contributed HTTP route (agentd-agnostic path template)."""

    name: str
    template: str
    methods: tuple[str, ...] = ("GET",)


@dataclass(frozen=True, slots=True)
class DomainRouteResponse:
    """HTTP-shaped response returned by a domain route handler."""

    status_code: int
    body: JsonMapping
    headers: Mapping[str, str] = field(default_factory=dict)


def domain_json_response(body: JsonMapping, *, status_code: int = 200) -> DomainRouteResponse:
    return DomainRouteResponse(status_code=status_code, body=body)


def domain_bad_request(message: str) -> DomainRouteResponse:
    return DomainRouteResponse(
        status_code=400,
        body=immutable_json({"error": {"code": "bad_request", "message": message}}),
    )


def domain_method_not_allowed(allowed: tuple[str, ...]) -> DomainRouteResponse:
    message = "method is not allowed for this route"
    return DomainRouteResponse(
        status_code=405,
        body=immutable_json({"error": {"code": "method_not_allowed", "message": message}}),
        headers={"content-type": "application/json", "allow": ", ".join(allowed)},
    )


def match_domain_route(
    definitions: tuple[DomainRouteDefinition, ...],
    method: str,
    path: str,
) -> tuple[DomainRouteDefinition, bool] | None:
    """Match ``path`` against ``definitions`` (``{param}`` segment templates).

    Returns ``(definition, method_allowed)`` or ``None`` when no template
    matches the path shape.
    """

    for definition in definitions:
        template_segments = definition.template.strip("/").split("/")
        path_segments = path.strip("/").split("/")
        if len(template_segments) != len(path_segments):
            continue
        if all(
            segment.startswith("{") or segment == path_segment
            for segment, path_segment in zip(template_segments, path_segments, strict=True)
        ):
            return definition, method.upper() in definition.methods
    return None


DomainRouteHandler = Callable[
    ["RuntimeService", str, str, JsonMapping],
    Awaitable[DomainRouteResponse | None],
]
"""Handler signature: ``(service, method, path, body)`` -> response or None."""


@dataclass(frozen=True)
class DomainRouteContribution:
    """One domain's contribution to the agentd HTTP surface."""

    domain: str
    route_definitions: tuple[DomainRouteDefinition, ...]
    handle: DomainRouteHandler
    # route name -> (summary, description, tag)
    openapi_metadata: Mapping[str, tuple[str, str, str]] = field(default_factory=dict)
    # (tag name, description) appended to the OpenAPI tags list.
    openapi_tags: tuple[tuple[str, str], ...] = ()


def load_route_contributions() -> tuple[DomainRouteContribution, ...]:
    """Discover domain route contributions via entry points."""

    contributions: list[DomainRouteContribution] = []
    for entry_point in entry_points(group=AGENTD_ROUTES_ENTRY_POINT_GROUP):
        factory = entry_point.load()
        contribution = factory()
        if isinstance(contribution, DomainRouteContribution):
            contributions.append(contribution)
    return tuple(sorted(contributions, key=lambda item: item.domain))


# ---------------------------------------------------------------------------
# CLI command contributions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandOutcome:
    """Result of a domain-owned command dispatch."""

    payload: object
    status: int = 0


@dataclass(frozen=True)
class InitDomainOutcome:
    """Domain config + secrets contributed by a domain for `agent init`."""

    domain_name: str
    domain_config: dict[str, object]
    secrets: Mapping[str, Mapping[str, object]] = field(default_factory=dict)


@runtime_checkable
class RemoteAgentdClient(Protocol):
    """Structural view of the agentd thin-client used by remote dispatchers."""

    async def post_json(
        self,
        path: str,
        *,
        body: Mapping[str, JsonValue] | None = None,
    ) -> JsonMapping: ...


# Embedded dispatch: run the domain command against an in-process service.
EmbeddedDispatch = Callable[[argparse.Namespace, "RuntimeService"], Awaitable[CommandOutcome]]
# Remote thin-client dispatch: forward the command to a running agentd API.
RemoteDispatch = Callable[[argparse.Namespace, RemoteAgentdClient], Awaitable[CommandOutcome]]


@dataclass(frozen=True)
class CliDomainContribution:
    """One domain's contribution to the CLI surface.

    All hooks are optional; a domain contributes only what it owns.
    """

    domain: str
    # Advanced command (e.g. a domain operator command).
    command_name: str | None = None
    add_command: Callable[[argparse._SubParsersAction[argparse.ArgumentParser]], None] | None = None
    dispatch: EmbeddedDispatch | None = None
    remote_dispatch: RemoteDispatch | None = None
    # Long-running command: agentd client requests default to the 900s timeout.
    long_running_command: bool = False
    # Embedded serve wiring.
    is_probe_service_command: Callable[[argparse.Namespace], bool] | None = None
    # When True, dispatching this command embedded (no --profile-config)
    # starts the agentd subprocess with this domain's default service.
    uses_embedded_default_service: bool = False
    # Probe-style service builder: profile config path -> RuntimeService that
    # never requires the configured model to connect.
    build_probe_service: Callable[[str], RuntimeService] | None = None
    # `agent init` integration.
    init_backends: tuple[str, ...] = ()
    init_add_arguments: Callable[[argparse._ArgumentGroup], None] | None = None
    init_resolve_domain: Callable[[argparse.Namespace], InitDomainOutcome | None] | None = None
    # Profile name this domain considers its local/operator default.
    local_profile_name: str | None = None


def load_cli_contributions() -> tuple[CliDomainContribution, ...]:
    """Discover domain CLI contributions via entry points."""

    contributions: list[CliDomainContribution] = []
    for entry_point in entry_points(group=CLI_CONTRIBUTIONS_ENTRY_POINT_GROUP):
        factory = entry_point.load()
        contribution = factory()
        if isinstance(contribution, CliDomainContribution):
            contributions.append(contribution)
    return tuple(sorted(contributions, key=lambda item: item.domain))


def single_secret_source(
    label: str,
    *,
    env_key: str | None,
    file_path: str | None,
) -> tuple[str, str] | None:
    """Resolve one secret source (env var or file) for `agent init`.

    Shared generic helper so domain contributions collect secrets with the
    same semantics as the kernel-owned init options.
    """

    if env_key is not None and file_path is not None:
        raise ValueError(f"{label} accepts either env or file, not both")
    if env_key is not None:
        return ("env", env_key)
    if file_path is not None:
        return ("file", file_path)
    return None


def add_secret(
    secrets: dict[str, dict[str, object]],
    name: str,
    source: tuple[str, str],
) -> None:
    """Register one named secret (source kind + key) for `agent init`."""

    source_name, key = source
    if not name.strip():
        raise ValueError("secret name must not be empty")
    if not key.strip():
        raise ValueError(f"secret {name} {source_name} key must not be empty")
    if name in secrets:
        raise ValueError(f"duplicate runtime secret: {name}")
    secrets[name] = {"source": source_name, "key": key, "required": True}
