# Universal Agent Runtime + Pluggable Domain Runtime
## Architecture, Runtime, Domain, Multi-Agent & Product Specification v3.0

> **Document status:** Normative architecture specification
>
> **Role:** This document is the primary architectural authority for the project.
>
> **Rule:** When implementation decisions conflict with this document, the implementation must be changed or this document must be explicitly revised. Do not silently introduce architectural behavior that contradicts these boundaries.
>
> **Scope:** This document defines the long-term architecture. The current implementation phase is tracked separately in the roadmap at the end of this document. A roadmap item being specified does not mean it must already exist in code.

---

# 0. Executive Summary

The project is a:

> **Universal Agent Runtime Platform**

It is simultaneously:

1. **A Framework** — reusable Agent Kernel, SDK, interfaces, and extension points.
2. **A Runtime** — a stateful execution engine capable of running long-lived Agent sessions.
3. **A Service** — `agentd`, exposing a stable runtime API.
4. **An Application Platform** — CLI, TUI, and Web clients built on the runtime API.
5. **An Extension Platform** — pluggable Domain Runtimes and Agent Profiles.
6. **A Coordination Platform** — optional Multi-Agent orchestration built above the single-Agent runtime.

The central architectural idea is:

```text
Universal Agent Kernel
        +
Agent Runtime
        +
Pluggable Domain Runtime
        +
Agent Profile
        +
World Model
        +
Policy / Evaluation / Recovery
        ↓
Universal Agent
```

The system must support both:

```text
One Agent
    +
One Domain
```

and:

```text
One Agent
    +
Multiple Domains
```

without requiring multiple Agents merely because multiple domains are involved.

Multi-Agent is a separate capability used when the problem requires **multiple independent execution/autonomy boundaries**, not merely multiple areas of expertise.

---

# 1. Non-Negotiable Architectural Principles

These rules are normative.

## 1.1 Kernel must not know concrete domains

The Kernel must never contain domain-specific branches such as:

```python
if domain == "kubernetes":
    ...
elif domain == "coding":
    ...
```

The correct dependency direction is:

```text
Kernel
  ↑
Domain Interface / SDK
  ↑
Concrete Domain Runtime
```

Adding a new Domain must not require modifying Kernel business logic.

---

## 1.2 LLM is a decision component, not the system authority

The LLM is allowed to:

- interpret intent;
- reason about available information;
- propose a decision;
- select an available capability;
- propose parameters;
- propose a recovery strategy;
- propose delegation.

The LLM is **not authoritative** for:

- lifecycle state;
- task state;
- execution state;
- world state;
- evidence validity;
- authorization;
- policy;
- confirmation;
- retry limits;
- timeouts;
- cancellation;
- completion.

The authoritative state belongs to the Runtime and its controlled stores.

```text
Authoritative Runtime State
            ↓
       Context Compiler
            ↓
           LLM
            ↓
   Proposed Structured Decision
            ↓
     Runtime Validation
            ↓
         Execution
```

---

## 1.3 LLM must not control the runtime control plane

The model may propose:

```text
execute
wait
ask_user
recover
finish
delegate
```

But the Runtime decides whether that proposal is legal and executable.

The model cannot directly:

- create arbitrary infinite loops;
- bypass policy;
- bypass authorization;
- bypass confirmation;
- extend its own limits;
- mark a Goal completed without evaluation;
- execute tools outside the capability set;
- create arbitrary Agents;
- access raw credentials.

---

## 1.4 Capability, Tool and Action are different concepts

These terms must not be conflated.

### Capability

A semantic ability exposed to the Agent:

```text
inspect_workload
query_metrics
restart_workload
```

### Tool

A concrete mechanism used to implement a Capability:

```text
kubernetes_api
kubectl
prometheus_http_api
```

### Action

A concrete execution request created from a Decision:

```text
restart deployment/dify-api
```

The normal path is:

```text
Decision
   ↓
Capability Resolution
   ↓
Action Construction
   ↓
Policy / Authorization
   ↓
Tool Resolution
   ↓
Tool Runtime
   ↓
Environment
```

A Capability may have multiple Tool implementations.

---

## 1.5 Tool success is not Task success

Never equate:

```text
ToolResult = success
```

with:

```text
Task = completed
```

Correct:

```text
Tool Result
    ↓
Observation
    ↓
Evidence
    ↓
Evaluator
    ↓
Task State
```

---

## 1.6 Knowledge is not World Model

Knowledge:

```text
A Deployment manages ReplicaSets.
```

World Model:

```text
deployment/dify-api
desired=3
available=2
```

Knowledge describes relatively stable information.

World Model represents the Agent's current model of the external world.

The World Model may be incomplete, stale, uncertain, or contradicted by new observations.

---

## 1.7 Evidence is not truth

Evidence is a traceable basis for a claim.

```text
Observation
    ↓
Evidence
    ↓
Claim
```

Evidence must include provenance and timestamp where applicable.

A high-confidence Evidence item does not automatically make a claim universally true.

---

## 1.8 Policy is an execution boundary

Policy is enforced by trusted Runtime components.

The LLM cannot:

```text
Decision
 ↓
Policy DENY
 ↓
"ignore policy"
```

and then execute.

A denied Action must not reach the Tool Runtime.

---

## 1.9 Domain is not a prompt

A Domain is a structured extension package, not:

```text
"You are a Kubernetes expert."
```

A Domain may contain:

```text
Manifest
Ontology
Capabilities
Tools
Knowledge
Procedures
Policies
Evaluators
Context Providers
Prompt Templates
```

Prompts are optional implementation resources, not the definition of a Domain.

---

## 1.10 Multi-Domain is the default composition mechanism

If a task spans:

```text
Kubernetes
+
Dify
+
Prometheus
```

the default design is:

```text
One Agent
+
Three Domains
+
Shared World Model
```

Do not create three Agents merely because there are three Domains.

---

## 1.11 Multi-Agent is an autonomy boundary

The fundamental distinction is:

> **Domain defines expertise/capability boundaries; Agent defines an execution and autonomy boundary.**

Multi-Agent is justified by factors such as:

- independent execution;
- isolated permissions;
- isolated runtime/environment;
- separate lifecycle;
- independent context requirements;
- meaningful parallelism;
- separate objectives;
- organizational or security boundaries.

No single condition is mandatory in every case.

---

## 1.12 Runtime is the authority over execution

The Runtime owns:

```text
Lifecycle
Scheduling
Concurrency
Cancellation
Timeout
Retry
Persistence
Event Publication
Recovery
Policy Enforcement
Execution Coordination
```

Kernel defines Agent semantics; Runtime operationalizes them.

---

# 2. System Identity and Layering

The project must not be described as only a Framework.

Its architecture is:

```text
                 Universal Agent Platform
                          │
          ┌───────────────┼────────────────┐
          │               │                │
      Framework         Runtime        Applications
          │               │                │
    Kernel + SDK       agentd         CLI / TUI / Web
          │               │                │
          └───────────────┼────────────────┘
                          │
                  Pluggable Domains
                          │
                   Agent Profiles
```

