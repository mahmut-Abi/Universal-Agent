# Universal Agent Runtime Documentation

This directory is the project documentation entry point. It complements, but
does not replace:

- [`AGENTS.md`](../AGENTS.md) for repository working rules.
- [`universal-agent-runtime-domain-runtime-design.md`](../universal-agent-runtime-domain-runtime-design.md)
  for the normative architecture target.
- [`README.md`](../README.md) for the current implementation summary and command list.
- [`docs/revision/`](revision/) for dated implementation audits.

## Current Implementation Scope

The repository currently implements a local Universal Agent Runtime foundation
across the roadmap layers below:

| Layer | Implemented foundation |
| --- | --- |
| P0-P3 | Typed Agent loop, Domain Runtime, Evidence, World Model, Recovery, Memory, Multi-Domain composition, Agent Profiles |
| P3.5 | Runtime API, RuntimeService, agentd route adapter, standard-library HTTP bridge, CLI, persistence, cursor Event reads, bounded wait polling |
| P3.6-P3.7 | Metrics, cost, structured logs, traces, OTLP-shaped export, audit, doctor, evaluation harness, replay, deterministic runtime mode |
| P4 | Structured Multi-Agent task/result contracts, registry, delegation, conflict resolution, merge/evaluation foundations |
| P5 | Read-only TUI, Web Console, Session/Evidence/World/Domain/Doctor/Distributed/Evaluation views |
| P6 | Local queue, worker registry, worker, leased locks, scheduler, coordinator, health and snapshot primitives |
| P7 | Domain Package registry/scaffold, Evaluation Dataset catalog, Profile Catalog, Ecosystem Registry metadata/install planning |

The project is not yet a production cluster control plane. It does not provide
cross-node high availability, production database migrations, a real model
provider binding, real Kubernetes API remediation by default, or automatic
external package installation.

## Documentation Map

- [Architecture Map](architecture-map.md): module ownership and runtime seams.
- [Developer Guide](developer-guide.md): setup, tests, examples, and contribution workflow.
- [Runtime Operator Guide](runtime-operator-guide.md): running the local CLI/agentd/runtime surfaces.
- [Revision Notes](revision/): dated status snapshots and implementation audits.

## Primary Commands

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m ruff format --check src tests examples
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
```

The examples in [`examples/`](../examples/) are executable documentation for
individual roadmap slices.
