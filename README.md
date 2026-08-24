# Universal Agent Runtime Platform

A typed Universal Agent Kernel and Runtime with pluggable Domain Runtimes.

The long-term architecture is defined in
`universal-agent-runtime-domain-runtime-design.md`. The current implementation is a typed runtime
with fake-backed Kubernetes remediation, a P3 Multi-Domain composition foundation,
and the first P3.5 productization foundation: a stable
in-process Runtime API, immutable Session read models, cursor-readable Events, explicit
pause/resume/cancel lifecycle controls, a framework-free `agentd` route adapter, a standard-library
HTTP bridge, a local CLI adapter, local file-backed session/event persistence, the first P3.6
operations surface with cost tracking and OpenTelemetry-shaped trace span projections, a P3.7
Evaluation Harness / Replay foundation, the first read-only TUI and Web Console snapshot
foundations, the first P6 local scheduler, queue, worker registry, worker, lock, snapshot, health,
and coordinator primitives, and the first P7 Domain Package registry metadata plus SDK scaffold,
Evaluation Dataset catalog, Profile Catalog and unified Ecosystem Catalog foundations. The v3.0 design document also defines later productization layers such as production
database persistence, long-lived event delivery, OpenTelemetry exporters, optional Multi-Agent,
distributed runtime, and ecosystem packaging.

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
- `get_session_diagnostics(session_id)` returns a stable session diagnostics read model with
  traceable Evidence projections for Session Explorer consumers.
- `list_sessions()` returns recent `SessionSummaryView` projections without exposing stored
  snapshots to applications.
- `stream_sessions(after_session_id=..., limit=...)` returns a cursor batch of session summaries.
- `list_events(session_id)` returns immutable event projections filtered to one session.
- `stream_events(session_id, after_event_id=..., limit=...)` returns a cursor batch for CLI/Web/SSE
  consumers.

This remains usable in-process, while the standard-library HTTP bridge now wraps the same
`AgentdApp` route adapter for local `agentd` hosting. SSE-formatted event batches now share the same
cursor semantics as JSON event reads; production database persistence and long-lived push delivery are later
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
`POST /v1/sessions`, session/event reads, Profile catalog/detail reads via `GET /v1/profiles`,
Policy/Evaluator/Memory catalog reads via `GET /v1/policies`, `GET /v1/evaluators` and `GET /v1/memory`, configuration reads via `GET /v1/config`, confirmation resume via
`POST /v1/sessions/{id}/resume`, explicit pause via `POST /v1/sessions/{id}/pause`, cancellation via
`POST /v1/sessions/{id}/cancel`, operations reads via `/v1/metrics`,
`/v1/metrics/prometheus`, `/v1/cost`, `/v1/logs`, `/v1/traces`, `/v1/traces/otlp`,
`/v1/doctor`, `POST /v1/doctor/state-events/repair` for dry-run or confirmed state/event
consistency repair, and `/v1/audit`, per-session audit/cost/log/trace
reads including `/v1/sessions/{id}/traces/otlp`, `GET /v1/sessions/{id}/diagnostics` for
session/evidence/world fact inspection, dedicated `GET /v1/sessions/{id}/evidence` and
`GET /v1/sessions/{id}/world` explorer routes, and cursor session/event reads with `after` / `limit`
query parameters. `GET /v1/sessions/{id}/events/stream`
returns the same cursor batch as `text/event-stream` frames for SSE clients. `GET /console` returns a
read-only HTML Web Console snapshot and `GET /console/sessions/{id}` returns a focused Session
Detail page; `GET /console/sessions/{id}/evidence` and `/world` return focused Evidence and World
Model Explorer pages, `GET /console/domains/{name}/{version}` returns a read-only Domain
Manager detail page, and `GET /console/settings` returns Runtime settings built from the same
RuntimeService projections. `AgentdHttpServer`
is the standard-library HTTP bridge for this adapter: it owns socket/body/header translation only and
does not touch Runtime internals.

