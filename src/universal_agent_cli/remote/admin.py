"""Remote admin command dispatch (user/tenant/role/credential management)."""

from __future__ import annotations

import argparse
from typing import TextIO, cast

from universal_agent.core import JsonValue
from universal_agent_api import AgentdClient
from universal_agent_cli.io import _write_json


def _required(args: argparse.Namespace, key: str) -> str:
    value = cast(str | None, getattr(args, key, None))
    if value is None or not value:
        raise ValueError(f"--{key.replace('_', '-')} is required")
    return value


def _admin_request(
    args: argparse.Namespace,
) -> tuple[str, str, dict[str, JsonValue]]:
    """Map (admin_command, admin_verb, args) to (method, path, body)."""

    command = cast(str, args.admin_command)
    verb = cast(str, args.admin_verb)

    if command == "tenant" and verb == "list":
        return ("GET", "/v1/admin/tenants", {})

    if command == "tenant" and verb in ("disable", "enable"):
        status = "disabled" if verb == "disable" else "active"
        return (
            "PUT",
            f"/v1/admin/tenants/{_required(args, 'tenant')}/status",
            {"status": status},
        )

    if command == "user" and verb == "list":
        return ("GET", "/v1/admin/users", {})

    if command == "user" and verb in ("disable", "enable"):
        status = "disabled" if verb == "disable" else "active"
        return (
            "PUT",
            f"/v1/admin/users/{_required(args, 'user')}/status",
            {"status": status},
        )

    if command == "member" and verb == "remove":
        return (
            "DELETE",
            f"/v1/admin/tenants/{_required(args, 'tenant')}/members/{_required(args, 'user')}",
            {},
        )

    if command == "credential" and verb == "list":
        from urllib.parse import urlencode

        filters = {
            key: value
            for key in ("user", "tenant")
            if (value := cast(str | None, getattr(args, key, None)))
        }
        query = f"?{urlencode({k + '_id': v for k, v in filters.items()})}" if filters else ""
        return ("GET", "/v1/admin/credentials" + query, {})

    if command == "tenant" and verb == "create":
        return (
            "POST",
            "/v1/admin/tenants",
            {"tenant_id": _required(args, "tenant_id"), "name": _required(args, "name")},
        )

    if command == "user" and verb == "create":
        body: dict[str, JsonValue] = {
            "user_id": _required(args, "user_id"),
            "email": _required(args, "email"),
        }
        display_name = cast(str | None, getattr(args, "display_name", None))
        if display_name is not None:
            body["display_name"] = display_name
        return ("POST", "/v1/admin/users", body)

    if command == "role" and verb == "set":
        return (
            "PUT",
            f"/v1/admin/tenants/{_required(args, 'tenant')}/members/{_required(args, 'user')}",
            {"role": _required(args, "role")},
        )

    if command == "member" and verb == "list":
        return ("GET", f"/v1/admin/tenants/{_required(args, 'tenant')}/members", {})

    if command == "credential" and verb == "create":
        return (
            "POST",
            "/v1/admin/credentials",
            {
                "user_id": _required(args, "user"),
                "tenant_id": _required(args, "tenant"),
                "role": _required(args, "role"),
            },
        )

    if command == "credential" and verb == "revoke":
        return ("DELETE", f"/v1/admin/credentials/{_required(args, 'credential_id')}", {})

    raise ValueError(f"unsupported admin command: {command} {verb}")


async def dispatch_remote_admin(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    method, path, body = _admin_request(args)

    # Requests with a body send it; otherwise the verb-specific client method
    # issues the call. Server errors raise AgentdClientError and are surfaced
    # by the CLI error path (denial reasons stay visible).
    if method == "GET":
        payload = await client.get_json(path)
    elif method == "DELETE":
        payload = await client.delete_json(path)
    elif method == "PUT":
        payload = await client.put_json(path, body=body)
    else:
        payload = await client.post_json(path, body=body)

    # Credential issuance is the one time the raw token is visible: mark the
    # one-shot nature inside the payload so JSON output stays machine-readable.
    if isinstance(payload, dict) and "token" in payload:
        payload["note"] = (
            "Store this token securely now - it is shown only once "
            "(only its hash is retained server-side)."
        )

    _write_json(out, payload)
