# Revision Master Backlog

> Last updated: 2026-09-11  
> Purpose: single issue-style checklist for everything tracked under `docs/revision/`.  
> Principle: finish user-visible P0/P1 reliability before expanding P4/P6/P7 production features.

## How To Use This File

Use this file as the working backlog for the project.

- Keep each item as a checkbox heading: change `[ ]` to `[x]` only when the item meets its **Done when** criteria.
- If an item is blocked, keep `[ ]` unchecked and add a `Blocked:` line with the external dependency.
- If an item is superseded, keep it visible and mark `Status: superseded by <id>` rather than deleting it.
- Add evidence when completing an item: commit hash, test command, demo artifact, or linked revision note.
- Prefer small follow-up docs over expanding this file too much; this file is the index, not the full design.

### Status Labels

- `[ ]` open / not done
- `[x]` completed and verified
- `Priority: P0` must be fixed before new capability work
- `Priority: P1` next product/reliability milestone
- `Priority: P2` important but can wait until P0/P1 are stable
- `Priority: P3` productionization or strategic follow-up
- `Blocked:` requires external infra, credentials, or product decision

### Issue Template

```markdown
### [ ] UA-AREA-000 — Short imperative title

- Priority: P0/P1/P2/P3
- Area: Product / Runtime / Kubernetes / Security / Docs / Tests / Maintenance / Production
- Source: `docs/revision/...`
- Why: one sentence explaining user/runtime value
- Done when:
  - Specific observable acceptance criterion
  - Specific test/demo/doc evidence
```

## Source Documents

- `Universal-Agent-P0-spec.md` — P0 productization contract and DoD.
- `docs/product.md` — current product vocabulary and CLI entry decisions.
- `docs/revision/2026-09-11-p0-golden-path-validation.md` — P0 validation snapshot.
- `docs/revision/2026-09-11-project-optimization-recommendations.md` — detailed optimization recommendations.
- `docs/revision/2026-08-31-remaining-todo.md` — remaining production and infrastructure TODOs.
- `docs/revision/2026-08-31-live-kubernetes-run-recommendations.md` — live Kubernetes drill findings.
- `docs/revision/2026-08-29-critical-review.md` — critical review of architecture breadth, API surface, examples, and large files.
- `docs/revision/2026-08-26-project-status.md` — implementation status and engineering boundaries.
- `docs/revision/2026-08-24-project-status.md` and `docs/revision/2026-08-23-project-status.md` — earlier status snapshots.
- `docs/revision/2026-08-21-21-40-33.md` — earlier code audit record.

## Current Priority Order

1. **P0.1 product polish** — make the first-day CLI/config/run/session experience unambiguous.
2. **P1 behavior proof** — prove the runtime completes real or live-like tasks, not only payload contracts.
3. **P1/P2 reliability** — close Kubernetes correctness, model contract, recovery, timeout, and secret-safety gaps.
4. **P2 maintenance reduction** — split large files and shrink public API without changing behavior.
5. **P3 production decisions** — choose real Postgres/broker/AuthN/AuthZ/KMS/HA/package-trust approaches before building more.
6. **Advanced freeze** — keep P4/P6/P7 features available but clearly experimental until P0/P1 are solid.

---

# A. Completed / Baseline Items

### [x] UA-DONE-001 — Validate P0 Golden Path first pass

- Priority: P0
- Area: Product / CLI / Runtime
- Source: `docs/revision/2026-09-11-p0-golden-path-validation.md`
- Why: proves the product has a usable install/init/doctor/run/session path.
- Done when:
  - `uv run ua --help`, `ua init`, `ua doctor`, `ua run`, `ua session list`, and `ua session show` complete in clean-room validation.
  - Validation record exists under `docs/revision/`.

### [x] UA-DONE-002 — Restore local quality gates after P0 changes

- Priority: P0
- Area: Tests / Tooling
- Source: `docs/revision/2026-09-11-p0-golden-path-validation.md`
- Why: prevents productization changes from breaking the typed runtime.
- Done when:
  - `uv run ruff format --check src tests examples` passes.
  - `uv run ruff check .` passes.
  - `uv run mypy` passes.
  - `uv run pytest tests/` passes except expected live skips.

### [x] UA-DONE-003 — Prove Kubernetes operator loop in a real manual drill

- Priority: P0
- Area: Kubernetes / Runtime
- Source: `docs/revision/2026-08-31-live-kubernetes-run-recommendations.md`
- Why: proves the runtime can inspect, diagnose, policy-gate, mutate, and verify a real Kubernetes workload.
- Done when:
  - Real cluster drill reaches inspect -> diagnose -> confirmation -> scale -> fresh verification.
  - Evidence and session records demonstrate policy outside the model.

### [x] UA-DONE-004 — Establish product vocabulary and entry decisions