`agent` is the first local CLI adapter. It exposes version, health/readiness, Domain/Profile/
Capability/Tool/Policy/Evaluator/Memory catalogs, `config show`, and session
list/show/diagnostics/evidence/world/events/pause/resume/cancel commands through `RuntimeService`, with cursor flags and optional SSE text output for session event reads. It
can also load an
`agent init` Profile JSON through `--profile-config` and assemble the service through
`RuntimeHost`, so generated memory/file/SQLite store settings are used by subsequent CLI commands.
It also exposes operations commands for metrics, cost, logs, traces, doctor, audit and
`agent repair state-events --dry-run` / `--confirmed true` projections;
`agent metrics --format prometheus` emits Prometheus text exposition, while
`agent traces --format otlp` and `agent session traces <id> --format otlp` emit OTLP
JSON-compatible trace payloads from the same event-derived span projection. `agent serve` starts the
standard-library `AgentdHttpServer` around the same service; `agent eval run` executes the
local or file-backed evaluation suite through `EvaluationRunner`, and `agent eval compare` compares
persisted golden reports for CLI/CI regression checks. `agent eval replay` records and checks
deterministic golden replay recordings through the same suite selector. `agent eval list`,
`agent eval run` and `agent eval replay` support `--suite-file` plus kind/tag subset filters, and
all eval gate commands support `--fail-on-fail` to preserve JSON output while returning a non-zero
process status. `agent eval console` renders a deterministic read-only HTML Evaluation Console from
persisted reports. `agent tui` renders a read-only RuntimeService snapshot covering health,
readiness, metrics, catalogs, sessions, selected session details, recent events and audit
records. The CLI does not access Kernel internals directly.

`EvaluationHarness` is the first P3.7 behavior evaluation foundation. It runs explicit
`EvaluationScenario` objects through a RuntimeService-like interface, then verifies observable
Session, Event, Metrics and Audit projections. `EvaluationSuite` and
`EvaluationScenarioSelector` make scenario, regression, policy and recovery subsets first-class
contracts for local CI-style runs. `EvaluationQualityGate` evaluates suite-level pass rates,
completion rates, action success and tool failure rates, recovery budgets, execution duration
budgets, intervention rates, resource lock safety, action efficiency and model budget thresholds after execution.
`EvaluationRunner` composes suite execution, quality gates and optional
`EvaluationReportStore` persistence into one reusable application-facing module.
`compare_evaluation_reports` compares stable suite recordings for golden report regression checks.
`replay_execution` reconstructs execution history from recorded runtime events without calling a
model, tool or Domain backend.
`DeterministicReplayHarness` records a stable trace from those projections and replays later runs
against it while ignoring dynamic IDs and timestamps.
`FileReplayRecordingStore` persists those traces as JSON golden recordings for local regression tests.
`DeterministicRuntimeMode` supplies mock clock and ID primitives for tests that need stable recorded
events from the Runtime itself.
The harnesses are intentionally outside the Kernel: Domain `Evaluator`s still decide task/goal
semantics during execution, while the Harness decides whether a completed scenario satisfies
regression, policy, recovery, token/cost budget and replay expectations. See
`examples/p3_7_evaluation_harness.py`, `examples/p3_7_evaluation_runner.py`,
`examples/p3_7_execution_replay.py`, `examples/p3_7_replay.py` and
`examples/p3_7_deterministic_mode.py`.

`AgentProfile` is the first application-level Profile foundation. A Profile declares a selectable
runtime identity — name, version, Domain identity and Runtime Configuration — for future CLI/agentd
entry points. Profile selection is intentionally single-Runtime: agentd accepts only Profiles whose
configured Domains match the already assembled RuntimeService. It is not a new Kernel, not a Domain
implementation, not a multi-Runtime router, and not a routing Agent.

