# Universal Agent Runtime Platform

A typed Universal Agent Kernel and Runtime with pluggable Domain Runtimes.

The long-term architecture is defined in
`universal-agent-runtime-domain-runtime-design.md`. The current implementation is a typed runtime
with fake-backed Kubernetes remediation, a P3 Multi-Domain composition foundation,
and the first P3.5 productization foundation: a stable
in-process Runtime API, immutable Session read models, cursor-readable Events, explicit
pause/resume/cancel lifecycle controls, a framework-free `agentd` route adapter, a standard-library
HTTP bridge, a local CLI adapter, local file-backed session/event persistence, the first P3.6
operations surface with cost tracking and OpenTelemetry-shaped trace span projections, and a P3.7
Evaluation Harness / Replay foundation. The v3.0 design document also defines later productization
layers such as database persistence, SSE delivery, OpenTelemetry exporters,
optional Multi-Agent, UI, distributed runtime, and ecosystem packaging.

## Architectural boundaries

- The model proposes structured capability decisions; it never selects concrete tools or owns state.
- The Runtime controls validation, capability resolution, policy checks, confirmation, execution,
  observation processing, task expansion, recovery budgets, evaluation, and completion.
- An Observation is not automatically a fact. Domain extractors produce Evidence with provenance;
  World Model updates and Evaluators decide what that Evidence supports.
- Tool success is not task success. A Domain evaluator must complete the current task, and all required
  tasks must finish before a model `finish` proposal can complete a goal.
- Every normal and recovered capability passes deterministic resolution and policy enforcement.
  Mutation capabilities without an explicit allow policy are denied by default.
- Domain code supplies manifests, capabilities, tools, policies, evaluators, context providers,
  Evidence extractors, World updaters, Task expanders, and Recovery rules. The Kernel contains no
  domain-name branches.
- Multi-domain collaboration is intended to be handled by Domain Composition inside one Runtime and
  one Shared World Model. Multi-Agent orchestration is a later, optional execution boundary for
  independent goals, state, permissions, lifecycles, or isolation requirements; it is not the default
  way to route between Domains.
- Applications should consume a stable Runtime API or SDK boundary. Future `agentd`, CLI, TUI, and
  Web clients must not manipulate Kernel internals directly.
- A session lives in its store, not in a Runtime instance. Everything needed to continue — task graph,
  Evidence, recovery budget, pending confirmation, activated Domain composition — is saved as a
  `SessionSnapshot`, so a rebuilt Runtime resumes from the snapshot instead of from shared objects.
- The World Model is never a second source of truth. It is replayed from Evidence through the Domain's
  World updaters, which is why recovery cannot silently invent facts.

## Runtime flow

```text
Goal -> Context -> Decision(capability) -> Capability Resolver -> Policy -> Tool
  -> Observation -> Evidence -> World Model -> Task Expansion -> Evaluator -> State
                         ^                                      |
                         +-------- bounded Recovery <-----------+
```

Recovery is classified and budgeted. Retries and alternative capabilities receive new action IDs and
return through Capability Resolution and Policy; Recovery never calls a Tool directly. Unknown or
exhausted failures stop deterministically. The loop is bounded iteration rather than recursion, so a
misconfigured Domain can exhaust the step budget but can never grow the Python stack.

## Session recovery

`resume(session_id, confirmed=...)` is a rebuild, not a continuation: load the snapshot, verify the
activated Domain name and version, rehydrate the task graph, replay Evidence into a fresh World Model,
re-resolve the tool, re-check policy, and only then execute. A Domain mismatch or a drifted tool
resolution is refused rather than replayed against the wrong Domain.

Recovery attempts are persisted with the snapshot, so restarting mid-recovery continues the existing
budget instead of handing the session a fresh set of retries.

`RuntimeBuilder` gives each build its own in-memory Evidence store and World Model by default; store
factories can be injected when two runtimes should genuinely share one backend. The default isolation
is what makes cross-runtime tests exercise the snapshot rather than object identity.

## Runtime API

`RuntimeAPI` is the current application-facing execution interface. It wraps the Kernel-facing
`AgentRuntime` with stable read models:

- `run_goal(goal, task)` executes a goal and returns both the `ExecutionResult` and a `SessionView`.
- `pause_session(session_id, reason=...)` moves a non-terminal session into an explicit waiting state.
- `resume_session(session_id, confirmed=...)` resumes either a waiting confirmation or a paused
  session through the same runtime-controlled path as `AgentRuntime.resume`.
