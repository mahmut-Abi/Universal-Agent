#!/usr/bin/env python3
"""Smoke-test every agentd endpoint used by the web dashboard.

Usage:
  AGENTD_URL=http://10.18.127.182:8765 AGENTD_TOKEN=<token> python3 scripts/smoke_web_api.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

BASE = os.environ.get("AGENTD_URL", "http://127.0.0.1:8765").rstrip("/")
TOKEN = os.environ.get("AGENTD_TOKEN", "")
results: list[tuple[str, str, Any, str]] = []


def _payload(resp: Any) -> Any:
    try:
        return json.load(resp)
    except Exception:
        return {}


def call(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[Any, Any]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            status: Any = resp.status
            payload: Any = _payload(resp)
    except urllib.error.HTTPError as e:
        status = e.code
        payload = _payload(e)
    except Exception as e:
        status = "ERR"
        payload = {"msg": str(e)}
    keys = (
        ",".join(list(payload.keys())[:4]) if isinstance(payload, dict) else type(payload).__name__
    )
    results.append((method, path, status, keys))
    return status, payload


def as_dict(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def extract_id(payload: Any, *keys: str) -> str | None:
    d = as_dict(payload)
    for k in keys:
        nested = d.get(k)
        if isinstance(nested, dict):
            for idkey in ("id", k + "_id", "memory_id", "session_id"):
                val = nested.get(idkey)
                if isinstance(val, str):
                    return str(val)
        val = d.get(k)
        if isinstance(val, str):
            return str(val)
    return None


def main() -> int:
    # ── GET reads used by dashboard views ──
    for p in [
        "/v1/sessions",
        "/v1/sessions?limit=200",
        "/v1/metrics",
        "/v1/doctor",
        "/v1/profiles",
        "/v1/domains",
        "/v1/tools",
        "/v1/memory",
        "/v1/audit",
        "/v1/audit/integrity",
        "/v1/logs",
        "/v1/traces",
        "/v1/cost",
        "/v1/distributed/snapshot",
        "/v1/multi-agent",
    ]:
        call("GET", p)

    # ── session lifecycle ──
    st, d = call(
        "POST",
        "/v1/sessions",
        {
            "goal": {
                "description": "web api smoke test",
                "success_criteria": [{"key": "done", "expected": True}],
            },
            "compile_goal": True,
        },
    )
    sid = extract_id(d, "session", "session_id", "id")
    if sid:
        call("GET", f"/v1/sessions/{sid}")
        call("GET", f"/v1/sessions/{sid}/events")
        call("GET", f"/v1/sessions/{sid}/evidence")
        call("POST", f"/v1/sessions/{sid}/messages", {"message": "ping"})
        call("POST", f"/v1/sessions/{sid}/pause", {})
        call("POST", f"/v1/sessions/{sid}/resume", {"confirmed": True})
        call("POST", f"/v1/sessions/{sid}/cancel", {})
    else:
        print(f"!! session create failed ({st}); skipping session-scoped endpoints")

    # ── profile CRUD ──
    call(
        "POST",
        "/v1/profiles",
        {
            "name": "smoke-t",
            "version": "0.1.0",
            "description": "smoke",
            "domains": [{"name": "local", "version": "0.1.0"}],
        },
    )
    call(
        "PATCH",
        "/v1/profiles/smoke-t",
        {"description": "smoke2", "domains": [{"name": "local", "version": "0.1.0"}]},
    )
    call("DELETE", "/v1/profiles/smoke-t")

    # ── memory add/delete (agentd: kind/subject/content/scope/confidence) ──
    st, d = call(
        "POST",
        "/v1/memory",
        {
            "kind": "semantic",
            "subject": "smoke",
            "content": "smoke memory text",
            "scope": "local",
            "confidence": 0.9,
        },
    )
    mid = extract_id(d, "memory", "memory_id", "id")
    if mid:
        call("DELETE", f"/v1/memory/{mid}")

    # ── writes / optional routes ──
    call("PUT", "/v1/config", {"policy": {"mutation-confirm": True}})
    call("POST", "/v1/eval/list", {})
    call("POST", "/v1/eval/reports", {})
    call("POST", "/v1/eval/run", {})
    call("POST", "/v1/ecosystem/registry", {})
    call("POST", "/v1/ecosystem/catalog", {})
    call("POST", "/v1/kubernetes/preflight", {"skip_cluster": True})

    print(f"backend: {BASE}  token: {'yes' if TOKEN else 'NO'}\n")
    print(f"{'method':<8}{'endpoint':<44}{'status':<7}payload-keys")
    print("-" * 92)
    bad = 0
    for method, path, status, keys in results:
        flag = ""
        if status == 401:
            flag = "  <-- auth"
        elif status not in (200, 201, 204):
            flag = "  <-- CHECK"
            bad += 1
        print(f"{method:<8}{path:<44}{status!s:<7}{keys[:40]}{flag}")
    print("-" * 92)
    print(f"total: {len(results)}  non-2xx (excl. auth): {bad}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