`FileSessionStore` / `FileEventStore` and `SQLiteSessionStore` / `SQLiteEventStore` are local
persistence adapters for P3.5 recovery tests and embedded deployments. They persist and list
`SessionSnapshot` documents and runtime events behind the same `SessionStore` and
`EventSink/EventReader` seams used by in-memory stores. `FileRuntimeStore` adds a local
write-ahead commit journal for file-backed state/event commits, while `SQLiteRuntimeStore` commits
the same state/event pair in one SQLite transaction. Session snapshots carry a store-managed
version, and memory/file/SQLite session stores reject stale snapshot saves instead of allowing silent
overwrites. These adapters are local persistence backends for `RuntimeHost` configuration, not
event-sourcing models or production migration systems.

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
  `parameters_hash`, `attempt`) carried through pending-action views, events and persistence,
  explicit `UNKNOWN_EXECUTION` observations for uncertain tool outcomes,
  runtime-owned resource locking and optimistic resource version checks for side-effecting actions
  (`resource_key`, optional `resource_version`, conflict detection, version check/update events
  and lock lifecycle events), and
  integration tests covering run/list/get/events plus explicit pause, non-confirmation resume,
  confirmation resume and cancellation.
  `RuntimeService` now adds framework-free `agentd` foundation metadata: health, readiness, domains,
  capabilities, tools, delegated execution, runnable examples, an `AgentdApp` route adapter for
  HTTP-shaped goal submission, cursor session listing, JSON and SSE-formatted session/event reads,
  pause/resume/cancel routes, runtime configuration reads,
  Profile catalog reads, a standard-library `AgentdHttpServer` bridge, file-backed session/event
  stores for local recovery, a local CLI adapter, and typed
  `RuntimeConfig` / `RuntimeHost` / `AgentProfile` assembly for environment, limits, memory/file/SQLite store backends,
  Domain identity validation, multi-Domain composition activation, and CLI loading of generated
  Profile config files through `RuntimeHost`.
- P3.6/P3.7 foundation: event-derived `metrics`, Prometheus metrics text export, `cost`, `logs`,
  `traces`, OTLP trace export, `doctor` and `audit` projections exposed through RuntimeService,
  agentd-shaped routes and CLI commands, plus optional
  `ModelUsageRecorded` events from model adapters. Structured log projections preserve runtime identifiers, event types, severity and redacted event data for CLI/agentd consumers. Trace span projections derive session/action trees plus decision, model usage, policy, observation, resource lock, resource conflict and evaluation phase spans from the same event stream with redacted attributes for OpenTelemetry-shaped consumers, and the OTLP adapter projects those spans into dependency-free collector payloads. Resource lock metrics and doctor checks report acquired/released locks, conflicts and active locks derived from runtime events; Doctor also validates state/event consistency by detecting orphan events and terminal sessions missing matching terminal events. The Evaluation Harness can assert status, error
  codes, events, Evidence claims, executed capabilities, audit coverage, policy denials, recovery plans, criteria,
  resource lock conflicts, active resource locks, action counts, iteration budgets, execution duration
  budgets and model token/cost budgets for behavior scenarios.
  Evaluation suites classify scenarios by kind and tags so regression, policy and recovery subsets
  can be selected without changing Kernel code. File-backed suite configs load those same typed
  scenario contracts plus optional quality gates from JSON for local CI runs, and quality gates turn suite metrics into CI-ready
  pass/fail checks. `EvaluationRunner` packages suite execution, gate evaluation and optional
  stable report persistence behind one interface for future CLI/CI adapters. Stable evaluation
  report recordings preserve scenario kind/tags and Evidence claim summaries, so comparisons can
  detect suite, scenario, gate, evidence and metric drift. Scenario, suite, report and replay keys
  are validated up front so persisted recordings cannot be silently ambiguous. The local CLI exposes these through
  `agent eval run` and `agent eval compare` without adding Kernel-specific evaluation branches.
  Execution replay can reconstruct decisions, actions, observations, evidence references and
  terminal status from recorded Runtime events without re-executing side effects.
  Deterministic Replay can record stable behavior traces and detect later drift in event shape,
  actions, policy effects, audit entries and metrics without depending on runtime-generated IDs.
  `DeterministicRuntimeMode` can also install stable runtime ID and clock primitives while building
  golden fixtures.
  Replay recordings can be encoded as versioned JSON and saved through `FileReplayRecordingStore`
  for golden regression fixtures.
- Evaluation Console foundation: `build_evaluation_console_snapshot` loads persisted evaluation
  reports and `render_evaluation_console` produces deterministic read-only HTML for CLI/CI review
  without coupling report visualization to Kernel or RuntimeService internals.
- Session Explorer foundation: `RuntimeService.session_explorer` rebuilds read-only world facts from
  persisted Evidence through Domain world updaters and exposes combined diagnostics plus dedicated
  Evidence and World Model Explorer routes through agentd/CLI.