A developer can use the Framework directly:

```python
runtime = AgentRuntime(...)
await runtime.run(...)
```

A local user can use:

```bash
agent run ...
```

A service deployment can use:

```text
agentd
```

Applications must consume stable Runtime/API interfaces rather than Kernel internals.

---

# 3. Architectural Layers

## 3.1 Agent Kernel

The Kernel defines the semantic model of an Agent.

Responsibilities:

```text
Goal
Task
State
Decision
Action
Observation
Evidence
World Model
Context
Policy Model
Evaluation Model
Recovery Model
Memory Interfaces
```

The Kernel should be as environment-independent as practical.

---

## 3.2 Agent Runtime

The Runtime turns Kernel semantics into a running system.

Responsibilities:

```text
Session
Execution
Scheduler
Lifecycle
Model Invocation
Capability Execution
Persistence
Event Bus
Cancellation
Timeout
Retry
Recovery Coordination
Concurrency
Resource Locking
```

The Runtime may use Kernel abstractions but must not redefine their semantic meaning.

---

## 3.3 Domain Runtime

A Domain Runtime supplies expertise and executable capabilities for a domain.

Responsibilities:

```text
Domain Manifest
Ontology
Capabilities
Tool Adapters
Knowledge
Procedures
Context Providers
Domain Evaluators
Domain Policies
```

A Domain Runtime is loaded by the Domain Manager.

---

## 3.4 Agent Profile

An Agent Profile is a declarative composition/configuration of an Agent.

It specifies:

```text
Domains
Capability constraints
Policies
Knowledge sources
Model policy
Memory policy
Runtime limits
Confirmation policy
Optional Agent behavior configuration
```

An Agent Profile is **not** a new Kernel and is **not** a Domain.

---

## 3.5 Application Layer

Applications include:

```text
CLI
TUI
Web
SDK Clients
```

They consume:

```text
Runtime API
```

and must not directly manipulate Runtime internals.

---

# 4. Core Architecture

```text
                                  User
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                   CLI             TUI             Web
                    │               │               │
                    └───────────────┼───────────────┘
                                    │
                              Runtime API
                                    │
                               ┌────▼────┐
                               │ agentd  │
                               └────┬────┘
                                    │
                         ┌──────────┴──────────┐
                         │                     │
                  Agent Runtime          Domain Manager
                         │                     │
                         │              ┌──────┼──────┐
                         │              │      │      │
                         │           Domain  Domain  Domain
                         │              │      │      │
                         └──────────────┴──────┴──────┘
                                        │
                                Domain Composition
                                        │
                              Shared World Model
                                        │
                                  Context Compiler
                                        │
                                  Decision Engine
                                        │
                         ┌──────────────┴──────────────┐
                         │                             │
                   Capability Resolver            Policy Engine
                         │                             │
                         └──────────────┬──────────────┘
                                        │
                                  Action Runtime
                                        │
                                   Tool Runtime
                                        │
                                   Environment
                                        │
                                   Observation
                                        │
                                     Evidence
                                        │
                                World Model Update
                                        │
                                    Evaluator
                                        │
                         ┌──────────────┼──────────────┐
                         │              │              │
                      Continue       Recover         Finish
```

Multi-Agent is an optional layer above this:

```text
Agent Runtime
      │
      └── Agent Orchestrator
              │
       ┌──────┼──────┐
       │      │      │
    Agent A Agent B Agent C
       │      │      │
       └──────┼──────┘
              │
       Task Contracts
              │
       Result + Evidence
```

---

# 5. Agent Execution Model

The canonical execution loop is:

```text
Goal
  ↓
State / World Model
  ↓
Current Task
  ↓
Context Compilation
  ↓
LLM Decision Proposal
  ↓
Decision Validation
  ↓
Policy / Authorization
  ↓
Action
  ↓
Observation
  ↓
Evidence
  ↓
World Model Update
  ↓
Evaluation
  ↓
Continue / Recover / Finish
```

Important:

> The LLM proposes the next semantic action. The Runtime determines whether and how that action is executed.

---

# 6. Goal Model

A Goal contains:

```text
Intent
Constraints
Success Criteria
Risk Constraints
Relevant Context
```

Example:

```yaml
goal:
  id: goal-001
  description: "检查 Dify API 为什么持续重启并安全修复"

  constraints:
    environment: production
    destructive_action_requires_confirmation: true

  success_criteria:
    - root_cause_identified
    - workload_healthy
    - restart_count_stable
```

Goal state:

```text
PENDING
RUNNING
BLOCKED
COMPLETED
FAILED
CANCELLED
```

---

# 7. Goal Compilation

Goal Compiler converts user intent into an executable Goal specification.

```text
User Input
    ↓
Intent Interpretation
    ↓
Goal Specification
    ├── Constraints
    ├── Success Criteria
    ├── Risk
    └── Candidate Domains
```

Goal Compiler should not be treated as a one-shot planner that must produce a complete static Task Tree.

The system must support incremental task discovery.

---

# 8. Task Model

A Task is a bounded unit of work within a Goal.

```text
Goal
  │
  └── Task
        ├── Decision
        ├── Action
        ├── Observation
        └── Evaluation
```

Task may produce new Tasks.

Example:

```text
inspect_environment
       ↓
Object Storage missing
       ↓
resolve_object_storage
       ↓
deploy_dify
       ↓
verify_dify
```

Tasks should carry:

```text
id
parent_id
goal_id
status
dependencies
constraints
input
expected_output
attempt_count
timestamps
```

---

# 9. World Model

The World Model is the Agent's structured representation of the external environment.

```text
World Model
├── Entities
├── Relations
├── Facts
├── State
├── Events
├── Artifacts
└── Evidence References
```

Example:

```text
Pod/dify-api-123
    ├── owned_by → ReplicaSet/xxx
    ├── scheduled_on → Node/node-01
    ├── uses → Secret/dify-secret
    └── state → CrashLoopBackOff
```

The World Model is:

```text
Queryable
Versioned where required
Evidence-linked
Potentially stale
Potentially uncertain
Updated by observations
```

It must not be treated as a perfect database mirror of reality.

---

# 10. Cross-Domain World Model

Cross-domain reasoning depends on shared entities and relations.

Example:

```text
Dify Application
      │
      ├── Kubernetes Deployment
      │        └── Pod
      │             └── Node
      │
      ├── PostgreSQL
      │
      ├── Redis
      │
      └── Observability
               └── Metrics
```

Reasoning path:

```text
Dify
 ↓
Pod
 ↓
CPU throttling
 ↓
Prometheus
 ↓
Application latency
```

The Domain Runtime provides domain-specific semantics; the World Model provides cross-domain state and relations.

---

# 11. Domain Runtime

A Domain Runtime is a pluggable package.

```text
DomainRuntime
├── Manifest
├── Ontology
├── Capabilities
├── Tools
├── Knowledge
├── Procedures
├── Policies
├── Evaluators
├── Context Providers
└── Optional Prompt Resources
```