- `cancel_session(session_id, reason=...)` cancels a non-terminal session, clears any pending action,
  and returns a cancelled run plus the latest session projection.
- `get_session(session_id)` loads an immutable projection of the latest `SessionSnapshot`.
- `list_sessions()` returns recent `SessionSummaryView` projections without exposing stored
  snapshots to applications.
- `stream_sessions(after_session_id=..., limit=...)` returns a cursor batch of session summaries.
- `list_events(session_id)` returns immutable event projections filtered to one session.
- `stream_events(session_id, after_event_id=..., limit=...)` returns a cursor batch for CLI/Web/SSE
  consumers.

This remains usable in-process, while the standard-library HTTP bridge now wraps the same
`AgentdApp` route adapter for local `agentd` hosting. SSE-formatted event batches now share the same
cursor semantics as JSON event reads; database persistence and long-lived push delivery are later
P3.5 work built on this interface, not replacements for it.

`RuntimeService` is the first framework-free `agentd` foundation. It delegates execution, session and
event reads to `RuntimeAPI`, and adds service-level health, readiness, Domain, Capability and Tool
catalog views, plus a typed runtime configuration projection for HTTP and CLI adapters. It does not
access Kernel internals directly. `RuntimeHost` is the typed application assembly boundary for Runtime
Configuration: it validates the configured Domain identity, builds memory or file-backed stores,
applies runtime limits/environment, optionally binds an application-level Agent Profile, and exposes
both `RuntimeAPI` and `RuntimeService` without teaching applications Kernel internals. See
`examples/p3_5_runtime_api.py`, `examples/p3_5_runtime_service.py`,
`examples/p3_5_runtime_config.py`, `examples/p3_5_cli_config.py`, and
`examples/p3_5_cli_event_stream.py` for minimal
application-facing usage.

`AgentdApp` is the framework-free route adapter foundation for `agentd`. It accepts small
`HttpRequest` objects and returns JSON-safe `HttpResponse` objects for `GET /health`, `GET /ready`,
catalog routes, cursor session listing via `GET /v1/sessions`, route-level goal submission via
`POST /v1/sessions`, session/event reads, and Profile catalog/detail reads via `GET /v1/profiles`,
configuration reads via `GET /v1/config`, confirmation resume via
`POST /v1/sessions/{id}/resume`, explicit pause via `POST /v1/sessions/{id}/pause`, cancellation via
`POST /v1/sessions/{id}/cancel`, operations reads via `/v1/metrics`,
`/v1/metrics/prometheus`, `/v1/cost`, `/v1/logs`, `/v1/traces`, `/v1/traces/otlp`,
`/v1/doctor` and `/v1/audit`, per-session audit/cost/log/trace
reads including `/v1/sessions/{id}/traces/otlp`, and cursor session/event reads with `after` /
`limit` query parameters. `GET /v1/sessions/{id}/events/stream`
returns the same cursor batch as `text/event-stream` frames for SSE clients. `AgentdHttpServer` is the
standard-library HTTP bridge for this adapter: it owns socket/body/header translation only and does
not touch Runtime internals.

`agent` is the first local CLI adapter. It exposes version, health/readiness, Domain/Profile/
Capability/Tool catalogs, `config show`, and session list/show/events/pause/resume/cancel commands
through `RuntimeService`, with cursor flags for session and event reads. It also exposes operations
commands for metrics, cost, logs, traces, doctor and audit
projections; `agent metrics --format prometheus` emits Prometheus text exposition, while
`agent traces --format otlp` and `agent session traces <id> --format otlp` emit OTLP
JSON-compatible trace payloads from the same event-derived span projection. `agent serve` starts the
standard-library `AgentdHttpServer` around the same service; the CLI does not access Kernel
internals directly.

