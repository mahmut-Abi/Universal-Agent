from __future__ import annotations

from collections.abc import Mapping

import pytest

from universal_agent.core import JsonValue, immutable_json
from universal_agent.domains.observability.backend import (
    StaticMetricsBackend,
    resource_subject_from_labels,
)
from universal_agent.domains.observability.prometheus import (
    PrometheusBackend,
    PrometheusQueryError,
    PrometheusResponse,
)

pytestmark = pytest.mark.unit


class FakePrometheusTransport:
    def __init__(self, responses: Mapping[str, PrometheusResponse]) -> None:
        self._responses = dict(responses)
        self.requests: list[tuple[str, dict[str, str]]] = []

    async def request(
        self,
        path: str,
        *,
        query: Mapping[str, str],
        timeout_seconds: float | None = None,
    ) -> PrometheusResponse:
        self.requests.append((path, dict(query)))
        response = self._responses.get(path)
        if response is None:
            raise AssertionError(f"unexpected prometheus path: {path}")
        return response


def _success(data: JsonValue) -> PrometheusResponse:
    return PrometheusResponse(200, {"status": "success", "data": data})


def _backend(**responses: PrometheusResponse) -> tuple[PrometheusBackend, FakePrometheusTransport]:
    transport = FakePrometheusTransport(responses)
    return PrometheusBackend("http://prometheus:9090", transport=transport), transport


def test_resource_subject_from_labels_follows_kubernetes_label_priority() -> None:
    assert resource_subject_from_labels({"pod": "api-x", "namespace": "prod"}) == "pod/api-x"
    assert resource_subject_from_labels({"deployment": "api"}) == "deployment/api"
    assert resource_subject_from_labels({"service": "api-svc"}) == "service/api-svc"
    assert resource_subject_from_labels({"namespace": "prod"}) == "namespace/prod"


def test_resource_subject_from_labels_handles_missing_and_unknown_labels() -> None:
    assert resource_subject_from_labels(None) is None
    assert resource_subject_from_labels({}) is None
    assert resource_subject_from_labels({"job": "kubelet", "instance": "1.2.3.4"}) is None
    assert resource_subject_from_labels({"pod": "  "}) is None
    assert resource_subject_from_labels({"pod": 7}) is None


@pytest.mark.asyncio
async def test_prometheus_instant_query_returns_metric_value_and_resource_subject() -> None:
    backend, transport = _backend(
        **{
            "/api/v1/query": _success(
                {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {"pod": "api-ready", "namespace": "prod"},
                            "value": [1767000000, "0.91"],
                        }
                    ],
                }
            )
        }
    )

    body = await backend.query(immutable_json({"query": "kube_pod_status_ready"}))

    assert body["sample_count"] == 1
    assert body["metric_value"] == 0.91
    assert body["resource_subject"] == "pod/api-ready"
    assert "subject" not in body
    assert transport.requests == [("/api/v1/query", {"query": "kube_pod_status_ready"})]


@pytest.mark.asyncio
async def test_prometheus_instant_query_keeps_explicit_subject() -> None:
    backend, _ = _backend(
        **{
            "/api/v1/query": _success(
                {
                    "resultType": "vector",
                    "result": [{"metric": {}, "value": [1767000000, "2"]}],
                }
            )
        }
    )

    body = await backend.query(immutable_json({"query": "up", "subject": "deployment/example"}))

    assert body["subject"] == "deployment/example"
    assert body["metric_value"] == 2.0
    assert "resource_subject" not in body


@pytest.mark.asyncio
async def test_prometheus_query_range_summarizes_matrix_samples() -> None:
    backend, transport = _backend(
        **{
            "/api/v1/query_range": _success(
                {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"deployment": "api"},
                            "values": [[1767000000, "1"], [1767000060, "2.5"]],
                        },
                        {
                            "metric": {"deployment": "api"},
                            "values": [[1767000000, "5"]],
                        },
                    ],
                }
            )
        }
    )

    body = await backend.query_range(
        immutable_json(
            {
                "query": "kube_deployment_status_replicas",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-01T00:10:00Z",
                "step": "60s",
            }
        )
    )

    assert body["result_type"] == "matrix"
    assert body["series_count"] == 2
    assert body["samples_total"] == 3
    assert body["first_value"] == 1.0
    assert body["last_value"] == 2.5
    assert body["resource_subject"] == "deployment/api"
    assert transport.requests == [
        (
            "/api/v1/query_range",
            {
                "query": "kube_deployment_status_replicas",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-01T00:10:00Z",
                "step": "60s",
            },
        )
    ]