Lifecycle:

```text
Discover
  ↓
Load
  ↓
Validate
  ↓
Register
  ↓
Activate
  ↓
Compose
  ↓
Deactivate / Unload
```

Domain loading must be validated before activation.

---

# 12. Domain Manager

The Domain Manager owns:

```text
Discovery
Loading
Validation
Registration
Version Compatibility
Activation
Deactivation
Composition
Dependency Resolution
```

It should not execute Domain Actions itself.

---

# 13. Domain Composition

Multiple Domains are composed into an Agent execution context.

The Domain Composer must handle:

## 13.1 Domain Discovery

```text
Goal
 ↓
Candidate Domains
 ↓
Relevant Domains
```

## 13.2 Capability Merge

```text
Kubernetes → inspect_workload
Dify       → inspect_application
Metrics    → query_metrics
```

Result:

```text
Available Capability Set
```

## 13.3 Ontology Composition

Domain-specific entities and relations must be mapped into a shared semantic model.

## 13.4 Policy Composition

When multiple policies apply, policy resolution must be deterministic.

Default safety rule:

> A less permissive applicable policy must not be weakened by a more permissive Domain policy.

## 13.5 Conflict Detection

Conflicts must be surfaced explicitly rather than silently overwritten.

---

# 14. Agent Profile

Agent Profile is the declarative runtime identity of a configured Agent.

It is not merely a prompt.

Example:

```yaml
apiVersion: agent.nantian.dev/v1alpha1
kind: AgentProfile

metadata:
  name: production-ai-operator

spec:
  domains:
    - kubernetes
    - dify
    - observability

  capabilities:
    allow:
      - inspect_cluster
      - inspect_workload
      - query_metrics
      - rollout_restart
    deny:
      - namespace_delete

  policies:
    - production-safety

  knowledge:
    - company-runbook

  model:
    routing_policy: reasoning-first

  confirmation:
    destructive_action: required

  limits:
    max_steps: 100
    max_duration: 30m
```

A Domain can serve many Profiles.

A Profile can compose many Domains.

A Profile may also define runtime behavior, model routing, safety, and resource limits.

---

# 15. Capability Model

Capabilities describe semantic operations.

Categories:

```text
Observation
Analysis
Mutation
Communication
Coordination
```

Example:

```yaml
capability:
  name: restart_workload
  category: mutation
  risk: medium
  idempotency:
    mode: semantic
  confirmation:
    default: false
```

Capability availability is determined by:

```text
Profile
+
Active Domains
+
Policy
+
Runtime State
```

---

# 16. Tool Model

Tools are concrete execution adapters.

A Tool definition should include:

```text
Name
Input Schema
Output Schema
Capability Binding
Risk Metadata
Timeout
Retry Policy
Credential References
Execution Environment
Idempotency Semantics
```

The Tool Runtime is responsible for:

```text
Schema Validation
Authorization
Timeout
Cancellation
Retry
Execution
Result Normalization
Audit
Secret Resolution
```

---

# 17. Action Model

An Action is the concrete execution intent derived from a Decision.

Example:

```yaml
action:
  id: action-123
  capability: restart_workload
  target: deployment/dify-api
  parameters:
    strategy: rolling
```

Action lifecycle:

```text
PROPOSED
AUTHORIZED
WAITING_CONFIRMATION
DISPATCHED
RUNNING
SUCCEEDED
FAILED
CANCELLED
UNKNOWN
```

`UNKNOWN` is important for uncertain execution outcomes.

For example:

```text
Tool executed
    ↓
Network disconnected
    ↓
Runtime does not know whether it succeeded
```

The system must not assume failure or success without reconciliation.

---

# 18. Policy Engine

Policy evaluation is deterministic and external to the LLM.

```text
Proposed Action
      ↓
Authentication
      ↓
Authorization
      ↓
Policy Evaluation
      ↓
ALLOW / CONFIRM / DENY
```

Example:

```yaml
rules:
  - action: namespace.delete
    environment: production
    effect: deny

  - action: deployment.delete
    environment: production
    effect: require_confirmation

  - action: deployment.restart
    environment: production
    effect: allow
```

Policy decisions must be auditable.

---

# 19. Human-in-the-Loop

When confirmation is required:

```text
Agent
 ↓
Policy
 ↓
REQUIRE_CONFIRMATION
 ↓
WAITING_USER
 ↓
Approve / Reject
 ↓
Resume
```

A confirmation request must bind:

```text
session_id
goal_id
task_id
action_id
policy_decision_id
```

The user must see the concrete Action:

```text
Action:
restart deployment/dify-api

Reason:
Pod restart count is increasing.

Risk:
medium

Expected effect:
Rolling restart.

[Approve] [Reject]
```

Confirmation must expire when its underlying Action becomes stale or materially changes.

---

# 20. Decision Engine

Decision Pipeline:

```text
Goal
 ↓
Current Task
 ↓
Authoritative State
 ↓
Relevant World Model
 ↓
Relevant Evidence
 ↓
Available Capabilities
 ↓
Relevant Policies
 ↓
Context Compiler
 ↓
LLM
 ↓
Structured Decision Proposal
 ↓
Schema Validation
 ↓
Policy / Authorization
 ↓
Action Runtime
```

Decision types:

```text
execute
wait
ask_user
recover
finish
delegate
```

`delegate` is only available when Multi-Agent capability is enabled.

---

# 21. Structured Decision

The model must return a machine-validatable structure.

Example:

```json
{
  "type": "execute",
  "capability": "query_metrics",
  "arguments": {
    "query": "container_memory_working_set_bytes"
  },
  "reasoning_summary": "Need memory evidence before deciding whether OOM is causal.",
  "expected_observation": "memory usage metric"
}
```

The Runtime must validate:

```text
Decision Schema
Capability Existence
Argument Schema
Capability Availability
Policy
Authorization
Limits
```

The model must not directly invoke a Tool.

---

# 22. Context Compiler

Context Compiler converts Runtime state into a bounded Decision Context.

Inputs:

```text
Goal
Current Task
Authoritative State
Relevant World Model
Evidence
Memory
Capabilities
Policy
Recent Events
Domain Context
```

Outputs:

```text
DecisionContext
```

Required capabilities:

```text
Token Budgeting
Relevance Ranking
Deduplication
Compression
Summarization
Evidence Selection
Capability Filtering
Context Prioritization
```

Never send the complete Session history to the model by default.

---

# 23. Memory

Memory is persistent information that may be useful across Tasks or Sessions.

Recommended classes:

```text
Episodic
Semantic
Procedural
Preference
```

Memory retrieval:

```text
Memory Store
 ↓
Retriever
 ↓
Relevance / Policy Filter
 ↓
Context Compiler
```

Memory is not authoritative for current world state.

Current state must come from:

```text
Runtime State
+
World Model
+
Fresh Observation
```

---

# 24. Evidence System

Observation is raw or normalized information returned by the environment.

