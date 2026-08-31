"""Gated live Prometheus contract: real HTTP backend, redacted contract artifacts."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from universal_agent.core import JsonMapping, immutable_json
from universal_agent.domains.kubernetes.live_contract import (
    write_kubernetes_live_contract_artifact,
)
from universal_agent.domains.observability.prometheus import PrometheusBackend

ENABLED_ENV = "UNIVERSAL_AGENT_LIVE_PROMETHEUS_ENABLED"
URL_ENV = "UNIVERSAL_AGENT_LIVE_PROMETHEUS_URL"
ARTIFACT_DIR_ENV = "UNIVERSAL_AGENT_LIVE_PROMETHEUS_ARTIFACT_DIR"
DEFAULT_ARTIFACT_DIR = ".universal-agent/live-prometheus/artifacts"

pytestmark = pytest.mark.live


def _require_enabled() -> str:
    if os.environ.get(ENABLED_ENV) != "true":
        pytest.skip(f"set {ENABLED_ENV}=true to execute the live Prometheus contract")
    base_url = os.environ.get(URL_ENV)
    if not base_url:
        pytest.skip(f"set {URL_ENV} to run live Prometheus contract tests")
    return base_url


def _write_artifact(name: str, payload: JsonMapping) -> None:
    artifact_dir = os.environ.get(ARTIFACT_DIR_ENV, DEFAULT_ARTIFACT_DIR)
    write = write_kubernetes_live_contract_artifact(
        artifact_dir,
        name=name,
        status=0,
        payload=payload,
    )
    assert write.path.exists()


@pytest.mark.asyncio
async def test_prometheus_live_contract_covers_query_range_rules_alerts() -> None:
    base_url = _require_enabled()
    backend = PrometheusBackend(base_url)
    end = datetime.now(tz=UTC)
    start = end - timedelta(minutes=5)

    instant = await backend.query(immutable_json({"query": "up"}))
    ranged = await backend.query_range(
        immutable_json(
            {
                "query": "up",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "step": "15s",
            }
        )
    )
    rules_body = await backend.rules(immutable_json({}))
    alerts_body = await backend.alerts(immutable_json({}))

    _write_artifact("prometheus-instant-query", instant)
    _write_artifact("prometheus-range-query", ranged)
    _write_artifact("prometheus-rules-inspection", rules_body)
    _write_artifact("prometheus-alerts-inspection", alerts_body)

    sample_count = instant.get("sample_count")
    assert isinstance(sample_count, int) and sample_count >= 1
    assert ranged["result_type"] == "matrix"
    samples_total = ranged.get("samples_total")
    assert isinstance(samples_total, int) and samples_total >= 1
    rule_count = rules_body.get("rule_count")
    assert isinstance(rule_count, int) and rule_count >= 0
    alert_count = alerts_body.get("alert_count")
    assert isinstance(alert_count, int) and alert_count >= 0
