# Architecture Map

The runtime follows the design rule:

```text
Kernel defines how an Agent works.
Domain defines where and with what knowledge/capabilities it works.
```

The model proposes structured decisions. Runtime-owned components validate,
authorize, execute, observe, produce Evidence, update World state, evaluate and
recover.

## Runtime Flow

```text
Goal
  -> Task
  -> Context
  -> Decision(capability)
  -> Capability Resolution
  -> Policy
  -> Tool
  -> Observation
  -> Evidence
  -> World Model
  -> Task Expansion
  -> Evaluation
  -> Continue / Recover / Finish
```

Important invariants:

- LLM output is not authoritative state.
- Tool success is not task success.
- Policy is deterministic runtime code, not prompt text.
- Capability and Tool are separate concepts.
- Evidence is the traceable basis for World Model and Evaluation.
- Domain-specific logic stays outside the Kernel.

## Module Ownership

| Area | Main modules | Responsibility |
| --- | --- | --- |
| Core contracts | `src/universal_agent/core/` | IDs, Goal/Task/Decision/Observation/Event contracts |
| Agent Kernel | `src/universal_agent/runtime/` | loop orchestration, state transitions, actions, session rebuild, RuntimeAPI |
| Domain Runtime | `src/universal_agent/domain/` | active domains, composition, package metadata, Domain loading |
| Capability/Tool | `src/universal_agent/capability/`, `src/universal_agent/tools/` | semantic capability registry and concrete tool execution |
| Policy | `src/universal_agent/policy/` | allow/confirm/deny rules and policy engine |
| Evidence/World | `src/universal_agent/evidence/`, `src/universal_agent/world/` | Evidence store, facts, entities, relations, replayable World projection |
| Recovery | `src/universal_agent/recovery/` | classified, budgeted recovery plans |
| Context | `src/universal_agent/context/` | budget-aware context compilation from runtime-owned state |
| Memory | `src/universal_agent/memory/` | advisory prior/episodic memory, not Evidence |
| Service/API | `src/universal_agent/service/`, `src/universal_agent/agentd/` | RuntimeService projections, framework-free route adapter, HTTP bridge |
| Persistence | `src/universal_agent/persistence/`, `src/universal_agent/state/` | in-memory/file/SQLite session and event stores |
| Operations | `src/universal_agent/operations/` | metrics, logs, traces, cost, audit, doctor, repair views |
| Evaluation | `src/universal_agent/evaluation/` | scenarios, suites, quality gates, reports, replay, deterministic mode |
| UI | `src/universal_agent/tui.py`, `src/universal_agent/web/`, `src/universal_agent/console.py` | TUI/Web/console projections; the Web Console adds controlled operator actions (pause/resume/confirm/cancel) behind the same policy and confirmation boundaries as the CLI and agentd |
| Distributed local primitives | `src/universal_agent/distributed/`, `src/universal_agent/coordination/` | queue, worker, lease, lock, scheduler, coordinator, health |
| Multi-Agent optional layer | `src/universal_agent/multi_agent/` | structured task/result contracts, registry, delegation, merge/evaluation |
| Ecosystem | `src/universal_agent/ecosystem/`, `src/universal_agent/profile/` | package/dataset/profile catalogs and registry metadata |
| Kubernetes domain | `src/universal_agent/domains/kubernetes/` | first serious Domain Runtime and optional kubectl backend |
| Observability domain | `src/universal_agent/domains/observability/` | read-only metrics Domain with fixture and Prometheus backends |

## Application Boundaries

Applications should use:

- `RuntimeAPI` for stable in-process execution and session/event reads.
- `RuntimeService` for application-facing projections.
- `AgentdApp` / `AgentdHttpServer` for HTTP-shaped local service hosting.
- `agent` CLI commands for operator workflows.

They should not manipulate Kernel internals, stored snapshots or Domain
implementation objects directly.

## Current Boundary Conditions

- The default Kubernetes domain uses injected fake/test backends unless a caller
  explicitly wires `KubectlBackend`.
- The Observability domain is read-only and supports instant metrics queries,
  explicit range queries with bounded series summaries, alert/rule inspection
  and Kubernetes-label-to-resource subject mapping for shared world identity.
- File and SQLite adapters are local persistence/coordination adapters, not a
  high-availability distributed database layer.
- The Web and TUI surfaces render the same projections; only the Web Console
  exposes operator actions, and those dispatch through RuntimeService so policy
  checks and pending-action confirmation stay identical to CLI and agentd.
- Multi-Agent is optional and structured; Domain Composition remains the
  default way to combine multiple domains inside one Agent.
- Ecosystem registry install planning validates metadata and checksums; it does
  not import package entrypoints or install external dependencies.

## Package Structure (client/server split)

The repository is organized so client packages can be extracted to their own
repository without dragging the runtime along. Clients talk to the runtime only
over its HTTP API (JSON Runtime API + SSE event streams); gRPC was evaluated and
deferred — the HTTP surface is battle-tested, documented through OpenAPI, needs
no extra dependencies, and SSE already covers event push.

```text
src/
├── universal_agent/           # Kernel + agentd HTTP server (server -> kernel only)
├── universal_agent_api/       # Client SDK: AgentdClient, SSE streaming, types.
│                              #   Zero kernel imports — enforced by tests.
├── universal_agent_cli/       # CLI client (embedded mode still imports the kernel;
│                              #   remote --api-url mode depends on the SDK only)
├── universal_agent_tui/       # TUI client incl. the remote snapshot projection
└── universal_agent/           # web console rendering is still kernel-side
                               #   (server-rendered; pure-frontend rewrite pending)
```

Boundary rules, enforced by `tests/unit/test_package_boundaries.py`:

- `universal_agent_api` must not import `universal_agent`.
- `universal_agent` must not import client packages.

Transition debt before the split is complete: CLI/TUI embedded mode still
imports the kernel directly (the full CLI contract is far beyond what the HTTP
API covers today); the web console is server-rendered inside the kernel package.
The extraction plan is: move CLI local-mode dispatch behind a locally spawned
agentd subprocess, then cut the client packages into their own repositories.


## Extracted Client SDK

The `universal_agent_api` SDK is extracted to its own repository
(`universal-agent-api`, private) with an independent build, tests and CI. The
monorepo keeps an editable copy under `src/universal_agent_api` for in-repo
development; the standalone repo is the publication source. The remaining
client packages (CLI/TUI/Web) follow once their embedded-mode transition debt
(see above) is cleared.
