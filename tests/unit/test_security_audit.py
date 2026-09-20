"""Audit event recording tests (Phase 3)."""

from __future__ import annotations

from universal_agent.security import (
    AuditEvent,
    FileAuditRecorder,
    InMemoryAuditRecorder,
)


def test_in_memory_recorder_records_and_lists() -> None:
    recorder = InMemoryAuditRecorder()
    recorder.record(AuditEvent(event="denied", actor="mallory", reason="cross_tenant"))
    recorder.record(AuditEvent(event="tenant_created", actor="boss", tenant_id="acme"))

    events = recorder.events()
    assert [e.event for e in events] == ["denied", "tenant_created"]
    assert events[0].reason == "cross_tenant"
    assert events[1].tenant_id == "acme"


def test_in_memory_recorder_limit_and_capacity() -> None:
    recorder = InMemoryAuditRecorder(capacity=3)
    for index in range(5):
        recorder.record(AuditEvent(event=f"e{index}", actor="a"))
    assert [e.event for e in recorder.events()] == ["e2", "e3", "e4"]
    assert [e.event for e in recorder.events(limit=2)] == ["e3", "e4"]


def test_to_json_omits_empty_fields() -> None:
    payload = AuditEvent(event="denied", actor="x", reason="rbac").to_json()
    assert payload == {
        "event": "denied",
        "actor": "x",
        "reason": "rbac",
        "occurred_at": payload["occurred_at"],
    }


def test_file_recorder_round_trip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    recorder = FileAuditRecorder(tmp_path / "audit" / "security-audit.jsonl")
    recorder.record(AuditEvent(event="credential_issued", actor="boss", tenant_id="acme"))
    recorder.record(AuditEvent(event="denied", actor="m", reason="rbac"))

    events = recorder.events()
    assert [e.event for e in events] == ["credential_issued", "denied"]
    assert events[0].tenant_id == "acme"