Evidence is a traceable claim derived from one or more observations.

Example:

```yaml
evidence:
  id: evidence-123
  subject: pod/dify-api-123
  claim: container_oom_killed
  source: kubernetes
  confidence: 0.99
  observed_at: "..."
  provenance:
    tool_call_id: call-123
```

Evidence should support:

```text
Provenance
Timestamp
Source
Confidence / Uncertainty
Subject
Claim
Supporting Observations
```

Multiple evidence items may support one conclusion.

---

# 25. Evaluator

Evaluator determines whether an Action, Task, or Goal satisfies its completion criteria.

Evaluator types:

```text
Action Evaluator
Task Evaluator
Goal Evaluator
Evidence Evaluator
```

Example:

```text
desired replicas = 3
available replicas = 3
ready replicas = 3
restart count stable
HTTP health = 200
```

Only the Evaluator may establish semantic completion.

The LLM may propose:

```text
finish
```

but cannot unilaterally mark the Goal completed.

---

# 26. Recovery

Recovery is a Runtime-controlled process.

Possible strategies:

```text
Retry
Re-observe
Diagnose
Alternative Capability
Rollback
Ask User
Escalate
Stop
```

Limits:

```text
max_attempts
max_duration
max_cost
max_steps
```

Recovery must be observable and auditable.

Infinite autonomous loops are forbidden.

---

# 27. Session

A Session represents one long-lived Agent execution context.

Example:

```yaml
session:
  id: sess_xxx
  profile: production-ai-operator
  status: running
  created_at: ...
  updated_at: ...
```

Session supports:

```text
Create
Run
Pause
Resume
Cancel
Inspect
Replay
Export
```

Session is the primary unit for:

```text
Persistence
Events
Observability
Authorization
Audit
User Interaction
```

---

# 28. Lifecycle and State Machines

## 28.1 Session

```text
CREATED
   ↓
INITIALIZING
   ↓
RUNNING
   ├── WAITING_USER
   ├── WAITING_TOOL
   ├── RECOVERING
   └── PAUSED
   ↓
COMPLETED / FAILED / CANCELLED
```

A Session may resume from:

```text
PAUSED
WAITING_USER
RECOVERING
```

depending on state and policy.

## 28.2 Goal

```text
PENDING
RUNNING
BLOCKED
COMPLETED
FAILED
CANCELLED
```

## 28.3 Task

```text
PENDING
READY
RUNNING
WAITING
BLOCKED
RECOVERING
COMPLETED
FAILED
CANCELLED
```

State transitions are Runtime-owned and must be validated.

---

# 29. Event System

The Runtime should be event-oriented, but the execution loop does not have to be implemented as a distributed event-sourcing system.

This distinction is important.

Events are used for:

```text
Observability
Audit
UI Updates
Replay
Evaluation
Metrics
Integration
Future Distribution
```

Canonical events:

```text
SessionCreated
SessionStarted
GoalCreated
TaskCreated
TaskStarted
DecisionProposed
DecisionValidated
PolicyEvaluated
ConfirmationRequired
ActionDispatched
ActionStarted
ActionCompleted
ActionFailed
ObservationReceived
EvidenceCreated
WorldModelUpdated
EvaluationCompleted
RecoveryStarted
AgentDelegated
AgentCompleted
GoalCompleted
GoalFailed
SessionPaused
SessionResumed
SessionCancelled
SessionFailed
```

Events should be versioned.

---

# 30. Event Store and Replay

Important events should be persisted.

```text
Session
  ├── Event 1
  ├── Event 2
  ├── Event 3
  └── Event N
```

Replay means:

> Reconstructing the recorded execution history and state transitions.

Replay does **not** guarantee that re-running external side effects will reproduce the exact same outcome.

For deterministic testing, use:

```text
Recorded Events
+
Mock Model
+
Mock Tools
+
Mock Clock
+
Fixture World
```

---

# 31. Persistence

Runtime persistence should use logical interfaces:

```text
SessionStore
StateStore
EventStore
WorldModelStore
MemoryStore
ArtifactStore
```

These are logical boundaries, not necessarily separate databases.

A first implementation may use:

```text
PostgreSQL
```

for multiple stores while preserving independent interfaces.

SQLite may be used for embedded/local mode.

---

# 32. Runtime Service: agentd

`agentd` is the canonical long-running Runtime service.

Responsibilities:

```text
Load Configuration
Load Domains
Load Profiles
Manage Sessions
Execute Agents
Persist State
Expose Runtime API
Publish Events
Handle Resume
Handle Cancellation
Enforce Runtime Limits
```

Example:

```bash
agentd start
```

Health:

```text
GET /health
GET /ready
```

`agentd` is not the Kernel.

It is an application/service built around the Agent Runtime.

---

# 33. Runtime API

The Runtime API is the stable boundary for applications.

Core API:

```text
POST   /v1/sessions
GET    /v1/sessions/{id}
POST   /v1/sessions/{id}/goals
GET    /v1/sessions/{id}/events
POST   /v1/sessions/{id}/pause
POST   /v1/sessions/{id}/resume
POST   /v1/sessions/{id}/cancel

GET    /v1/profiles
GET    /v1/domains
GET    /v1/capabilities
GET    /v1/tools
GET    /v1/policies
GET    /v1/evaluators
```

Streaming:

```text
SSE
```

should be the initial default because it is simple for CLI/Web consumption.

WebSocket may be added when bidirectional real-time interaction requires it.

---

# 34. CLI

CLI is the first application interface to implement.

Recommended:

```bash
agent version

agent init

agent run <profile> "<goal>"

agent session list
agent session show <id>
agent session events <id>
agent session pause <id>
agent session resume <id>
agent session cancel <id>

agent profile list
agent profile show <name>

agent domain list
agent domain install <package>

agent capabilities list
agent tools list
agent policies list
agent evaluators list

agent config show
agent doctor
```

Example:

```bash
agent run production-ai-operator \
  "检查 Dify 是否健康，如果发现安全问题可以自动修复"
```

CLI must call the Runtime API or a stable SDK boundary.

It must not access Kernel internals directly.

---

# 35. TUI

TUI is primarily a developer/operator interface.

It should display:

```text
Goal
Current Task
Agent State
Active Domains
World Model
Decision
Action
Tool
Observation
Evidence
Events
Token Usage
Latency
Policy
```

TUI should consume the same Runtime API/Event Stream as Web.

---

# 36. Web Console

Web is a later application layer.

Architecture:

```text
Browser
   ↓
Web API / Frontend
   ↓
Runtime API
   ↓
agentd
   ↓
Agent Runtime
```

Core pages:

```text
Dashboard
Sessions
Session Detail
Agent Profiles
Domains
Capabilities
Tools
Policies
Memory
World Model
Evaluations
Settings
```

The Session Detail view is the primary debugging/operations interface.

---

# 37. Framework API / SDK

Developers should be able to embed the Runtime.

Example:

