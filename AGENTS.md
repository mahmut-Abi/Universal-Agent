# AGENTS.md

## 1. Project Mission

This repository implements a **Universal Agent Runtime + Pluggable Domain Runtime**.

The primary goal is NOT to build another chatbot, workflow engine, or prompt wrapper.

The goal is to build a reusable Agent Runtime in which:

```text
Universal Agent Kernel
        +
Domain Runtime
        +
Model
        +
Capabilities / Tools
        +
Knowledge
        +
Policy
        +
Evaluator
```

can produce specialized agents without modifying the core runtime.

The central architectural principle is:

> The Kernel defines HOW an Agent works. The Domain defines WHERE and WITH WHAT knowledge/capabilities it works.

The project should eventually support domains such as:

- Kubernetes
- Coding
- Browser
- Database
- Research
- DevOps
- AI infrastructure
- Custom enterprise domains

Do not optimize for a demo. Optimize for a clean runtime architecture that can evolve into a production-grade Agent platform.

---

# 2. Read This First

Before making meaningful code changes:

1. Read this file completely.
2. Read `universal-agent-runtime-domain-runtime-design.md`.
3. Inspect the current repository structure.
4. Identify what has already been implemented.
5. Preserve existing architectural decisions unless there is a concrete reason to change them.
6. Do not blindly implement the entire design document at once.

The design document is the architectural target.

The repository state is the implementation truth.

When they differ:

- do not silently rewrite large parts of the project;
- explain the discrepancy internally through code structure;
- implement incrementally;
- preserve compatibility where reasonable.

---

# 3. Core Architecture

The core execution loop is:

```text
Goal
 ↓
Current State
 ↓
World Model
 ↓
Current Task
 ↓
Context Compilation
 ↓
Decision
 ↓
Policy Check
 ↓
Action
 ↓
Observation
 ↓
Evidence
 ↓
State / World Model Update
 ↓
Evaluation
 ↓
Continue / Recover / Ask User / Finish
```

This loop is the architectural center of the project.

Do not replace it with:

```text
Prompt → LLM → Tool → Prompt → Tool
```

The LLM is a reasoning component inside the runtime, not the runtime itself.

---

# 4. Non-Negotiable Design Principles

## 4.1 LLM Does Not Own State

The LLM must never be treated as the authoritative source of:

- task state
- goal state
- world state
- execution history
- policy state
- evidence
- completion state

State belongs to runtime-managed components.

Bad:

```python
prompt = previous_messages + "current state..."
```

Good:

```python
state = state_store.load(...)
world = world_model.snapshot(...)
context = context_compiler.build(...)
```

---

## 4.2 LLM Does Not Own Control Flow

The LLM may propose:

```text
execute
wait
ask_user
recover
finish
```

but Runtime controls:

- retries
- timeout
- cancellation
- scheduling
- state transitions
- policy enforcement
- recovery
- maximum iterations
- human confirmation

Never allow model output to directly execute arbitrary runtime transitions.

---

## 4.3 Policy Is Outside the Model

Security, authorization, destructive-action rules, confirmation requirements, and environment restrictions must be enforced by deterministic runtime code.

Never implement:

```text
"You are not allowed to delete production resources."
```

as the only protection.

Instead:

```text
LLM Decision
    ↓
Policy Engine
    ↓
ALLOW / CONFIRM / DENY
```

Prompt-level policy is supplementary, never authoritative.

---

## 4.4 Tool != Capability

A Tool is an implementation.

A Capability is an abstract ability.

Example:

```text
Capability:
    inspect_pod

Tools:
    kubectl_get_pod
    kubernetes_api_get_pod
```

Decision making should reason about capabilities first and tools second.

Do not expose hundreds of low-level tools directly to the model if they can be represented as higher-level capabilities.

---

## 4.5 Tool Success != Task Success

Never mark a task complete merely because a tool returned successfully.

Required conceptual pipeline:

```text
Action
 ↓
Tool Result
 ↓
Observation
 ↓
Evidence
 ↓
Evaluator
 ↓
Task / Goal Result
```

Example:

```text
kubectl apply succeeded
```

does NOT mean:

