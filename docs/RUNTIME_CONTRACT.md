# Runtime Contract

This document defines the core concepts of the Universal-Agent Runtime, who
creates and calls each one, their lifecycle, persistence, and their relation to
Policy. It is the contract README, CLI, SDK, agentd and Web all rely on; when
code and this document disagree, fix one of them.

The architecture target is `universal-agent-runtime-domain-runtime-design.md`;
the product entry points are described in `README.md` and `docs/product.md`.

## Concept table

| Concept | What it is | Created by | Called by | Lifecycle | Persisted | Can bypass Policy? |
| --- | --- | --- | --- | --- | --- | --- |
| **Agent** | The user-facing AI Agent: one configured Profile running on one Runtime. | `Agent` facade (SDK) or the CLI `run` command. | Users, applications. | Lives as long as its host process/CLI invocation; state outlives it via Sessions. | No (Sessions are). | No. |
| **Runtime** (`AgentRuntime`) | The execution engine: owns state, control flow, retries, timeouts, recovery, iteration budgets. | `RuntimeHost` / `RuntimeBuilder` at assembly time. | Runtime API / RuntimeService. | One per assembled host; stateless across Sessions (state lives in stores). | No (delegates to stores). | No — it *enforces* Policy. |
| **Profile** (`AgentProfile`) | A named work identity: Domain set, Runtime Configuration, model, secrets references. | Config file (`profile.json`) via `agent init`, or programmatically. | `RuntimeHost`, CLI, SDK, agentd. | Immutable after load; validated at assembly. | Yes (profile config file). | No. |
| **Domain** (`DomainRuntime`) | A pluggable world the Agent can act on: ontology, capabilities, tools, policies, evaluators, context providers. | Domain package author; loaded by `DomainLoader`. | Kernel via `DomainComposition`. | Activated at assembly; identity persisted in SessionSnapshot for resume. | Manifest + snapshot identity. | No — Domain policies are checked by the Kernel. |
| **Capability** | An abstract ability (`inspect_workload`, `scale_workload`) the model may decide to use. | Domain authors. | Decision engine → resolver. | Registered at assembly; resolved per decision. | No. | No — resolution output goes through Policy before any Tool runs. |
| **Tool** | A concrete implementation backing one or more Capabilities. | Domain authors. | Action executor only. | Registered at assembly; executed per action. | No. | No — only the Runtime executes Tools, after Policy ALLOW. |
| **Decision** | The model's structured proposal (execute/wait/finish + capability + arguments). | Model adapter (or `DecisionEngine`). | Runtime validation, then Policy. | Per iteration; validated before use; invalid decisions are rejected. | Events only. | No — a Decision can never execute anything by itself. |
| **Policy** (`PolicyEngine`, `PolicyRule`) | Deterministic execution boundary: ALLOW / REQUIRE_CONFIRMATION / DENY. | Domain authors + profile configuration. | Runtime before every action (normal and recovered). | Checked per action; results recorded as events/audit. | PolicyChecked events + audit records. | N/A — Policy *is* the boundary; nothing may skip it. |
| **Action** | A validated, policy-approved capability execution (with idempotency key, resource lock). | Runtime. | Tool runtime. | Pending → executed/confirmed/cancelled; new IDs on retries. | Yes (snapshot, pending actions). | No. |
| **Observation** | The normalized raw result of one Tool execution. | Runtime. | Domain extractors. | Per action; input to Evidence. | Events. | N/A. |
| **Evidence** | A domain-extracted claim with provenance and confidence. | Domain evidence extractors. | Runtime, evaluators, world model. | Appended per observation; replayed on resume. | Yes. | N/A. |
| **World Model** | Session-local replay of what is currently true, rebuilt from Evidence. | Domain world updaters via Runtime. | Evaluators, context compiler, expanders. | Rebuilt from Evidence on resume — never a second source of truth. | Derived (replayable). | N/A. |
| **Evaluator** | Domain component that decides task/goal completion from Evidence. | Domain authors. | Runtime after each observation. | Per evaluation; the only completion authority. | EvaluationCompleted events. | N/A. |
| **Recovery** | Budgeted, classified handling of failures (retry/alternative/diagnose). | Runtime recovery rules (Domain-supplied). | Runtime on failure. | Bounded by `max_recovery_steps`; retries get new action IDs and re-enter Policy. | Recovery events + snapshot counters. | No — Recovery never calls Tools directly. |
| **Session** | One Agent execution's persistent context: task graph, evidence, budget, pending confirmation. | Runtime on first goal run. | CLI/SDK/agentd/Web read models. | Created → running → waiting/completed/failed/cancelled. | Yes (`SessionSnapshot`, file/SQLite stores). | No — resume re-checks Policy. |
| **Evaluation** | The per-observation decision that a task/goal is (in)complete. | Runtime via Domain evaluator. | Runtime. | Recorded per iteration; terminal evaluation completes the goal. | Events. | N/A. |

## Invariants (non-negotiable)

1. **The model never owns state or control flow.** Model output is a `Decision`
   proposal; the Runtime validates it, checks Policy, and only then executes.
2. **Policy is outside the model.** Every capability execution — initial or
   recovered — passes `PolicyEngine.check`. Mutations without an explicit allow
   rule are denied by default. There is no code path from a Decision to a Tool
   that skips Policy.
3. **Tool success ≠ task success.** Only the Domain Evaluator completes tasks;
   only evaluator-approved `finish` completes a goal.
4. **The World Model is replayed from Evidence.** It is never authoritative and
   never updated except through Domain world updaters consuming Evidence.
5. **Recovery is bounded and re-enters the front door.** Retries get new action
   IDs and pass capability resolution and Policy again; iteration and recovery
   budgets are hard limits.
6. **Sessions outlive processes.** A resumed Session is rebuilt from its
   snapshot: Domain identity is verified, Evidence is replayed, and any pending
   action is re-resolved and re-checked against Policy before execution.
7. **Clients never touch Kernel internals.** CLI, SDK, agentd, TUI and Web go
   through `RuntimeAPI` / `RuntimeService` read models only.

## Golden Path mapping

| User question | Answering surface |
| --- | --- |
| "Where do I start?" | `agent run` (CLI creates a Session automatically) |
| "Where do I configure?" | `universal-agent/profile.json` + `config.json` (`agent init`, `agent config`) |
| "Is my setup correct?" | `agent doctor` |
| "What did the Agent do?" | `agent session show <id>` (timeline, evidence, actions) |
| "Who guards dangerous actions?" | Domain `PolicyRule`s enforced by `PolicyEngine` (see above) |
