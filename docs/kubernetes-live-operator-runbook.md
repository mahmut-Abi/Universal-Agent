# Kubernetes Live Operator Runbook

Status: first live-cluster runbook for the Kubernetes production operator path.

This runbook is for the first practical production-style flow:

```text
OpenAI-compatible Chat Completions model
  + Kubernetes domain backend
  + scoped model probe
  + Kubernetes preflight
  + runtime-owned remediation loop
  + explicit production confirmation
```

The commands below use the local module entry point so they work from a checked
out repository. Replace placeholder values before running them against a real
cluster.

## 1. Prepare Environment

Install the editable package with development dependencies:

```bash
.venv/bin/python -m pip install -e '.[dev]'
```

Set the model credential in the shell. The profile stores only the secret
reference name and environment key, not the secret value.

```bash
export OPENAI_API_KEY='<openai-compatible-api-key>'
```

If using the Kubernetes HTTP API backend instead of `kubectl`, also set a
Kubernetes bearer token:

```bash
export KUBERNETES_API_TOKEN='<kubernetes-bearer-token>'
```

## 2. Create A Production Profile

For a `kubectl` backed run:

```bash
.venv/bin/python -m universal_agent.cli init \
  --output .universal-agent/kubernetes-production-profile.json \
  --profile production-operator \
  --environment production \
  --store-backend file \
  --store-path .universal-agent/runtime-store \
  --domain-backend kubectl \
  --kubectl-namespace prod \
  --kubectl-context prod-cluster \
  --model-provider openai_chat_completions \
  --model-name '<model-name>' \
  --model-endpoint '<https://provider.example/v1/chat/completions>' \
  --model-api-key-env OPENAI_API_KEY \
  --model-api-key-secret openai_api_key \
  --model-response-format prompt_json \
  --force
```

Notes:

- Omit `--model-endpoint` when using the default OpenAI Chat Completions
  endpoint.
- For OpenAI-compatible providers, `--model-endpoint` may be either the provider
  base URL or the full `/v1/chat/completions` URL.
- Use `--model-response-format json_schema` when the provider supports Chat
  Completions JSON Schema response format.
- Use `--model-response-format json_object` when the provider supports JSON mode
  but not schemas.
- Use `--model-response-format prompt_json` for legacy OpenAI-compatible
  providers that reject `response_format`.

For a direct Kubernetes HTTP API backend:

```bash
.venv/bin/python -m universal_agent.cli init \
  --output .universal-agent/kubernetes-api-production-profile.json \
  --profile production-operator \
  --environment production \
  --store-backend file \
  --store-path .universal-agent/runtime-store \
  --domain-backend kubernetes_api \
  --kubernetes-api-server '<https://cluster.example>' \
  --kubernetes-api-namespace prod \
  --kubernetes-api-token-env KUBERNETES_API_TOKEN \
  --model-provider openai_chat_completions \
  --model-name '<model-name>' \
  --model-api-key-env OPENAI_API_KEY \
  --model-response-format prompt_json \
  --force
```

## 3. Verify Configuration Projection

Confirm the Runtime can read the profile and that secrets are available without
printing secret values:

```bash
.venv/bin/python -m universal_agent.cli \
  --profile-config .universal-agent/kubernetes-production-profile.json \
  config show
```

Expected:

- `environment.environment` is `production`.
- Kubernetes domain backend is `kubectl` or `kubernetes_api`.
- model provider is `openai_chat_completions`.
- secret availability is `available`.
- secret values are not printed.

## 4. Probe The Model Contract

Run the model-only gate first. This contacts the model provider but executes no
Kubernetes tool.

```bash
.venv/bin/python -m universal_agent.cli \
  --profile-config .universal-agent/kubernetes-production-profile.json \
  kubernetes model-probe production-operator \
  --workload deployment/api \
  --namespace prod
```

Expected:

