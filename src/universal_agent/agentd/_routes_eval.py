"""HTTP routes for the evaluation and ecosystem commands.

These routes expose the CLI's eval/ecosystem dispatch (which lives in the
kernel now) over the Runtime API, so the CLI can run them remotely without
importing kernel internals. Output payloads mirror the CLI output bodies.
"""

from __future__ import annotations

import argparse
import json
from io import StringIO

from universal_agent.agentd.http import (
    HttpRequest,
    HttpResponse,
    json_response,
    method_not_allowed,
)
from universal_agent.agentd.routing import AgentdRouteDefinition, AgentdRouteMatcher
from universal_agent.core import JsonMapping, immutable_json
from universal_agent.ecosystem.dispatch import _dispatch_ecosystem
from universal_agent.evaluation.dispatch import DispatchExit, _dispatch_eval
from universal_agent.service import RuntimeService

_EVAL_ROUTE_DEFINITIONS = (
    AgentdRouteDefinition("eval_list", "/v1/eval/list", ("POST",)),
    AgentdRouteDefinition("eval_run", "/v1/eval/run", ("POST",)),
    AgentdRouteDefinition("eval_replay", "/v1/eval/replay", ("POST",)),
    AgentdRouteDefinition("eval_recordings", "/v1/eval/recordings", ("POST",)),
    AgentdRouteDefinition("eval_compare", "/v1/eval/compare", ("POST",)),
    AgentdRouteDefinition("eval_reports", "/v1/eval/reports", ("POST",)),
    AgentdRouteDefinition("eval_datasets", "/v1/eval/datasets", ("POST",)),
    AgentdRouteDefinition("eval_dataset", "/v1/eval/dataset", ("POST",)),
)
_EVAL_ROUTES = AgentdRouteMatcher(_EVAL_ROUTE_DEFINITIONS)

_EVAL_COMMAND_NAMES = {
    "eval_list": "list",
    "eval_run": "run",
    "eval_replay": "replay",
    "eval_recordings": "recordings",
    "eval_compare": "compare",
    "eval_reports": "reports",
    "eval_datasets": "datasets",
    "eval_dataset": "dataset",
}

_ECOSYSTEM_ROUTE_DEFINITIONS = (
    AgentdRouteDefinition("ecosystem_catalog", "/v1/ecosystem/catalog", ("POST",)),
    AgentdRouteDefinition("ecosystem_verify", "/v1/ecosystem/verify", ("POST",)),
    AgentdRouteDefinition("ecosystem_export", "/v1/ecosystem/export", ("POST",)),
    AgentdRouteDefinition("ecosystem_registry", "/v1/ecosystem/registry", ("POST",)),
    AgentdRouteDefinition("ecosystem_install", "/v1/ecosystem/install", ("POST",)),
    AgentdRouteDefinition("ecosystem_store", "/v1/ecosystem/store", ("POST",)),
)
_ECOSYSTEM_ROUTES = AgentdRouteMatcher(_ECOSYSTEM_ROUTE_DEFINITIONS)

_ECOSYSTEM_COMMAND_NAMES = {
    "ecosystem_catalog": "catalog",
    "ecosystem_verify": "verify",
    "ecosystem_export": "export",
    "ecosystem_registry": "registry",
    "ecosystem_install": "install",
    "ecosystem_store": "store",
}


def _text(body: JsonMapping, key: str) -> str | None:
    value = body.get(key)
    return value if isinstance(value, str) and value else None


def _text_list(body: JsonMapping, key: str) -> list[str] | None:
    value = body.get(key)
    if not isinstance(value, list):
        return None
    items = [item for item in value if isinstance(item, str)]
    return items or None


