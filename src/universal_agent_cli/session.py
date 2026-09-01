from __future__ import annotations

import argparse
from typing import TextIO, cast

from universal_agent.agentd.representations import (
    audit_integrity_body,
    audit_records_body,
    cost_body,
    event_batch_body,
    log_records_body,
    runtime_run_body,
    session_batch_body,
    sse_event_batch_text,
    trace_spans_body,
)
from universal_agent.agentd.session_representations import (
    session_body,
    session_evidence_body,
    session_explorer_body,
    session_world_body,
)
from universal_agent.core import EventId, SessionId
from universal_agent.core.config_validation import parse_bounded_float
from universal_agent.core.polling import poll_async_result
from universal_agent.runtime.api import RuntimeEventBatch
from universal_agent.service import RuntimeService
from universal_agent_cli.io import (
    _optional_bool,
    _write_json,
    _write_text,
)


async def _dispatch_session(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    command = cast(str, args.session_command)
    if command == "list":
        after = cast(str | None, args.after)
        limit = cast(int | None, args.limit)
        _write_json(
            out,
            session_batch_body(
                await service.stream_sessions(
                    after_session_id=None if after is None else SessionId(after),
                    limit=limit,
                )
            ),
        )
        return
    if command == "show":
        session_id = SessionId(cast(str, args.session_id))
        _write_json(out, session_body(await service.get_session(session_id)))
        return
    if command == "diagnostics":
        session_id = SessionId(cast(str, args.session_id))
        _write_json(out, session_explorer_body(await service.session_explorer(session_id)))
        return
    if command == "evidence":
        session_id = SessionId(cast(str, args.session_id))
        _write_json(out, session_evidence_body(await service.session_explorer(session_id)))
        return
    if command == "world":
        session_id = SessionId(cast(str, args.session_id))
        _write_json(
            out,
            session_world_body(
                await service.session_world(
                    session_id,
                    entity_id=cast(str | None, args.entity),
                    relation=cast(str | None, args.relation),
                )
            ),
        )
        return
    if command == "events":
        session_id = SessionId(cast(str, args.session_id))
        after = cast(str | None, args.after)
        limit = cast(int | None, args.limit)
        batch = await _stream_events_for_cli(
            service,
            session_id,
            after_event_id=None if after is None else EventId(after),
            limit=limit,
            wait=cast(bool, args.wait),
            timeout_seconds=cast(float, args.timeout_seconds),
            poll_interval_seconds=cast(float, args.poll_interval_seconds),
        )
        if cast(str, args.format) == "sse":
            _write_text(out, sse_event_batch_text(batch))
            return
        _write_json(out, event_batch_body(batch))
        return
    if command == "audit":
        session_id = SessionId(cast(str, args.session_id))
        if cast(bool, args.integrity):
            _write_json(out, audit_integrity_body(await service.audit_integrity(session_id)))
            return
        _write_json(out, audit_records_body(await service.audit_records(session_id)))
        return
    if command == "cost":
        session_id = SessionId(cast(str, args.session_id))
        _write_json(out, cost_body(await service.cost(session_id)))
        return
    if command == "logs":
        session_id = SessionId(cast(str, args.session_id))
        _write_json(out, log_records_body(await service.logs(session_id)))
        return
    if command == "traces":
        session_id = SessionId(cast(str, args.session_id))
        if cast(str, args.format) == "otlp":
            _write_json(out, await service.opentelemetry_traces(session_id))
            return
        _write_json(out, trace_spans_body(await service.traces(session_id)))
        return
    if command == "pause":
        session_id = SessionId(cast(str, args.session_id))
        run = await service.pause_session(session_id, reason=cast(str, args.reason))
        _write_json(out, runtime_run_body(run))
        return
    if command == "resume":
        session_id = SessionId(cast(str, args.session_id))
        run = await service.resume_session(
            session_id,
            confirmed=_optional_bool(cast(str | None, args.confirmed)),
        )
        _write_json(out, runtime_run_body(run))
        return
    if command == "cancel":
        session_id = SessionId(cast(str, args.session_id))
        run = await service.cancel_session(session_id, reason=cast(str, args.reason))
        _write_json(out, runtime_run_body(run))
        return
    raise ValueError(f"unknown session command: {command}")


async def _stream_events_for_cli(
    service: RuntimeService,
    session_id: SessionId,
    *,
    after_event_id: EventId | None,
    limit: int | None,
    wait: bool,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> RuntimeEventBatch:
    if not wait:
        return await service.stream_events(
            session_id,
            after_event_id=after_event_id,
            limit=limit,
        )
    timeout_seconds = parse_bounded_float(
        timeout_seconds,
        "timeout_seconds",
        minimum=0.0,
        maximum=30.0,
    )
    poll_interval_seconds = parse_bounded_float(
        poll_interval_seconds,
        "poll_interval_seconds",
        minimum=0.001,
        maximum=5.0,
    )

    return await poll_async_result(
        lambda: service.stream_events(
            session_id,
            after_event_id=after_event_id,
            limit=limit,
        ),
        retry_if=lambda batch: not batch.events,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