`EvaluationHarness` is the first P3.7 behavior evaluation foundation. It runs explicit
`EvaluationScenario` objects through a RuntimeService-like interface, then verifies observable
Session, Event, Metrics and Audit projections. `EvaluationSuite` and
`EvaluationScenarioSelector` make scenario, regression, policy and recovery subsets first-class
contracts for local CI-style runs. `EvaluationQualityGate` evaluates suite-level pass rates,
completion rates, intervention rates, action efficiency and model budget thresholds after execution.
`DeterministicReplayHarness` records a stable trace from those projections and replays later runs
against it while ignoring dynamic IDs and timestamps.
`FileReplayRecordingStore` persists those traces as JSON golden recordings for local regression tests.
`DeterministicRuntimeMode` supplies mock clock and ID primitives for tests that need stable recorded
events from the Runtime itself.
The harnesses are intentionally outside the Kernel: Domain `Evaluator`s still decide task/goal
semantics during execution, while the Harness decides whether a completed scenario satisfies
regression, policy, recovery, token/cost budget and replay expectations. See
`examples/p3_7_evaluation_harness.py`, `examples/p3_7_replay.py` and
`examples/p3_7_deterministic_mode.py`.

`AgentProfile` is the first application-level Profile foundation. A Profile declares a selectable
runtime identity — name, version, Domain identity and Runtime Configuration — for future CLI/agentd
entry points. It is not a new Kernel, not a Domain implementation, and not a routing Agent.

`FileSessionStore` and `FileEventStore` are local persistence adapters for P3.5 recovery tests and
embedded demos. They persist and list `SessionSnapshot` JSON documents and JSONL runtime events
behind the same `SessionStore` and `EventSink/EventReader` seams used by in-memory stores. They are
not a database layer, event-sourcing model, or production migration system.

## Current scope

- P0: typed state, model/tool boundaries, observations, events, and the asynchronous loop.
- P1: Domain Manifest/Runtime, capability-first resolution, policy allow/confirm/deny, evaluator
  boundaries, context compilation, and a read-only Kubernetes Domain skeleton.
- P2: session-local World Model, Evidence provenance, deterministic dynamic Task expansion, relevant
  World/Evidence context, and bounded Recovery.
- P2.1: a rebuildable session aggregate — `SessionSnapshot`, a serializable task graph, Evidence
  export/replace, World replay, non-recursive Recovery, and a Runtime split into action, transition,
  session, and processing collaborators.
- P3.1: advisory Memory — a three-stage `retrieve → filter → compile` pipeline, Domain-declared
  prior knowledge (Semantic/Procedural/Preference), runtime-written Episodic records at terminal
  transitions, and a dedicated context budget. Memory is advisory only: it never becomes Evidence,
  never updates the World Model, never enters the evaluator, and never alone completes a Task or
  Goal. It is excluded from `SessionSnapshot` so the World stays replayable from Evidence alone.
- P3.2: fake-backed Kubernetes remediation — policy-gated `scale_workload`, deterministic
  confirmation, capability-scoped timeout recovery, dynamic remediation tasks, and fresh health
  verification. Mutation receipts never substitute for verification evidence.
- P3.5 foundation: in-process `RuntimeAPI`, immutable `SessionView` / `RuntimeEventView`
  projections, lightweight cursor-aware `SessionSummaryView` listing, cursor-aware `EventReader`,
  `RuntimeSessionBatch` / `RuntimeEventBatch`, action idempotency metadata (`idempotency_key`,
  `parameters_hash`, `attempt`) carried through pending-action views, events and persistence, and
  integration tests covering run/list/get/events plus explicit pause, non-confirmation resume,
  confirmation resume and cancellation.
  `RuntimeService` now adds framework-free `agentd` foundation metadata: health, readiness, domains,
  capabilities, tools, delegated execution, runnable examples, an `AgentdApp` route adapter for
  HTTP-shaped goal submission, cursor session listing, JSON and SSE-formatted session/event reads,
  pause/resume/cancel routes, runtime configuration reads,
  Profile catalog reads, a standard-library `AgentdHttpServer` bridge, file-backed session/event
  stores for local recovery, a local CLI adapter, and typed
  `RuntimeConfig` / `RuntimeHost` / `AgentProfile` assembly for environment, limits, store backend,
  Domain identity validation, and multi-Domain composition activation.