```text
deployment is healthy
```

Verification is mandatory for meaningful tasks.

---

## 4.6 World Model != Knowledge Base

Knowledge describes what is generally true.

World Model describes what is currently true.

Example:

```text
Knowledge:
    Kubernetes Deployment manages ReplicaSets.

World Model:
    deployment/dify-api has 3 desired replicas,
    2 available replicas,
    and is currently unhealthy.
```

Do not use RAG as a substitute for runtime state.

---

## 4.7 Domain Must Not Leak Into Kernel

Never add code like:

```python
if domain == "kubernetes":
    ...
elif domain == "coding":
    ...
```

inside the core runtime.

Domain-specific behavior belongs in Domain Runtime implementations.

The Kernel should depend on interfaces/protocols.

---

## 4.8 Domain Profile Is Not Just a Prompt

A Domain must be structured.

At minimum:

```text
Ontology
Capabilities
Tools
Knowledge
Procedures
Policies
Evaluators
Context Providers
```

Prompt fragments may exist, but they are only one part of the Domain.

---

## 4.9 Multi-Domain Is Not Multi-Agent

Multi-domain collaboration is a core capability.

Multi-agent orchestration is an optional advanced capability.

Default architecture:

```text
Universal Agent
        |
        v
Domain Composition
        |
        +-- Kubernetes Domain
        +-- Dify Domain
        +-- PostgreSQL Domain
        +-- Observability Domain
        |
        v
Shared World Model
```

Do not turn domain selection into agent routing.

Bad:

```text
Supervisor Agent
        |
        +-- Kubernetes Agent
        +-- Database Agent
        +-- Observability Agent
```

Good:

```text
One Agent
        |
        +-- Multiple Active Domains
        |
        v
Shared World Model
```

Domain is a knowledge/capability boundary.

Agent is an autonomous execution boundary.

Use multiple Agents only when there are genuinely separate:

- goals
- state
- permissions
- lifecycles
- execution environments
- autonomous loops
- isolation requirements

When Multi-Agent is eventually implemented, Agents must communicate through structured Task / Result / Evidence contracts, not chat transcripts.

---

## 4.10 Avoid Static Mega-Planners

Do not build a planner that attempts to generate the entire task tree before execution.

Prefer:

```text
Goal
 ↓
Current World
 ↓
Current Task
 ↓
Decision
 ↓
Action
 ↓
Observation
 ↓
World Update
 ↓
Dynamic Task Expansion
```

Tasks should be discovered and expanded as information becomes available.

---

# 5. Separation of Responsibilities

The following boundaries should remain explicit.

## Agent Kernel

Responsible for:

- lifecycle
- goal execution
- task execution
- state
- decision orchestration
- action orchestration
- observation
- evidence
- recovery
- context
- evaluation

Not responsible for:

- Kubernetes logic
- GitHub semantics
- database-specific workflows
- domain-specific safety rules

---

## Domain Runtime

Responsible for:

- ontology
- domain capabilities
- domain tools
- domain knowledge
- procedures
- domain policies
- domain evaluators
- domain context providers

Not responsible for:

- global session lifecycle
- generic scheduling
- generic state transitions
- global retry mechanics

---

## Model Layer

Responsible for:

- reasoning
- interpretation
- decision proposal
- structured output

Not responsible for:

- authorization
- state persistence
- task completion
- tool execution
- security policy
- retry control

---

## Tool Runtime

Responsible for:

- validation
- authorization integration
- execution
- timeout
- cancellation
- result normalization
- audit information

Not responsible for:

- deciding whether the overall goal is complete

---

## Evaluator

Responsible for determining whether:

- an action succeeded
- a task succeeded
- a goal succeeded
- evidence is sufficient
- safety requirements are satisfied

Never use an LLM's final prose as the sole completion signal.

---

# 6. Preferred Data Flow

Use explicit objects between stages.

Prefer:

```text
Goal
Task
DecisionContext
Decision
PolicyResult
Action
Observation
Evidence
EvaluationResult
```

Avoid passing arbitrary dictionaries through the entire system.

Use typed schemas wherever practical.

For Python, prefer:

