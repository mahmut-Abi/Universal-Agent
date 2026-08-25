# Runtime Operator Guide

This guide covers the current local operator surfaces: CLI, agentd route
adapter, RuntimeService projections, read-only TUI/Web views and local
coordination primitives.

## CLI

Use the local CLI through the installed console script or the module entry point.
In this repository, tests and examples use:

```bash
.venv/bin/python -m universal_agent.cli health
```

Frequently used commands:

```bash
.venv/bin/python -m universal_agent.cli ready
.venv/bin/python -m universal_agent.cli config show
.venv/bin/python -m universal_agent.cli capabilities list
.venv/bin/python -m universal_agent.cli tools list
.venv/bin/python -m universal_agent.cli policies list
.venv/bin/python -m universal_agent.cli profiles list
.venv/bin/python -m universal_agent.cli session list
```

`agent init` can generate either environment-backed or file-backed secret
references:

```bash
.venv/bin/python -m universal_agent.cli init --model-provider json_http --model-endpoint https://model-bridge.example/decide --model-api-key-file /run/secrets/model-api-key
.venv/bin/python -m universal_agent.cli init --domain-backend kubernetes_api --kubernetes-api-server https://cluster.example.test --kubernetes-api-token-file /run/secrets/kubernetes-token
```

Session event reads support cursor semantics:

```bash
.venv/bin/python -m universal_agent.cli session events <session-id> --limit 20
.venv/bin/python -m universal_agent.cli session events <session-id> --after <event-id>
.venv/bin/python -m universal_agent.cli session events <session-id> --format sse
.venv/bin/python -m universal_agent.cli session events <session-id> --after <event-id> --wait
```

`--wait` is bounded polling, not an infinite push stream. The default timeout is
10 seconds and the maximum accepted timeout is 30 seconds.

## agentd

`AgentdApp` is a framework-free HTTP-shaped route adapter. `AgentdHttpServer`
wraps it with the Python standard library HTTP server.

Current route families include:

- `GET /health`, `GET /ready`
- `GET /v1/domains`, `/v1/capabilities`, `/v1/tools`, `/v1/policies`,
  `/v1/evaluators`, `/v1/memory`
- `POST /v1/sessions`, `GET /v1/sessions`, `GET /v1/sessions/{id}`
- `GET /v1/sessions/{id}/events`
- `GET /v1/sessions/{id}/events/stream`
- `POST /v1/sessions/{id}/pause`, `/resume`, `/cancel`
- operations routes for metrics, cost, logs, traces, doctor and audit
- read-only console routes under `/console`

SSE event batches share cursor semantics with JSON event reads. The stream route
also accepts bounded wait polling parameters for local clients:

```text
GET /v1/sessions/{id}/events/stream?after=<event-id>&wait=true&timeout_seconds=10
```

## RuntimeService

`RuntimeService` is the application-facing projection layer over `RuntimeAPI`.
It exposes health/readiness, catalogs, sessions, events, diagnostics, operations,
distributed coordination views and ecosystem/package views without reaching into
Kernel internals.

Use `RuntimeService` when building new local adapters. Use `RuntimeAPI` when the
caller needs in-process execution and session/event read models.


## Runtime Configuration

Profile runtime config can declare a model provider without storing credentials.
The current built-in providers are `scripted` for deterministic local runs and
`json_http` for dependency-free HTTP model bridges. JSON HTTP model config stores
only endpoint/model metadata plus an optional `api_key_secret` reference; the
secret value is resolved by `RuntimeHost` from the declared `secrets` block and
is never returned by `config show` or `/v1/config`. Built-in secret references
support `env` keys and local `file` paths; both are projected as availability
metadata only.

```json
{
  "secrets": {
    "openai_api_key": {"source": "env", "key": "OPENAI_API_KEY", "required": true}
  },
  "model": {
    "provider": "json_http",
    "name": "runtime-decider",
    "endpoint": "https://model-bridge.example/decide",
    "api_key_secret": "openai_api_key",
    "timeout_seconds": 30
  }
}
```

File-backed secrets use the same declaration shape with `source=file` and the
secret file path in `key`:

```json
{
  "secrets": {
    "openai_api_key": {
      "source": "file",
      "key": "/run/secrets/openai-api-key",
      "required": true
    }
  }
}
```

Kubernetes HTTP API profiles follow the same rule. The Domain settings store the
API server and a secret reference name, while the bearer token is resolved from
the declared runtime secret only at the CLI/host boundary.

```json
{
  "secrets": {
    "kubernetes_api_token": {
      "source": "env",
      "key": "KUBERNETES_API_TOKEN",
      "required": true
    }
  },
  "domain": {
    "name": "kubernetes",
    "version": "0.2.0",
    "backend": "kubernetes_api",
    "settings": {
      "api_server": "https://cluster.example.test",
      "default_namespace": "prod",
      "bearer_token_secret": "kubernetes_api_token",
      "timeout_seconds": 10
    }
  }
}
```

