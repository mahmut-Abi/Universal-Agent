# Kubernetes Production Readiness Plan

Status: active implementation slice as of 2026-08-26.

## Decision

Development order is narrowed to a production-usable Kubernetes domain flow before
more Web, TUI, ecosystem, distributed, or optional multi-agent work.

The immediate operator path is:

```text
profile config
  -> scoped model probe
  -> Kubernetes preflight
  -> runtime-owned remediation run
  -> deterministic policy gate
  -> explicit production confirmation when required
  -> fresh verification evidence
```

This slice treats the model probe as a production gate, not just a connectivity
check. A provider must return a useful first Kubernetes inspection Decision for
the requested workload scope before the Runtime touches the cluster.

## Problem

The Runtime already supports OpenAI-compatible Chat Completions, Kubernetes
preflight, scoped remediation goals, policy-gated scale mutations, confirmation,
evidence, world updates, and session reads. The remaining practical risk is
operator flow quality:

- `kubernetes run` can begin after cluster preflight without proving that the
  configured model can return a scoped Kubernetes Decision.
- a model can return a syntactically valid Decision that points at the wrong
  workload or namespace during a pre-run probe.
- a model can return an immediate mutation or finish Decision during probe,
  which validates the generic schema but is not an acceptable first operator
  action.

## Scope

This slice adds:

- scoped model probe validation for the Kubernetes production operator flow;
- `kubernetes run` execution order of model probe before Kubernetes preflight by
  default;
- a `--skip-model-probe` escape hatch for operators who intentionally want only
  cluster preflight before the run;
- run output that includes the model probe report when it is executed;
- tests proving out-of-scope probe Decisions stop before Kubernetes preflight or
  runtime submission;
- operator guide updates that document the production gate sequence.

## Non-Scope

This slice does not add:

- unattended production mutation confirmation;
- model-owned tool calls;
- model-owned state, completion, or policy;
- new multi-agent orchestration;
- new Web or TUI requirements;
- Kubernetes deployment manifests for the Runtime itself.

## Contract

For `kubernetes model-probe`, `kubernetes check`, and default `kubernetes run`:

- the probe Decision must be `execute`;
- the capability must be `inspect_workload`;
- arguments must satisfy the capability argument contract;
- the target, when present, must match the requested workload resource;
- the `name` argument must identify the requested workload resource;
- when the operator provides `--namespace`, the Decision must include that same
  namespace;
- mutation, finish, wait, ask_user, unavailable capability, malformed arguments,
  out-of-scope target, or out-of-scope namespace all fail before any Kubernetes
  backend action.

Runtime policy remains authoritative during the actual run. Probe validation is
an earlier operator safety gate, not a replacement for deterministic policy.

## Plan

1. Add scoped Kubernetes model-probe validation.
2. Make `kubernetes run` execute model probe before preflight by default.
3. Preserve explicit `--skip-preflight` behavior and add `--skip-model-probe`
   for operators who intentionally bypass only the model probe.
4. Include model probe status in run output for traceability.
5. Add integration tests for scoped probe failure and default run ordering.
6. Update operator documentation and examples.
7. Run focused CLI/Kubernetes/model tests, then the broader test suite if the
   focused set is clean.

## Acceptance Criteria

- `kubernetes model-probe` rejects a valid JSON Decision if it is not an
  `inspect_workload` Decision scoped to the requested workload and namespace.
- `kubernetes check` stops before preflight when scoped model probe validation
  fails.
- default `kubernetes run` stops before preflight and before Runtime session
  submission when scoped model probe validation fails.
- default `kubernetes run` includes `model_probe` in its JSON output.
- `kubernetes run --skip-model-probe` keeps the previous preflight-first path.
- `kubernetes run --skip-preflight` still skips all pre-run checks explicitly.