- dataclasses
- Pydantic models
- TypedDict
- Protocol
- explicit enums

depending on the project's existing conventions.

---

# 7. Decision Contract

A model decision should be structured.

Conceptually:

```json
{
  "type": "execute",
  "capability": "inspect_pod",
  "target": "pod/dify-api-123",
  "arguments": {},
  "reason": "The pod is restarting and logs are required.",
  "expected_observations": [
    "exit_code",
    "recent_logs",
    "container_state"
  ]
}
```

The exact schema can evolve, but these principles should remain:

1. structured output;
2. explicit decision type;
3. explicit capability;
4. explicit arguments;
5. explicit expected observation;
6. runtime validation before execution.

---

# 8. State Model

State should be explicit.

At minimum distinguish:

```text
Goal State
Task State
Execution State
World State
Evidence
Memory
```

Do not collapse everything into a single giant `state` dictionary.

Use stable identifiers:

```text
goal_id
task_id
action_id
observation_id
evidence_id
session_id
```

These IDs are important for:

- tracing
- debugging
- replay
- audit
- evaluation
- recovery

---

# 9. Error Handling

Errors must be classified.

Prefer categories such as:

```text
Transient
Timeout
PermissionDenied
ValidationError
DependencyMissing
InvalidState
ToolFailure
PolicyDenied
UserRequired
Unknown
```

Recovery should be deterministic where possible:

```text
Timeout
  → retry

PermissionDenied
  → ask user / alternative capability

Health Check Failed
  → diagnose

Policy Denied
  → stop / ask user

Unknown
  → investigate
```

Do not solve every failure with:

```text
retry()
```

Infinite retry loops are prohibited.

---

# 10. Context Management

Never blindly send:

```text
all conversation
+
all tool outputs
+
all logs
+
all memory
```

to the model.

Use:

```text
Context Compiler
```

to construct relevant context from:

```text
Goal
Current Task
Relevant State
Relevant World Model
Relevant Evidence
Relevant Memory
Available Capabilities
Relevant Policies
Recent Actions
```

Context must be budget-aware.

Future implementation should support:

- relevance ranking
- token budgeting
- compression
- summarization
- deduplication
- evidence selection
- tool/capability selection

---

# 11. Domain Package Rules

A Domain Package should be independently installable.

Conceptually:

```text
domain/
├── manifest.yaml
├── ontology/
├── capabilities/
├── tools/
├── policies/
├── procedures/
├── knowledge/
├── evaluators/
└── prompts/
```

A Domain should not require changes to the Kernel source code.

If adding a Domain requires editing Kernel `if/elif` branches, the architecture is wrong.

---

# 12. API Design

Prefer stable interfaces.

Core interfaces should conceptually include:

```python
AgentRuntime
RuntimeAPI
SessionAPI
GoalManager
TaskManager
StateStore
WorldModel
DecisionEngine
ContextCompiler
CapabilityRegistry
ToolRegistry
ActionRuntime
ObservationSystem
EvidenceSystem
PolicyEngine
RecoveryManager
Evaluator
MemoryStore
EventStore
EventStream
DomainManager
DomainRuntime
AgentProfile
RuntimeService
```

Do not prematurely over-abstract.

Only introduce an interface when it represents a real architectural boundary.

---

# 13. Development Strategy

Implement incrementally.

## P0

Build the smallest complete Agent Loop:

```text
Goal
 ↓
Task
 ↓
Decision
 ↓
Tool
 ↓
Observation
 ↓
State
 ↓
Decision
```

Required:

- Goal
- State
- Task
- Decision
- Tool
- Observation
- Model adapter
- basic context

Do not start with Marketplace, Multi-Agent, or advanced memory.

---

## P1

Add:

- Domain Manifest
- Capability
- Policy
- Evaluator
- Context Compiler
- Domain Composition interface

At the end of P1, the runtime should be able to load one Domain without modifying Kernel code.

The Domain Composition interface may exist at P1, but it should not force multi-domain execution yet.

---

## P2

Add:

- World Model
- Evidence
- Dynamic Task Expansion
- Recovery
- Cross-domain entity/relation schema