- Priority: P0
- Area: Product / Docs
- Source: `docs/product.md`
- Why: aligns README, CLI help, init defaults, and user-facing concepts.
- Done when:
  - Agent, Runtime, Profile, Domain, Policy, Session, Web, and agentd definitions exist.
  - CLI entry decision is documented.
  - Default profile and advanced command positioning decisions are documented.

---

# B. P0.1 Product Polish Backlog

These are the next highest-ROI improvements. They are not new agent capabilities; they make the product easier to start, understand, and debug.

### [x] UA-P01-001 — Reconcile config location semantics in all contexts

- Priority: P0
- Area: Product / CLI / Config
- Source: `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: `agent init`, help text, README, and discovery behavior must describe the same config location.
- Done when:
  - The project explicitly decides default behavior for running `agent init` inside a repo, outside a repo, with `$AGENT_CONFIG_DIR`, and with `--global` if added.
  - `agent init --help`, `agent init` output, `agent config`, README, and `docs/product.md` all agree.
  - Tests cover clean HOME + clean cwd config discovery.

### [x] UA-P01-002 — Make `default` profile truly domain-neutral or explicitly rename it

- Priority: P0
- Area: Product / Profile / Domain
- Source: `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: first-day `agent run "Hello"` should not look like a Kubernetes cluster inspection unless the user chose a Kubernetes profile.
- Done when:
  - `agent run "Hello"` with the default profile does not mention Kubernetes, cluster, workload, pod, or workload health criteria.
  - Kubernetes fake/local profiles remain available under explicit names such as `local-kubernetes` or `sre-kubernetes`.
  - A regression test asserts default profile output is domain-neutral.

### [x] UA-P01-003 — Split beginner and advanced CLI help surfaces

- Priority: P0
- Area: CLI / UX
- Source: `docs/product.md`, `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: new users should not see a command/flag wall before they know init -> doctor -> run -> session.
- Done when:
  - `agent --help` visually prioritizes `init`, `doctor`, `run`, `session`, `config`, and `profile`.
  - Advanced commands are grouped and clearly labeled advanced/experimental where appropriate.
  - `agent init --help` shows only first-day options by default or clearly separates advanced backend/model/distributed flags.

### [x] UA-P01-004 — Enrich `agent config` and `agent profile show`

- Priority: P0
- Area: CLI / Config / Profile
- Source: `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: users need one command to answer where model, profile, policy, domain, secrets, and persistence are configured.
- Done when:
  - `agent config` shows active profile, model, credentials status without values, runtime store absolute path, policy, domains, config paths, and discovery order.
  - `agent profile show default` shows model, domains, policy, runtime settings, and description.
  - JSON output remains stable for machines.

### [x] UA-P01-005 — Make `agent session show` human-first

- Priority: P1
- Area: CLI / Session / Evidence
- Source: `docs/revision/2026-09-11-project-optimization-recommendations.md`, `docs/revision/2026-08-31-live-kubernetes-run-recommendations.md`
- Why: users should understand what happened without reading raw runtime event names.
- Done when:
  - Default `session show` has a concise Summary and What happened section.
  - Raw technical timeline remains available through `session events` or JSON.
  - Evidence/action counts and terminal reason are still visible.

### [x] UA-P01-006 — Add `agent session explain <id>` for failures and waiting sessions

- Priority: P1
- Area: Session / Diagnostics
- Source: `docs/revision/2026-08-31-live-kubernetes-run-recommendations.md`
- Why: failed sessions currently require expert event/world-model reconstruction.
- Done when:
  - `session explain` translates common failures into Reason/Try output.
  - It explains policy waits, invalid finish decisions, evaluator mismatch, missing credentials, tool failure, and session-not-waiting cases.
  - Implementation is a service/read-model projection; it does not change runtime state.

### [x] UA-P01-007 — Standardize repairable CLI errors

- Priority: P1
- Area: CLI / UX / Errors
- Source: `Universal-Agent-P0-spec.md`, `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: ordinary users should see Error / Reason / Try, not stack traces or raw internal exceptions.
- Done when:
  - Common CLI failures use a shared renderer.
  - Covered errors include missing API key, invalid config, model unavailable, domain unavailable, policy denied, tool failed, session not found, profile not found, unsupported backend, and agentd unavailable.
  - Tests assert user-facing text contains an actionable `Try:` line.

### [x] UA-P01-008 — Add doctor final next-step guidance

- Priority: P1
- Area: CLI / Doctor
- Source: `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: doctor should tell users the next command to run, not only status.
- Done when:
  - Successful doctor ends with `Next: agent run "Hello"` or equivalent.
  - Warning/error doctor output recommends the exact command or environment variable to fix the issue.
  - Exit-code behavior remains controlled by `--fail-on`.

---

# C. P1 Behavior Proof Backlog

### [x] UA-P1-001 — Add reproducible local Golden Demo script

