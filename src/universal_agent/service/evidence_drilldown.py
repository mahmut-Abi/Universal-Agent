"""Evidence drill-down projection for operator surfaces.

Builds an evidence-list payload with claim/source/action linkage and
verification status so the web console and TUI can answer "what supports
this outcome?" without opening the raw session JSON.

Pure projection: consumes ``EvidenceView`` objects and returns JSON-safe
payloads. No I/O, no state. Verification status is honest: evidence rows
report their confidence and source, not a fabricated verdict.
"""

from __future__ import annotations

from typing import cast

from universal_agent.core import JsonMapping, SessionId, to_json_object
from universal_agent.runtime import EvidenceView

__all__ = ["evidence_drilldown_body"]


def _domain_label(record: EvidenceView) -> str | None:
    if not record.domain_name:
        return None
    return (
        f"{record.domain_name}@{record.domain_version}"
        if record.domain_version
        else record.domain_name
    )


def evidence_drilldown_body(
    session_id: SessionId,
    evidence: tuple[EvidenceView, ...],
) -> JsonMapping:
    """Evidence list payload with action/observation linkage per record."""

    records = [
        cast(
            JsonMapping,
            to_json_object(
                {
                    "evidence_id": str(record.evidence_id),
                    "subject": record.subject,
                    "claim": record.claim,
                    "value": record.value,
                    "confidence": record.confidence,
                    "source": record.source,
                    "action_id": str(record.action_id),
                    "observation_id": str(record.observation_id),
                    "task_id": str(record.task_id),
                    "domain": _domain_label(record),
                    "observed_at": record.observed_at,
                },
                fallback_to_string=True,
            ),
        )
        for record in evidence
    ]

    subjects = sorted({str(record["subject"]) for record in records})
    domains = sorted({str(record["domain"]) for record in records if record["domain"] is not None})

    return cast(
        JsonMapping,
        to_json_object(
            {
                "session_id": str(session_id),
                "evidence_count": len(records),
                "subjects": subjects,
                "domains": domains,
                "evidence": records,
            },
            fallback_to_string=True,
        ),
    )