def _flag(body: JsonMapping, key: str) -> bool:
    value = body.get(key)
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.lower() == "true"


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{value!r} must be a finite number") from exc


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _eval_namespace(operation: str, body: JsonMapping) -> argparse.Namespace:
    return argparse.Namespace(
        eval_command=operation,
        profile=_text(body, "profile") or "local-kubernetes",
        suite=_text(body, "suite") or "local evaluation suite",
        suite_file=_text(body, "suite_file"),
        kind=_text_list(body, "kind"),
        tag=_text_list(body, "tag"),
        exclude_tag=_text_list(body, "exclude_tag"),
        report_dir=_text(body, "report_dir"),
        recording_dir=_text(body, "recording_dir"),
        update=_flag(body, "update"),
        fail_on_fail=_flag(body, "fail_on_fail"),
        format="json",
        min_pass_rate=_optional_float(body.get("min_pass_rate")),
        min_goal_completion_rate=_optional_float(body.get("min_goal_completion_rate")),
        min_task_success_rate=_optional_float(body.get("min_task_success_rate")),
        min_action_success_rate=_optional_float(body.get("min_action_success_rate")),
        max_tool_failure_rate=_optional_float(body.get("max_tool_failure_rate")),
        max_policy_denial_rate=_optional_float(body.get("max_policy_denial_rate")),
        max_average_recoveries=_optional_float(body.get("max_average_recoveries")),
        max_human_intervention_rate=_optional_float(body.get("max_human_intervention_rate")),
        max_average_actions=_optional_float(body.get("max_average_actions")),
        max_average_active_resource_locks=_optional_float(
            body.get("max_average_active_resource_locks")
        ),
        max_average_duration_ms=_optional_float(body.get("max_average_duration_ms")),
        max_average_model_calls=_optional_float(body.get("max_average_model_calls")),
        max_average_model_tokens=_optional_float(body.get("max_average_model_tokens")),
        max_resource_conflict_rate=_optional_float(body.get("max_resource_conflict_rate")),
        max_total_model_cost_micros=_optional_int(body.get("max_total_model_cost_micros")),
        expected=_text(body, "expected"),
        actual=_text(body, "actual"),
        dataset_dir=_text(body, "dataset_dir"),
        domain=_text(body, "domain"),
        name=_text(body, "name"),
        version=_text(body, "version"),
        verify=_flag(body, "verify"),
    )


def _ecosystem_namespace(operation: str, body: JsonMapping) -> argparse.Namespace:
    return argparse.Namespace(
        ecosystem_command=operation,
        domain_package_dir=_text(body, "domain_package_dir"),
        dataset_dir=_text(body, "dataset_dir"),
        profile_dir=_text(body, "profile_dir"),
        name=_text(body, "name"),
        version=_text(body, "version"),
        description=_text(body, "description"),
        output=_text(body, "output"),
        force=_flag(body, "force"),
        manifest=_text(body, "manifest"),
        verify=_flag(body, "verify"),
        base_path=_text(body, "base_path"),
        no_verify=_flag(body, "no_verify"),
        plan_only=_flag(body, "plan_only"),
        allow_unverified_signatures=_flag(body, "allow_unverified_signatures"),
        store_dir=_text(body, "store_dir"),
        ecosystem_store_command=_text(body, "store_command") or "list",
    )


async def _run_eval_dispatch(args: argparse.Namespace, service: RuntimeService) -> JsonMapping:
    out = StringIO()
    try:
        await _dispatch_eval(args, service, out)
    except DispatchExit:
        pass
    except Exception as exc:
        return immutable_json({"error": {"type": type(exc).__name__, "message": str(exc)}})
    return _json_payload(out)


def _run_ecosystem_dispatch(args: argparse.Namespace) -> JsonMapping:
    out = StringIO()
    try:
        _dispatch_ecosystem(args, out)
    except Exception as exc:
        return immutable_json({"error": {"type": type(exc).__name__, "message": str(exc)}})
    return _json_payload(out)


def _json_payload(out: StringIO) -> JsonMapping:
    text = out.getvalue()
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return immutable_json({"text": text})
    return immutable_json(loaded) if isinstance(loaded, dict) else immutable_json({"value": loaded})


async def handle_eval_route(
    service: RuntimeService,
    request: HttpRequest,
    method: str,
    path: str,
) -> HttpResponse | None:
    route = _EVAL_ROUTES.match(path, method)
    if route is None:
        return None
    if not route.method_allowed:
        return method_not_allowed(route.allowed_methods)

    operation = _EVAL_COMMAND_NAMES[route.name]
    payload = await _run_eval_dispatch(_eval_namespace(operation, request.body), service)
    return json_response(payload)


def handle_ecosystem_route(
    service: RuntimeService,
    request: HttpRequest,
    method: str,
    path: str,
) -> HttpResponse | None:
    route = _ECOSYSTEM_ROUTES.match(path, method)
    if route is None:
        return None
    if not route.method_allowed:
        return method_not_allowed(route.allowed_methods)

    operation = _ECOSYSTEM_COMMAND_NAMES[route.name]
    payload = _run_ecosystem_dispatch(_ecosystem_namespace(operation, request.body))
    return json_response(payload, status_code=200)


def eval_route_definitions() -> tuple[AgentdRouteDefinition, ...]:
    return _EVAL_ROUTE_DEFINITIONS


def ecosystem_route_definitions() -> tuple[AgentdRouteDefinition, ...]:
    return _ECOSYSTEM_ROUTE_DEFINITIONS