```python
runtime = AgentRuntime(config)

session = await runtime.create_session(
    profile="production-ai-operator"
)

result = await runtime.submit_goal(
    session.id,
    goal
)
```

Public SDK types must be separated from internal implementation objects.

---

# 38. Domain SDK

A Domain must be implementable without modifying Kernel code.

Conceptual interface:

```python
class Domain:
    def manifest(self):
        ...

    def ontology(self):
        ...

    def capabilities(self):
        ...

    def tools(self):
        ...

    def policies(self):
        ...

    def evaluators(self):
        ...

    def context_providers(self):
        ...
```

The exact language-level API may evolve, but the semantic contract must remain stable.

---

# 39. Domain Package

Recommended structure:

```text
kubernetes-domain/
├── manifest.yaml
├── ontology/
├── capabilities/
├── tools/
├── policies/
├── procedures/
├── knowledge/
├── evaluators/
├── prompts/
└── tests/
```

The package may contain executable code, declarative resources, or both.

Domain package installation must be validated before activation.

---

# 40. Domain Registry

A Registry is a package discovery/distribution mechanism, not necessarily a marketplace.

Metadata:

```text
Name
Version
Author
Dependencies
Capabilities
Required Tools
Security Metadata
Runtime Compatibility
Domain API Compatibility
```

Early implementation should focus on:

```text
Package Format
Manifest
Compatibility
Discovery
Install
Verify
```

Marketplace UI is optional and should not constrain Kernel design.

---

# 41. Model Layer

Models are external reasoning providers.

Examples:

```text
OpenAI
Anthropic
DeepSeek
Local Models
Custom Providers
```

The Model layer should expose a stable abstraction for:

```text
Text Generation
Structured Output
Tool/Capability Proposal
Streaming
Token Usage
Latency
```

The Kernel must not depend on one vendor's API.

---

# 42. Model Routing

Model selection belongs to Runtime/model infrastructure.

Possible routing dimensions:

```text
Task Type
Complexity
Latency Budget
Cost Budget
Context Requirements
Modality
Availability
Reliability
```

Example:

```text
Simple Classification
    ↓
Fast / Cheap Model

Complex Diagnosis
    ↓
Reasoning Model

Code Generation
    ↓
Coding Model

Vision Task
    ↓
Vision-capable Model
```

Domain may declare model requirements, but the final routing decision belongs to Runtime configuration/policy.

---

# 43. Security Architecture

The security model is:

```text
User
 ↓
Authentication
 ↓
Session
 ↓
Agent Profile
 ↓
Capability
 ↓
Authorization
 ↓
Policy
 ↓
Action
 ↓
Tool
 ↓
Environment
```

Security controls include:

```text
Authentication
Authorization
Capability Permission
Tool Permission
Domain Permission
Secret Management
Audit
Isolation
Sandboxing
Human Confirmation
Resource Limits
```

---

# 44. Security Trust Boundary

The system must treat:

```text
Model
    = Untrusted Decision Source

Runtime
    = Trusted Control Plane

Tool Runtime
    = Controlled Side-Effect Boundary

Environment
    = External World
```

This means:

> Model output must never be trusted merely because it is syntactically valid.

All consequential decisions must pass through Runtime validation.

---

# 45. Secrets

Secrets must be referenced, not placed into model context.

Example:

```yaml
credential:
  ref: secret/kubernetes-prod
```

Tool Runtime resolves the secret.

The model should receive only the minimum information required to reason about the task.

Absolute secret non-disclosure cannot be guaranteed if a user explicitly places a secret into normal conversation; therefore the Runtime must also provide secret redaction and secret-scanning controls.

---

# 46. Sandbox

Coding, Browser, Shell, and similar high-risk Domains should support:

```text
Filesystem Isolation
Network Policy
Process Isolation
Resource Limits
Timeout
Credential Isolation
Execution Policy
```

A general Agent must not receive host-level root access by default.

---

# 47. Observability

The Runtime must be observable.

Recommended:

```text
OpenTelemetry
Prometheus
Structured Logs
Tracing
Event Store
```

Trace structure:

```text
Session
 └── Goal
      └── Task
           ├── LLM Call
           ├── Decision Validation
           ├── Policy Check
           ├── Tool Call
           ├── Observation
           └── Evaluation
```

Metrics:

```text
agent_goal_success_total
agent_goal_failure_total
agent_task_duration
agent_llm_latency
agent_llm_tokens
agent_tool_calls
agent_tool_failures
agent_policy_denials
agent_recovery_total
agent_human_intervention_total
```

---

# 48. Cost Tracking

Per Session, track:

```text
Input Tokens
Output Tokens
Cached Tokens
Model Cost
Tool Cost
Execution Duration
```

Aggregate:

```text
Cost per Goal
Cost per Profile
Cost per Domain
Cost per Model
```

Cost limits may be enforced by Runtime.

---

# 49. Evaluation Platform

Traditional unit tests are necessary but insufficient.

The project must support Agent behavior evaluation.

An Evaluation Scenario contains:

```text
Scenario
Initial State
Goal
Allowed Actions
Expected Outcome
Expected Evidence
Constraints
```

Evaluation should support:

```text
Scenario Tests
Regression Tests
Policy Tests
Recovery Tests
Cross-Domain Tests
Multi-Agent Tests
```

---

# 50. Evaluation Metrics

Recommended:

```text
Goal Completion Rate
Task Success Rate
Action Accuracy
Recovery Rate
Policy Violation Rate
False Action Rate
Human Intervention Rate
Tool Efficiency
Token Efficiency
Time To Completion
```

For Multi-Agent:

```text
Delegation Accuracy
Task Routing Accuracy
Coordination Success
Conflict Rate
Duplicate Work Rate
Cross-Agent Cost
```

Goal completion rate is important, but must not be the sole metric. A system that completes goals by violating policy is not successful.

---

# 51. Deterministic Test Mode

Provide:

```text
Mock Model
Mock Tool
Mock World
Mock Clock
Recorded Events
```

Example:

```text
CrashLoopBackOff
    ↓
Diagnose
    ↓
Patch
    ↓
Verify
```

without requiring a real Kubernetes cluster.

Deterministic mode tests Runtime semantics, not real-world model quality.

---

# 52. Replay

Replay must distinguish:

### Execution Replay

Reconstruct:

```text
Events
State Transitions
Decisions
Actions
Observations
Evidence
```

### Re-execution

Actually run the Agent again.

These are not the same operation.

External systems are nondeterministic, so exact reproduction requires recorded/mocked external inputs.

---

# 53. Idempotency and Uncertain Execution

Long-running Agents must handle uncertain Action outcomes.

Example:

```text
Action dispatched
   ↓
Tool executes
   ↓
Network disconnects
   ↓
Runtime receives no response
```

Action state becomes:

```text
UNKNOWN
```

Runtime must reconcile before blindly retrying.

Action should contain:

```text
action_id
idempotency_key
execution_status
attempt
target
parameters_hash
```

Idempotency is tool/domain dependent. Not every mutation is naturally idempotent.

---

# 54. Concurrency

