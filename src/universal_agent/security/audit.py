"""Audit event recording (Phase 3).

Covers the event-source side of the audit design: admin-plane mutations and
authentication denials produce structured, reason-tagged audit events. Durable
/ tamper-resistant audit *storage* remains a deferred decision (see
docs/security-production-decisions.md, Audit storage row) — this module only
guarantees the events exist, with an in-memory ring and an optional JSONL sink
following the config-audit precedent.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from universal_agent.core import JsonValue, utc_now


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One auditable security-relevant occurrence."""

    event: str  # e.g. "denied", "tenant_created", "credential_issued"
    actor: str  # subject id, "anonymous" when unauthenticated
    reason: str | None = None  # denial reason: rbac | cross_tenant | unauthorized
    tenant_id: str | None = None
    resource: str | None = None  # e.g. request path or credential id
    details: dict[str, JsonValue] = field(default_factory=dict)
    occurred_at: str = field(default_factory=lambda: utc_now().isoformat())

    def to_json(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "event": self.event,
            "actor": self.actor,
            "occurred_at": self.occurred_at,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.tenant_id is not None:
            payload["tenant_id"] = self.tenant_id
        if self.resource is not None:
            payload["resource"] = self.resource
        if self.details:
            payload["details"] = self.details
        return payload


class AuditRecorder(Protocol):
    """Receives audit events; implementations decide durability."""

    def record(self, event: AuditEvent) -> None: ...
    def events(self, *, limit: int | None = None) -> tuple[AuditEvent, ...]: ...


class InMemoryAuditRecorder:
    """Bounded in-memory ring of audit events (dev/test and embedded use)."""

    def __init__(self, *, capacity: int = 1000) -> None:
        self._events: deque[AuditEvent] = deque(maxlen=capacity)

    def record(self, event: AuditEvent) -> None:
        self._events.append(event)

    def events(self, *, limit: int | None = None) -> tuple[AuditEvent, ...]:
        if limit is not None and limit >= 0:
            return tuple(list(self._events)[-limit:])
        return tuple(self._events)


class FileAuditRecorder:
    """Append-only JSONL audit sink (single-process deployments).

    Follows the config-audit precedent: one JSON object per line, oldest
    first. Rotation/retention/tamper-proofing are deferred decisions.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def record(self, event: AuditEvent) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_json(), sort_keys=True) + "\n")

    def events(self, *, limit: int | None = None) -> tuple[AuditEvent, ...]:
        if not self._path.is_file():
            return ()
        events: list[AuditEvent] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            loaded = json.loads(line)
            events.append(
                AuditEvent(
                    event=str(loaded.get("event", "")),
                    actor=str(loaded.get("actor", "")),
                    reason=loaded.get("reason"),
                    tenant_id=loaded.get("tenant_id"),
                    resource=loaded.get("resource"),
                    details=loaded.get("details") or {},
                    occurred_at=str(loaded.get("occurred_at", "")),
                )
            )
        if limit is not None and limit >= 0:
            return tuple(events[-limit:])
        return tuple(events)
