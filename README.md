# Universal-Agent

**Universal-Agent is a typed Agent Runtime: one universal Agent kernel + pluggable
Domain Runtimes, with runtime-owned state, policy-enforced actions, evidence-based
verification and persistent sessions.**

Why: most "agents" are a prompt loop. Universal-Agent makes the Runtime — not the
LLM — own state, control flow, policy, evidence and completion, so an Agent can
act on real infrastructure (Kubernetes today, more Domains later) reliably and
safely.

**The CLI is `agent`** (also installed as the short alias `ua`). Everything below
works with either name.

- **CLI** — run an Agent and inspect sessions from your terminal.
- **Web** — read-only observation/management UI over the same Runtime API (agentd
  `/console/*` pages). It is not a second Runtime.
- **agentd** — the long-running Runtime server for server/API/multi-user
  deployments. Personal use never needs it.

Architecture details live further down (after the Golden Path); the normative
design is [`universal-agent-runtime-domain-runtime-design.md`](universal-agent-runtime-domain-runtime-design.md)
and the core concept contract is [`docs/RUNTIME_CONTRACT.md`](docs/RUNTIME_CONTRACT.md).

## Quick Start (Golden Path)

Requires Python **3.12+**.

```bash
git clone <repo>
cd Universal-Agent

# 1. Install
uv sync

# 2. First-time setup — creates universal-agent/profile.json + config.json
uv run ua init

# 3. Check your environment/config/model/runtime (prints fixes when something is off)
uv run ua doctor

# 4. Run your first Agent task
uv run ua run "Analyze the demo workload"

# 5. Find your Session and see what happened
uv run ua session list
uv run ua session show <session-id>
```

That's the whole Golden Path: `install → init → doctor → run → session`.
No `agentd`, no web server, no worker, no scheduler and **no API key** are needed
for the default offline profile — it uses a deterministic built-in model
(`scripted` / `FakeModel`) and the fake Kubernetes backend, so you can verify
everything locally. Session state is persisted (file store) and survives restarts.

Status output of `run` looks like:

```text
Agent started
Session: session-7d1e…
Goal: Analyze the demo workload

Agent completed

Status: success
Session: session-7d1e…
Duration: 0.42s
Steps: 3
Tool calls: 2
Evidence: 4
```

## CLI

One main entry point: `agent` (= `ua`). Golden Path commands come first in
`agent --help`; everything else is an advanced/developer command and stays out of
the Golden Path.

```text
agent init        Create the local profile config (idempotent; --force resets with backups)
agent doctor      Environment/config/model/runtime/profiles/domains/policy checks + fixes
agent run GOAL    Run one goal; prints the session summary (creates a Session)
agent session     list | show | resume | cancel (+ events/evidence/diagnostics/…)
agent config      Show the active configuration (secrets are never printed)
agent profile     list | show — available Agent profiles

Advanced: serve, kubernetes, chat, tui, eval, ecosystem, distributed,
          domain-packages, memory, policies, evaluators, audit, repair, …
```

Common flags:

```bash
agent run "goal" --profile default          # explicit profile (positional profile also works)
agent run "goal" --output json              # machine-readable output (text is the default)
agent session list --output json
agent session show <id> --output json       # same payloads the API returns
agent doctor --output json --fail-on error  # exit 1 on failures (default for doctor)
agent --api-url http://host:8765 run "..."  # thin-client mode against a running agentd
```

## Configuration

`agent init` writes one small config tree (project-local by default):

```text
universal-agent/
  config.json     human-readable settings: profile, model, policy mode, runtime, domains
  profile.json    the AgentProfile the Runtime loads (model provider/name, store, secrets refs)
```

- Where is my **model** configured? → `profile.json` → `runtime.model`
  (`provider`, `name`, optional `api_key_secret` reference).
- Where is my **Profile** configured? → `profile.json` (name, domains, runtime).
- Where is my **Policy** configured? → Domain-declared `PolicyRule`s (enforced by
  the Runtime); `config.json` records the policy mode (`safe`).
- Where is my **Domain** configured? → `profile.json` → `domain` / `domains`
  (backend: `fake` | `kubectl` | `kubernetes_api` + settings).

Config discovery: `$AGENT_CONFIG_DIR/profile.json` (container convention) →
`./universal-agent/profile.json` (project) → `~/.universal-agent/profile.json`
(user home). `agent config show` prints the effective Runtime configuration as
JSON; **secret values are never written to config or printed** — only
environment/file secret *references* and whether they resolve.

Re-running `agent init` is safe (idempotent, `status=reused`); use `--force` to
reset (previous files are kept as `*.bak`).

Connecting a real model:

```bash
uv run ua init --force \
  --model-provider openai_chat_completions \
  --model-name gpt-4o-mini \
  --model-api-key-env OPENAI_API_KEY
```

Connecting a real cluster:

```bash
uv run ua init --force --domain-backend kubectl --kubectl-context my-cluster
```