This is where the system becomes a real Agent Runtime rather than a tool-calling loop.

The World Model should be shaped so future Domains can contribute entities, relations, and evidence into one shared model.

---

## P3

Add:

- Memory
- Multi-Domain
- Cross-Domain World Model
- Agent Profile

---

## P3.5

Add Runtime Productization:

- Runtime API
- Session API
- `agentd`
- CLI
- Event Stream
- Persistence
- Resume / Pause / Cancel
- Runtime Configuration

This is the preferred next implementation gap once the P3 semantic architecture is stable.

---

## P3.6

Add Operations:

- OpenTelemetry
- Metrics
- Structured Logs
- Audit
- Cost Tracking
- Runtime Doctor

---

## P3.7

Add Evaluation Platform foundations:

- Evaluation Harness
- Scenario Tests
- Regression Tests
- Policy Tests
- Recovery Tests
- Replay
- Deterministic Test Mode

---

## P4

Add Optional Multi-Agent Runtime:

- Agent Registry
- Agent Task Contract
- Delegation
- Parallel Agents
- Result / Evidence Merge
- Conflict Resolution
- Multi-Agent Evaluation

Optional Multi-Agent Runtime belongs after the single-Agent multi-Domain architecture is proven.

When added, it must be built around:

- Agent Task Contract
- Agent Result Contract
- Evidence handoff
- Session isolation
- Permission isolation

It must not replace Domain Composition.

---

## P5

Add User Interfaces:

- TUI
- Web Console
- Session Explorer
- Domain Manager UI
- Evaluation Console
- World Model Explorer

The Web UI must not become a prerequisite for validating the Runtime.

---

## P6

Add Distributed Runtime only when operational requirements justify it:

- Scheduler
- Worker
- Queue
- Lease
- Heartbeat
- Distributed State
- Distributed Lock
- High Availability

---

## P7

Add Ecosystem capabilities:

- Domain SDK
- Domain Registry
- Package Registry
- Agent Profile Ecosystem
- Evaluation Dataset
- Community / Enterprise Domains

---

# 14. First Domain

Use Kubernetes as the first serious Domain because it exercises almost every difficult aspect:

- structured ontology
- read operations
- mutations
- asynchronous state
- health verification
- logs/events
- permissions
- destructive actions
- recovery
- cross-resource relationships

The first meaningful end-to-end scenario should be something like:

```text
"Find out why this Deployment is unhealthy and fix it if it is safe."
```

The Agent should be able to:

```text
inspect
 ↓
observe
 ↓
diagnose
 ↓
produce evidence
 ↓
select capability
 ↓
apply safe action
 ↓
verify
 ↓
recover if needed
 ↓
finish
```

---

# 15. Testing Requirements

Every core component must have unit tests.

Important integration tests:

### Normal execution

```text
Goal → Action → Observation → Complete
```

### Tool failure

```text
Tool Failure → Recovery → Retry / Alternative
```

### Policy denial

```text
Decision → Policy → Deny
```

### Confirmation

```text
Decision → Policy → Require Confirmation → Pause
```

### Verification failure

```text
Action succeeds → Health check fails → Diagnose
```

### Dynamic expansion

```text
Observation → New information → New Task
```

### Long context

```text
Large execution history → Context Compiler → Relevant Context
```

### Multi-domain

```text
Domain A + Domain B → Shared World Model
```

---

# 16. Evaluation Metrics

Do not optimize only for model response quality.

Track:

```text
Goal Completion Rate
Task Success Rate
Action Accuracy
Recovery Rate
Policy Violation Rate
Human Intervention Rate
False Action Rate
Tool Calls / Goal
Token Usage / Goal
Time To Completion
Verification Success Rate
```

The primary metric should be:

> Can the Agent reliably complete real tasks?

---

# 17. Coding Rules

## 17.1 Prefer small modules

Avoid giant files such as:

```text
agent.py
```

containing the entire runtime.

Prefer domain-oriented modules.

## 17.2 Keep side effects at boundaries

Core decision logic should be as deterministic and testable as possible.

Tool execution, network access, filesystem access, and model calls should live behind explicit interfaces.

## 17.3 Use async where execution is naturally asynchronous

