from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import MappingProxyType

from universal_agent.agentd.app import AgentdApp
from universal_agent.agentd.http import HttpRequest, HttpResponse
from universal_agent.core import JsonMapping, JsonValue, immutable_json


@dataclass(frozen=True, slots=True)
class AgentdServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    max_body_bytes: int = 1_000_000


class AgentdHttpServer(ThreadingHTTPServer):
    """Standard-library HTTP adapter for AgentdApp.

    The server owns socket/request translation only. Runtime behavior remains
    behind AgentdApp and RuntimeService, preserving the application/runtime seam.
    """

    app: AgentdApp
    config: AgentdServerConfig

    def __init__(self, app: AgentdApp, config: AgentdServerConfig | None = None) -> None:
        config = config or AgentdServerConfig()
        self.app = app
        self.config = config
        super().__init__((config.host, config.port), agentd_request_handler())

    @property
    def base_url(self) -> str:
        host = str(self.server_address[0])
        port = int(self.server_address[1])
        return f"http://{host}:{port}"


def agentd_request_handler() -> type[BaseHTTPRequestHandler]:
    class _AgentdRequestHandler(BaseHTTPRequestHandler):
        server: AgentdHttpServer

        def do_GET(self) -> None:
            self._dispatch()

        def do_POST(self) -> None:
            self._dispatch()

        def do_PUT(self) -> None:
            self._dispatch()

        def do_PATCH(self) -> None:
            self._dispatch()

        def do_DELETE(self) -> None:
            self._dispatch()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _dispatch(self) -> None:
            request = self._request()
            if isinstance(request, HttpResponse):
                self._write_response(request)
                return
            try:
                response = asyncio.run(self.server.app.handle(request))
            except Exception as exc:  # pragma: no cover - defensive adapter boundary
                response = _error_response(500, "internal_error", str(exc))
            self._write_response(response)

        def _request(self) -> HttpRequest | HttpResponse:
            body = self._body()
            if isinstance(body, HttpResponse):
                return body
            return HttpRequest(
                method=self.command,
                path=self.path,
                body=body,
                headers=MappingProxyType(dict(self.headers.items())),
            )

        def _body(self) -> JsonMapping | HttpResponse:
            length_value = self.headers.get("content-length", "0")
            try:
                length = int(length_value)
            except ValueError:
                return _error_response(400, "bad_request", "content-length must be an integer")
            if length < 0:
                return _error_response(400, "bad_request", "content-length must be non-negative")
            if length > self.server.config.max_body_bytes:
                return _error_response(413, "payload_too_large", "request body is too large")
            if length == 0:
                return immutable_json()
            try:
                raw = self.rfile.read(length)
                loaded = json.loads(raw.decode("utf-8"))
            except UnicodeDecodeError:
                return _error_response(400, "bad_request", "request body must be UTF-8 JSON")
            except json.JSONDecodeError as exc:
                return _error_response(400, "bad_request", f"invalid JSON body: {exc.msg}")
            try:
                return _json_mapping(loaded)
            except ValueError as exc:
                return _error_response(400, "bad_request", str(exc))

        def _write_response(self, response: HttpResponse) -> None:
            body = _response_body(response)
            self.send_response(response.status_code)
            headers = dict(response.headers)
            if "content-type" not in {key.lower() for key in headers}:
                headers["content-type"] = "application/json"
            headers["content-length"] = str(len(body))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

    return _AgentdRequestHandler


def _error_response(status_code: int, code: str, message: str) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        body=immutable_json({"error": {"code": code, "message": message}}),
    )


def _response_body(response: HttpResponse) -> bytes:
    if response.text_body is not None:
        return response.text_body.encode("utf-8")
    return json.dumps(_to_json(response.body), sort_keys=True).encode("utf-8")


def _json_mapping(value: object) -> JsonMapping:
    if not isinstance(value, dict):
        raise ValueError("request body must be a JSON object")
    payload: dict[str, JsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("request body keys must be strings")
        payload[key] = _json_value(item)
    return immutable_json(payload)


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        payload: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("request body keys must be strings")
            payload[key] = _json_value(item)
        return payload
    raise ValueError(f"request body contains non-JSON value: {type(value).__name__}")


def _to_json(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        return {str(key): _to_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_to_json(item) for item in value]
    return str(value)
