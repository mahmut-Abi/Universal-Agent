# Universal Agent Runtime

A typed Universal Agent Kernel with a pluggable P2 Domain Runtime.

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

## Runtime flow

```text
Goal -> Context -> Decision(capability) -> Capability Resolver -> Policy -> Tool
  -> Observation -> Evidence -> World Model -> Task Expansion -> Evaluator -> State
                         ^                                      |
                         +-------- bounded Recovery <-----------+
```

Recovery is classified and budgeted. Retries and alternative capabilities receive new action IDs and
return through Capability Resolution and Policy; Recovery never calls a Tool directly. Unknown or
exhausted failures stop deterministically.

## Current scope

- P0: typed state, model/tool boundaries, observations, events, and the asynchronous loop.
- P1: Domain Manifest/Runtime, capability-first resolution, policy allow/confirm/deny, evaluator
  boundaries, context compilation, and a read-only Kubernetes Domain skeleton.
- P2: session-local World Model, Evidence provenance, deterministic dynamic Task expansion, relevant
  World/Evidence context, and bounded Recovery.

The Kubernetes Domain uses an injected backend. Tests and examples use fake backends; no real cluster
is accessed, no `kubectl` command is executed, and no mutation capability is exposed. Memory,
multi-domain operation, cross-domain World Model, persistent databases, packaging, marketplace
behavior, and real Kubernetes remediation remain intentionally outside P2.

## Development

Python 3.12 or newer is required.

```bash
python -m pip install -e '.[dev]'
ruff format --check .
ruff check .
mypy
pytest
python examples/p0_agent_loop.py
python examples/p1_kubernetes_domain.py
python examples/p2_evidence_recovery.py
```
