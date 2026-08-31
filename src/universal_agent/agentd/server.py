from __future__ import annotations

import socket
from contextlib import suppress
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError
from starlette.applications import Starlette
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse
from starlette.responses import Response as StarletteResponse
from starlette.routing import Route
from uvicorn import Config, Server

from universal_agent.agentd.app import AgentdApp
from universal_agent.agentd.http import HttpRequest, HttpResponse
from universal_agent.core import JsonCodecError, JsonMapping, dumps_json, immutable_json, loads_json
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticNonEmptyString,
    parse_json_object,
    parse_non_negative_int_text,
    pydantic_error_details,
)


class _AgentdServerConfigPayload(ConfigPayload):
    host: PydanticNonEmptyString
    port: int = Field(ge=0)
    max_body_bytes: int = Field(ge=0)


class _OrjsonResponse(JSONResponse):
    def render(self, content: object) -> bytes:
        return dumps_json(content).encode("utf-8")


@dataclass(frozen=True, slots=True)
class AgentdServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    max_body_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        _validate_agentd_server_config(self)


def _validate_agentd_server_config(config: AgentdServerConfig) -> None:
    try:
        _AgentdServerConfigPayload.model_validate(
            {
                "host": config.host,
                "port": config.port,
                "max_body_bytes": config.max_body_bytes,
            }
        )
    except PydanticValidationError as exc:
        raise ValueError(_agentd_server_config_error_message(exc)) from exc


def _agentd_server_config_error_message(error: PydanticValidationError) -> str:
    details = pydantic_error_details(error)
    if details.path == "host":
        if details.error_type == "string_type":
            return "agentd host must be a string"
        if details.error_type == "value_error" and details.message.endswith("must not be empty"):
            return "agentd host must not be empty"
        return details.message.removeprefix("Value error, ")
    if details.path == "port":
        if details.error_type == "greater_than_equal":
            return "agentd port must be non-negative"
        return "agentd port must be an integer"
    if details.path == "max_body_bytes":
        if details.error_type == "greater_than_equal":
            return "agentd max_body_bytes must be non-negative"
        return "agentd max_body_bytes must be an integer"
    return details.message or str(error)


class AgentdHttpServer:
    """Uvicorn/Starlette HTTP adapter for AgentdApp.

    The server owns socket/request translation only. Runtime behavior stays
    behind AgentdApp and RuntimeService, preserving the application/runtime seam.
    The public shape intentionally mirrors the previous local server adapter so
    CLI tests and embedded callers can keep injecting server runners.
    """

    app: AgentdApp
    config: AgentdServerConfig
    server_address: tuple[str, int]
    _server: Server
    _socket: socket.socket
    _closed: bool

    def __init__(self, app: AgentdApp, config: AgentdServerConfig | None = None) -> None:
        config = config or AgentdServerConfig()
        self.app = app
        self.config = config
        self._socket = _bind_socket(config.host, config.port)
        host, port = _socket_address(self._socket)
        self.server_address = (host, port)
        self._closed = False
        self._server = Server(
            Config(
                build_agentd_asgi_app(app, config),
                host=config.host,
                port=config.port,
                access_log=False,
                lifespan="off",
                log_level="warning",
            )
        )

    @property
    def base_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"

    def serve_forever(self) -> None:
        if self._closed:
            raise RuntimeError("agentd server socket is closed")
        self._server.run(sockets=[self._socket])

    async def serve(self) -> None:
        if self._closed:
            raise RuntimeError("agentd server socket is closed")
        await self._server.serve(sockets=[self._socket])

    def shutdown(self) -> None:
        self._server.should_exit = True

    def server_close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with suppress(OSError):
            self._socket.close()


def build_agentd_asgi_app(app: AgentdApp, config: AgentdServerConfig | None = None) -> Starlette:
    """Build the ASGI adapter used by the local agentd server."""

    config = config or AgentdServerConfig()

    async def dispatch(request: StarletteRequest) -> StarletteResponse:
        agentd_request = await _agentd_request(request, config)
        if isinstance(agentd_request, HttpResponse):
            return _starlette_response(agentd_request)
        try:
            response = await app.handle(agentd_request)
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            response = _error_response(500, "internal_error", str(exc))
        return _starlette_response(response)

    return Starlette(
        routes=[
            Route(
                "/{path:path}",
                dispatch,
                methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
            )
        ]
    )


async def _agentd_request(
    request: StarletteRequest,
    config: AgentdServerConfig,
) -> HttpRequest | HttpResponse:
    body = await _request_body(request, config.max_body_bytes)
    if isinstance(body, HttpResponse):
        return body
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    return HttpRequest(
        method=request.method,
        path=path,
        body=body,
        headers=MappingProxyType(dict(request.headers.items())),
    )


async def _request_body(
    request: StarletteRequest,
    max_body_bytes: int,
) -> JsonMapping | HttpResponse:
    length_value = request.headers.get("content-length")
    if length_value is not None:
        try:
            length = parse_non_negative_int_text(length_value, "content-length")
        except ValueError as exc:
            return _error_response(400, "bad_request", str(exc))
        if length > max_body_bytes:
            return _error_response(413, "payload_too_large", "request body is too large")

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_body_bytes:
            return _error_response(413, "payload_too_large", "request body is too large")
        if chunk:
            chunks.append(chunk)
    if not chunks:
        return immutable_json()

    try:
        raw_body = b"".join(chunks)
        raw_body.decode("utf-8")
        loaded = loads_json(raw_body)
    except UnicodeDecodeError:
        return _error_response(400, "bad_request", "request body must be UTF-8 JSON")
    except JsonCodecError as exc:
        return _error_response(400, "bad_request", f"invalid JSON body: {_json_error_message(exc)}")
    try:
        return immutable_json(parse_json_object(loaded, "request body"))
    except ValueError as exc:
        return _error_response(400, "bad_request", _request_body_error_message(str(exc)))


def _starlette_response(response: HttpResponse) -> StarletteResponse:
    headers = dict(response.headers)
    if response.stream_body is not None:
        return StreamingResponse(
            response.stream_body,
            status_code=response.status_code,
            headers=headers,
            media_type="text/event-stream",
        )
    if response.text_body is not None:
        return PlainTextResponse(
            response.text_body,
            status_code=response.status_code,
            headers=headers,
            media_type=None,
        )
    return _OrjsonResponse(
        response.body,
        status_code=response.status_code,
        headers=headers,
    )


def _error_response(status_code: int, code: str, message: str) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        body=immutable_json({"error": {"code": code, "message": message}}),
    )


def _request_body_error_message(message: str) -> str:
    if message == "request body must be an object":
        return "request body must be a JSON object"
    return message


def _json_error_message(error: JsonCodecError) -> str:
    return str(error).removeprefix("invalid JSON: ")


def _bind_socket(host: str, port: int) -> socket.socket:
    last_error: OSError | None = None
    for family, socktype, proto, _canonname, address in socket.getaddrinfo(
        host,
        port,
        type=socket.SOCK_STREAM,
    ):
        sock = socket.socket(family, socktype, proto)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(address)
            sock.listen()
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    if last_error is not None:
        raise last_error
    raise OSError(f"could not resolve bind address: {host}:{port}")


def _socket_address(sock: socket.socket) -> tuple[str, int]:
    address = sock.getsockname()
    return str(address[0]), int(address[1])
