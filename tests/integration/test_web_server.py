"""Standalone web tier smoke test (web/ Node.js proxy).

Spawns the zero-dependency Node server against a stub agentd backend and
verifies static serving, /api/config, authenticated proxying, and proxy
error handling. Skipped when Node.js is not installed.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
NODE = shutil.which("node")

_WEB_REBUILD_SKIP = pytest.mark.skipif(
    True, reason="web frontend under active rebuild"
)
pytestmark = [pytest.mark.integration, _WEB_REBUILD_SKIP]

_skip_without_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


class _StubAgentd(BaseHTTPRequestHandler):
    def __init__(self, seen_auth: list[str | None], *args: object, **kwargs: object) -> None:
        self._seen_auth = seen_auth
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:
        if self.path == "/health":
            self._seen_auth.append(self.headers.get("authorization"))
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(name="web_server")
def web_server_fixture(request: pytest.FixtureRequest) -> Iterator[str]:
    web_password = ""
    if getattr(request, "param", None):
        web_password = request.param
    stub_port = _free_port()
    seen_auth: list[str | None] = []

    def handler(*args: object, **kwargs: object) -> _StubAgentd:
        return _StubAgentd(seen_auth, *args, **kwargs)

    stub = ThreadingHTTPServer(("127.0.0.1", stub_port), handler)
    thread = threading.Thread(target=stub.serve_forever, daemon=True)
    thread.start()

    web_port = _free_port()
    env = dict(os.environ)
    env.update(
        {
            "PORT": str(web_port),
            "AGENTD_URL": f"http://127.0.0.1:{stub_port}",
            "AGENTD_TOKEN": "stub-token",
        }
    )
    if web_password:
        env["WEB_PASSWORD"] = web_password
    assert NODE is not None
    process = subprocess.Popen(
        [NODE, str(WEB_DIR / "server.mjs")],
        cwd=WEB_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{web_port}"
    try:
        deadline = 10.0
        ready = False
        while deadline > 0:
            try:
                urllib.request.urlopen(f"{base}/api/health", timeout=1)
                ready = True
                break
            except urllib.error.HTTPError:
                # Auth-gated deployments answer 401 — the server is up.
                ready = True
                break
            except Exception:
                time.sleep(0.1)
                deadline -= 0.1
        if not ready:
            pytest.fail("web server did not become ready")
        yield base
    finally:
        process.terminate()
        process.wait(timeout=5)
        stub.shutdown()
        thread.join(timeout=5)


@_skip_without_node
def test_web_serves_static_index(web_server: str) -> None:
    with urllib.request.urlopen(f"{web_server}/", timeout=5) as response:
        body = response.read().decode("utf-8")
    assert "<html" in body.lower()


@_skip_without_node
def test_web_reports_config(web_server: str) -> None:
    with urllib.request.urlopen(f"{web_server}/api/config", timeout=5) as response:
        payload = json.loads(response.read())
    assert payload["server_configured"] is True


@_skip_without_node
def test_web_proxies_agentd_with_token(web_server: str) -> None:
    with urllib.request.urlopen(f"{web_server}/api/health", timeout=5) as response:
        payload = json.loads(response.read())
    assert payload["status"] == "ok"


@_skip_without_node
def test_web_proxies_agentd_error_status(web_server: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(f"{web_server}/api/v1/missing", timeout=5)
    assert excinfo.value.code == 404


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


@_skip_without_node
@pytest.mark.parametrize("web_server", ["deployment-secret"], indirect=True)
def test_web_login_gate_blocks_and_accepts(web_server: str) -> None:
    opener = urllib.request.build_opener(_NoRedirect)

    # Unauthenticated page request redirects to /login.
    with pytest.raises(urllib.error.HTTPError) as redirect:
        opener.open(f"{web_server}/", timeout=5)
    assert redirect.value.status == 303
    assert redirect.value.headers.get("location") == "/login"

    # Unauthenticated API request gets a structured 401.
    with pytest.raises(urllib.error.HTTPError) as api_err:
        opener.open(f"{web_server}/api/health", timeout=5)
    assert api_err.value.status == 401

    # Wrong password bounces back to the login page with an error flag.
    body = urllib.parse.urlencode({"password": "wrong"}).encode()
    with pytest.raises(urllib.error.HTTPError) as wrong:
        opener.open(f"{web_server}/login", data=body, timeout=5)
    assert wrong.value.status == 303
    assert wrong.value.headers.get("location") == "/login?error=1"

    # Correct password sets the session cookie (303 to /) and grants access.
    body = urllib.parse.urlencode({"password": "deployment-secret"}).encode()
    with pytest.raises(urllib.error.HTTPError) as success:
        opener.open(f"{web_server}/login", data=body, timeout=5)
    assert success.value.status == 303
    set_cookie = success.value.headers.get("set-cookie") or ""
    assert "ua_web_session=" in set_cookie
    assert "HttpOnly" in set_cookie

    cookie = set_cookie.split(";")[0]
    authed = urllib.request.Request(f"{web_server}/", headers={"cookie": cookie})
    with urllib.request.urlopen(authed, timeout=5) as response:
        assert "Universal Agent" in response.read().decode("utf-8")
