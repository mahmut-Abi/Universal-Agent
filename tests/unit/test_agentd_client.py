from __future__ import annotations

from collections.abc import Mapping

import httpx
import pytest

from universal_agent.core import JsonValue, SessionId, immutable_json
from universal_agent_api import AgentdClient, AgentdClientError


@pytest.mark.asyncio
@pytest.mark.contract
async def test_agentd_client_gets_json_with_bearer_token_and_query() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"status": "ok", "service": "runtime"},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agentd = AgentdClient("http://agentd.example.test/", bearer_token="secret-token", client=client)

    try:
        body = await agentd.get_json("/health", query={"wait": True, "limit": 2, "empty": None})
    finally:
        await client.aclose()

    assert body == {"status": "ok", "service": "runtime"}
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "http://agentd.example.test/health?wait=true&limit=2"
    assert request.headers["authorization"] == "Bearer secret-token"
    assert request.headers["accept"] == "application/json"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_agentd_client_uses_httpx_query_params_for_encoding() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "ok"}, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agentd = AgentdClient("http://agentd.example.test", client=client)

    try:
        await agentd.get_json(
            "v1/domain-packages",
            query={"tag": "ops team/a", "enabled": False, "limit": 10, "empty": None},
        )
    finally:
        await client.aclose()

    assert len(requests) == 1
    assert (
        str(requests[0].url)
        == "http://agentd.example.test/v1/domain-packages?tag=ops+team%2Fa&enabled=false&limit=10"
    )


@pytest.mark.asyncio
@pytest.mark.contract
async def test_agentd_client_posts_json() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"result": {"status": "completed"}}, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agentd = AgentdClient("http://agentd.example.test", client=client)

    try:
        body = await agentd.post_json("/v1/sessions", body=goal_submission_body())
    finally:
        await client.aclose()

    assert body == {"result": {"status": "completed"}}
    assert requests[0].headers["content-type"] == "application/json"
    assert json_loads(requests[0].content) == dict(goal_submission_body())


@pytest.mark.asyncio
@pytest.mark.unit
async def test_agentd_client_maps_structured_http_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"code": "unauthorized", "message": "authentication required"}},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agentd = AgentdClient("http://agentd.example.test", client=client)

    try:
        with pytest.raises(AgentdClientError) as raised:
            await agentd.get_json("/v1/config")
    finally:
        await client.aclose()

    assert raised.value.status_code == 401
    assert raised.value.code == "unauthorized"
    assert str(raised.value) == "agentd returned HTTP 401 unauthorized: authentication required"


@pytest.mark.asyncio
@pytest.mark.contract
async def test_agentd_client_rejects_invalid_json_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agentd = AgentdClient("http://agentd.example.test", client=client)

    try:
        with pytest.raises(AgentdClientError, match="invalid JSON"):
            await agentd.get_json("/health")
    finally:
        await client.aclose()


def goal_submission_body() -> Mapping[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "goal": {
            "description": "Verify",
            "success_criteria": [{"key": "healthy", "expected": True}],
        },
        "task": {"description": "Inspect", "required_criteria": ["healthy"]},
    }
    return immutable_json(body)


def json_loads(content: bytes) -> object:
    import json

    return json.loads(content.decode("utf-8"))


@pytest.mark.asyncio
@pytest.mark.contract
async def test_agentd_client_stream_events_parses_sse_frames() -> None:
    sse_body = (
        "id: ev-1\n"
        "event: ActionCompleted\n"
        'data: {"event_id": "ev-1"}\n'
        "\n"
        ": heartbeat\n"
        "\n"
        "event: Broken\n"
        "data: not-json\n"
        "\n"
        'data: {"event_id": "ev-2"}\n'
        "\n"
    )
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=sse_body.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agentd = AgentdClient("http://agentd.example.test", client=client)
    try:
        events = [event async for event in agentd.stream_events(SessionId("s-1"))]
    finally:
        await client.aclose()

    assert events == [{"event_id": "ev-1"}, {"event_id": "ev-2"}]
    assert requests[0].url.path == "/v1/sessions/s-1/events/stream"
    assert requests[0].headers["accept"] == "text/event-stream"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_agentd_client_stream_events_raises_on_http_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"error": {"code": "not_found", "message": "unknown session"}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agentd = AgentdClient("http://agentd.example.test", client=client)
    try:
        with pytest.raises(AgentdClientError) as exc_info:
            async for _event in agentd.stream_events(SessionId("s-missing")):
                pass
    finally:
        await client.aclose()

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "not_found"
