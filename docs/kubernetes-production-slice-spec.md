# Kubernetes Production Slice Spec

Status: implemented foundation for the current Kubernetes production path.

Follow-up production run entry point:
[`docs/kubernetes-production-run-spec.md`](kubernetes-production-run-spec.md).

## Problem

The runtime already has a tested Kubernetes remediation domain, kubectl/API backends,
policy-gated scale mutations, evidence, world model updates, and verification. The
current production blocker is that the direct OpenAI adapter only targets the
Responses API, while the intended deployment will use the older OpenAI-compatible
Chat Completions API shape.

The development order is therefore adjusted away from broad platform expansion and
toward one practical Kubernetes flow:

```text
Goal
  -> Chat Completions Decision
  -> Runtime validation
  -> Kubernetes capability
  -> Policy
  -> kubectl / Kubernetes API backend
  -> Observation
  -> Evidence / World
  -> Evaluator
  -> Continue / Confirm / Finish
```

## Scope

This slice adds:

- `openai_chat_completions` as a first-class model provider.
- A dependency-free `OpenAIChatCompletionsModelAdapter`.
- Runtime config and CLI profile generation for Chat Completions endpoints.
- `json_schema`, `json_object`, and explicit `prompt_json` modes so
  legacy-compatible providers can omit `response_format` while still returning
  locally validated Decision JSON.
- `agent kubernetes preflight` for read-only profile/backend/capability checks
  before running a remediation goal.
- Tests covering request shape, usage extraction, decision validation, config loading,
  host construction, CLI init, and Kubernetes preflight output.
- Operator docs and an example for a kubectl-backed Kubernetes remediation run.

This slice does not add:

- model-owned tool calls;
- model-owned runtime state;
- unattended production mutations;
- new multi-agent routing;
- new UI requirements.

## Runtime Contract

The Chat Completions adapter must return a runtime `Decision` only. It must not
expose OpenAI tools or function calls to the model. The prompt can describe
available runtime capabilities, but execution remains:

```text
Decision JSON
  -> local decode
  -> Decision.validate()
  -> capability and argument contract validation
  -> PolicyEngine
  -> ToolRuntime
```

If the provider returns a tool/function call finish reason, content filtering,
refusal, non-JSON content, or a decision outside the compiled context, the adapter
must fail before the runtime acts.

`prompt_json` is a compatibility mode for OpenAI-style Chat Completions providers
that reject the `response_format` request field. It changes only the outbound
provider request shape; the Runtime still decodes and validates a structured
`Decision` before policy or action execution.

## Kubernetes Safety Contract

For the initial production path:

- `kubectl` and `kubernetes_api` remain Domain-owned backend adapters.
- `scale_workload` remains policy-gated.
- `production` still requires confirmation before mutation.
- mutation receipts never satisfy the goal; fresh workload verification is required.
- mutation retries remain bounded and must not blindly retry uncertain side effects.

## Implementation Plan

1. Add the Chat Completions model adapter and tests.
2. Wire the adapter through `RuntimeConfig`, `RuntimeHost`, package exports, and CLI `init`.
3. Add explicit legacy `prompt_json` compatibility for providers without
   `response_format` support.
4. Add a Kubernetes Chat Completions example and operator guide updates.
5. Add a read-only Kubernetes preflight command for cluster/workload inspection.
6. Run focused tests and static checks.
7. Commit each feature node before moving to live-cluster runbooks or further Kubernetes workflows.

## Acceptance Criteria

- A profile can be generated with:

  ```bash
  python -m universal_agent.cli init \
    --domain-backend kubectl \
    --model-provider openai_chat_completions \
    --model-name <model> \
    --model-api-key-env OPENAI_API_KEY
  ```

- `RuntimeHost` can construct the adapter without storing the secret value in config.
- `prompt_json` Chat Completions profiles omit `response_format` from provider
  requests but still reject invalid Decision JSON before execution.
- Chat Completions responses decode `choices[0].message.content` into a validated
  runtime `Decision`.
- OpenAI token usage is projected through the existing `ModelUsage` path.
- Invalid model output is rejected before policy/action execution.
- A configured profile can be preflighted with:

  ```bash
  python -m universal_agent.cli --profile-config profile.json \
    kubernetes preflight --workload deployment/api --namespace prod
  ```

- Preflight performs only inspection capabilities and reports failed checks before
  any remediation goal is run.