- TUI foundation: `build_tui_snapshot` consumes RuntimeService projections and `render_tui_snapshot`
  produces a deterministic text view for CLI/operator use, including Profile/Capability/Tool/Policy/Evaluator/Memory
  catalogs plus selected-session Evidence and World Facts without touching Kernel internals.
- Web Console foundation: `build_web_console_snapshot` consumes the shared console snapshot builder
  and `render_web_console` / `render_web_session_detail` / `render_web_evidence_explorer` /
  `render_web_world_model_explorer` / `render_web_domain_detail` / `render_web_settings` produce
  deterministic read-only HTML for `AgentdApp`, including
  Profile/Domain/Capability/Tool/Policy/Evaluator/Memory catalogs plus focused Session Detail,
  Evidence, World Model, Domain Manager and Settings views without a web framework dependency or
  Kernel access.
- P6 Distributed Runtime foundation: `WorkScheduler` maps session/task/action identity into stable local
  work kinds and idempotency keys; `InMemoryWorkQueue`, `FileWorkQueue` and `SQLiteWorkQueue` provide typed `WorkItem`, `WorkerLease` and
  status contracts for local scheduler/worker adapters, including priority ordering, idempotent enqueue,
  lease acquisition, heartbeat renewal, retry-aware failure, cancellation and lease expiry; `WorkQueueWorker`
  consumes those leases through per-kind handlers, leases only declared work kinds by default, can
  register/heartbeat through `InMemoryWorkerRegistry`, renews queue and worker leases while async
  handlers run, stops leasing when draining/offline/lost, and maps handler completion, retry, failure
  and cancellation back into queue state; `InMemoryDistributedLockRegistry` and
  `FileDistributedLockRegistry` add leased lock acquisition, heartbeat, conflict rejection, expiry,
  release and local file-backed lock state for host rebuilds; `InMemoryWorkerRegistry` and
  `FileWorkerRegistry` track worker registration, heartbeat, draining, offline, lost states and
  local file-backed worker registry state for host rebuilds; `DistributedRuntimeCoordinator` exposes session, goal, task and confirmed pending-action scheduling, worker lifecycle, lock lifecycle, snapshot, health, expiry sweep and work-item cancellation over the queue, lock and worker primitives without changing AgentRuntime semantics; `RuntimeService.distributed_schedule_pending_actions` can sweep Runtime-owned waiting sessions and idempotently enqueue already-confirmed pending Actions; distributed session, task and action worker handlers acquire a session-scoped execution lock before resuming Runtime state; `RuntimeService.distributed_run_worker_once` and bounded `distributed_run_worker_until_idle` provide local queue → worker → RuntimeAPI paths for existing non-confirmation waiting sessions, matching current Tasks, confirmed pending Actions and newly scheduled Goals; `build_distributed_runtime_snapshot` aggregates queue, lock and worker state into a read-only local coordination view; `build_distributed_health_report` projects that snapshot into HA-oriented checks for worker capacity, backlog, lease freshness, leased-work owners and worker registry health.
  `RuntimeConfig.distributed_queue`, `RuntimeConfig.distributed_locks` and
  `RuntimeConfig.distributed_workers` let `RuntimeHost` assemble in-memory coordination primitives,
  local file-backed queue/lock/worker adapters, or SQLite-backed queue/lock/worker adapters for CLI/agentd deployments
  that need coordination state to survive host rebuilds.
- P7 Domain Package foundation: `DomainPackageManifest` defines package metadata for independently
  packaged Domain runtimes, including entrypoint, resources, dependencies, required tools,
  compatibility and security metadata. `DomainPackageRegistry` can validate, install and discover
  package manifests without importing Domain code or mutating Kernel runtime state.
  `DomainPackageRegistry.verify()` and `agent domain-packages verify` expose dependency-closure
  checks for local package metadata so CLI/CI can catch missing package dependencies before
  activation.
  `DomainPackageScaffoldSpec` and `scaffold_domain_package` provide the first Domain SDK surface for
  generating a standard package layout and validated manifest from typed metadata.
