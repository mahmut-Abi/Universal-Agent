"""Admin plane: user / tenant / role / credential management over HTTP.

These routes back the multi-tenant user-management surface (Phase 2 of
docs/multitenancy-user-management-design.md). They require a
:class:`CredentialAdminStore` (wired by agentd when a credential store is
configured); without one the handler falls through so deployments can run
without the admin plane.

Authorization: every route requires an ADMIN-role principal (resolved by the
agentd auth layer). Requests authenticated with a legacy shared bearer token
carry no principal and keep admin-equivalent access for backward compatibility
— this is the bootstrap path before any credential exists. Denials are
reason-tagged (``rbac``) exactly like the request-scope gate.

The raw token returned by credential issuance is shown exactly once in the
response; only its hash is retained by the store.
"""

from __future__ import annotations

from collections.abc import Mapping

from universal_agent.agentd.http import (
    HttpRequest,
    HttpResponse,
    forbidden,
    json_response,
    not_found,
)
from universal_agent.agentd.routing import (
    AgentdRouteDefinition,
    AgentdRouteMatcher,
    _optional_query_value,
)
from universal_agent.core import JsonValue, immutable_json
from universal_agent.security import (
    AuditEvent,
    AuditRecorder,
    CredentialAdminStore,
    PrincipalAlreadyExistsError,
    PrincipalNotFoundError,
    RequestPrincipal,
    Role,
    TenantStatus,
    UserAccountStatus,
)

_ADMIN_ROUTE_DEFINITIONS = (
    AgentdRouteDefinition("admin_tenant_write", "/v1/admin/tenants", ("POST", "GET")),
    AgentdRouteDefinition(
        "admin_tenant_status",
        "/v1/admin/tenants/{tenant}/status",
        ("PUT",),
    ),
    AgentdRouteDefinition("admin_user_write", "/v1/admin/users", ("POST", "GET")),
    AgentdRouteDefinition("admin_user_status", "/v1/admin/users/{user}/status", ("PUT",)),
    AgentdRouteDefinition(
        "admin_members_list",
        "/v1/admin/tenants/{tenant}/members",
        ("GET",),
    ),
    AgentdRouteDefinition(
        "admin_member_write",
        "/v1/admin/tenants/{tenant}/members/{user}",
        ("PUT", "DELETE"),
    ),
    AgentdRouteDefinition(
        "admin_credential_write",
        "/v1/admin/credentials",
        ("POST", "GET"),
    ),
    AgentdRouteDefinition(
        "admin_credential_revoke",
        "/v1/admin/credentials/{credential}",
        ("DELETE",),
    ),
    AgentdRouteDefinition("admin_audit", "/v1/admin/audit", ("GET",)),
)

_ADMIN_ROUTES = AgentdRouteMatcher(_ADMIN_ROUTE_DEFINITIONS)


def admin_route_definitions() -> tuple[AgentdRouteDefinition, ...]:
    return _ADMIN_ROUTE_DEFINITIONS


def _admin_gate(principal: RequestPrincipal | None) -> HttpResponse | None:
    """Only ADMIN-role principals may use the admin plane.

    ``None`` means legacy shared-token auth: kept as the bootstrap admin path
    for backward compatibility.
    """

    if principal is None:
        return None
    if principal.role is not Role.ADMIN:
        return forbidden(
            f"rbac: subject {principal.subject} has role {principal.role.value}; "
            "the admin plane requires admin"
        )
    return None


def _body_value(body: object, key: str) -> JsonValue | None:
    if not isinstance(body, Mapping):
        return None
    return body.get(key)


def _required_string(body: object, key: str) -> str | None:
    value = _body_value(body, key)
    return value if isinstance(value, str) and value else None


def _parse_role(body: object) -> Role | None:
    value = _required_string(body, "role")
    if value is None:
        return None
    try:
        return Role(value)
    except ValueError:
        return None