Runtime must support:

```text
Sequential
Parallel
Dependent
Conflicting
```

Example:

```text
query_pod
query_metrics
query_logs
```

may run in parallel.

But:

```text
patch_deployment
verify_deployment
```

must respect dependency ordering.

Concurrency must be controlled by Runtime, not by arbitrary LLM behavior.

---

# 55. Resource Locking and Conflict Control

Multiple Tasks or Agents may affect the same resource.

Example:

```text
Agent A → patch deployment/foo
Agent B → restart deployment/foo
```

Runtime must support:

```text
Resource Lock
Conflict Detection
Optimistic Concurrency
Reconciliation
```

At minimum, mutation operations should identify:

```text
resource
version / revision when available
action_id
```

---

# 56. Multi-Domain vs Multi-Agent

| Scenario | Multi-Domain | Multi-Agent |
|---|---:|---:|
| Kubernetes + Prometheus | Yes | No |
| Dify + Kubernetes | Yes | No |
| Coding + GitHub | Yes | Usually No |
| Parallel independent research | Optional | Yes |
| Separate security audit with read-only boundary | Optional | Yes |
| Different execution environments | Optional | Yes |
| Independent lifecycle | No | Yes |
| Independent autonomous loops | No | Yes |
| Only different domain knowledge | Yes | No |
| Need meaningful parallel autonomy | Optional | Yes |

Core rule:

> **Do not use Multi-Agent to solve a Domain Composition problem.**

---

# 57. Multi-Agent Runtime

Multi-Agent is an optional Runtime layer.

```text
Universal Agent Runtime
        │
        ├── Single-Agent Execution
        │
        └── Agent Orchestrator
                 │
          ┌──────┼──────┐
          │      │      │
       Agent A Agent B Agent C
```

Multi-Agent must reuse:

```text
Kernel
Session Model
Policy
Evidence
World Model
Evaluation
Runtime Controls
```

It must not create a second incompatible execution architecture.

---

# 58. Agent Task Contract

Agent-to-Agent communication must use a structured contract.

Request:

```json
{
  "api_version": "agent.nantian.dev/v1",
  "task_id": "task-123",
  "parent_task_id": "task-100",
  "goal": "Audit deployment security",
  "input": {
    "resource": "deployment/foo"
  },
  "constraints": {
    "read_only": true
  },
  "expected_output": {
    "type": "security_report"
  }
}
```

Result:

```json
{
  "api_version": "agent.nantian.dev/v1",
  "task_id": "task-123",
  "status": "completed",
  "result": {
    "risk_level": "medium",
    "findings": []
  },
  "evidence": [
    "evidence-123",
    "evidence-456"
  ]
}
```

Natural-language messages may be included as explanatory content, but must not be the protocol contract.

---

# 59. Agent Orchestrator

The Orchestrator manages Agent-level coordination.

Components:

```text
Agent Registry
Task Dispatcher
Dependency Manager
Result Collector
Conflict Resolver
Timeout Manager
Agent Lifecycle Manager
```

Flow:

```text
Parent Goal
   ↓
Task Decomposition
   ↓
Delegation
   ↓
Agent A ─────┐
Agent B ─────┼──→ Results
Agent C ─────┘
              ↓
       Evidence Merge
              ↓
          Evaluation
```

The Orchestrator must not bypass the target Agent's policy boundary.

---

# 60. Agent Registry

Registry records available Agent Profiles/Agent instances.

Example:

```yaml
agent:
  name: security-auditor
  profile: security-readonly
  domains:
    - kubernetes
    - security
  permissions:
    - read_only
```

Registry must distinguish:

```text
Agent Profile
    = configuration/template

Agent Instance
    = running execution identity
```

This distinction is mandatory for future distributed execution.

---

# 61. Delegation

Delegation flow:

```text
Parent Agent
   ↓
Propose delegate
   ↓
Runtime validates
   ↓
Policy
   ↓
Agent Registry
   ↓
Task Contract
   ↓
Target Agent
```

The LLM must not arbitrarily instantiate unrestricted Agents.

Delegation limits should include:

```text
max_depth
max_children
max_duration
max_cost
allowed_profiles
```

---

# 62. Multi-Agent Conflict Resolution

Conflicting proposals:

```text
Agent A:
restart workload

Agent B:
do not restart workload
```

must not be resolved by “last response wins”.

Resolution:

```text
Conflict
 ↓
Evidence
 ↓
Policy
 ↓
Resource State
 ↓
Priority / Constraints
 ↓
Coordinator
```

Safety policy has priority over convenience.

---

# 63. Event-Driven vs Event-Sourced

The project should be **event-oriented**, but must not prematurely require full event sourcing.

Recommended initial model:

```text
State Store = authoritative current state
Event Store = durable execution history
```

Later, if requirements justify it:

```text
Event Sourcing
CQRS
Distributed Event Bus
```

may be introduced.

This avoids coupling the core architecture to a distributed event-sourcing implementation too early.

---

# 64. Deployment Modes

Support three modes.

## 64.1 Embedded

```text
Application
    ↓
Agent Runtime
```

For developers and tests.

## 64.2 Local Service

```text
CLI / TUI
    ↓
agentd
```

For local use.

## 64.3 Server

```text
CLI / TUI / Web / API Client
             ↓
           agentd
             ↓
       Runtime Services
```

For production.

Distributed Runtime is a later optimization/scale architecture, not a prerequisite for the semantic Runtime.

---

# 65. Distributed Runtime

Only introduce distributed execution when required.

Possible architecture:

```text
API
 ↓
Scheduler
 ↓
Agent Worker
 ↓
Tool Worker
```

Required primitives:

```text
Queue
Lease
Heartbeat
Cancellation
Retry
Distributed Lock
Idempotency
```

Distributed execution must preserve the same Session / Task / Action semantics as local execution.

---

# 66. Model Routing

Model routing is Runtime/model infrastructure.

Domain may declare requirements such as:

```text
requires_vision
requires_long_context
requires_structured_output
```

But Runtime determines the concrete provider/model.

Routing may consider:

```text
Capability
Task Complexity
Latency
Cost
Context
Modality
Availability
Reliability
Policy
```

---

# 67. Configuration

Global Runtime configuration:

```yaml
runtime:
  max_steps: 100
  default_timeout: 30m
  max_cost: 10.0

model:
  routing_policy: default

storage:
  state: postgres
  events: postgres
  world_model: postgres

api:
  host: 0.0.0.0
  port: 8080

security:
  confirmation_required_for:
    - high-risk

observability:
  otel:
    enabled: true
```

Domain-specific configuration must remain within Domain boundaries unless it is a general Runtime concern.

---

# 68. Audit

Every side-effecting Action must generate an Audit Record.

Record:

```text
Actor
Session
Agent
Goal
Task
Capability
Tool
Target
Parameters / Parameter Hash
Policy Decision
Result
Timestamp
```

Sensitive values must be redacted.

Audit records must be append-oriented and protected from normal Agent modification.

---

