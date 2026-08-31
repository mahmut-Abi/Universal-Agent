# Remaining Implementation TODO (2026-08-31)

This list tracks the gaps identified from
`universal-agent-runtime-domain-runtime-design.md` against the current working
tree. It separates locally-completable foundation work from items that need a
real deployment environment, credentials, or production infrastructure.

## Completed In This Pass

- [x] Wire `GoalCompiler` into `AgentRuntime` when configured and expose an
  explicit compiled-goal execution path through Runtime API, RuntimeService,
  SDK, agentd and CLI.
- [x] Make compiled goal task graphs executable by assigning the compiler root a
  stable task id and adding `TaskManager.from_specs()`.
- [x] Wire `DecisionEngine` into `AgentRuntime` so custom engines can own
  decision proposal before runtime validation.
- [x] Wire `ModelRouter` into `AgentRuntime` for risk-aware model selection
  before decision proposal.
- [x] Wire `Sandbox` guard into `ActionExecutor` after deterministic policy and
  before tool invocation.
- [x] Add a read-only Observability Domain with `query_metrics`, evidence
  extraction, world projection, metric evaluation, fixture backend and
  Prometheus HTTP adapter.
- [x] Add a Kubernetes + Observability multi-domain behavior test proving shared
  world/evidence composition across two concrete domains.
- [x] Add a durable-store event stream fallback: any `EventReader` can now be
  watched through cursor-preserving polling, and File/SQLite event stores expose
  `watch_events()` for SSE consumers.
- [x] Add a SQLite runtime event outbox foundation so committed/appended events
  can be listed as pending publisher work and marked published without changing
  Runtime state semantics.
- [x] Add an audit integrity hash-chain projection and expose it through
  RuntimeService, agentd and CLI audit surfaces.
- [x] Add a Multi-Agent delegation ledger foundation with in-memory/file-backed
  lifecycle events, payload round-tripping and DelegationManager integration.
- [x] Reduce core runtime surface by wiring `CapabilityAdvisor`,
  `MemoryConsultant` and `EventEmitter` into `AgentRuntime`, then extracting
  initial-state seeding into `runtime/initial_state.py`.
- [x] Reduce evaluation harness surface by extracting evaluation initial-state
  seed models and payload conversion into `evaluation/initial_state.py` while
  preserving existing `harness` imports.
- [x] Add a documented gated CI job for live Kubernetes contract checks, with
  redacted provider/cluster contract artifact upload controlled by explicit
  repository variables and secrets.
- [x] Add a store-agnostic transactional outbox publisher seam that leases
  committed RuntimeEvents, publishes them through an abstract broker adapter,
  marks successful publishes, and releases failed publishes for retry.
- [x] Add a provider-neutral broker event stream watcher contract where broker
  notifications are lossy wakeups, durable `EventReader` state owns reconnect
  cursors, and notification backpressure is explicitly coalesced without losing
  committed events.
- [x] Add explicit fencing tokens to P6 work queue leases and distributed lock
  leases, including JSON/SQLite payload compatibility and runtime snapshot
  projection.
- [x] Add cross-domain World identity canonicalization for `relation:same_as`
  aliases, expose identity mappings from merged snapshots, and add bounded
  multi-hop relation graph traversal on `WorldSnapshot`.
- [x] Add explicit cross-domain world merge policy with configurable fact
  selection strategy while keeping confidence-then-recency as the default.
- [x] Restore full local quality gates: `ruff check .`, `mypy`, and full
  `pytest` all pass in the current workspace.

## Remaining TODO

- [ ] Requested execution queue from 2026-08-31 follow-up:
  - [ ] Add real Kubernetes live-like contract harness and documentation; requires
    real cluster credentials and model provider credentials for completion.
  - [ ] Add production-grade Postgres persistence, migrations and outbox
    publisher; requires final DB deployment decisions for completion.
  - [ ] Add production broker-backed event stream with reconnect/backpressure
    semantics; requires broker and deployment decisions for completion.
  - [ ] Add enterprise AuthN/AuthZ, tenant, KMS/Vault and audit storage;
    requires identity and key-management system decisions for completion.
  - [ ] Upgrade P5 UI from read-only snapshots to an operator console; requires
    product interaction scope confirmation for full UX completion.
  - [ ] Complete P6 cross-node distributed queue, lease, fencing and HA
    semantics; requires cluster backend decisions for completion.
  - [ ] Complete P7 external package install, mandatory signature verification
    and isolated activation; requires supply-chain policy decisions for
    completion.
