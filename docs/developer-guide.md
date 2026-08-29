# Developer Guide

This guide describes the current local development workflow for Universal Agent
Runtime.

## Setup

Python 3.12 or newer is required. The repository convention is to use the local
virtual environment when present:

```bash
.venv/bin/python -m pip install -e '.[dev]'
```

## Quality Gates

Run these before committing meaningful changes:

```bash
.venv/bin/python -m ruff format --check src tests examples
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest -q
```

Targeted checks are acceptable while developing, but a feature node should pass
the relevant focused tests and the full static gates before commit.

The CI workflow in `.github/workflows/ci.yml` runs the same formatting, lint,
type-checking and test gates on pull requests and `main` pushes, plus a
container image build gate for the generic runtime Dockerfile.

## Development Rules

- Read `AGENTS.md` and the architecture design before changing runtime behavior.
- Keep Domain-specific behavior out of the Kernel.
- Prefer typed objects over loosely passed dictionaries for runtime contracts.
- Do not use model output as authoritative state, authorization or completion.
- Add tests through public seams such as `RuntimeAPI`, `RuntimeService`, CLI,
  agentd routes, Domain package registries or UI renderers.
- Keep examples as executable documentation when adding user-facing surfaces.

## Common Test Targets

```bash
.venv/bin/python -m pytest tests/integration/test_agent_runtime.py -q
.venv/bin/python -m pytest tests/integration/test_runtime_api.py -q
.venv/bin/python -m pytest tests/integration/test_agentd_routes.py -q
.venv/bin/python -m pytest tests/integration/test_cli.py -q
.venv/bin/python -m pytest tests/unit/test_operations.py -q
.venv/bin/python -m pytest tests/unit/test_web_console.py tests/unit/test_tui.py -q
.venv/bin/python -m pytest tests/unit/test_ecosystem_catalog.py -q
```

## Live Kubernetes Gate

Live Kubernetes tests are opt-in and skipped by default. They call the configured
OpenAI-compatible model provider and Kubernetes backend, so run them only against
a profile and workload that are safe to inspect:

```bash
export UNIVERSAL_AGENT_LIVE_KUBERNETES_PROFILE=.universal-agent/kubernetes-production-profile.json
export UNIVERSAL_AGENT_LIVE_KUBERNETES_PROFILE_NAME=production-operator
export UNIVERSAL_AGENT_LIVE_KUBERNETES_WORKLOAD=deployment/api
export UNIVERSAL_AGENT_LIVE_KUBERNETES_NAMESPACE=prod
.venv/bin/python -m pytest tests/live/test_kubernetes_live_operator.py -q
```

The default live test runs `kubernetes check` and requires
`contract.status=ok`. Set `UNIVERSAL_AGENT_LIVE_KUBERNETES_RUN=true` only when
you intentionally want the test to submit the runtime-owned remediation goal; a
production mutation must still stop at the explicit confirmation boundary.

## Executable Examples

Examples are organized by roadmap slice. Useful entry points:

```bash
.venv/bin/python examples/p0_agent_loop.py
.venv/bin/python examples/p1_kubernetes_domain.py
.venv/bin/python examples/p2_evidence_recovery.py
.venv/bin/python examples/p3_memory.py
.venv/bin/python examples/p3_2_kubernetes_remediation.py
.venv/bin/python examples/p3_5_runtime_api.py
.venv/bin/python examples/p3_5_runtime_service.py
.venv/bin/python examples/p3_5_runtime_sdk.py
.venv/bin/python examples/p3_5_openai_chat_completions_model.py
.venv/bin/python examples/p3_5_openai_chat_prompt_json_model.py
.venv/bin/python examples/p3_5_kubernetes_model_probe.py
.venv/bin/python examples/p3_5_kubernetes_check.py
.venv/bin/python examples/p3_5_kubernetes_production_run.py
.venv/bin/python examples/p3_5_agentd_routes.py
.venv/bin/python examples/p3_5_cli_event_stream.py
.venv/bin/python examples/p3_5_cli_config_validate.py
.venv/bin/python examples/p3_6_state_event_commit.py
.venv/bin/python examples/p3_6_secret_redaction.py
.venv/bin/python examples/p3_7_evaluation_runner.py
.venv/bin/python examples/p4_multi_agent_contract.py
.venv/bin/python examples/p5_evaluation_console.py
.venv/bin/python examples/p5_tui.py
.venv/bin/python examples/p5_web_console.py
.venv/bin/python examples/p6_distributed_worker.py
.venv/bin/python examples/p6_distributed_prune.py
.venv/bin/python examples/p7_domain_package_runtime_loader.py
.venv/bin/python examples/p7_domain_sdk_base_runtime.py
.venv/bin/python examples/p7_domain_sdk_runtime_spec.py
.venv/bin/python examples/p7_ecosystem_catalog.py
```

## Where To Put Changes

- Runtime loop semantics: `src/universal_agent/runtime/`.
- Application projections: `src/universal_agent/service/`.
- HTTP route behavior: `src/universal_agent/agentd/`.
- CLI commands: `src/universal_agent/cli.py`.
- Read-only Web UI: `src/universal_agent/web.py`.
- Read-only TUI: `src/universal_agent/tui.py`.
- Domain metadata/package behavior: `src/universal_agent/domain/` or
  `src/universal_agent/ecosystem/`.
- New Domain implementations can subclass `BaseDomainRuntime` or use
  `DomainRuntimeSpec` plus `build_domain_runtime` to derive manifest references
  from declared capabilities, tools and evaluators. Use
  `domain_package_scaffold_spec_from_runtime_spec` when package scaffold metadata
  should stay aligned with that runtime spec.
- Domain package runtime activation should go through `load_domain_package_runtime`.
  That seam imports the declared entrypoint only when explicitly called, validates
  the result through `DomainLoader`, and rejects package metadata drift. Registry
  install/discovery must remain metadata-only. Use
  `agent domain-packages load-runtime <path>` when the same check is needed from
  CLI/CI.
- Host-level configured activation should go through
  `RuntimeHost.from_configured_domain_packages` with
  `RuntimeConfig.domain_package_paths`. Domain package entrypoints that need
  backend settings or secrets should accept `DomainRuntimeLoadContext` and keep
  concrete Domain adapter construction inside the Domain package.
- Domain package resources should stay package-local; scaffold custom resource
  parents through `DomainPackageScaffoldSpec.resources` rather than writing
  paths outside the package root.
- Kubernetes-specific behavior: `src/universal_agent/domains/kubernetes/`.

Avoid broad refactors across these areas unless the feature requires a real
architectural seam change.

## Commit Practice

Commit meaningful feature nodes separately. A good node includes implementation,
tests, any affected examples, and concise documentation updates when the
behavior is user-facing.