- Priority: P1
- Area: Demo / Docs / Tests
- Source: `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: README should be backed by a script that proves the local product path.
- Done when:
  - `scripts/demo-local.sh` runs with clean HOME and clean workspace.
  - It performs init, doctor, run, session list, and session show.
  - README commands match the script commands.

### [x] UA-P1-002 — Add live-like Kubernetes kind/minikube contract test

- Priority: P1
- Area: Kubernetes / Testing
- Source: `docs/revision/2026-09-11-project-optimization-recommendations.md`, `docs/revision/2026-08-31-remaining-todo.md`
- Why: live-like tests reduce dependency on real cluster credentials while exercising real Kubernetes semantics.
- Done when:
  - A local kind/minikube scenario can provision an unhealthy workload.
  - The runtime diagnoses, applies safe remediation or reaches confirmation, and performs fresh verification.
  - Test can be skipped cleanly when kind/minikube is unavailable.

### [ ] UA-P1-003 — Add gated real Kubernetes + real model CI contract

- Priority: P1
- Area: Kubernetes / CI / Production Proof
- Source: `docs/revision/2026-08-31-remaining-todo.md`
- Why: the project needs a repeatable proof that the production operator path works outside manual runs.
- Done when:
  - Gated CI job uses explicit repository variables/secrets.
  - Artifacts are redacted and rejected if secret scanning fails.
  - The job verifies diagnose -> remediation/confirmation -> fresh verification.
- Blocked: requires scoped cluster credentials, model provider credentials, and approved target workload.

### [x] UA-P1-004 — Re-confirm or fix Kubernetes evidence claim granularity collision

- Priority: P0
- Area: Kubernetes / Evidence / Evaluation
- Source: `docs/revision/2026-08-31-live-kubernetes-run-recommendations.md`
- Why: workload-level and pod-level `resource` claims must not conflict and block completed health evaluation.
- Done when:
  - Healthy workload run reaches completed status in regression coverage.
  - Pod-level evidence uses scoped claim names such as `pod.resource`, or evaluator matching is scope-aware.
  - Test proves tool success is not treated as task success without evaluator completion.

### [x] UA-P1-005 — Normalize or cheaply recover invalid model `finish` decisions

- Priority: P1
- Area: Model / Runtime / Recovery
- Source: `docs/revision/2026-08-31-live-kubernetes-run-recommendations.md`
- Why: a task should not fail after successful remediation because a model attached extra fields to a finish decision.
- Done when:
  - Runtime either strips illegal action fields from otherwise valid finish decisions deterministically or performs one bounded corrective retry.
  - Policy/control-flow authority remains runtime-owned.
  - Regression test covers invalid finish with already-satisfied criteria.

### [x] UA-P1-006 — Extend model probe to cover `finish` contract

- Priority: P1
- Area: Model / Kubernetes / Production Contract
- Source: `docs/revision/2026-08-31-live-kubernetes-run-recommendations.md`
- Why: a probe that only validates execute decisions gives false confidence.
- Done when:
  - Model probe includes a healthy fixture expecting a compliant finish decision.
  - Contract report marks `finish_contract` verified or explicitly unverified.
  - Docs explain provider limitations.

### [x] UA-P1-007 — Add CLI wall-clock budget and clean boundary stop

- Priority: P1
- Area: CLI / Runtime / Recovery
- Source: `docs/revision/2026-08-31-live-kubernetes-run-recommendations.md`
- Why: long model/tool runs should pause or wait cleanly instead of leaving sessions stranded after client timeout.
- Done when:
  - Long-running commands support `--timeout-seconds` or equivalent wall-clock budget.
  - On budget expiry, Runtime commits a waiting/paused boundary state that can be resumed or recovered.
  - Tests cover budget expiry without external process kill.

### [ ] UA-P1-008 — Add human-readable confirmation banner

- Priority: P1
- Area: Policy / CLI / Kubernetes
- Source: `docs/revision/2026-08-31-live-kubernetes-run-recommendations.md`
- Why: policy confirmation should be safe and understandable, not raw JSON only.
- Done when:
  - Pending mutation output includes target, before/after, reason, risk, and exact resume command.
  - JSON output remains available for machines.
  - Tests cover at least a scale-workload confirmation prompt.

### [x] UA-P1-009 — Add model provider presets and better probe failure advice

- Priority: P1
- Area: CLI / Model Config
- Source: `docs/revision/2026-08-31-live-kubernetes-run-recommendations.md`
- Why: users should not discover compatible response format, headers, and timeout through repeated failed probes.
- Done when:
  - `agent init` supports provider presets such as `360zhinao`, `deepseek`, or `moonshot` if desired.
  - Probe failures recommend response-format/header/timeout fixes.
  - Docs explain OpenAI-compatible endpoint caveats.

---

# D. P1/P2 Testing And Quality Backlog

### [x] UA-TEST-001 — Add clean-room config and Golden Path regression tests

- Priority: P0
- Area: Tests / Product
- Source: `Universal-Agent-P0-spec.md`, `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: first-day product flow should never regress silently.
- Done when:
  - Tests cover clean HOME and clean cwd.
  - Tests cover init idempotency, doctor, config, run, session list/show, and profile list/show.
  - Tests assert config paths and session persistence behavior.

