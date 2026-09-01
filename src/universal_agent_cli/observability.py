from __future__ import annotations

import argparse
from typing import TextIO, cast

from universal_agent.agentd.representations import (
    audit_integrity_body,
    audit_records_body,
    cost_body,
    doctor_body,
    health_body,
    log_records_body,
    metrics_body,
    multi_agent_body,
    ready_body,
    state_event_repair_body,
    trace_spans_body,
)
from universal_agent.service import RuntimeService
from universal_agent_cli.io import (
    CliExit,
    _doctor_should_fail,
    _write_json,
    _write_text,
)


async def _dispatch_observability(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    command = cast(str, args.command)
    if command == "health":
        _write_json(out, health_body(service.health()))
        return
    if command == "ready":
        _write_json(out, ready_body(service.ready()))
        return
    if command == "metrics":
        if cast(str, args.format) == "prometheus":
            _write_text(out, await service.prometheus_metrics())
            return
        _write_json(out, metrics_body(await service.metrics()))
        return
    if command == "cost":
        _write_json(out, cost_body(await service.cost()))
        return
    if command == "logs":
        _write_json(out, log_records_body(await service.logs()))
        return
    if command == "traces":
        if cast(str, args.format) == "otlp":
            _write_json(out, await service.opentelemetry_traces())
            return
        _write_json(out, trace_spans_body(await service.traces()))
        return
    if command == "doctor":
        report = await service.doctor()
        _write_json(out, doctor_body(report))
        if _doctor_should_fail(report.status, cast(str, args.fail_on)):
            raise CliExit(1)
        return
    if command == "audit":
        if cast(bool, args.integrity):
            _write_json(out, audit_integrity_body(await service.audit_integrity()))
            return
        _write_json(out, audit_records_body(await service.audit_records()))
        return
    if command == "multi-agent":
        _write_json(out, multi_agent_body(service.multi_agent()))
        return
    if command == "repair":
        repair_command = cast(str, args.repair_command)
        if repair_command == "state-events":
            confirmed = cast(str, args.confirmed) == "true"
            _write_json(
                out,
                state_event_repair_body(
                    await service.repair_state_event_consistency(
                        confirmed=confirmed,
                        dry_run=cast(bool, args.dry_run),
                    )
                ),
            )
            return
        raise ValueError(f"unknown repair command: {repair_command}")
