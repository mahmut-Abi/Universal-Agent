"""Structured-400 guards for eval/ecosystem routes missing required paths.

Regression: a missing report_dir/dataset_dir/manifest reached store
constructors as None and surfaced as a 500 TypeError (eval) or a 200-wrapped
error body (ecosystem) instead of a structured 400 naming the parameter.
"""

from __future__ import annotations

from argparse import Namespace
from typing import cast

import pytest

from universal_agent.agentd._routes_eval import (
    _ecosystem_namespace,
    _eval_namespace,
    _missing_ecosystem_required,
    handle_ecosystem_route,
)
from universal_agent.agentd.http import HttpRequest
from universal_agent.core import immutable_json
from universal_agent.service import RuntimeService


class _StubService:
    def profiles(self) -> tuple[()]:  # pragma: no cover - only used for default profile
        return ()


def _eval_args(operation: str) -> Namespace:
    return _eval_namespace(operation, {}, cast(object, _StubService()))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("operation", "param"),
    [
        ("reports", "report_dir"),
        ("datasets", "dataset_dir"),
        ("recordings", "recording_dir"),
        ("replay", "recording_dir"),
    ],
)
def test_eval_missing_dir_param_is_named(operation: str, param: str) -> None:
    from universal_agent.agentd._routes_eval import _missing_required_dir

    message = _missing_required_dir(operation, _eval_args(operation))
    assert message is not None and param in message


def test_eval_compare_requires_report_paths() -> None:
    from universal_agent.agentd._routes_eval import _missing_required_dir

    message = _missing_required_dir("compare", _eval_args("compare"))
    assert message is not None and "expected" in message and "actual" in message


def test_eval_run_has_no_required_dir() -> None:
    from universal_agent.agentd._routes_eval import _missing_required_dir

    assert _missing_required_dir("run", _eval_args("run")) is None


def test_ecosystem_registry_without_manifest_returns_400() -> None:
    request = HttpRequest("POST", "/v1/ecosystem/registry", immutable_json({}))
    response = handle_ecosystem_route(
        cast(RuntimeService, _StubService()), request, "POST", "/v1/ecosystem/registry"
    )
    assert response is not None and response.status_code == 400
    assert "manifest" in str(response.body)


def test_ecosystem_store_without_store_dir_returns_400() -> None:
    request = HttpRequest("POST", "/v1/ecosystem/store", immutable_json({}))
    response = handle_ecosystem_route(
        cast(RuntimeService, _StubService()), request, "POST", "/v1/ecosystem/store"
    )
    assert response is not None and response.status_code == 400
    assert "store_dir" in str(response.body)


def test_ecosystem_catalog_has_no_required_params() -> None:
    args = _ecosystem_namespace("catalog", immutable_json({}))
    assert _missing_ecosystem_required("catalog", args) is None