- P7 Evaluation Dataset foundation: `EvaluationDatasetManifest` groups reusable evaluation suite
  files into discoverable datasets with Domain, tag, suite and author metadata. `EvaluationDatasetRegistry`
  validates referenced suite configs and lists or retrieves datasets without executing scenarios or
  coupling dataset cataloging to RuntimeService internals. `EvaluationDatasetRegistry.verify()` and
  `agent eval datasets --verify` re-check local dataset manifests and suite files for CLI/CI without
  running evaluation scenarios.
- P7 Profile Catalog foundation: `ProfileCatalog` discovers `profile.json` and `*.profile.json`
  files, validates them through `ProfileConfig`, preserves source paths and exposes a `ProfileRegistry`
  view for application adapters without changing RuntimeHost configuration semantics.
- P7 Ecosystem Catalog foundation: `EcosystemCatalog` composes Domain Package, Evaluation Dataset
  and Profile catalogs into one local read-only index with counts and typed entries. It remains a
  metadata surface only: it does not activate Domains, run scenarios or assemble RuntimeHost objects.
  `EcosystemCatalog.verify()` adds an ecosystem integrity check for missing Profile Domain,
  Evaluation Dataset Domain and Domain Package dependency references. `EcosystemRegistryManifest`
  exports the discovered package, dataset and Profile references as a stable local registry manifest
  with Domain package compatibility/security metadata, a read-only query index, file-backed registry
  store, local Domain Package install plan and full ecosystem install plan/result that validates
  referenced package manifests, evaluation datasets and Profile configs before registering metadata
  for CLI/CI and future package-registry adapters. `agent ecosystem install` now exposes that full
  package/dataset/Profile metadata install surface from registry manifests.

The Kubernetes Domain uses injected backends. Tests and examples use fake backends; no real cluster
is accessed and no `kubectl` command is executed. The read-only `KubernetesDomain` remains available,
while `KubernetesRemediationDomain` adds the fake-backed mutation path. Multi-domain operation now
has a conservative `DomainManager` / `DomainComposition` foundation: Domain identities,
capabilities and tools are validated before activation, Domain Loader rejects empty evaluator sets,
Observation processing routes Evidence extraction, World updating, Task expansion and evaluation
by the executed action's Domain, Profiles may declare ordered Domain sets, and snapshots persist
the activated composition for safe resume.
Cross-domain World Model reasoning,
production database migration systems, packaging, marketplace behavior, optional Multi-Agent Runtime, and real
Kubernetes API remediation remain outside P3.2. Persistence includes in-memory stores plus local file-backed and SQLite-backed session/event adapters with
snapshot isolation; event sourcing and schema migration are not included.

## Roadmap alignment

The design roadmap now separates semantic runtime maturity from productization:

- P0-P3: core Agent semantics, Domain Runtime, World/Evidence/Recovery, Memory, Multi-Domain, and
  Agent Profiles.
- P3.5: Runtime Productization — Runtime API, Session API, `agentd`, CLI, Event Stream, Persistence,
  Resume / Pause / Cancel, and Runtime Configuration.
- P3.6-P3.7: Operations and Evaluation — OpenTelemetry, metrics, audit, cost tracking, runtime
  doctor, evaluation suites, quality gates, replay, and deterministic test mode.
- P5: Read-only TUI/Web application views for runtime, session, evidence, world, domain and settings inspection.
- P6: Distributed Runtime foundations — typed local Scheduler, Work Queue, Worker Registry, Worker Lease, capability-aware Worker handler
  execution, scheduled Goal execution, current Task resume, leased lock, Runtime Snapshot, Health Report, Coordinator, Heartbeat, retry, cancellation and lease expiry primitives.
- P7: Ecosystem packaging and registry work.

`PROMPT.md` is intentionally not kept as a project authority. Development instructions live in
`AGENTS.md`; architecture lives in `universal-agent-runtime-domain-runtime-design.md`; operational
usage stays in this README.

