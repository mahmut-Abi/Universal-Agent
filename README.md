# Universal Agent Runtime Platform

A typed Universal Agent Kernel and Runtime with pluggable Domain Runtimes.

The long-term architecture is defined in
`universal-agent-runtime-domain-runtime-design.md`. The current implementation is a typed runtime
with fake-backed Kubernetes remediation plus the first P3.5 productization foundation: a stable
in-process Runtime API, immutable Session read models, readable Events, and local file-backed
session/event persistence. The v3.0 design document also defines later productization layers such as
real `agentd`, CLI, database persistence, streaming event delivery, operations, evaluation, optional
Multi-Agent, UI, distributed runtime, and ecosystem packaging.

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
  Evidence, recovery budget, pending confirmation, activated Domain identity — is saved as a
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
- `resume_session(session_id, confirmed=...)` resumes a waiting confirmation through the same
  policy-checked runtime path as `AgentRuntime.resume`.
- `cancel_session(session_id, reason=...)` cancels a non-terminal session, clears any pending action,
  and returns a cancelled run plus the latest session projection.
- `get_session(session_id)` loads an immutable projection of the latest `SessionSnapshot`.
- `list_events(session_id)` returns immutable event projections filtered to one session.

This is intentionally still in-process. Real HTTP `agentd`, database persistence, SSE delivery, CLI,
and explicit pause endpoints are later P3.5 work built on this interface, not replacements for it.

`RuntimeService` is the first framework-free `agentd` foundation. It delegates execution, session and
event reads to `RuntimeAPI`, and adds service-level health, readiness, Domain, Capability and Tool
catalog views for future HTTP and CLI adapters. It does not start a daemon, open sockets, or access
Kernel internals directly. See `examples/p3_5_runtime_api.py` and
`examples/p3_5_runtime_service.py` for the minimal application-facing usage.

`AgentdApp` is the framework-free route adapter foundation for `agentd`. It accepts small
`HttpRequest` objects and returns JSON-safe `HttpResponse` objects for `GET /health`, `GET /ready`,
catalog routes, session/event reads, and confirmation resume via
`POST /v1/sessions/{id}/resume` plus cancellation via `POST /v1/sessions/{id}/cancel`. It still does
not open sockets; a real HTTP server can wrap this adapter later without touching Runtime internals.

`FileSessionStore` and `FileEventStore` are local persistence adapters for P3.5 recovery tests and
embedded demos. They persist `SessionSnapshot` JSON documents and JSONL runtime events behind the
same `SessionStore` and `EventSink/EventReader` seams used by in-memory stores. They are not a
database layer, event-sourcing model, or production migration system.

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
  projections, `EventReader`, and integration tests covering run/get/events plus confirmation resume
  and cancellation. `RuntimeService` now adds framework-free `agentd` foundation metadata: health,
  readiness, domains, capabilities, tools, delegated execution, runnable examples, an `AgentdApp`
  route adapter for HTTP-shaped reads plus confirmation resume and cancellation, and file-backed
  session/event stores for local recovery.

The Kubernetes Domain uses injected backends. Tests and examples use fake backends; no real cluster
is accessed and no `kubectl` command is executed. The read-only `KubernetesDomain` remains available,
while `KubernetesRemediationDomain` adds the fake-backed mutation path. Multi-domain operation,
Domain Composition, cross-domain World Model, Agent Profile, persistent databases, packaging,
marketplace behavior, optional Multi-Agent Runtime, and real Kubernetes API remediation remain outside
P3.2. Persistence includes in-memory stores plus local file-backed session/event adapters with
snapshot isolation; no database backend, event sourcing, or schema migration is included.

## Roadmap alignment

The design roadmap now separates semantic runtime maturity from productization:

- P0-P3: core Agent semantics, Domain Runtime, World/Evidence/Recovery, Memory, Multi-Domain, and
  Agent Profiles.
- P3.5: Runtime Productization — Runtime API, Session API, `agentd`, CLI, Event Stream, Persistence,
  Resume / Pause / Cancel, and Runtime Configuration.
- P3.6-P3.7: Operations and Evaluation — OpenTelemetry, metrics, audit, cost tracking, runtime
  doctor, evaluation harness, replay, and deterministic test mode.
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
```

`mypy` runs in strict mode over `src`, `tests` and `examples`, and passes with no `type: ignore`
anywhere in the repository: the Domain extension points are `Protocol`s, so a Domain is recognised by
shape and its implementations are checked structurally rather than through inheritance. Tests and
examples are held to the same standard deliberately — an unannotated fake Domain would be exactly the
place where a broken extension point could hide.

`ruff format --check` is scoped to the source directories because newer Ruff releases also reformat
fenced code blocks inside the design Markdown, which is not part of the Python formatting contract.
