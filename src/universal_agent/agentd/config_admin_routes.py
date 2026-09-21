"""Config-management write surface: profile CRUD, validation, domain
bindings, and the config-change audit log.

These routes are the write plane for persisted configuration; the runtime
read models (GET /v1/profiles, GET /v1/profiles/{profile}) stay authoritative
for the profiles loaded into the running service — new or changed profile
configs apply on restart/reload.

The routes require a :class:`ProfileStore` (wired by agentd from
``--profiles-dir``); without one the handler falls through so deployments can
run without the config-management plane.

Audit: every successful mutation appends a record to the store's JSONL audit
log, exposed via GET /v1/config/audit (action-audit records at /v1/audit are
session/action-scoped and intentionally stay separate). The acting principal
comes from the ``X-Acting-Principal`` header (default ``api``).
"""

from __future__ import annotations

from typing import Any

from universal_agent.agentd.http import (
    HttpRequest,
    HttpResponse,
    json_response,
    not_found,
)
from universal_agent.agentd.routing import (
    AgentdRouteDefinition,
    AgentdRouteMatcher,
)
from universal_agent.core import JsonMapping, JsonValue, immutable_json
from universal_agent.deployment_config import (
    DeploymentConfigStore,
    DeploymentConfigValidationError,
)
from universal_agent.profile.store import (
    ProfileAlreadyExistsError,
    ProfileBuiltinError,
    ProfileNotFoundError,
    ProfileStore,
    ProfileStoreValidationError,
)

_CONFIG_ADMIN_ROUTE_DEFINITIONS = (
    AgentdRouteDefinition("config_profiles_create", "/v1/profiles", ("POST",)),
    AgentdRouteDefinition(
        "config_profile_write",
        "/v1/profiles/{profile}",
        ("PATCH", "DELETE"),
    ),
    AgentdRouteDefinition("config_validate", "/v1/config/validate", ("POST",)),
    AgentdRouteDefinition("config_profiles_list", "/v1/config/profiles", ("GET",)),
    AgentdRouteDefinition("config_audit", "/v1/config/audit", ("GET",)),
    AgentdRouteDefinition("config_read", "/v1/config", ("PUT",)),
    AgentdRouteDefinition(
        "config_domain_profiles",
        "/v1/domains/{name}/profiles",
        ("GET", "PUT"),
    ),
)

_CONFIG_ADMIN_ROUTES = AgentdRouteMatcher(_CONFIG_ADMIN_ROUTE_DEFINITIONS)


def config_admin_route_definitions() -> tuple[AgentdRouteDefinition, ...]:
    return _CONFIG_ADMIN_ROUTE_DEFINITIONS


def _actor(request: HttpRequest) -> str:
    value = request.headers.get("x-acting-principal")
    return value or "api"


def _validation_error_response(error: ProfileStoreValidationError) -> HttpResponse:
    """422 per the config-plane convention: {errors: [{path, message}]}."""

    return json_response(
        immutable_json(
            {
                "status": "error",
                "errors": [dict(item) for item in error.errors],
            }
        ),
        status_code=422,
    )


def _config_profiles_list(store: ProfileStore) -> HttpResponse:
    """Store-backed listing: profiles persisted through the config API.

    Includes profiles not loaded into the running service. Complements
    GET /v1/profiles (loaded-only) so clients can show the full set.
    """

    return json_response(immutable_json({"stored_profiles": list(store.names())}))