Especially for:

- tool execution
- model calls
- observation
- event handling
- long-running tasks

## 17.4 Make execution observable

Every meaningful transition should be traceable:

```text
GoalCreated
TaskCreated
DecisionGenerated
PolicyChecked
ActionStarted
ActionCompleted
ObservationReceived
EvidenceCreated
StateUpdated
EvaluationCompleted
RecoveryStarted
GoalCompleted
```

An event model is preferred over hidden state mutations.

---

# 18. Logging and Debugging

Every execution should have:

```text
session_id
goal_id
task_id
action_id
```

Logs should make it possible to reconstruct:

```text
Why did the Agent make this decision?
What information did it have?
What tool did it call?
What happened?
What evidence did it receive?
Why did it continue?
Why did it stop?
```

Never log secrets, credentials, API keys, tokens, or sensitive tool payloads.

---

# 19. What NOT To Build Prematurely

Do not implement these merely because they appear in Agent frameworks:

- multi-agent orchestration
- supervisor-agent domain routing
- agent handoff through chat transcripts
- web UI before Runtime API / Event Stream
- distributed runtime before local runtime productization
- autonomous subagents
- infinite planning
- reflection loops
- self-generated prompts
- automatic prompt rewriting
- complex graph databases
- vector database dependency everywhere
- browser automation before core runtime is stable
- marketplace
- distributed scheduler
- Kubernetes deployment of the Agent itself

First prove the core loop.

---

# 20. Anti-Patterns

Reject designs that depend on:

```text
"Let the LLM decide everything."
```

```text
"Put all state in conversation history."
```

```text
"Use RAG to represent current system state."
```

```text
"Let the model decide whether an operation is allowed."
```

```text
"Create a new Agent for every domain."
```

```text
"Generate the entire plan before observing the environment."
```

```text
"Tool returned success, therefore task succeeded."
```

```text
"Retry until it works."
```

---

# 21. Change Discipline

When implementing a feature:

1. Identify the architectural layer.
2. Identify the owning component.
3. Check whether an existing interface already solves it.
4. Avoid modifying unrelated modules.
5. Add tests before or together with behavior changes.
6. Keep public contracts stable.
7. Prefer backward-compatible changes.
8. Document non-obvious architectural decisions.

Do not perform broad refactors unless explicitly required.

---

# 22. Definition of Done

A feature is not done when the code compiles.

It should have:

- implementation
- tests
- error handling
- observability
- typed contracts
- documentation where appropriate
- no architectural boundary violations

For Agent behavior, also verify:

```text
What state changed?
What evidence was produced?
What happens on failure?
What happens if the policy denies the action?
How is completion verified?
```

---

# 23. Codex Working Mode

When working in this repository, behave as a senior Agent Runtime engineer.

Before coding:

```text
Understand → Inspect → Design → Implement → Test → Review
```

Do not immediately start writing code after reading a task.

For non-trivial tasks, first identify:

```text
Current architecture
Relevant modules
Existing interfaces
Desired behavior
Potential side effects
Test strategy
```

When uncertain, prefer the smallest change that preserves the architecture.

If the requested change conflicts with this document, do not silently violate the architecture.

Instead:

1. identify the conflict;
2. determine whether the design itself needs to evolve;
3. make the smallest coherent architectural change;
4. update tests and documentation.

---

# 24. Architectural North Star

Always preserve this mental model:

```text
                 Goal
                  |
                  v
             World Model
                  |
                  v
               Decide
                  |
                  v
               Policy
                  |
                  v
                Act
                  |
                  v
              Observe
                  |
                  v
              Evidence
                  |
                  v
              Update
                  |
                  v
             Evaluate
                  |
          +-------+-------+
          |       |       |
       Continue Recover  Finish
```

The system should become more capable by improving:

```text
World Model
Decision Quality
Capability Selection
Evidence
Recovery
Context
Evaluation
```

not by continuously adding more prompt text.

The ultimate goal is:

> **A stable Universal Agent Runtime Platform where one universal Agent can acquire new domains, capabilities, tools, knowledge, policies, profiles, and execution environments without changing the Kernel.**