- P3.6/P3.7 foundation: event-derived `metrics`, Prometheus metrics text export, `cost`, `logs`,
  `traces`, OTLP trace export, `doctor` and `audit` projections exposed through RuntimeService,
  agentd-shaped routes and CLI commands, plus optional
  `ModelUsageRecorded` events from model adapters. Structured log projections preserve runtime identifiers, event types, severity and redacted event data for CLI/agentd consumers. Trace span projections derive session/action trees plus decision, model usage, policy, observation and evaluation phase spans from the same event stream with redacted attributes for OpenTelemetry-shaped consumers, and the OTLP adapter projects those spans into dependency-free collector payloads. The Evaluation Harness can assert status, error
  codes, events, executed capabilities, audit coverage, policy denials, recovery plans, criteria,
  action counts, iteration budgets and model token/cost budgets for behavior scenarios.
  Evaluation suites classify scenarios by kind and tags so regression, policy and recovery subsets
  can be selected without changing Kernel code, and quality gates turn suite metrics into CI-ready
  pass/fail checks.
  Deterministic Replay can record stable behavior traces and detect later drift in event shape,
  actions, policy effects, audit entries and metrics without depending on runtime-generated IDs.
  `DeterministicRuntimeMode` can also install stable runtime ID and clock primitives while building
  golden fixtures.
  Replay recordings can be encoded as versioned JSON and saved through `FileReplayRecordingStore`
  for golden regression fixtures.

The Kubernetes Domain uses injected backends. Tests and examples use fake backends; no real cluster
is accessed and no `kubectl` command is executed. The read-only `KubernetesDomain` remains available,
while `KubernetesRemediationDomain` adds the fake-backed mutation path. Multi-domain operation now
has a conservative `DomainManager` / `DomainComposition` foundation: Domain identities,
capabilities and tools are validated before activation, Profiles may declare ordered Domain sets,
and snapshots persist the activated composition for safe resume. Cross-domain World Model reasoning,
persistent databases, packaging, marketplace behavior, optional Multi-Agent Runtime, and real
Kubernetes API remediation remain outside P3.2. Persistence includes in-memory stores plus local file-backed session/event adapters with
snapshot isolation; no database backend, event sourcing, or schema migration is included.

## Roadmap alignment

The design roadmap now separates semantic runtime maturity from productization:

- P0-P3: core Agent semantics, Domain Runtime, World/Evidence/Recovery, Memory, Multi-Domain, and
  Agent Profiles.
- P3.5: Runtime Productization — Runtime API, Session API, `agentd`, CLI, Event Stream, Persistence,
  Resume / Pause / Cancel, and Runtime Configuration.
- P3.6-P3.7: Operations and Evaluation — OpenTelemetry, metrics, audit, cost tracking, runtime
  doctor, evaluation suites, quality gates, replay, and deterministic test mode.
- P4+: Optional Multi-Agent, user interfaces, distributed runtime, and ecosystem packaging.

`PROMPT.md` is intentionally not kept as a project authority. Development instructions live in
`AGENTS.md`; architecture lives in `universal-agent-runtime-domain-runtime-design.md`; operational
usage stays in this README.

## Development

Python 3.12 or newer is required.

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m ruff format --check src tests examples
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
.venv/bin/python examples/p0_agent_loop.py
.venv/bin/python examples/p1_kubernetes_domain.py
.venv/bin/python examples/p2_evidence_recovery.py
.venv/bin/python examples/p3_memory.py
.venv/bin/python examples/p3_2_kubernetes_remediation.py
.venv/bin/python examples/p3_5_runtime_api.py
.venv/bin/python examples/p3_5_runtime_service.py
.venv/bin/python examples/p3_5_agentd_routes.py
.venv/bin/python examples/p3_5_persistence.py
.venv/bin/python examples/p3_5_runtime_config.py
.venv/bin/python examples/p3_5_cli_config.py
.venv/bin/python examples/p3_5_cli_event_stream.py
.venv/bin/python examples/p3_5_cli_run.py
.venv/bin/python examples/p3_6_cost_tracking.py
.venv/bin/python examples/p3_6_structured_logs.py
.venv/bin/python examples/p3_6_traces.py
.venv/bin/python examples/p3_7_evaluation_harness.py
.venv/bin/python examples/p3_7_replay.py
.venv/bin/python examples/p3_7_deterministic_mode.py
.venv/bin/agent ready
```

`mypy` runs in strict mode over `src`, `tests` and `examples`, and passes with no `type: ignore`
anywhere in the repository: the Domain extension points are `Protocol`s, so a Domain is recognised by
shape and its implementations are checked structurally rather than through inheritance. Tests and
examples are held to the same standard deliberately — an unannotated fake Domain would be exactly the
place where a broken extension point could hide.

`ruff format --check` is scoped to the source directories because newer Ruff releases also reformat
fenced code blocks inside the design Markdown, which is not part of the Python formatting contract.