@pytest.mark.asyncio
async def test_prometheus_query_range_requires_range_arguments() -> None:
    backend, _ = _backend()

    with pytest.raises(ValueError, match="start"):
        await backend.query_range(immutable_json({"query": "up", "end": "x", "step": "1m"}))


@pytest.mark.asyncio
async def test_prometheus_rules_counts_rules_health_and_firing_alerts() -> None:
    backend, transport = _backend(
        **{
            "/api/v1/rules": _success(
                {
                    "groups": [
                        {
                            "name": "workload",
                            "rules": [
                                {"name": "PodNotReady", "type": "alerting", "health": "ok"},
                                {
                                    "name": "DeploymentScaledDown",
                                    "type": "alerting",
                                    "health": "err",
                                    "alerts": [{"state": "firing"}, {"state": "pending"}],
                                },
                            ],
                        },
                        {
                            "name": "capacity",
                            "rules": [{"name": "replicas", "type": "recording", "health": "ok"}],
                        },
                    ]
                }
            )
        }
    )

    body = await backend.rules(immutable_json({}))

    assert body == {
        "rule_count": 3,
        "alerting_rule_count": 2,
        "recording_rule_count": 1,
        "unhealthy_rule_count": 1,
        "firing_alert_count": 1,
    }
    assert transport.requests == [("/api/v1/rules", {})]


@pytest.mark.asyncio
async def test_prometheus_alerts_counts_states_and_maps_resource_subjects() -> None:
    backend, _ = _backend(
        **{
            "/api/v1/alerts": _success(
                {
                    "alerts": [
                        {"state": "firing", "labels": {"deployment": "api"}},
                        {"state": "firing", "labels": {"pod": "api-failing"}},
                        {"state": "pending", "labels": {"job": "node"}},
                    ]
                }
            )
        }
    )

    body = await backend.alerts(immutable_json({}))

    assert body["alert_count"] == 3
    assert body["firing_alert_count"] == 2
    assert body["pending_alert_count"] == 1
    assert body["resource_subjects"] == ["deployment/api", "pod/api-failing"]


@pytest.mark.asyncio
async def test_prometheus_backend_raises_on_http_and_prometheus_errors() -> None:
    http_error_backend, _ = _backend(
        **{"/api/v1/query": PrometheusResponse(503, None, "unavailable")}
    )
    with pytest.raises(PrometheusQueryError, match="unavailable"):
        await http_error_backend.query(immutable_json({"query": "up"}))

    prom_error_backend, _ = _backend(
        **{
            "/api/v1/query": PrometheusResponse(
                200,
                {"status": "error", "error": "parse error"},
            )
        }
    )
    with pytest.raises(PrometheusQueryError, match="parse error"):
        await prom_error_backend.query(immutable_json({"query": "up"}))


@pytest.mark.asyncio
async def test_static_backend_supports_range_rules_and_alerts_fixtures() -> None:
    backend = StaticMetricsBackend(
        range_responses={
            "kube_pod_status_ready": {"series_count": 1, "samples_total": 4, "last_value": 1.0}
        },
        default_range_response={"series_count": 0, "samples_total": 0},
        rules_response={"rule_count": 2, "alerting_rule_count": 1, "recording_rule_count": 1},
        alerts_response={
            "alert_count": 1,
            "firing_alert_count": 1,
            "resource_subjects": ["pod/api-failing"],
        },
    )

    ranged = await backend.query_range(
        immutable_json({"query": "kube_pod_status_ready", "start": "s", "end": "e", "step": "1m"})
    )
    assert ranged["series_count"] == 1
    assert ranged["last_value"] == 1.0
    assert len(backend.range_calls) == 1

    empty = await backend.query_range(
        immutable_json({"query": "unknown", "start": "s", "end": "e", "step": "1m"})
    )
    assert empty["series_count"] == 0

    rules_body = await backend.rules(immutable_json({}))
    assert rules_body["rule_count"] == 2
    alerts_body = await backend.alerts(immutable_json({}))
    assert alerts_body["firing_alert_count"] == 1
    assert backend.rule_calls == [immutable_json({})]
    assert backend.alert_calls == [immutable_json({})]