For the latest dated implementation assessment, current limitations, verification snapshot, and
recommended next steps, see [`docs/revision/2026-08-24-project-status.md`](docs/revision/2026-08-24-project-status.md).
The previous snapshot remains at [`docs/revision/2026-08-23-project-status.md`](docs/revision/2026-08-23-project-status.md).

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
.venv/bin/python examples/p3_multi_domain_evaluator_routing.py
.venv/bin/python examples/p3_2_kubernetes_remediation.py
.venv/bin/python examples/p3_5_runtime_api.py
.venv/bin/python examples/p3_5_runtime_service.py
.venv/bin/python examples/p3_5_agentd_routes.py
.venv/bin/python examples/p3_5_persistence.py
.venv/bin/python examples/p3_5_sqlite_persistence.py
.venv/bin/python examples/p3_5_runtime_config.py
.venv/bin/python examples/p3_5_cli_config.py
.venv/bin/python examples/p3_5_cli_profile_config.py
.venv/bin/python examples/p3_5_cli_event_stream.py
.venv/bin/python examples/p3_5_cli_run.py
.venv/bin/python examples/p3_6_cost_tracking.py
.venv/bin/python examples/p3_6_structured_logs.py
.venv/bin/python examples/p3_6_traces.py
.venv/bin/python examples/p3_7_evaluation_harness.py
.venv/bin/python examples/p3_7_evaluation_runner.py
.venv/bin/python examples/p3_7_execution_replay.py
.venv/bin/python examples/p3_7_replay.py
.venv/bin/python examples/p3_7_cli_replay.py
.venv/bin/python examples/p3_7_cli_artifacts.py
.venv/bin/python examples/p3_7_cli_quality_gates.py
.venv/bin/python examples/p3_7_suite_file.py
.venv/bin/python examples/p3_7_deterministic_mode.py
.venv/bin/python examples/p5_evaluation_console.py
.venv/bin/python examples/p5_tui.py
.venv/bin/python examples/p5_web_console.py
.venv/bin/python examples/p5_session_diagnostics.py
.venv/bin/python examples/p6_distributed_queue.py
.venv/bin/python examples/p6_file_work_queue.py
.venv/bin/python examples/p6_distributed_worker.py
.venv/bin/python examples/p6_capability_aware_worker.py
.venv/bin/python examples/p6_runtime_service_worker.py
.venv/bin/python examples/p6_runtime_service_worker_batch.py
.venv/bin/python examples/p6_distributed_scheduler.py
.venv/bin/python examples/p6_distributed_lock.py
.venv/bin/python examples/p6_file_distributed_locks.py
.venv/bin/python examples/p6_worker_registry.py
.venv/bin/python examples/p6_file_worker_registry.py
.venv/bin/python examples/p6_distributed_snapshot.py
.venv/bin/python examples/p6_distributed_health.py
.venv/bin/python examples/p6_distributed_coordinator.py
.venv/bin/python examples/p6_distributed_cancel.py
.venv/bin/python examples/p6_distributed_schedule.py
.venv/bin/python examples/p6_distributed_action.py
.venv/bin/python examples/p6_distributed_pending_actions.py
.venv/bin/python examples/p6_runtime_host_file_queue.py
.venv/bin/python examples/p6_runtime_host_sqlite_queue.py
.venv/bin/python examples/p6_runtime_host_sqlite_locks.py
.venv/bin/python examples/p6_runtime_host_sqlite_workers.py
.venv/bin/python examples/p6_runtime_host_file_coordination.py
.venv/bin/python examples/p6_worker_lifecycle.py
.venv/bin/python examples/p6_distributed_lock_lifecycle.py
.venv/bin/python examples/p7_domain_package_registry.py
.venv/bin/python examples/p7_domain_package_scaffold.py
.venv/bin/python examples/p7_evaluation_dataset.py
.venv/bin/python -m universal_agent.cli eval datasets --dataset-dir .tmp/evaluation-datasets --verify
.venv/bin/python examples/p7_profile_catalog.py
.venv/bin/python examples/p7_ecosystem_catalog.py
.venv/bin/python examples/p7_ecosystem_registry_manifest.py
.venv/bin/python examples/p7_ecosystem_registry_store.py
.venv/bin/python examples/p7_ecosystem_registry_install.py
.venv/bin/python -m universal_agent.cli ecosystem store list --store-dir .tmp/ecosystem-registries
.venv/bin/python -m universal_agent.cli ready
.venv/bin/python -m universal_agent.cli distributed health
.venv/bin/python -m universal_agent.cli distributed snapshot
.venv/bin/python -m universal_agent.cli distributed expire
.venv/bin/python -m universal_agent.cli distributed schedule-session session-1 --priority 5 --max-attempts 2
.venv/bin/python -m universal_agent.cli distributed schedule-action session-1 task-1 action-1 --confirmed true --priority 5
.venv/bin/python -m universal_agent.cli distributed schedule-pending-actions --confirmed true --priority 5
.venv/bin/python -m universal_agent.cli distributed worker-register worker-a --capability agent_session
.venv/bin/python -m universal_agent.cli distributed worker-heartbeat worker-a
.venv/bin/python -m universal_agent.cli distributed worker-run-once worker-a
.venv/bin/python -m universal_agent.cli distributed worker-run worker-a --max-items 5
.venv/bin/python -m universal_agent.cli distributed worker-drain worker-a --reason "finish current lease"
.venv/bin/python -m universal_agent.cli distributed worker-offline worker-a --reason "shutdown complete"
.venv/bin/python -m universal_agent.cli distributed lock-acquire session/session-1 --owner-id worker-a
.venv/bin/python -m universal_agent.cli distributed lock-heartbeat lock-lease-1 --owner-id worker-a
.venv/bin/python -m universal_agent.cli distributed lock-release lock-lease-1 --owner-id worker-a
.venv/bin/python -m universal_agent.cli distributed cancel work-1 --reason "operator cancelled queued work"
.venv/bin/python -m universal_agent.cli init --output .tmp/sqlite-profile.json --store-backend sqlite --store-path .tmp/runtime.sqlite3 --force
.venv/bin/python -m universal_agent.cli --profile-config .tmp/sqlite-profile.json config show
.venv/bin/python -m universal_agent.cli init --output .tmp/file-queue-profile.json --distributed-queue-backend file --distributed-queue-path .tmp/work-queue.json --force
.venv/bin/python -m universal_agent.cli --profile-config .tmp/file-queue-profile.json config show
.venv/bin/python -m universal_agent.cli init --output .tmp/sqlite-locks-profile.json --distributed-locks-backend sqlite --distributed-locks-path .tmp/distributed-locks.sqlite3 --force
.venv/bin/python -m universal_agent.cli --profile-config .tmp/sqlite-locks-profile.json config show
.venv/bin/python -m universal_agent.cli init --output .tmp/sqlite-queue-profile.json --distributed-queue-backend sqlite --distributed-queue-path .tmp/work-queue.sqlite3 --force
.venv/bin/python -m universal_agent.cli --profile-config .tmp/sqlite-queue-profile.json config show
.venv/bin/python -m universal_agent.cli init --output .tmp/sqlite-workers-profile.json --distributed-workers-backend sqlite --distributed-workers-path .tmp/workers.sqlite3 --force
.venv/bin/python -m universal_agent.cli --profile-config .tmp/sqlite-workers-profile.json config show
.venv/bin/python -m universal_agent.cli eval list local-kubernetes --kind policy --tag kubernetes
.venv/bin/python -m universal_agent.cli eval run local-kubernetes --kind regression --tag smoke --report-dir .tmp/eval-reports --fail-on-fail
.venv/bin/python -m universal_agent.cli eval reports --report-dir .tmp/eval-reports
.venv/bin/python -m universal_agent.cli eval replay local-kubernetes --recording-dir .tmp/replay-recordings --kind regression --update
.venv/bin/python -m universal_agent.cli eval recordings --recording-dir .tmp/replay-recordings
.venv/bin/python -m universal_agent.cli eval replay local-kubernetes --recording-dir .tmp/replay-recordings --kind regression --fail-on-fail
```

`mypy` runs in strict mode over `src`, `tests` and `examples`, and passes with no `type: ignore`
anywhere in the repository: the Domain extension points are `Protocol`s, so a Domain is recognised by
shape and its implementations are checked structurally rather than through inheritance. Tests and
examples are held to the same standard deliberately — an unannotated fake Domain would be exactly the
place where a broken extension point could hide.

`ruff format --check` is scoped to the source directories because newer Ruff releases also reformat
fenced code blocks inside the design Markdown, which is not part of the Python formatting contract.