### [x] UA-TEST-002 — Add default-profile domain-neutral regression

- Priority: P0
- Area: Tests / Profile / UX
- Source: `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: the first run should not silently become a Kubernetes demo.
- Done when:
  - Test asserts default `agent run "Hello"` output/session does not mention Kubernetes terms.
  - Separate explicit Kubernetes profile test still covers fake/local Kubernetes path.

### [ ] UA-TEST-003 — Add behavior/contract/unit ratio reporting to CI

- Priority: P2
- Area: Tests / Observability
- Source: `docs/revision/2026-08-29-critical-review.md`
- Why: the project should track whether tests prove real behavior or only payload shape.
- Done when:
  - CI prints behavior/contract/unit ratio.
  - Short-term target is documented: behavior >= 30%.
  - Optional future gate can fail below target.

### [ ] UA-TEST-004 — Expand secret redaction tests across all user-visible outputs

- Priority: P1
- Area: Security / Tests
- Source: `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: config, doctor, session, and live artifacts must not leak credentials.
- Done when:
  - Tests cover text and JSON outputs for config, doctor, session show/events, and live artifact writer.
  - Known secret-shaped values are masked or rejected.
  - Error output also avoids leaking secret values.

### [ ] UA-TEST-005 — Fix basedpyright strictness gaps or document non-gate status

- Priority: P2
- Area: Tooling / Typing
- Source: `docs/revision/2026-08-31-remaining-todo.md`
- Why: mypy is the current gate, but pyright/basedpyright drift creates repeated noise.
- Done when:
  - The approximately 12 known standard-mode gaps are fixed, or pyright scope/non-goal is documented.
  - Tooling docs say which type checker is authoritative.
  - No developer workflow treats stale pyright findings as release blockers unless it is a configured gate.

---

# E. P2 Maintenance Surface Backlog

### [ ] UA-MAINT-001 — Split `src/universal_agent_cli/parser.py`

- Priority: P2
- Area: Maintenance / CLI
- Source: `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: a single large parser function makes Golden Path and advanced command changes risky.
- Done when:
  - Core parser, advanced parser, Kubernetes parser, distributed parser, and eval parser responsibilities are separated.
  - Existing CLI behavior is unchanged except intentional help improvements.
  - Targeted CLI tests pass.

### [ ] UA-MAINT-002 — Split `src/universal_agent_cli/agentd.py`

- Priority: P2
- Area: Maintenance / CLI / agentd
- Source: `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: remote dispatch for run/session/config/distributed/Kubernetes/eval/ecosystem is too concentrated.
- Done when:
  - Remote command modules exist under a clear package such as `universal_agent_cli/remote/`.
  - `agentd.py` mainly handles client setup and routing.
  - Agentd client integration tests pass.

### [ ] UA-MAINT-003 — Split `src/universal_agent/runtime/agent.py`

- Priority: P2
- Area: Maintenance / Runtime Kernel
- Source: `docs/revision/2026-08-29-critical-review.md`, `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: core runtime loop, observation, recovery, settlement, and persistence are overloaded in one class file.
- Done when:
  - Public `AgentRuntime` methods remain stable.
  - Internal loop/session control/observation/recovery/settlement/persistence helpers are separated.
  - Behavior tests pass and no domain-specific branches are introduced.

### [ ] UA-MAINT-004 — Split `src/universal_agent/service/runtime.py`

- Priority: P2
- Area: Maintenance / Runtime Service
- Source: `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: RuntimeService mixes sessions, distributed, memory, profiles, ops, audit, and doctor surfaces.
- Done when:
  - Service areas are separated internally while preserving the existing public methods.
  - New composition or helper modules are documented.
  - Runtime service integration tests pass.

### [ ] UA-MAINT-005 — Shrink root `universal_agent.__init__` public API surface

