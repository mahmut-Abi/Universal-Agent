from __future__ import annotations

from typing import cast

from universal_agent.core import JsonMapping, JsonValue, ObservationStatus
from universal_agent.evidence import Evidence, EvidenceContext


class KubernetesEvidenceExtractor:
    name = "kubernetes-evidence"

    def extract(self, context: EvidenceContext) -> tuple[Evidence, ...]:
        if context.observation.status is not ObservationStatus.SUCCEEDED:
            return ()

        subject = _observation_subject(context)
        evidence = [
            _evidence(context, subject, key, value)
            for key, value in context.observation.data.items()
        ]
        evidence.extend(_diagnostic_evidence(context, subject))
        evidence.extend(_pod_evidence(context, subject, context.observation.data.get("pods")))
        return tuple(evidence)


def _pod_evidence(
    context: EvidenceContext,
    workload_subject: str,
    raw_pods: JsonValue | None,
) -> list[Evidence]:
    if not isinstance(raw_pods, list):
        return []

    evidence: list[Evidence] = []
    pod_resources: list[JsonValue] = []
    for raw_pod in raw_pods:
        if not isinstance(raw_pod, dict):
            continue
        pod = cast(JsonMapping, raw_pod)
        pod_resource = _valid_resource(pod.get("resource"), expected_kind="pod")
        if pod_resource is None:
            continue

        pod_resources.append(pod_resource)
        evidence.append(_evidence(context, pod_resource, "kind", "Pod"))
        for key, value in pod.items():
            evidence.append(_evidence(context, pod_resource, key, value))

    if pod_resources:
        evidence.append(_evidence(context, workload_subject, "relation:owns", pod_resources))
    return evidence


def _diagnostic_evidence(context: EvidenceContext, subject: str) -> list[Evidence]:
    if _valid_resource(context.observation.data.get("resource"), expected_kind="pod") is None:
        return []
    if "recent_logs" in context.observation.data:
        return [_evidence(context, subject, "pod_diagnostics_observed", True)]
    return []


def _observation_subject(context: EvidenceContext) -> str:
    resource = context.observation.data.get("resource")
    if isinstance(resource, str) and resource.strip():
        return resource.strip()
    return context.observation.source


def _evidence(
    context: EvidenceContext,
    subject: str,
    claim: str,
    value: JsonValue,
) -> Evidence:
    return Evidence(
        context.session_id,
        context.task.id,
        context.observation.action_id,
        context.observation.id,
        subject,
        claim,
        value,
        context.observation.source,
        0.99,
        observed_at=context.observation.observed_at,
    )


def _valid_resource(value: JsonValue | None, *, expected_kind: str) -> str | None:
    if not isinstance(value, str):
        return None
    resource = value.strip()
    prefix = f"{expected_kind}/"
    if not resource.startswith(prefix) or resource == prefix:
        return None
    return resource
