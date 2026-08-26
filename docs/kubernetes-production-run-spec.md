# Kubernetes Production Run Spec

Status: active implementation slice as of 2026-08-26.

## Decision

Development order is adjusted toward one production-usable Kubernetes flow before
additional UI, distributed runtime, ecosystem, or optional multi-agent work.

The target workflow is:

```text
profile config
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

- run Kubernetes preflight first unless `--skip-preflight` is explicit;
- construct a Runtime-owned `Goal` with `healthy=true` success criteria;
- construct a `Task` that names the workload and namespace;
- keep mutation authorization in deterministic policy code;
- keep production mutations paused for explicit confirmation;
- return the normal runtime run body plus a focused operator `next_step`;
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
- `RuntimeConfig.from_mapping()` model configuration parsing and validation;
- `RuntimeHost.build_configured_model_adapter()` host assembly with resolved secrets;
- `agent init` profile generation;
- `agent kubernetes run` operator command output and confirmation path.

## Implementation Plan

1. Preserve Chat Completions as a Runtime-owned `Decision` adapter, including
   `json_schema` and `json_object` response formats for OpenAI-compatible providers.
2. Add `agent kubernetes run` as the first production-oriented command over the
   existing RuntimeService and Kubernetes Domain Runtime.
3. Run preflight before the runtime loop by default, and fail before goal
   submission if preflight checks fail.
4. Return an explicit `confirm_pending_action` next step when production policy
   pauses a remediation mutation.
5. Document the operator flow and keep an offline example so the path remains
   easy to verify without a live cluster.

## Acceptance Criteria

- A profile can be initialized for `openai_chat_completions` with either
  `json_schema` or `json_object` response format.
- A Kubernetes profile can run:

  ```bash
  python -m universal_agent.cli --profile-config profile.json \
    kubernetes run production-operator \
    --workload deployment/api \
    --namespace prod
  ```

- Failed preflight returns status `failed` and no Runtime session is submitted.
- Production `scale_workload` decisions return status `waiting` with a
  confirmation command instead of mutating immediately.
- Completed runs expose the normal session, evidence, world, and event surfaces
  through existing session commands.