- Priority: P2
- Area: API / SDK / Maintenance
- Source: `docs/revision/2026-08-29-critical-review.md`, `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: exporting hundreds of names from the root package makes SDK use and compatibility harder.
- Done when:
  - Root package exports a small facade-focused set.
  - Legacy imports remain available through submodules or a compatibility plan.
  - README uses only the facade imports.

### [ ] UA-MAINT-006 — Split Kubernetes CLI reports and runtime helpers

- Priority: P2
- Area: Maintenance / Kubernetes
- Source: `docs/revision/2026-08-29-critical-review.md`
- Why: Kubernetes CLI/reporting files exceed 1,000 lines and are likely to collect unrelated UX logic.
- Done when:
  - Report rendering, contract artifact writing, operator flow, and backend-specific formatting are separated.
  - Kubernetes contract tests pass.

### [ ] UA-MAINT-007 — Reduce repetitive projection/view/codec code

- Priority: P3
- Area: Maintenance / API / Persistence
- Source: `docs/revision/2026-08-29-critical-review.md`, `docs/revision/2026-08-31-remaining-todo.md`
- Why: hand-written view/codec/projection pairs create metadata tax and compatibility risk.
- Done when:
  - A unified projection or serialization pattern exists for new code.
  - Existing repetitive codecs are consolidated opportunistically without breaking persisted data.
  - Migration/compatibility expectations are documented.

---

# F. P2/P3 Documentation Backlog

### [ ] UA-DOC-001 — Keep README first screen strictly user-oriented

- Priority: P1
- Area: Docs / Product
- Source: `Universal-Agent-P0-spec.md`, `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: README should answer what to run before explaining architecture depth.
- Done when:
  - Quick Start appears before architecture, distributed, multi-agent, ecosystem, and deep internals.
  - First screen mentions CLI, Web, and agentd roles plainly.
  - README commands are verified by demo or tests.

### [ ] UA-DOC-002 — Maintain `docs/RUNTIME_CONTRACT.md` as architecture boundary reference

- Priority: P1
- Area: Docs / Runtime
- Source: `Universal-Agent-P0-spec.md`, `docs/revision/2026-09-11-p0-golden-path-validation.md`
- Why: prevents future changes from moving state, policy, or control flow back into the model.
- Done when:
  - Goal, Task, Decision, Action, Observation, Evidence, Policy, Evaluation, Session, Domain, and Profile ownership are documented.
  - CLI/API/Web/agentd all point to the same runtime contract.
  - Contract is updated when public runtime semantics change.

### [ ] UA-DOC-003 — Split docs by reader type

