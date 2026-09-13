"""Tests for long-run client timeouts in the agentd API client and CLI dispatch."""

from __future__ import annotations

import httpx
import pytest

from universal_agent_api import AgentdClient, AgentdClientError


class TimeoutTransport(httpx.AsyncBaseTransport):
    """httpx transport that raises a timeout like a hung server would."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.timeouts: list[float | None] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.timeouts.append(request.extensions.get("timeout"))
        raise httpx.ReadTimeout("timed out", request=request)


@pytest.mark.asyncio
async def test_request_timeout_raises_typed_error_with_guidance() -> None:
    transport = TimeoutTransport()
    client = AgentdClient("http://agentd.test", transport=transport)

    with pytest.raises(AgentdClientError) as excinfo:
        await client.post_json("/v1/sessions", body={"goal": {"description": "x"}})

    error = excinfo.value
    assert error.timed_out is True
    assert "timed out" in str(error)
    assert "session" in str(error)


@pytest.mark.asyncio
async def test_connection_error_is_not_marked_as_timeout() -> None:
    class RefusedTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

    client = AgentdClient("http://agentd.test", transport=RefusedTransport())

    with pytest.raises(AgentdClientError) as excinfo:
        await client.get_json("/v1/sessions")

    assert excinfo.value.timed_out is False


@pytest.mark.asyncio
async def test_per_request_timeout_overrides_client_default() -> None:
    transport = TimeoutTransport()
    client = AgentdClient("http://agentd.test", timeout_seconds=30.0, transport=transport)

    with pytest.raises(AgentdClientError):
        await client.post_json("/v1/sessions", body={"goal": {"description": "x"}})

    # The request must carry the explicit per-request timeout in its httpx
    # timeout extension, not a client-level default.
    extension = transport.timeouts[0]
    assert isinstance(extension, dict)
    assert extension["read"] == 30.0


def test_cli_parser_accepts_api_timeout_seconds() -> None:
    from universal_agent_cli.parser import build_parser

    parser = build_parser()
    args = parser.parse_args(
        [
            "--api-url",
            "http://agentd.test",
            "--api-timeout-seconds",
            "900",
            "run",
            "hello",
        ]
    )

    assert args.api_timeout_seconds == 900.0


def test_cli_parser_api_timeout_defaults_to_unset() -> None:
    from universal_agent_cli.parser import build_parser

    parser = build_parser()
    args = parser.parse_args(["run", "hello"])

    assert args.api_timeout_seconds is None


def test_long_run_commands_get_elevated_default_timeout() -> None:
    from argparse import Namespace

    from universal_agent_cli.remote.client import _client_timeout_seconds

    args = Namespace(api_timeout_seconds=None, command="kubernetes")

    assert _client_timeout_seconds(args) == 900.0


def test_client_timeout_flag_overrides_elevated_default() -> None:
    from argparse import Namespace

    from universal_agent_cli.remote.client import _client_timeout_seconds

    args = Namespace(api_timeout_seconds=120.0, command="kubernetes")

    assert _client_timeout_seconds(args) == 120.0
