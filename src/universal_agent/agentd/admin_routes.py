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
)
from universal_agent.core import JsonValue, immutable_json
from universal_agent.security import (
    CredentialAdminStore,
    PrincipalAlreadyExistsError,
    PrincipalNotFoundError,
    RequestPrincipal,
    Role,
)

_ADMIN_ROUTE_DEFINITIONS = (
    AgentdRouteDefinition("admin_tenant_create", "/v1/admin/tenants", ("POST",)),
    AgentdRouteDefinition("admin_user_create", "/v1/admin/users", ("POST",)),
    AgentdRouteDefinition(
        "admin_member_set_role",
        "/v1/admin/tenants/{tenant}/members/{user}",
        ("PUT",),
    ),
    AgentdRouteDefinition(
        "admin_members_list",
        "/v1/admin/tenants/{tenant}/members",
        ("GET",),
    ),
    AgentdRouteDefinition("admin_credential_create", "/v1/admin/credentials", ("POST",)),
    AgentdRouteDefinition(
        "admin_credential_revoke",
        "/v1/admin/credentials/{credential}",
        ("DELETE",),
    ),
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

    try:
        return _dispatch(admin_store, match.name, request, match.path_params)
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


def _dispatch(
    store: CredentialAdminStore,
    route: str,
    request: HttpRequest,
    params: object,
) -> HttpResponse:
    path_params: dict[str, str] = {}
    if isinstance(params, dict):
        path_params = {str(k): str(v) for k, v in params.items()}

    if route == "admin_tenant_create":
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

    if route == "admin_user_create":
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

    if route == "admin_member_set_role":
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

    if route == "admin_credential_create":
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