# 69. Package and API Versioning

Public contracts must be versioned.

```text
Domain API:
v1alpha1
v1beta1
v1

Agent Task Contract:
v1

Runtime HTTP API:
v1
```

Domain Manifest:

```yaml
apiVersion: agent.nantian.dev/v1alpha1
```

Domain compatibility:

```yaml
compatibility:
  runtime: ">=0.3,<1.0"
  domain_api: "v1alpha1"
```

Version compatibility must be checked before Domain activation.

---

# 70. Repository Architecture

Recommended monorepo:

```text
universal-agent/
│
├── kernel/
│   ├── goal/
│   ├── task/
│   ├── state/
│   ├── decision/
│   ├── action/
│   ├── observation/
│   ├── evidence/
│   ├── world_model/
│   ├── policy/
│   ├── recovery/
│   ├── context/
│   ├── memory/
│   └── evaluation/
│
├── runtime/
│   ├── lifecycle/
│   ├── scheduler/
│   ├── executor/
│   ├── event_bus/
│   ├── persistence/
│   ├── cancellation/
│   ├── concurrency/
│   └── coordination/
│
├── model/
│   ├── providers/
│   ├── routing/
│   └── structured_output/
│
├── domain-sdk/
│
├── domain-manager/
│   ├── discovery/
│   ├── loader/
│   ├── registry/
│   ├── compatibility/
│   └── composer/
│
├── domains/
│   ├── kubernetes/
│   ├── coding/
│   ├── browser/
│   ├── research/
│   └── ...
│
├── agent/
│   ├── profiles/
│   ├── registry/
│   └── delegation/
│
├── orchestrator/
│   ├── dispatcher/
│   ├── dependency/
│   ├── conflict/
│   └── result/
│
├── storage/
│   ├── state/
│   ├── event/
│   ├── world_model/
│   ├── memory/
│   └── artifact/
│
├── api/
├── cli/
├── tui/
├── web/
├── observability/
├── evaluation/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── domain/
│   ├── runtime/
│   ├── evaluation/
│   └── scenarios/
│
├── examples/
├── docs/
├── AGENTS.md
└── README.md
```

The exact repository layout may differ during implementation, but dependency boundaries must remain equivalent.

---

# 71. Dependency Direction

The preferred dependency direction is:

```text
Applications
    ↓
Runtime API / SDK
    ↓
Agent Runtime
    ↓
Kernel
    ↑
Domain SDK
    ↑
Domain Implementations
```

Additional infrastructure:

```text
Runtime → Storage
Runtime → Model
Runtime → Tool Runtime
Runtime → Observability
Runtime → Domain Manager
```

Kernel must not depend on concrete:

```text
Kubernetes
Prometheus
PostgreSQL
OpenAI
Anthropic
FastAPI
CLI framework
Web framework
```

---

# 72. Public API Boundaries

Distinguish:

```text
Internal API
Public Python API
Domain API
Agent Task Contract
Runtime HTTP API
CLI Contract
Event Schema
```

Internal classes must not automatically become public API.

---

# 73. Runtime vs Kernel vs agentd

These three concepts must never be merged in documentation.

```text
Kernel
  = Agent semantic primitives and rules

Runtime
  = execution engine

agentd
  = long-running service process hosting Runtime
```

Therefore:

```text
agentd != Runtime
Runtime != Kernel
Kernel != agentd
```

---

# 74. Agent Profile vs Domain vs Agent Instance

These concepts must remain separate.

```text
Domain
  = reusable expertise/capability package

Agent Profile
  = declarative configuration/composition

Agent Instance
  = running autonomous execution identity
```

Example:

```text
Kubernetes Domain
       ↓
Production Operator Profile
       ↓
Session / Agent Instance
```

---

# 75. Framework vs Runtime vs Product

The project contains all three:

```text
Framework
  └── Kernel / SDK / Extension Contracts

Runtime
  └── Agent Runtime / agentd

Product Interfaces
  └── CLI / TUI / Web
```

This is intentional.

The same Runtime must be usable:

```text
Embedded
Local Service
Server
```

---

# 76. Operating-System Analogy

Useful analogy:

```text
Operating System
├── Kernel
├── Process
├── Scheduler
├── Filesystem
├── Permission
├── Device
└── User Interface
```

Agent Platform:

```text
Agent Platform
├── Agent Kernel
├── Agent Session / Instance
├── Scheduler
├── World Model / Memory
├── Policy
├── Tool Runtime
└── CLI / TUI / Web
```

`agentd` is analogous to a system service/daemon that hosts the Runtime.

This is an analogy, not a literal implementation requirement.

---

# 77. Non-Goals

The project should explicitly avoid becoming:

## 77.1 A Workflow Engine

Static workflows are not the primary abstraction.

## 77.2 A Prompt Marketplace

Prompts are not the primary extension model.

## 77.3 A Tool Collection

Tools without Agent semantics are not the product.

## 77.4 A Mandatory Multi-Agent Framework

Single-Agent + Multi-Domain must remain first-class.

## 77.5 A Distributed System by Default

Distribution should be introduced only when operational requirements justify it.

## 77.6 A Model Abstraction that hides all model differences

The Model interface should normalize common behavior while preserving capability metadata where required.

---

# 78. Product Entry Points

The project must eventually expose:

```text
CLI
TUI
Web
Python SDK
HTTP API
```

Priority:

```text
1. Python SDK / Runtime API
2. agentd
3. CLI
4. Event Stream
5. TUI
6. Web
```

The Web UI must not become a prerequisite for validating the Runtime.

---

# 79. Current Development Gap

If the current codebase has completed the P3 semantic architecture, the next gap is **Runtime Productization**, not another layer of prompting.

The first product milestone should prove:

```text
agentd
  ↓
Session
  ↓
Goal
  ↓
Task
  ↓
Decision
  ↓
Action
  ↓
Observation
  ↓
Evidence
  ↓
Evaluation
  ↓
Persistent Result
```

The minimum usable system should be able to:

```bash
agent run <profile> "<goal>"
```

and:

```text
- execute the Goal;
- expose status;
- stream events;
- persist the Session;
- resume after restart;
- enforce Policy;
- report the final evaluated result.
```

---

# 80. Roadmap

The roadmap is a development plan, not an architectural dependency.

## P0 — Core Agent Semantics

```text
Agent Loop
Goal
Task
State
Decision
Action
Observation
```

## P1 — Domain and Control

```text
Domain Runtime
Domain Manifest
Capability
Policy
Evaluator
Context Compiler
```

## P2 — World and Recovery

```text
World Model
Evidence
Dynamic Task Expansion
Recovery
```

## P3 — Personalization and Composition

```text
Memory
Multi-Domain
Cross-Domain World Model
Agent Profile
```

## P3.5 — Runtime Productization

```text
agentd
Runtime API
Session API
CLI
Event Stream
Persistence
Resume / Pause / Cancel
Runtime Configuration
```

## P3.6 — Operations