- [ ] P0/P3.7 live Kubernetes proof: run the production operator path against a
  real cluster and real model provider in live CI, including diagnose -> safe
  remediation or confirmation -> fresh verification.
  - [x] Add a shared Kubernetes live contract artifact writer that redacts
    secret-shaped fields and refuses to write artifacts that fail the runtime
    secret scanner.
  - [x] Wire the opt-in live Kubernetes tests to write check/run contract
    artifacts when `UNIVERSAL_AGENT_LIVE_KUBERNETES_ARTIFACT_DIR` is set.
  - [ ] Execute the gated live flow with real profile, model credential,
    scoped Kubernetes credential and approved workload target.
- [ ] P3.5 persistence: add production Postgres-backed session/event/world
  stores, schema migrations, production transactional outbox publisher and
  replay repair semantics.
  - [x] Add Postgres schema DDL, tenant-scoped session/event tables and leased
    runtime event outbox rows.
  - [x] Add a production outbox publisher contract above leased outbox stores
    and broker adapters.
  - [ ] Execute the Postgres adapter and publisher against the selected
    production Postgres topology.
- [ ] P3.5 event stream: replace local polling-backed delivery with production
  event delivery, including reconnect contracts, backpressure and broker-backed
  delivery where needed.
  - [x] Add the provider-neutral broker signal seam and watcher that drains the
    authoritative EventReader by cursor after reconnect or signal delivery.
  - [x] Add local coalescing backpressure semantics for broker notifications so
    dropped wakeups do not imply dropped RuntimeEvents.
  - [ ] Select the production broker and implement its signal adapter.
- [ ] P3.6/P5 security: add production AuthN/AuthZ, tenant boundaries,
  KMS/Vault secret resolution, credential rotation and tamper-resistant audit
  storage beyond the current integrity projection.
- [x] Observability domain depth: extend beyond instant Prometheus queries with
  range queries, alert/rule inspection, metric-to-resource relationship mapping
  and live contract tests.
- [x] Cross-domain world depth: extend conflict resolution strategy and richer
  graph query semantics beyond the first `relation:same_as` canonicalization,
  bounded relation traversal and configurable fact merge policy.
  - [x] Canonicalize entity aliases during cross-domain merge.
  - [x] Expose identity mappings in merged world results.
  - [x] Add bounded multi-hop relation traversal to `WorldSnapshot`.
  - [x] Add configurable fact merge policy.
  - [x] Add explicit conflict resolution strategy and graph query predicates.
- [ ] P5 UI controls: upgrade Web UI from read-only snapshots to controlled
  operator actions with the same policy/confirmation boundaries as CLI and
  agentd.
- [ ] P4 Multi-Agent productionization: add parent/child runtime event
  correlation, network delegation transport, child session lifecycle recovery
  and cross-runtime evidence handoff beyond the local delegation ledger.
- [ ] P6 distributed runtime productionization: add cross-node queue/lease
  backend, worker fencing, clock-skew handling, duplicate execution policy,
  leader election and high availability.
  - [x] Add explicit monotonic fencing tokens to local queue and lock lease
    contracts so future cross-node adapters have a stable compare-and-fence
    field.
  - [ ] Select and implement the cross-node queue/lock backend, leader election
    and HA deployment policy.
- [ ] P7 ecosystem productionization: add external package acquisition,
  mandatory signature verification, dependency installation, sandboxed Domain
  activation and registry trust policy enforcement.
- [ ] Maintenance surface reduction: continue splitting large modules and
  consolidating repetitive view/codec/projection code without changing runtime
  contracts.

## Current Blockers

- Live Kubernetes completion requires a real cluster, scoped kube credentials,
  a real model endpoint/API key and an approved workload target.
- Production persistence, brokered event streaming, KMS/Vault and HA scheduling
  require infrastructure choices that should be made before implementation.