async def handle_config_admin_route(
    store: ProfileStore | None,
    request: HttpRequest,
    method: str,
    path: str,
    *,
    config_store: DeploymentConfigStore | None = None,
) -> HttpResponse | None:
    if store is None:
        return None
    route = _CONFIG_ADMIN_ROUTES.match(path, method)
    if route is None or not route.method_allowed:
        return None  # fall through: same templates may serve runtime GET models

    body = request.body
    actor = _actor(request)
    try:
        if route.name == "config_profiles_create":
            payload = store.create(dict(body), actor=actor)
            return json_response(immutable_json(payload), status_code=201)

        if route.name == "config_profiles_list":
            return _config_profiles_list(store)

        if route.name == "config_profile_write":
            name = route.path_params["profile"]
            if method == "PATCH":
                payload = store.patch(name, dict(body), actor=actor)
                return json_response(immutable_json(payload))
            store.delete(name, actor=actor)
            return HttpResponse(status_code=204, body=immutable_json())

        if route.name == "config_validate":
            kind = str(body.get("kind", ""))
            raw_payload = body.get("payload")
            if kind != "profile" or not isinstance(raw_payload, dict):
                return json_response(
                    immutable_json(
                        {
                            "status": "error",
                            "errors": [
                                {
                                    "path": "<root>",
                                    "message": (
                                        "unsupported kind or missing payload; "
                                        "supported kind: profile"
                                    ),
                                }
                            ],
                        }
                    ),
                    status_code=400,
                )
            errors = store.validate(dict(raw_payload))
            if errors:
                return json_response(
                    immutable_json({"status": "error", "errors": [dict(e) for e in errors]}),
                    status_code=400,
                )
            return json_response(immutable_json({"status": "ok", "kind": "profile"}))

        if route.name == "config_audit":
            limit_raw = (request.body or {}).get("limit")  # body unused for GET
            return json_response(immutable_json(_audit_payload(store, limit_raw)))

        if route.name == "config_read" and method == "PUT":
            if config_store is None:
                return json_response(
                    immutable_json(
                        {
                            "error": {
                                "code": "not_configured",
                                "message": "no deployment config store",
                            }
                        }
                    ),
                    status_code=503,
                )
            policies_raw = body.get("policies")
            preferences_raw = body.get("preferences")
            policies_list: list[dict[str, Any]] = []
            if isinstance(policies_raw, list):
                policies_list = [dict(p) for p in policies_raw if isinstance(p, dict)]
            prefs_dict: dict[str, Any] = (
                dict(preferences_raw) if isinstance(preferences_raw, dict) else {}
            )
            result = config_store.save(policies_list, prefs_dict, actor=actor)
            return json_response(immutable_json(result), status_code=200)

        if route.name == "config_read":
            if config_store is None:
                return json_response(
                    immutable_json(
                        {
                            "error": {
                                "code": "not_configured",
                                "message": "no deployment config store",
                            }
                        }
                    ),
                    status_code=503,
                )
            return json_response(immutable_json(dict(config_store.load())))

        if route.name == "config_domain_profiles":
            domain_name = route.path_params["name"]
            if method == "PUT":
                add_raw = body.get("add")
                remove_raw = body.get("remove")
                add_names = [str(x) for x in add_raw] if isinstance(add_raw, list) else []
                remove_names = [str(x) for x in remove_raw] if isinstance(remove_raw, list) else []
                settings_raw = body.get("settings")
                settings_map: JsonMapping | None = (
                    dict(settings_raw) if isinstance(settings_raw, dict) else None
                )
                results: list[dict[str, str]] = []
                binding_errors: list[dict[str, str]] = []
                for pname in add_names:
                    try:
                        store.set_domain_binding(
                            pname,
                            domain_name,
                            bound=True,
                            settings=settings_map,
                            actor=actor,
                        )
                        results.append({"profile": pname, "status": "bound"})
                    except (ProfileNotFoundError, ValueError) as exc:
                        binding_errors.append({"profile": pname, "message": str(exc)})
                for pname in remove_names:
                    try:
                        store.set_domain_binding(
                            pname,
                            domain_name,
                            bound=False,
                            actor=actor,
                        )
                        results.append({"profile": pname, "status": "unbound"})
                    except (ProfileNotFoundError, ValueError) as exc:
                        binding_errors.append({"profile": pname, "message": str(exc)})
                response: dict[str, Any] = {
                    "domain": domain_name,
                    "results": results,
                }
                if binding_errors:
                    response["errors"] = binding_errors
                return json_response(immutable_json(response))
            # GET: list profiles bound to this domain
            bound_profiles: list[str] = []
            for pname in store.names():
                config = store.load(pname)
                raw_domains = config.get("domains")
                if not isinstance(raw_domains, list):
                    continue
                for d in raw_domains:
                    if isinstance(d, dict) and str(d.get("name", "")) == domain_name:
                        bound_profiles.append(pname)
                        break
            return json_response(
                immutable_json(
                    {"domain": domain_name, "profiles": [str(p) for p in bound_profiles]}
                )
            )

        return not_found(f"unknown config-admin route: {route.name}")
    except ProfileAlreadyExistsError as exc:
        return json_response(
            immutable_json({"error": {"code": "already_exists", "message": str(exc)}}),
            status_code=409,
        )
    except ProfileNotFoundError as exc:
        return not_found(str(exc))
    except ProfileStoreValidationError as exc:
        return _validation_error_response(exc)
    except DeploymentConfigValidationError as exc:
        return json_response(
            immutable_json({"status": "error", "errors": [dict(e) for e in exc.errors]}),
            status_code=422,
        )
    except ProfileBuiltinError as exc:
        return json_response(
            immutable_json({"error": {"code": "builtin_profile", "message": str(exc)}}),
            status_code=409,
        )
    except ValueError as exc:
        return json_response(
            immutable_json({"error": {"code": "bad_request", "message": str(exc)}}),
            status_code=400,
        )


def _audit_payload(store: ProfileStore, limit_raw: object) -> dict[str, JsonValue]:
    limit: int | None = None
    if isinstance(limit_raw, int) and not isinstance(limit_raw, bool) and limit_raw > 0:
        limit = limit_raw
    records = store.audit_records(limit=limit)
    payload: dict[str, JsonValue] = {
        "records": [dict(record) for record in records],
        "count": len(records),
    }
    return payload


def _apply_domain_bindings(
    store: ProfileStore,
    domain_name: str,
    body: dict[str, JsonValue],
    *,
    actor: str,
) -> dict[str, JsonValue]:
    add_raw = body.get("add")
    remove_raw = body.get("remove")
    settings_raw = body.get("settings")
    add_names = [str(item) for item in add_raw] if isinstance(add_raw, list) else []
    remove_names = [str(item) for item in remove_raw] if isinstance(remove_raw, list) else []
    settings_mapping: JsonMapping | None = settings_raw if isinstance(settings_raw, dict) else None

    results: list[JsonValue] = []
    errors: list[dict[str, str]] = []
    for profile_name in add_names:
        try:
            store.set_domain_binding(
                profile_name,
                domain_name,
                bound=True,
                settings=settings_mapping,
                actor=actor,
            )
            results.append({"profile": profile_name, "status": "bound"})
        except (ProfileNotFoundError, ValueError) as exc:
            errors.append({"profile": profile_name, "message": str(exc)})
    for profile_name in remove_names:
        try:
            store.set_domain_binding(profile_name, domain_name, bound=False, actor=actor)
            results.append({"profile": profile_name, "status": "unbound"})
        except (ProfileNotFoundError, ValueError) as exc:
            errors.append({"profile": profile_name, "message": str(exc)})

    payload: dict[str, JsonValue] = {"domain": domain_name, "results": results}
    if errors:
        payload["errors"] = [dict(item) for item in errors]
    return payload