- Priority: P2
- Area: Docs / Information Architecture
- Source: `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: users, operators, and runtime/domain developers need different entry points.
- Done when:
  - Docs are organized or indexed as user, operator, developer, and advanced sections.
  - P4/P6/P7 content is discoverable but not in the Golden Path.
  - `docs/index.md` clearly routes readers.

### [ ] UA-DOC-004 — Document OpenAI-compatible structured-output caveats

- Priority: P1
- Area: Docs / Model Providers
- Source: `docs/revision/2026-08-31-live-kubernetes-run-recommendations.md`
- Why: compatible endpoints may accept response-format parameters without enforcing schema.
- Done when:
  - Provider docs explain `json_schema`, `json_object`, and prompt-embedded schema behavior.
  - Model probe docs explain what is verified and what is not.
  - Recommended fallback settings are documented.

### [ ] UA-DOC-005 — Add security production decision document

- Priority: P3
- Area: Docs / Security / Production
- Source: `docs/revision/2026-09-11-project-optimization-recommendations.md`, `docs/revision/2026-08-31-remaining-todo.md`
- Why: AuthN/AuthZ, tenancy, KMS/Vault, and audit storage require product/infrastructure decisions before implementation.
- Done when:
  - `docs/security-production-decisions.md` lists identity provider, tenant model, secret provider, audit storage, and authorization model choices.
  - Each decision has selected/default/deferred status.
  - Implementation is not started until decisions are explicit.

---

# G. P3 Productionization Backlog

### [ ] UA-PROD-001 — Execute Postgres adapter and outbox against chosen topology

- Priority: P3
- Area: Production / Persistence
- Source: `docs/revision/2026-08-31-remaining-todo.md`
- Why: local/file/SQLite persistence is not the same as production transactional persistence.
- Done when:
  - Production Postgres topology is selected.
  - Session/event/world store and transactional outbox are validated against it.
  - Migration and replay/repair semantics are documented.
- Blocked: requires final DB deployment decision.

### [ ] UA-PROD-002 — Select broker and implement event signal adapter

- Priority: P3
- Area: Production / Event Stream
- Source: `docs/revision/2026-08-31-remaining-todo.md`
- Why: production event delivery needs reconnect/backpressure semantics beyond local polling.
- Done when:
  - Broker choice is documented.
  - Adapter implements lossy wakeup semantics while durable EventReader owns cursors.
  - Reconnect and backpressure tests pass.
- Blocked: requires broker/deployment decision.

### [ ] UA-PROD-003 — Define and implement enterprise AuthN/AuthZ and tenant boundaries

- Priority: P3
- Area: Security / Production
- Source: `docs/revision/2026-08-31-remaining-todo.md`
- Why: multi-user/enterprise agentd requires deterministic access control and tenant isolation.
- Done when:
  - AuthN/AuthZ and tenant model decisions are documented.
  - agentd/API enforce tenant and capability boundaries.
  - Security tests cover unauthorized access, cross-tenant access, and policy denial.
- Blocked: requires identity and tenant model decisions.

### [ ] UA-PROD-004 — Add KMS/Vault secret resolution and rotation plan

- Priority: P3
- Area: Security / Secrets
- Source: `docs/revision/2026-08-31-remaining-todo.md`
- Why: production secrets should not rely only on env/file references.
- Done when:
  - Secret provider interface supports selected KMS/Vault backend.
  - Secret values remain absent from logs/events/config/session output.
  - Credential rotation behavior is documented and tested.
- Blocked: requires key-management system decision.

### [ ] UA-PROD-005 — Upgrade audit storage beyond local integrity projection

- Priority: P3
- Area: Security / Audit
- Source: `docs/revision/2026-08-31-remaining-todo.md`
- Why: hash-chain projection is useful, but production audit needs durable/tamper-resistant storage.
- Done when:
  - Audit storage target is selected.
  - Export/retention/query semantics are documented.
  - Integrity verification works across restarts and storage backends.
- Blocked: requires audit storage decision.

### [ ] UA-PROD-006 — Select cross-node queue/lock backend and HA policy

- Priority: P3
- Area: Distributed / Production
- Source: `docs/revision/2026-08-31-remaining-todo.md`
- Why: local distributed primitives are not cross-node HA semantics.
- Done when:
  - Queue, lock, leader election, fencing, clock-skew, and duplicate-execution policy are selected.
  - Cross-node test or deployment simulation validates lease/fencing behavior.
  - Docs state operational guarantees and non-guarantees.
- Blocked: requires cluster backend decision.

### [ ] UA-PROD-007 — Define package trust and signature policy before external installs

- Priority: P3
- Area: Ecosystem / Supply Chain
- Source: `docs/revision/2026-08-31-remaining-todo.md`
- Why: external domain package installation is a supply-chain boundary, not just metadata validation.
- Done when:
  - Package acquisition, mandatory signature verification, dependency installation, sandboxed activation, and registry trust policy are defined.
  - Untrusted package activation is denied by default.
  - Tests cover failed signature/trust cases.
- Blocked: requires supply-chain policy decision.

---

# H. Advanced Surface Freeze Backlog

### [ ] UA-FREEZE-001 — Mark distributed runtime as advanced/experimental in user-facing surfaces

- Priority: P1
- Area: Product / Distributed / Docs
- Source: `docs/revision/2026-09-11-project-optimization-recommendations.md`, `docs/revision/2026-08-31-remaining-todo.md`
- Why: users should not confuse local/file/sqlite distributed foundation with production HA.
- Done when:
  - CLI help and docs label distributed commands advanced/experimental.
  - Doctor warns when distributed runtime is enabled without production backend.
  - README does not put distributed features in the Golden Path.

### [ ] UA-FREEZE-002 — Mark ecosystem/package registry as experimental and freeze feature growth

- Priority: P2
- Area: Product / Ecosystem
- Source: `docs/revision/2026-08-29-critical-review.md`, `docs/revision/2026-09-11-project-optimization-recommendations.md`
- Why: ecosystem work should not distract from proving core agent capability.
- Done when:
  - Docs say ecosystem package registry is not required for Golden Path.
  - New ecosystem features are deferred unless they unblock Domain SDK validation.
  - Existing catalog/verify behavior remains tested.

### [ ] UA-FREEZE-003 — Keep Multi-Agent explicitly optional and separate from Domain Composition

- Priority: P2
- Area: Product / Architecture / Multi-Agent
- Source: `AGENTS.md`, `docs/revision/2026-08-26-project-status.md`, `docs/revision/2026-08-31-remaining-todo.md`
- Why: domains are capability/knowledge boundaries; agents are autonomous execution boundaries.
- Done when:
  - Docs and help do not present Multi-Agent as the default way to use multiple domains.
  - Multi-Agent productionization remains deferred until single-agent multi-domain behavior is proven.
  - Any future multi-agent work uses structured task/result/evidence contracts, not chat transcripts.

---

# I. Suggested Sprint Plan

## Sprint 1 — Product Consistency

- [x] UA-P01-001 — Reconcile config location semantics in all contexts
- [x] UA-P01-002 — Make `default` profile truly domain-neutral or explicitly rename it
- [x] UA-P01-003 — Split beginner and advanced CLI help surfaces
- [x] UA-P01-004 — Enrich `agent config` and `agent profile show`
- [x] UA-TEST-001 — Add clean-room config and Golden Path regression tests
- [x] UA-TEST-002 — Add default-profile domain-neutral regression

## Sprint 2 — Better Runtime UX

- [x] UA-P01-005 — Make `agent session show` human-first
- [x] UA-P01-006 — Add `agent session explain <id>` for failures and waiting sessions
- [x] UA-P01-007 — Standardize repairable CLI errors
- [x] UA-P01-008 — Add doctor final next-step guidance
- [x] UA-P1-001 — Add reproducible local Golden Demo script

## Sprint 3 — Kubernetes / Model Reliability

- [x] UA-P1-002 — Add live-like Kubernetes kind/minikube contract test
- [x] UA-P1-004 — Re-confirm or fix Kubernetes evidence claim granularity collision
- [x] UA-P1-005 — Normalize or cheaply recover invalid model `finish` decisions
- [x] UA-P1-006 — Extend model probe to cover `finish` contract
- [x] UA-P1-007 — Add CLI wall-clock budget and clean boundary stop
- [ ] UA-P1-008 — Add human-readable confirmation banner
- [x] UA-P1-009 — Add model provider presets and better probe failure advice

## Sprint 4 — Maintenance Reduction

- [ ] UA-MAINT-001 — Split `src/universal_agent_cli/parser.py`
- [ ] UA-MAINT-002 — Split `src/universal_agent_cli/agentd.py`
- [ ] UA-MAINT-003 — Split `src/universal_agent/runtime/agent.py`
- [ ] UA-MAINT-004 — Split `src/universal_agent/service/runtime.py`
- [ ] UA-MAINT-005 — Shrink root `universal_agent.__init__` public API surface

## Sprint 5 — Production Decisions

- [ ] UA-DOC-005 — Add security production decision document
- [ ] UA-PROD-001 — Execute Postgres adapter and outbox against chosen topology
- [ ] UA-PROD-002 — Select broker and implement event signal adapter
- [ ] UA-PROD-003 — Define and implement enterprise AuthN/AuthZ and tenant boundaries
- [ ] UA-PROD-006 — Select cross-node queue/lock backend and HA policy
- [ ] UA-PROD-007 — Define package trust and signature policy before external installs

## Sprint 6 — Advanced Freeze / Labeling

- [ ] UA-FREEZE-001 — Mark distributed runtime as advanced/experimental in user-facing surfaces
- [ ] UA-FREEZE-002 — Mark ecosystem/package registry as experimental and freeze feature growth
- [ ] UA-FREEZE-003 — Keep Multi-Agent explicitly optional and separate from Domain Composition

---

# J. Completion Log

Add entries here when items are completed.

```text
2026-09-11 UA-DONE-001 completed via docs/revision/2026-09-11-p0-golden-path-validation.md.
2026-09-11 UA-DONE-002 completed via P0 validation quality gates.
2026-08-31 UA-DONE-003 completed via live Kubernetes manual drill record.
2026-09-11 Sprint 1 completed: UA-P01-001/002/003/004 and UA-TEST-001/002. Evidence: `uv run ruff check src tests`; `uv run pytest tests/integration/test_cli.py tests/integration/test_cli_agentd_client.py tests/unit/test_security_secrets.py -q`; `uv run pytest tests/integration/test_p0_golden_path.py tests/integration/test_facade_golden_path.py -q`; clean HOME smoke for init/doctor/config/profile/run/session list/show/explain. Main files: README.md, docs/product.md, src/universal_agent_cli/{defaults.py,init.py,parser.py,text_views.py}, src/universal_agent/agentd/__main__.py, tests/integration/test_p0_golden_path.py, tests/unit/test_p0_config_and_views.py.
2026-09-11 Sprint 2 completed: UA-P01-005/006/007/008 and UA-P1-001. Evidence: `scripts/demo-local.sh` clean HOME/workspace smoke; `agent session show` Summary/What happened; `agent session explain` Error/Reason/Try; `agent doctor` Next guidance; repairable CLI errors include machine-readable reason/try. Main files: src/universal_agent_cli/{agentd.py,doctor.py,io.py,session.py,text_views.py}, scripts/demo-local.sh, README.md, docs/revision/README.md.
2026-09-11 UA-P1-004 completed: Kubernetes pod evidence now records pod identity as `pod.resource`, preventing pod-level facts from overwriting workload-level `resource` criteria. Evidence: `uv run ruff check src/universal_agent/domains/kubernetes/evidence.py tests/unit/test_kubernetes_evidence.py tests/integration/test_kubernetes_remediation.py`; `uv run pytest tests/unit/test_kubernetes_evidence.py tests/integration/test_kubernetes_remediation.py -q`. Main files: src/universal_agent/domains/kubernetes/evidence.py, tests/unit/test_kubernetes_evidence.py, tests/integration/test_kubernetes_remediation.py.
2026-09-11 UA-P1-002 completed: added opt-in live-like kind/minikube contract coverage that provisions unhealthy zero-replica Deployments in temporary namespaces, verifies the production policy confirmation boundary, and includes a staging path that applies bounded remediation and asserts fresh `completion_verification=ok`; skips cleanly unless explicitly enabled against a kind/minikube context. Evidence: `uv run ruff check tests/live/test_kubernetes_kind_contract.py`; `uv run pytest tests/live/test_kubernetes_kind_contract.py -q` (clean skip without opt-in). Main file: tests/live/test_kubernetes_kind_contract.py.
2026-09-11 UA-P1-005 completed: added shared deterministic normalization for FINISH decisions that echo action fields at the provider decision-codec seam plus a runtime-side guard for direct/custom adapters, preserving strict validation for other decision types and keeping completion gated by evaluator state. Evidence: `uv run ruff check src/universal_agent/model/decision_codec.py src/universal_agent/runtime/decision.py src/universal_agent/runtime/agent.py tests/unit/test_model_http.py tests/integration/test_agent_runtime.py`; `uv run pytest tests/unit/test_model_http.py tests/integration/test_agent_runtime.py -q`. Main files: src/universal_agent/model/decision_codec.py, src/universal_agent/runtime/decision.py, src/universal_agent/runtime/agent.py, tests/unit/test_model_http.py, tests/integration/test_agent_runtime.py.
2026-09-11 UA-P1-006 completed: production contract reports `finish_contract` explicitly unverified for the execute-only Kubernetes model probe instead of implying full finish-decision coverage; provider finish quirks are handled by UA-P1-005 normalization. Evidence: `uv run ruff check src/universal_agent/domains/kubernetes/production_contract.py tests/unit/test_kubernetes_production_contract_payloads.py tests/integration/test_kubernetes_production_contract.py`; `uv run pytest tests/unit/test_kubernetes_production_contract_payloads.py tests/integration/test_kubernetes_production_contract.py -q`. Main files: src/universal_agent/domains/kubernetes/production_contract.py, tests/unit/test_kubernetes_production_contract_payloads.py.
2026-09-11 UA-P1-007 completed: added `agent run --timeout-seconds` wall-clock budget wiring through CLI, agentd submission, RuntimeService/RuntimeAPI, and AgentRuntime; expired budgets pause at the next clean runtime decision boundary instead of relying on external process kills. Evidence: `uv run ruff check src/universal_agent/runtime/agent.py src/universal_agent/runtime/api.py src/universal_agent/service/runtime.py src/universal_agent/agentd/http.py src/universal_agent/agentd/_routes_session.py src/universal_agent_cli/parser.py src/universal_agent_cli/__init__.py src/universal_agent_cli/agentd.py tests/integration/test_agent_runtime.py tests/integration/test_cli.py tests/unit/test_agentd_http_config.py`; `uv run mypy src/universal_agent/runtime/agent.py src/universal_agent/runtime/api.py src/universal_agent/service/runtime.py src/universal_agent/agentd/http.py src/universal_agent/agentd/_routes_session.py src/universal_agent_cli/__init__.py src/universal_agent_cli/agentd.py`; `uv run pytest tests/integration/test_agent_runtime.py tests/integration/test_cli.py tests/unit/test_agentd_http_config.py -q`. Main files: src/universal_agent/runtime/agent.py, src/universal_agent/runtime/api.py, src/universal_agent/service/runtime.py, src/universal_agent/agentd/http.py, src/universal_agent/agentd/_routes_session.py, src/universal_agent_cli/{parser.py,__init__.py,agentd.py}, tests/integration/{test_agent_runtime.py,test_cli.py}, tests/unit/test_agentd_http_config.py.
2026-09-11 UA-P1-008 confirmation banner evidence strengthened: `agent run` text now renders pending scale confirmations with target, before/after replicas, reason, guarded-mutation risk, and exact resume command while JSON surfaces remain unchanged. Evidence: `uv run ruff check src/universal_agent_cli/text_views.py tests/unit/test_p0_config_and_views.py tests/integration/test_cli.py`; `uv run pytest tests/unit/test_p0_config_and_views.py tests/integration/test_cli.py -q`. Main files: src/universal_agent_cli/text_views.py, tests/unit/test_p0_config_and_views.py, tests/integration/test_cli.py.
2026-09-11 UA-P1-009 completed: added `agent init --model-provider-preset {360zhinao,deepseek,moonshot}` conservative Chat Completions defaults, richer Kubernetes model-probe `next_step.try` guidance for credentials/response-format/headers/timeouts, and OpenAI-compatible endpoint caveat docs. Evidence: `uv run ruff check src/universal_agent_cli/init.py src/universal_agent_cli/parser.py src/universal_agent/domains/kubernetes/cli_reports.py tests/integration/test_cli.py`; `uv run pytest tests/integration/test_cli.py -q`. Main files: src/universal_agent_cli/{init.py,parser.py}, src/universal_agent/domains/kubernetes/cli_reports.py, docs/runtime-operator-guide.md, tests/integration/test_cli.py.
```