def handle_admin_route(
    admin_store: CredentialAdminStore | None,
    principal: RequestPrincipal | None,
    request: HttpRequest,
    method: str,
    path: str,
    *,
    audit: AuditRecorder | None = None,
) -> HttpResponse | None:
    """Dispatch /v1/admin/* routes; fall through when the plane is absent."""

    if admin_store is None:
        return None
    match = _ADMIN_ROUTES.match(path, method)
    if match is None:
        return None
    if not match.method_allowed:
        return None
    gate = _admin_gate(principal)
    if gate is not None:
        return gate

    actor = principal.subject if principal is not None else "bootstrap"
    try:
        response = _dispatch(
            admin_store,
            match.name,
            request,
            match.path_params,
            method=method,
            audit_recorder=audit,
        )
    except PrincipalAlreadyExistsError as exc:
        return json_response(
            immutable_json({"error": {"code": "already_exists", "message": str(exc)}}),
            status_code=409,
        )
    except PrincipalNotFoundError as exc:
        return not_found(str(exc))
    except ValueError as exc:
        return json_response(
            immutable_json({"error": {"code": "bad_request", "message": str(exc)}}),
            status_code=400,
        )
    if audit is not None:
        _record_mutation(
            audit, actor, match.name, request, response, match.path_params, method_arg=method
        )
    return response


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def path_for_query(request: HttpRequest) -> str:
    """The full request path including the query string."""

    return request.path


# Merged write routes resolve their audit event by HTTP method; read-only
# routes are not audited.
_AUDIT_EVENT_NAMES = {
    "admin_tenant_write": {"POST": "tenant_created"},
    "admin_user_write": {"POST": "user_created"},
    "admin_member_write": {"PUT": "membership_changed", "DELETE": "member_removed"},
    "admin_user_status": {"PUT": "user_status_changed"},
    "admin_tenant_status": {"PUT": "tenant_status_changed"},
    "admin_credential_write": {"POST": "credential_issued"},
    "admin_credential_revoke": {"DELETE": "credential_revoked"},
}


def _audit_event_name(route: str, method: str) -> str | None:
    by_method = _AUDIT_EVENT_NAMES.get(route)
    return by_method.get(method.upper()) if by_method else None


def _record_mutation(
    audit: AuditRecorder,
    actor: str,
    route: str,
    request: HttpRequest,
    response: HttpResponse,
    params: object,
    method_arg: str = "",
) -> None:
    event_name = _audit_event_name(route, method_arg)
    if event_name is None:
        return
    path_params: dict[str, str] = {}
    if isinstance(params, dict):
        path_params = {str(k): str(v) for k, v in params.items()}
    details: dict[str, JsonValue] = {str(k): v for k, v in dict(request.body).items()}
    details.update({f"param_{k}": v for k, v in path_params.items()})
    details["status_code"] = response.status_code
    audit.record(
        AuditEvent(
            event=event_name,
            actor=actor,
            tenant_id=_optional_str(details.get("tenant_id") or path_params.get("tenant")),
            resource=_optional_str(path_params.get("credential") or details.get("user_id")),
            details=details,
        )
    )