- status is `ok`.
- returned Decision type is `execute`.
- capability is `inspect_workload`.
- target is `deployment/api`.
- arguments include `name=api` and `namespace=prod`.

If this fails, fix the model endpoint, API key, response format, or provider
prompt behavior before touching the cluster.

## 4.1 Optional Live Test Gate

The repository includes opt-in live tests that are skipped unless the live
environment variables are set. Use them when wiring a real model endpoint and
cluster profile into local CI:

```bash
export UNIVERSAL_AGENT_LIVE_KUBERNETES_PROFILE=.universal-agent/kubernetes-production-profile.json
export UNIVERSAL_AGENT_LIVE_KUBERNETES_PROFILE_NAME=production-operator
export UNIVERSAL_AGENT_LIVE_KUBERNETES_WORKLOAD=deployment/api
export UNIVERSAL_AGENT_LIVE_KUBERNETES_NAMESPACE=prod
export UNIVERSAL_AGENT_LIVE_KUBERNETES_ARTIFACT_DIR=.universal-agent/live-contract-artifacts
.venv/bin/python -m pytest tests/live/test_kubernetes_live_operator.py -q
```

This gate runs `kubernetes check` and requires `contract.status=ok`. Set
`UNIVERSAL_AGENT_LIVE_KUBERNETES_RUN=true` only when intentionally submitting
the Runtime-owned remediation goal against the live profile.

When `UNIVERSAL_AGENT_LIVE_KUBERNETES_ARTIFACT_DIR` is set, the live tests write
redacted JSON artifacts for `check` and, when enabled, `run`. The artifact writer
uses the shared runtime secret scanner and refuses to write any artifact that
still contains unredacted secret-shaped fields.

## 4.2 GitHub Gated Live Contract

The CI workflow includes a disabled-by-default `kubernetes-live-contract` job.
Enable it only for trusted `main` branch pushes after provisioning an approved
cluster target and scoped credentials.

Required repository variable:

- `UNIVERSAL_AGENT_LIVE_KUBERNETES_ENABLED=true`
- `UNIVERSAL_AGENT_LIVE_KUBERNETES_WORKLOAD=deployment/api`

Optional repository variables:

- `UNIVERSAL_AGENT_LIVE_KUBERNETES_PROFILE_NAME=production-operator`
- `UNIVERSAL_AGENT_LIVE_KUBERNETES_NAMESPACE=prod`
- `UNIVERSAL_AGENT_LIVE_KUBERNETES_RUN=true`

Required repository secret:

- `UNIVERSAL_AGENT_LIVE_KUBERNETES_PROFILE_B64`: base64-encoded profile JSON
  whose secret references point at CI environment secret names.

Provider and cluster secrets depend on the chosen profile backend:

- `OPENAI_API_KEY` for the OpenAI-compatible model provider.
- `KUBERNETES_API_TOKEN` for the direct Kubernetes API backend.
- `UNIVERSAL_AGENT_LIVE_KUBECONFIG_B64` for kubectl-backed profiles that need a
  kubeconfig file.

The job uploads only files under
`.universal-agent/live-contract/artifacts`. Those files are produced by the
live test harness after redaction and secret scanning.

## 5. Run The Pre-Run Gate

Run the combined production gate. It runs model probe first, then read-only
Kubernetes preflight only if the model contract is valid.

```bash
.venv/bin/python -m universal_agent.cli \
  --profile-config .universal-agent/kubernetes-production-profile.json \
  kubernetes check production-operator \
  --workload deployment/api \
  --namespace prod
```

Expected:

- `model_probe.status` is `ok`.
- `preflight.status` is `ok`.
- `contract.status` is `ok` for a fully ready real backend, or `attention` if a
  non-blocking warning such as the fake backend is present.
- `preflight.observations.workload_inspection.pods` is present when the target
  workload exposes selector labels and the Kubernetes identity can list Pods.
- `next_step.type` is `run_kubernetes_remediation`.

