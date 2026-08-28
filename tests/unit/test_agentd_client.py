from __future__ import annotations

from collections.abc import Mapping

import httpx
import pytest

from universal_agent.agentd import AgentdClient, AgentdClientError
from universal_agent.core import JsonValue, immutable_json


@pytest.mark.asyncio
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