```text
OpenTelemetry
Metrics
Structured Logs
Audit
Cost Tracking
Runtime Doctor
```

## P3.7 — Evaluation

```text
Evaluation Harness
Scenario Tests
Regression Tests
Policy Tests
Recovery Tests
Replay
Deterministic Test Mode
```

## P4 — Multi-Agent

```text
Agent Registry
Agent Task Contract
Delegation
Parallel Agents
Dependency Management
Result / Evidence Merge
Conflict Resolution
Multi-Agent Evaluation
```

## P5 — User Interfaces

```text
TUI
Web Console
Session Explorer
Domain Manager
Evaluation Console
World Model Explorer
```

## P6 — Distributed Runtime

```text
Scheduler
Worker
Queue
Lease
Heartbeat
Distributed State
Distributed Lock
High Availability
Horizontal Scaling
```

## P7 — Ecosystem

```text
Domain SDK
Domain Registry
Package Registry
Agent Profile Ecosystem
Evaluation Dataset
Community / Enterprise Domains
```

---

# 81. Recommended Immediate Work

If the project is currently at P3, implement in this order:

```text
Step 1
Runtime API
        ↓
Step 2
agentd
        ↓
Step 3
Session Persistence
        ↓
Step 4
CLI
        ↓
Step 5
Event Stream
        ↓
Step 6
Pause / Resume / Cancel
        ↓
Step 7
Observability
        ↓
Step 8
Evaluation Harness
        ↓
Step 9
TUI
        ↓
Step 10
Multi-Agent
        ↓
Step 11
Web
        ↓
Step 12
Distributed Runtime
```

Do not start Multi-Agent merely because it is architecturally interesting.

First prove that one Agent can:

```text
run
persist
observe
recover
resume
evaluate
```

reliably.

---

# 82. Maturity Criteria

The project is mature when it can demonstrate:

## Task Reliability

```text
Goal Completion Rate
Task Success Rate
Recovery Rate
```

## Safety

```text
Policy Violation Rate
Unauthorized Action Rate
Confirmation Bypass Rate
```

## World Understanding

```text
World Model Accuracy
Evidence Traceability
Cross-Domain Success Rate
```

## Runtime Reliability

```text
Session Resume Success
Unknown Action Reconciliation
Cancellation Success
Execution Recovery
```

## Efficiency

```text
Token Efficiency
Tool Efficiency
Cost per Goal
Time to Completion
```

## Extensibility

```text
Domain Integration Cost
Profile Creation Cost
Tool Integration Cost
API Compatibility
```

## Multi-Agent Quality

```text
Delegation Accuracy
Coordination Success
Duplicate Work Rate
Conflict Rate
Cross-Agent Cost
```

---

# 83. Final Architecture

```text
                         Universal Agent Platform

        Framework                 Runtime                Application
           │                        │                       │
     Kernel + SDK                agentd                CLI / TUI / Web
           │                        │                       │
           └────────────────────────┼───────────────────────┘
                                    │
                             Runtime API
                                    │
                           Universal Agent Runtime
                                    │
             ┌──────────────────────┼──────────────────────┐
             │                                             │
      Domain Manager                                  Agent Profiles
             │                                             │
      Domain Composition                         Runtime Configuration
             │                                             │
             └──────────────────────┬──────────────────────┘
                                    │
                              Shared World Model
                                    │
                             Context Compiler
                                    │
                             Decision Engine
                                    │
                     ┌──────────────┴──────────────┐
                     │                             │
                Capability                    Policy / AuthZ
                     │                             │
                     └──────────────┬──────────────┘
                                    │
                              Action Runtime
                                    │
                               Tool Runtime
                                    │
                               Environment
                                    │
                                Observation
                                    │
                                 Evidence
                                    │
                            World Model Update
                                    │
                                Evaluator
                                    │
                      ┌─────────────┼─────────────┐
                      │             │             │
                   Continue      Recover       Finish


Optional:
                         Agent Orchestrator
                                  │
                     ┌────────────┼────────────┐
                     │            │            │
                   Agent A      Agent B      Agent C
                     │            │            │
                     └────────────┼────────────┘
                                  │
                          Task Contracts
                                  │
                         Results + Evidence
                                  │
                         Parent Agent / Goal
```

---

# 84. Final Conceptual Boundaries

```text
Model
  → Reasoning / Proposal

Kernel
  → Agent Semantics

Runtime
  → Execution / Lifecycle / Control

agentd
  → Runtime Service Process

Domain
  → Expertise / Ontology / Capabilities

Agent Profile
  → Configuration / Composition

Agent Instance
  → Running Autonomous Execution Identity

World Model
  → Current Belief About External Reality

Knowledge
  → General / Persistent Information

Memory
  → Reusable Historical Information

Capability
  → Semantic Ability

Tool
  → Concrete Execution Mechanism

Action
  → Concrete Side-Effect Intent

Policy
  → Permission / Safety Boundary

Evidence
  → Traceable Basis for Claims

Evaluator
  → Completion / Correctness Assessment

Session
  → Long-Lived Execution Context

Event
  → Durable/observable Execution Fact

Agent Orchestrator
  → Multi-Agent Coordination

Interface
  → User / Client Interaction
```

---

# 85. Final Goal

The goal is not:

> Build one extremely capable Agent.

The goal is:

> **Build a Runtime in which one universal Agent can continuously acquire new domains, capabilities, tools, knowledge, policies, and execution environments without changing its core architecture.**

Then:

```text
Universal Agent
    +
Kubernetes Domain
    ↓
Kubernetes Operator
```

```text
Universal Agent
    +
Coding Domain
    ↓
Software Engineer
```

```text
Universal Agent
    +
Research Domain
    ↓
Research Agent
```

```text
Universal Agent
    +
Kubernetes
    +
Dify
    +
Observability
    +
Database
    ↓
AI Infrastructure Operator
```

And only when necessary:

```text
Universal Agent Runtime
    +
Agent Orchestrator
    +
Multiple Agent Profiles
    ↓
Multi-Agent System
```

The architectural north star is therefore:

```text
Universal Agent Runtime
        +
Pluggable Domain Runtime
        +
Agent Profiles
        +
World Model
        +
Policy
        +
Evaluation
        +
Reliable Execution
        +
Optional Multi-Agent Coordination
        ↓
Universal Agent Platform
```

---

# 86. Engineering Rule for Future Changes

Before adding any new subsystem, answer these questions:

1. Does this belong to Kernel, Runtime, Domain, Profile, Application, or Orchestrator?
2. Does it introduce a concrete Domain dependency into Kernel?
3. Does it make the LLM authoritative over Runtime state or security?
4. Does it duplicate an existing concept?
5. Does it belong in Single-Agent + Multi-Domain instead of Multi-Agent?
6. Does it require persistent state?
7. Does it require an Event?
8. Does it need Policy enforcement?
9. Can it be tested deterministically?
10. Does it preserve the public API and version boundaries?

If the answer creates a new architectural boundary, update this document before implementing the subsystem.

> **This document is the source of truth for architecture.**
