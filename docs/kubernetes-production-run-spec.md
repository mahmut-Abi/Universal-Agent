# Kubernetes Production Run Spec

Status: active implementation slice as of 2026-08-26.

## Decision

Development order is adjusted toward one production-usable Kubernetes flow before
additional UI, distributed runtime, ecosystem, or optional multi-agent work.

The target workflow is:

```text
profile config
  -> Kubernetes check
     -> model probe
     -> Kubernetes preflight
  -> Chat Completions decision loop
  -> Kubernetes inspection / diagnosis
  -> policy-gated remediation
  -> production confirmation if mutation is required
  -> fresh verification
  -> session evidence / world state
```

## Problem

The runtime already has Kubernetes capabilities, policy, evidence, world model,
persistence, API, CLI, preflight, and OpenAI-compatible Chat Completions support.
The practical production gap is that operators still have to assemble a generic
goal string and success criteria manually through `agent run`.

That makes first production use too easy to mis-shape:

- workload target and namespace can be underspecified;
- model-provider incompatibility may only appear after an operator starts a run;
- preflight can be skipped accidentally;
- production confirmation instructions are not obvious from the first run output;
- the generic command does not communicate that fresh health verification remains
  required after a successful mutation.

## Scope

This slice adds a Kubernetes-specific operator entry point:

```bash
python -m universal_agent.cli --profile-config profile.json \
  kubernetes run production-operator \
  --workload deployment/api \
  --namespace prod
```

The command must:

- support a model-only probe that calls the configured model once, validates the
  returned Decision, and executes no Kubernetes tools;
- require the model probe Decision to be a scoped read-only `inspect_workload`
  action for the requested workload and namespace;
- support a pre-run check that runs model probe before Kubernetes preflight and
  stops before cluster inspection if the model contract fails;
- run model probe and Kubernetes preflight before Runtime submission unless
  `--skip-preflight` is explicit;
- construct a Runtime-owned `Goal` with `healthy=true`, `resource=<workload>`,
  and optional `namespace=<namespace>` success criteria;
- construct a `Task` that names the workload and namespace;
- keep mutation authorization in deterministic policy code;
- deny `scale_workload` before tool execution if the model proposes a resource
  or namespace outside the requested workload scope;
- keep production mutations paused for explicit confirmation;
- return the normal runtime run body plus model probe, preflight, and a focused
  operator `next_step`;
- return a deterministic `contract` report that summarizes model probe,
  preflight, runtime submission, verification evidence, and confirmation
  boundary status for operator review;
- avoid storing or printing secret values.

## Non-Scope

This slice does not add:

- unattended production mutation confirmation;
- model-owned tool calls;
- model-owned state or completion;
- new multi-agent routing;
- a UI requirement;
- Kubernetes deployment of the runtime itself.

## Test Seams

Tests target these public interfaces:

- `OpenAIChatCompletionsModelAdapter.decide()` request/response contract;
- `prompt_json` Chat Completions mode for legacy-compatible providers that do
  not accept `response_format`;
- `RuntimeConfig.from_mapping()` model configuration parsing and validation;
- `RuntimeHost.build_configured_model_adapter()` host assembly with resolved secrets;
- `agent init` profile generation;
- `agent kubernetes model-probe` model-only contract validation;
- `agent kubernetes check` ordered model/preflight validation;
- `agent kubernetes run` operator command output and confirmation path.

## Implementation Plan

1. Preserve Chat Completions as a Runtime-owned `Decision` adapter, including
   `json_schema`, `json_object`, and explicit `prompt_json` modes for
   OpenAI-compatible providers.
2. Add `agent kubernetes run` as the first production-oriented command over the
   existing RuntimeService and Kubernetes Domain Runtime.
3. Add `agent kubernetes model-probe` so operators can validate model endpoint,
   credentials, response format, and Decision JSON before cluster inspection.
4. Add `agent kubernetes check` as the single production pre-run gate over model
   probe and Kubernetes preflight.
5. Run model probe and preflight before the runtime loop by default, and fail
   before goal submission if either gate fails.
6. Enforce requested workload scope in deterministic Kubernetes mutation policy.
7. Return an explicit `confirm_pending_action` next step when production policy
   pauses a remediation mutation.
8. Add a production contract report to `kubernetes check` and `kubernetes run`
   outputs so skipped gates, fake backends, failed preflight checks and pending
   confirmations are visible without reading the full session body.
9. Document the operator flow and keep offline examples so the path remains
   easy to verify without a live cluster.

## Acceptance Criteria

- A profile can be initialized for `openai_chat_completions` with
  `json_schema`, `json_object`, or `prompt_json` response format.
- A Kubernetes profile can run:

  ```bash
  python -m universal_agent.cli --profile-config profile.json \
    kubernetes model-probe production-operator \
    --workload deployment/api \
    --namespace prod
  ```

- Model probe returns a validated scoped `inspect_workload` Decision or a
  structured model failure without executing any Kubernetes backend action.
- A Kubernetes profile can run:

  ```bash
  python -m universal_agent.cli --profile-config profile.json \
    kubernetes check production-operator \
    --workload deployment/api \
    --namespace prod
  ```

- Check runs model probe first, skips preflight when the model contract fails,
  does not submit a Runtime session, and includes a `contract` report.
- A Kubernetes profile can run:

  ```bash
  python -m universal_agent.cli --profile-config profile.json \
    kubernetes run production-operator \
    --workload deployment/api \
    --namespace prod
  ```

- Failed model probe returns status `failed`, skips preflight, and submits no
  Runtime session; the `contract.status` is `failed`.
- Failed preflight returns status `failed` and no Runtime session is submitted.
- The runtime denies scoped Kubernetes mutations whose target resource or
  namespace differs from the `kubernetes run` request.
- Production `scale_workload` decisions return status `waiting` with a
  confirmation command instead of mutating immediately, while
  `contract.checks.confirmation_boundary` remains `ok`.
- Completed runs expose the normal session, evidence, world, and event surfaces
  through existing session commands, and `contract.checks.completion_verification`
  is `ok` only when the requested workload health criteria are present.