## Profiles

A Profile is the Agent's work identity: which Domains it acts on, which model it
uses, which stores and limits the Runtime gets. `agent init` creates the generic
`default` profile; `local-kubernetes` remains for the Kubernetes operator flow.

```bash
agent profile list
agent profile show default
```

## Sessions

Every `agent run` creates a persistent Session — task graph, decisions, tool
calls, observations, evidence and evaluations. Sessions live in the configured
store (file-backed by default under the data dir, SQLite/memory optional via
`agent init --store-backend …`) and survive process restarts.

```bash
agent session list              # SESSION / STATUS / CREATED / GOAL
agent session show <id>         # status, goal, event timeline, evidence + action counts
agent session resume <id> --confirmed true   # approve a policy-held pending action
agent session cancel <id> --reason "…"
agent session events|evidence|world|diagnostics|audit|cost <id>   # advanced
```

A `waiting` session means the Runtime is holding a mutation for human
confirmation (Policy = REQUIRE_CONFIRMATION). Resume it with `--confirmed true`;
without confirmation the action never executes.

## Domains

A Domain is the world an Agent can operate on: ontology, capabilities, tools,
policies, evaluators. The first serious Domain is Kubernetes
(`inspect_workload`, `inspect_pod`, `inspect_logs`, policy-gated
`scale_workload`, health verification and recovery). A read-only Observability
(Prometheus) Domain and a Domain Package SDK for authoring more are included.
Domains compose inside one Runtime and one shared World Model — adding a Domain
never requires Kernel changes.

## Policy

Policy is enforced by the Runtime, not by prompts. Capabilities carry
category/risk metadata; `PolicyRule`s map them to ALLOW / REQUIRE_CONFIRMATION /
DENY. Mutations without an explicit allow rule are denied by default; a denied
action never reaches a tool; confirmation-required actions pause the Session
until a human resumes it. The LLM cannot bypass this boundary (see
`docs/RUNTIME_CONTRACT.md`, and `tests/integration/test_facade_golden_path.py`
for the executable proof).

## Web

The Web Console is a read-only observation/management UI served by `agentd`
(`/console`, `/console/sessions/{id}`, evidence/world explorers, doctor, …). It
talks to the same Runtime API as the CLI — it is not a separate Agent runtime.
Point your browser at an `agentd` deployment; the CLI remains the fastest way to
run Agents.

## Server / agentd

- **Personal use**: `agent run …` — an embedded Runtime is spawned per command;
  you never start a server by hand.
- **Server use**: run `agentd` (or `agent serve`) to expose the Runtime API
  (REST + SSE), the Web Console, pause/resume/cancel and multi-user access.
  The CLI can act as its thin client via `--api-url http://host:8765`. Container
  images and Kubernetes deployment examples: `docs/container-image.md`,
  `docker compose up --build`.

## Architecture

```text
Goal → Context → Decision(capability) → Capability Resolver → Policy → Tool
  → Observation → Evidence → World Model → Task Expansion → Evaluator → State
                         ^                                      |
                         +-------- bounded Recovery <-----------+
```

- The model proposes structured decisions; the Runtime owns state, control flow,
  policy, retries and completion. The LLM is a component, not the runtime.
- An Observation is not a fact until a Domain extractor turns it into Evidence;
  the World Model is replayed from Evidence; only Evaluators complete tasks/goals.
- Recovery is classified and budgeted and re-enters resolution + Policy.
- Concept-by-concept contract (who creates/calls, lifecycle, persistence,
  policy bypass): [`docs/RUNTIME_CONTRACT.md`](docs/RUNTIME_CONTRACT.md).
- Layered design target: `universal-agent-runtime-domain-runtime-design.md`.

## Development

Python 3.12+ with [uv](https://docs.astral.sh/uv/) (CI installs with pip via
`pip install -e '.[dev]'`; both work):

```bash
uv sync --extra dev
uv run ruff format --check src tests examples
uv run ruff check .
uv run mypy
uv run pytest -q
```

Key test suites for the Golden Path:

- `tests/integration/test_p0_golden_path.py` — init → config → doctor → run →
  session list/show end-to-end through the embedded runtime (no API keys).
- `tests/integration/test_facade_golden_path.py` — `Agent` facade, policy denial
  before tool execution, confirmation pause + rebuilt-runtime resume.
- `tests/unit/test_p0_config_and_views.py` — config discovery, `init`
  idempotency/backups, doctor rendering, human text views (secret masking).

Further reading: `docs/` (developer guide, operator guide, Domain SDK,
container image), `docs/product.md` (product vocabulary and entry-point
decisions), `examples/` (per-milestone runnable examples).

## Roadmap

Implemented layers and the remaining roadmap (P0 productization, P3.5+ runtime
productization, operations, evaluation, optional multi-agent, distributed runtime,
ecosystem) are tracked in `docs/revision/` and the design document.