## Read-Only UI Surfaces

- `agent tui` renders a deterministic text snapshot.
- `/console` renders a read-only Web Console snapshot.
- Focused Web pages exist for sessions, session detail, evidence, world model,
  domains, domain packages, catalogs, profiles, runtime doctor checks, local distributed runtime
  coordination, evaluation reports and settings.
- `agent eval console --report-dir <dir>` renders persisted evaluation reports
  as deterministic HTML.
- `agent eval console --report-dir <dir> --format text` renders the same
  report projection for terminal and CI logs.

These UI surfaces are inspection views. They do not mutate runtime state.

## Operations

Runtime operations are event-derived:

- metrics and Prometheus text export
- cost projections
- structured logs
- trace spans and OTLP-shaped export
- audit records
- doctor checks
- state/event commit strategy checks for persistent store wiring
- state/event consistency repair for terminal sessions missing terminal events

Run:

```bash
.venv/bin/python -m universal_agent.cli metrics
.venv/bin/python -m universal_agent.cli metrics --format prometheus
.venv/bin/python -m universal_agent.cli config show
.venv/bin/python -m universal_agent.cli doctor
.venv/bin/python -m universal_agent.cli repair state-events --dry-run
```

`config show`, TUI/Web settings and `/v1/config` include `state_event_commit`
metadata. For file and SQLite runtime stores, Doctor reports an error if the
session store is not also the event reader/sink used for committed state/event
writes.

When local distributed coordination is configured, `doctor` also reports queue
health, invalid session work references and terminal work backlog that should be
pruned through the distributed maintenance commands below.

## Local Distributed Runtime

The P6 implementation is a local coordination foundation:

- Work queue
- Worker registry
- Leased locks
- Scheduler
- Coordinator
- Snapshot and health projections
- Bounded worker execution
- Expiry and terminal item retention maintenance

It supports memory, file and SQLite-backed local adapters, but it is not a
networked high-availability control plane.

Useful inspection and maintenance commands:

```bash
.venv/bin/python -m universal_agent.cli distributed snapshot
.venv/bin/python -m universal_agent.cli distributed health
.venv/bin/python -m universal_agent.cli distributed expire
.venv/bin/python -m universal_agent.cli distributed prune-terminal --before 2026-01-01T00:00:01+00:00
.venv/bin/python -m universal_agent.cli init --output .tmp/retention-profile.json --distributed-terminal-retention-seconds 86400 --force
.venv/bin/python -m universal_agent.cli --profile-config .tmp/retention-profile.json distributed prune-terminal
```

Useful scheduling and worker commands:

```bash
.venv/bin/python -m universal_agent.cli distributed schedule-session session-1 --priority 5 --max-attempts 2
.venv/bin/python -m universal_agent.cli distributed schedule-pending-actions --confirmed true
.venv/bin/python -m universal_agent.cli distributed worker-register worker-a --capability agent_session
.venv/bin/python -m universal_agent.cli distributed worker-run-once worker-a
.venv/bin/python -m universal_agent.cli distributed worker-run worker-a --max-items 5
```

Useful lock and cancellation commands:

```bash
.venv/bin/python -m universal_agent.cli distributed lock-acquire session/session-1 --owner-id worker-a
.venv/bin/python -m universal_agent.cli distributed lock-heartbeat lock-lease-1 --owner-id worker-a
.venv/bin/python -m universal_agent.cli distributed lock-release lock-lease-1 --owner-id worker-a
.venv/bin/python -m universal_agent.cli distributed cancel work-1 --reason "operator cancelled queued work"
```

## Ecosystem Metadata

P7 ecosystem commands validate and register local metadata:

- Domain package manifests
- Evaluation dataset manifests
- Profile configs
- Ecosystem registry manifests

Domain package scaffolding can declare package-local resources for runbooks,
schemas, templates and other non-code assets:

```bash
.venv/bin/python -m universal_agent.cli domain-packages scaffold ai-ops --description "AI ops domain" --output .tmp/ai-ops-domain --resource resources/runbook.md --resource schemas/incident.json
```

They do not import Domain entrypoints, install external dependencies or activate
runtimes by themselves. Programmatic SDK callers that are ready to execute local
Domain code should use `load_domain_package_runtime`, which imports only the
declared entrypoint and validates it against the package metadata before
returning an activated Domain. CLI/CI callers can run the same explicit check
without changing install semantics:

```bash
.venv/bin/python -m universal_agent.cli domain-packages load-runtime .tmp/ai-ops-domain
```

CLI install refuses signature metadata by default unless the operator explicitly
allows unverified local signatures; programmatic callers can instead pass an
`EcosystemRegistrySignatureVerifier` before planning or installing signed
registry metadata.