Use `--skip-cluster` only when validating profile/model shape without touching a
cluster.

## 6. Run The Operator Flow

Run the scoped remediation goal. By default, this repeats model probe and
Kubernetes preflight before submitting a Runtime session.

```bash
.venv/bin/python -m universal_agent.cli \
  --profile-config .universal-agent/kubernetes-production-profile.json \
  kubernetes run production-operator \
  --workload deployment/api \
  --namespace prod
```

Expected successful no-mutation run:

- top-level `status` is `completed`.
- `model_probe.status` is `ok`.
- `preflight.status` is `ok`.
- `run.result.status` is `completed`.
- `contract.checks.completion_verification.status` is `ok`.
- session evidence and world state include fresh workload health.

Expected production mutation run:

- top-level `status` is `waiting`.
- `run.session.pending_action.capability` is `scale_workload`.
- `contract.checks.confirmation_boundary.status` is `ok`.
- `contract.checks.completion_verification.status` is `skipped` until the
  mutation is explicitly confirmed and re-verified.
- `next_step.type` is `confirm_pending_action`.
- no mutation has executed yet.

Use `--skip-model-probe` only when reusing a model endpoint that just passed
`kubernetes check`; Kubernetes preflight still runs. Use `--skip-preflight` only
for an intentional emergency bypass of all pre-run checks.

## 7. Confirm A Production Mutation

Before confirming, inspect the pending action from the `run` output. Confirm
only if the target resource, namespace, replica count, and policy reason are
acceptable.

```bash
.venv/bin/python -m universal_agent.cli \
  --profile-config .universal-agent/kubernetes-production-profile.json \
  session resume <session-id> \
  --confirmed true
```

After confirmation, the Runtime re-checks policy, executes the action, observes
fresh state, updates evidence/world state, and evaluates completion. A successful
`kubectl scale` or Kubernetes API patch is not considered sufficient by itself.

## 8. Inspect Results

Use these commands after a completed, waiting, or failed run:

```bash
.venv/bin/python -m universal_agent.cli \
  --profile-config .universal-agent/kubernetes-production-profile.json \
  session show <session-id>

.venv/bin/python -m universal_agent.cli \
  --profile-config .universal-agent/kubernetes-production-profile.json \
  session diagnostics <session-id>

.venv/bin/python -m universal_agent.cli \
  --profile-config .universal-agent/kubernetes-production-profile.json \
  session evidence <session-id>

.venv/bin/python -m universal_agent.cli \
  --profile-config .universal-agent/kubernetes-production-profile.json \
  session world <session-id>

.venv/bin/python -m universal_agent.cli \
  --profile-config .universal-agent/kubernetes-production-profile.json \
  session events <session-id> --limit 50
```

## Failure Guide

| Failure | Meaning | Next action |
| --- | --- | --- |
| `model_probe.status=failed` | Provider call, credentials, response format, or scoped Decision validation failed | Fix model config before cluster access |
| `preflight.status=failed` | Runtime config, secret availability, capability set, cluster, or workload inspection failed | Fix profile or Kubernetes access |
| `status=waiting` | Production mutation requires confirmation | Inspect pending action and resume with confirmation if safe |
| `run.result.status=failed` with `policy_denied` | Deterministic policy rejected the action | Inspect diagnostics; do not bypass policy without code review |
| `run.result.status=failed` with `timeout` or `tool_failure` | Backend command/API failed | Inspect events and backend error before retrying |
| completed mutation but unhealthy verification | Tool mutation ran, but evaluator did not verify health | Continue diagnosis from session diagnostics/evidence/world |

## Safety Rules

- Never paste API keys or Kubernetes bearer tokens into profile JSON.
- Treat `--skip-preflight` as an explicit emergency bypass.
- Do not confirm production mutations without reading `pending_action`.
- Do not treat a successful mutation receipt as completion; completion requires
  fresh verification evidence.
- Keep workload and namespace scoped in every production command.
