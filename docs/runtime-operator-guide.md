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
.venv/bin/python -m universal_agent.cli capabilities list   # includes required_arguments and argument_schema
.venv/bin/python -m universal_agent.cli tools list
.venv/bin/python -m universal_agent.cli policies list
.venv/bin/python -m universal_agent.cli profiles list
.venv/bin/python -m universal_agent.cli session list
```

`agent init` can generate either environment-backed or file-backed secret
references:

```bash
.venv/bin/python -m universal_agent.cli init --model-provider json_http --model-endpoint https://model-bridge.example/decide --model-api-key-file /run/secrets/model-api-key
.venv/bin/python -m universal_agent.cli init --domain-backend kubectl --kubectl-namespace prod --kubectl-context prod-cluster --environment production --model-provider openai_chat_completions --model-name gpt-runtime --model-api-key-env OPENAI_API_KEY
.venv/bin/python -m universal_agent.cli init --model-provider openai_responses --model-name gpt-runtime --model-api-key-env OPENAI_API_KEY
.venv/bin/python -m universal_agent.cli init --domain-backend kubernetes_api --kubernetes-api-server https://cluster.example.test --kubernetes-api-token-file /run/secrets/kubernetes-token
```

When `AGENT_CONFIG_DIR` or `AGENT_DATA_DIR` is set, `agent init` uses those
directories for default Profile output and local runtime state paths. This keeps
the same command usable inside the generic container image, where the defaults
are `/config/profile.json` and `/data/*`.

For an end-to-end production-style Kubernetes profile and run sequence, follow
[`kubernetes-live-operator-runbook.md`](kubernetes-live-operator-runbook.md).

Use Kubernetes preflight before running a production profile. It validates the
active Kubernetes domain, model secret availability, expected capability set and
optional read-only cluster/workload inspection. A failed inspection writes a JSON
report and exits with status `1`.

Use `kubernetes model-probe` first when validating a new OpenAI-compatible model
endpoint. It calls the configured model once with a Kubernetes remediation
Decision context, validates the returned structured Decision locally, and never
executes Kubernetes tools. The probe requires a first `inspect_workload`
Decision scoped to the requested workload and namespace; syntactically valid
Decisions that try to finish, mutate, or inspect a different workload fail before
cluster preflight.

```bash
.venv/bin/python -m universal_agent.cli --profile-config profile.json kubernetes model-probe production-operator --workload deployment/api --namespace prod
```

Use `kubernetes check` as the normal production pre-run gate. It runs
`model-probe` first and only proceeds to Kubernetes preflight if the model
contract is valid. The JSON response includes a `contract` report summarizing
whether the model probe, preflight checks and non-blocking warnings are
production-ready.

```bash
.venv/bin/python -m universal_agent.cli --profile-config profile.json kubernetes check production-operator --workload deployment/api --namespace prod
```

```bash
.venv/bin/python -m universal_agent.cli --profile-config profile.json kubernetes preflight --workload deployment/api --namespace prod
.venv/bin/python -m universal_agent.cli --profile-config profile.json kubernetes preflight --skip-cluster
```

Use `kubernetes run` for the first production-oriented Kubernetes flow. It runs
model probe and preflight by default, submits a health-remediation goal scoped
to the requested workload and namespace, and returns the normal runtime session
body plus a focused `contract` report and `next_step` for operators. Use
`--skip-model-probe` only
when intentionally reusing a recently validated model endpoint while still
running Kubernetes preflight. Use `--skip-preflight` only when intentionally
skipping all pre-run checks. If the model proposes `scale_workload` outside that
requested scope during the Runtime run, deterministic Kubernetes policy denies
it before tool execution.

```bash
.venv/bin/python -m universal_agent.cli --profile-config profile.json kubernetes run production-operator --workload deployment/api --namespace prod
.venv/bin/python -m universal_agent.cli --profile-config profile.json kubernetes run production-operator --workload deployment/api --namespace prod --skip-model-probe
.venv/bin/python -m universal_agent.cli --profile-config profile.json kubernetes run production-operator --workload deployment/api --namespace prod --skip-preflight
```

In `production`, policy-gated `scale_workload` decisions return `waiting` until
the pending action is reviewed and explicitly confirmed:

```bash
.venv/bin/python -m universal_agent.cli --profile-config profile.json session resume <session-id> --confirmed true
```

`agent run` defaults to the historical `healthy=true` success criterion. Use
`--success KEY=JSON` to run other concrete goals without changing code. Repeat
the flag for multiple required criteria; `distributed schedule-goal` accepts the
same option.

```bash
.venv/bin/python -m universal_agent.cli run local-kubernetes "Verify workload resource identity" --success 'resource="deployment/example"'
.venv/bin/python -m universal_agent.cli distributed schedule-goal local-kubernetes "Verify workload resource identity" --success 'resource="deployment/example"'
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
wraps it with a Starlette ASGI adapter served by uvicorn, while preserving the
same Runtime API request/response contract for embedded tests and local clients.

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

## Embedding SDK

`UniversalAgentRuntime` is the first public embedding facade over
`RuntimeService`. It accepts SDK-owned goal/task input types or simple strings,
validates optional Profile selection through the service, and returns compact SDK
run results while keeping lifecycle control in the Runtime.

```python
result = await sdk.submit_goal(
    "Verify workload health",
    success_criteria={"healthy": True},
    task="Inspect workload",
)
events = await sdk.stream_events(result.session_id)
```

See `examples/p3_5_runtime_sdk.py` for a complete local embedding example.

## Runtime Configuration

Profile runtime config can declare a model provider without storing credentials.
The current built-in providers are `scripted` for deterministic local runs,
`json_http` for `httpx`-backed HTTP model bridges, `openai_chat_completions`
for OpenAI SDK-backed `/v1/chat/completions` providers, and `openai_responses`
for OpenAI SDK-backed Responses structured decision output. Model config stores
only endpoint/model metadata plus an optional `api_key_secret` reference; the
secret value is resolved by `RuntimeHost` from the declared `secrets` block and
is never returned by `config show` or `/v1/config`. Built-in secret references
support `env` keys and local `file` paths; both are projected as availability
metadata only.

Model request context includes runtime-owned `goal_success_criteria` and
`current_task_required_criteria` fields plus each available capability's
`required_arguments` and `argument_schema`. Providers can use these explicit
contracts to choose the next capability and construct arguments, but Runtime
validation and evaluation remain authoritative for action execution and task or
goal completion.

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

Direct OpenAI Chat Completions config uses the same secret reference model.
`endpoint` is optional and defaults to the OpenAI Chat Completions endpoint when
omitted. Custom endpoints may use either a provider base URL or the full
`/v1/chat/completions` URL. The default response format is `json_schema`; use
`json_object` for providers that support JSON mode but not schemas, and
`prompt_json` for legacy OpenAI-compatible providers that reject the
`response_format` request field.

```json
{
  "secrets": {
    "openai_api_key": {"source": "env", "key": "OPENAI_API_KEY", "required": true}
  },
  "model": {
    "provider": "openai_chat_completions",
    "name": "gpt-runtime",
    "api_key_secret": "openai_api_key",
    "timeout_seconds": 30,
    "response_format": "prompt_json"
  }
}
```

Direct OpenAI Responses config uses the same secret reference model. `endpoint`
is optional and defaults to the OpenAI Responses endpoint when omitted.

```json
{
  "secrets": {
    "openai_api_key": {"source": "env", "key": "OPENAI_API_KEY", "required": true}
  },
  "model": {
    "provider": "openai_responses",
    "name": "gpt-runtime",
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

These distributed commands also support `--api-url` for operating against a
running `agentd` Runtime API instead of assembling a local service.

Useful scheduling and worker commands:

```bash
.venv/bin/python -m universal_agent.cli distributed schedule-session session-1 --priority 5 --max-attempts 2
.venv/bin/python -m universal_agent.cli distributed schedule-goal local-kubernetes "Verify workload health" --success healthy=true
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

- Domain package manifests (`manifest.json`, `manifest.yaml` or `manifest.yml`)
- Evaluation dataset manifests
- Profile configs
- Ecosystem registry manifests

Domain package scaffolding can declare package-local resources for runbooks,
schemas, templates and other non-code assets. Add `--runtime-stub` only when
you want the scaffold command to write starter Python Domain code for later
explicit activation checks:

```bash
.venv/bin/python -m universal_agent.cli domain-packages scaffold ai-ops --description "AI ops domain" --output .tmp/ai-ops-domain --capability inspect_incident --tool incident_api_get --evaluator incident_status --resource resources/runbook.md --resource schemas/incident.json --runtime-stub
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