def _dispatch(
    store: CredentialAdminStore,
    route: str,
    request: HttpRequest,
    params: object,
    *,
    method: str,
    audit_recorder: AuditRecorder | None = None,
) -> HttpResponse:
    path_params: dict[str, str] = {}
    if isinstance(params, dict):
        path_params = {str(k): str(v) for k, v in params.items()}

    if route == "admin_audit":
        if audit_recorder is None:
            return json_response(immutable_json({"events": []}))
        limit_raw = request.body.get("limit") if isinstance(request.body, Mapping) else None
        limit = int(limit_raw) if isinstance(limit_raw, (int, float)) and limit_raw >= 0 else None
        events = audit_recorder.events(limit=limit)
        return json_response(immutable_json({"events": [event.to_json() for event in events]}))

    if route == "admin_tenant_write" and method == "GET":
        tenants = store.list_tenants()
        return json_response(
            immutable_json(
                {
                    "tenants": [
                        {"tenant_id": t.tenant_id, "name": t.name, "status": t.status.value}
                        for t in tenants
                    ]
                }
            )
        )

    if route == "admin_tenant_status":
        tenant_id = path_params.get("tenant")
        status = _required_string(request.body, "status")
        if tenant_id is None or status not in ("active", "disabled"):
            raise ValueError("status is required and must be active or disabled")
        tenant = store.set_tenant_status(tenant_id=tenant_id, status=TenantStatus(status))
        return json_response(
            immutable_json({"tenant_id": tenant.tenant_id, "status": tenant.status.value})
        )

    if route == "admin_user_write" and method == "GET":
        users = store.list_users()
        return json_response(
            immutable_json(
                {
                    "users": [
                        {
                            "user_id": u.user_id,
                            "email": u.email,
                            "display_name": u.display_name,
                            "status": u.status.value,
                        }
                        for u in users
                    ]
                }
            )
        )

    if route == "admin_user_status":
        user_id = path_params.get("user")
        status = _required_string(request.body, "status")
        if user_id is None or status not in ("active", "disabled"):
            raise ValueError("status is required and must be active or disabled")
        updated_user = store.set_user_status(user_id=user_id, status=UserAccountStatus(status))
        return json_response(
            immutable_json({"user_id": updated_user.user_id, "status": updated_user.status.value})
        )

    if route == "admin_credential_write" and method == "GET":
        user_id = _optional_query_value(path_for_query(request), "user_id")
        tenant_id = _optional_query_value(path_for_query(request), "tenant_id")
        credentials = store.list_credentials(user_id=user_id, tenant_id=tenant_id)
        return json_response(
            immutable_json(
                {
                    "credentials": [
                        {
                            "credential_id": c.credential_id,
                            "user_id": c.user_id,
                            "tenant_id": c.tenant_id,
                            "scope": c.scope.value,
                            "revoked": c.revoked_at is not None,
                            "created_at": c.created_at.isoformat() if c.created_at else None,
                        }
                        for c in credentials
                    ]
                }
            )
        )

    if route == "admin_member_write" and method == "DELETE":
        tenant_id = path_params.get("tenant")
        user_id = path_params.get("user")
        if tenant_id is None or user_id is None:
            raise ValueError("tenant and user are required")
        removed = store.remove_member(tenant_id=tenant_id, user_id=user_id)
        return json_response(
            immutable_json({"tenant_id": tenant_id, "user_id": user_id, "removed": removed})
        )

    if route == "admin_tenant_write" and method == "POST":
        tenant_id = _required_string(request.body, "tenant_id")
        name = _required_string(request.body, "name")
        if tenant_id is None or name is None:
            raise ValueError("tenant_id and name are required")
        tenant = store.create_tenant(tenant_id=tenant_id, name=name)
        return json_response(
            immutable_json(
                {"tenant_id": tenant.tenant_id, "name": tenant.name, "status": tenant.status.value}
            ),
            status_code=201,
        )

    if route == "admin_user_write" and method == "POST":
        user_id = _required_string(request.body, "user_id")
        email = _required_string(request.body, "email")
        display_name = _required_string(request.body, "display_name")
        if user_id is None or email is None:
            raise ValueError("user_id and email are required")
        user = store.create_user(user_id=user_id, email=email, display_name=display_name)
        return json_response(
            immutable_json(
                {
                    "user_id": user.user_id,
                    "email": email,
                    "display_name": user.display_name,
                    "status": user.status.value,
                }
            ),
            status_code=201,
        )

    if route == "admin_member_write" and method == "PUT":
        tenant_id = path_params.get("tenant")
        user_id = path_params.get("user")
        role = _parse_role(request.body)
        if tenant_id is None or user_id is None or role is None:
            raise ValueError("role is required and must be one of: admin, operator, read_only")
        store.set_role(tenant_id=tenant_id, user_id=user_id, role=role)
        return json_response(
            immutable_json({"tenant_id": tenant_id, "user_id": user_id, "role": role.value})
        )

    if route == "admin_members_list":
        tenant_id = path_params.get("tenant")
        if tenant_id is None:
            raise ValueError("tenant is required")
        members = store.list_members(tenant_id)
        return json_response(
            immutable_json(
                {
                    "tenant_id": tenant_id,
                    "members": [{"user_id": m.user_id, "role": m.role.value} for m in members],
                }
            )
        )

    if route == "admin_credential_write" and method == "POST":
        user_id = _required_string(request.body, "user_id")
        tenant_id = _required_string(request.body, "tenant_id")
        role = _parse_role(request.body)
        if user_id is None or tenant_id is None or role is None:
            raise ValueError(
                "user_id, tenant_id and role are required; role must be one of: "
                "admin, operator, read_only"
            )
        raw_token, credential = store.issue_credential(
            user_id=user_id, tenant_id=tenant_id, role=role
        )
        return json_response(
            immutable_json(
                {
                    "credential_id": credential.credential_id,
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "role": role.value,
                    "scope": credential.scope.value,
                    # Shown exactly once; only the hash is retained server-side.
                    "token": raw_token,
                }
            ),
            status_code=201,
        )

    if route == "admin_credential_revoke":
        credential_id = path_params.get("credential")
        if credential_id is None:
            raise ValueError("credential id is required")
        revoked = store.revoke_credential(credential_id)
        return json_response(immutable_json({"credential_id": credential_id, "revoked": revoked}))

    raise ValueError(f"unsupported admin route: {route}")
