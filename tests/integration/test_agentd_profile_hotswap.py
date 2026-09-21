"""Profile hot-swap service registry over the agentd request handler.

Covers the X-Profile routing plane: lazy per-profile service construction,
the bad_request fallback when hot-swap is not configured, and the 409 guard
that refuses profile mutations while the profile's service has active
sessions (AGENTS.md §4.9: agents are autonomous execution boundaries).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from universal_agent.agentd import AgentdApp
from universal_agent.agentd.app import _ServiceBundle
from universal_agent.agentd.http import HttpRequest
from universal_agent.core import (
    ErrorCode,
    GoalId,
    GoalStatus,
    JsonValue,
    SessionId,
    TaskId,
    TaskStatus,
    immutable_json,
)
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.local import LocalDomain
from universal_agent.model import ScriptedModelAdapter
from universal_agent.profile.store import ProfileStore
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
from universal_agent.runtime.api import SessionSummaryView
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore

pytestmark = pytest.mark.integration


def _build_service() -> RuntimeService:
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(DomainLoader().load(LocalDomain()))
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(()),
        state_store=store,
        components=components,
        event_sink=events,
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )


def build_app(tmp_path: Any, *, factory_enabled: bool = True) -> AgentdApp:
    profile_store = ProfileStore(tmp_path / "profiles")
    return AgentdApp(
        _build_service(),
        profile_store=profile_store,
        profile_service_factory=(lambda name: _build_service()) if factory_enabled else None,
    )


def _request(
    method: str,
    path: str,
    body: Mapping[str, JsonValue] | None = None,
    *,
    profile: str | None = None,
) -> HttpRequest:
    headers: dict[str, str] = {}
    if profile is not None:
        headers["x-profile"] = profile
    return HttpRequest(
        method,
        path,
        immutable_json(dict(body)) if body else {},
        headers=headers,
    )


@pytest.mark.asyncio
async def test_x_profile_without_factory_returns_bad_request(tmp_path: Any) -> None:
    app = build_app(tmp_path, factory_enabled=False)

    response = await app.handle(_request("GET", "/v1/domains", profile="workspace"))

    assert response.status_code == 400
    assert "hot-swap is not configured" in str(response.body)


@pytest.mark.asyncio
async def test_x_profile_builds_and_caches_profile_service(tmp_path: Any) -> None:
    app = build_app(tmp_path)

    default_domains = await app.handle(_request("GET", "/v1/domains"))
    profile_domains = await app.handle(_request("GET", "/v1/domains", profile="hot-swap"))

    assert default_domains.status_code == 200
    assert profile_domains.status_code == 200
    # A separate service instance was built and cached for the profile.
    assert "hot-swap" in app._profile_bundles
    assert app._profile_bundles["hot-swap"].service is not app.service
    # Second request reuses the cached bundle.
    bundle = app._profile_bundles["hot-swap"]
    await app.handle(_request("GET", "/v1/domains", profile="hot-swap"))
    assert app._profile_bundles["hot-swap"] is bundle


@pytest.mark.asyncio
async def test_profile_mutation_rejected_with_409_while_session_active(tmp_path: Any) -> None:
    app = build_app(tmp_path)
    profile_store: ProfileStore = app._profile_store  # type: ignore[assignment]
    profile_store.create(
        immutable_json(
            {
                "name": "hot-swap",
                "version": "1.0.0",
                "description": "Hot-swapped profile",
                "domain": {"name": "local", "version": "0.1.0"},
            }
        )
    )
    # Pre-warm the bundle and simulate an active session.
    await app.handle(_request("GET", "/v1/domains", profile="hot-swap"))
    active = _ServiceBundle.build(cast(RuntimeService, _StubServiceWithActiveSession()))
    app._profile_bundles["hot-swap"] = active

    response = await app.handle(
        _request("PATCH", "/v1/profiles/hot-swap", {"description": "changed"})
    )

    assert response.status_code == 409
    error = cast(Mapping[str, object], response.body)["error"]
    assert cast(Mapping[str, object], error)["code"] == "profile_in_use"
    # The cached bundle survives a refused mutation.
    assert app._profile_bundles["hot-swap"] is active


@pytest.mark.asyncio
async def test_profile_mutation_invalidates_settled_bundle(tmp_path: Any) -> None:
    app = build_app(tmp_path)
    profile_store: ProfileStore = app._profile_store  # type: ignore[assignment]
    profile_store.create(
        immutable_json(
            {
                "name": "hot-swap",
                "version": "1.0.0",
                "description": "Hot-swapped profile",
                "domain": {"name": "local", "version": "0.1.0"},
            }
        )
    )
    await app.handle(_request("GET", "/v1/domains", profile="hot-swap"))
    app._profile_bundles["hot-swap"] = _ServiceBundle.build(
        cast(RuntimeService, _StubServiceSettled())
    )

    response = await app.handle(
        _request("PATCH", "/v1/profiles/hot-swap", {"description": "changed"})
    )

    assert response.status_code == 200
    assert "hot-swap" not in app._profile_bundles
    # The next request rebuilds a fresh bundle from the updated config.
    rebuilt = await app.handle(_request("GET", "/v1/domains", profile="hot-swap"))
    assert rebuilt.status_code == 200


_FIXED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _summary(goal_status: GoalStatus) -> SessionSummaryView:
    return SessionSummaryView(
        session_id=SessionId("s1"),
        goal_id=GoalId("g1"),
        goal_description="active",
        goal_status=goal_status,
        current_task_id=TaskId("t1"),
        current_task_description="running",
        current_task_status=TaskStatus.RUNNING,
        iteration=1,
        task_count=1,
        pending_action=False,
        termination_reason=None,
        error_code=ErrorCode.TOOL_FAILURE if goal_status is GoalStatus.RUNNING else None,
        tenant_id=None,
        domain_name="local",
        domain_version="0.1.0",
        created_at=_FIXED_AT,
    )


class _StubServiceWithActiveSession:
    async def list_sessions(self, **_: object) -> tuple[SessionSummaryView, ...]:
        return (_summary(GoalStatus.RUNNING),)


class _StubServiceSettled(_StubServiceWithActiveSession):
    async def list_sessions(self, **_: object) -> tuple[SessionSummaryView, ...]:
        return ()
