# Live Kubernetes Operator Verification (2026-09-13)

Completes the P0/P3.7 live-proof TODO from
`docs/revision/2026-08-31-remaining-todo.md`: the LLM-driven production
operator path executed against a real cluster and a real model provider,
including diagnose -> confirmation -> safe remediation -> fresh verification.

## Environment

- Cluster: real kubectl context `kubernetes-admin@kubernetes` (kubectl v1.34.2).
- Target: `deployment/agent-demo-nginx` in namespace `agent-demo`.
- Model: `z-ai/glm-5.3-flash` via the OpenAI-compatible Chat Completions
  endpoint `https://api.360.cn/v1/chat/completions` (provider profile
  `openai_chat_completions`, response format `json_object`).
- Profile: `.universal-agent/live-contract/kubernetes-production-profile.json`
  (kubectl backend, file store, environment `production`). Secret material is
  injected through the `ZAI_API_KEY` environment variable and is never printed
  by `config show`.

## Fault injection and operator flow

1. Baseline captured: `deployment/agent-demo-nginx` at 1 desired replica.
2. Fault injected: `kubectl scale --replicas=0` (zero-replica unhealthy state).
3. `agent kubernetes run production-operator` (embedded agentd, real LLM):
   - Model probe produced a validated, workload-scoped inspection decision.
   - Kubernetes preflight passed with pod discovery.
   - The Runtime session diagnosed the workload and proposed the
     policy-gated mutation `scale_workload` (0 -> 1 replicas), then **paused at
     the confirmation boundary** (status `waiting`, pending action
     `scale_workload`) — the LLM could not execute the mutation itself.
4. Human approval: `agent session resume <session-id> --confirmed true`.
   - Policy re-checked the mutation after confirmation.
   - The tool executed the real scale operation (`resource_version` CAS).
   - A bounded availability wait held through the pod startup window.
   - Fresh verification matched all goal criteria:
     `workload health criteria satisfied`, goal `completed`.
5. Cluster end state: `agent-demo-nginx` back to `1/1` READY, pod `Running`.

## Evidence

- Session `session-b453a7f2-e018-4ad3-92ae-c08e32857155`: 52 evidence items,
  3 actions, terminal reason `workload health criteria satisfied`.
- Live pytest gate: `tests/live/test_kubernetes_live_operator.py` — 2 passed
  with `UNIVERSAL_AGENT_LIVE_KUBERNETES_RUN=true` (check contract + operator
  run reaching `completed`).
- Redacted contract artifacts (shared runtime secret scanner applied, key
  material re-verified absent): `docs/revision/live-contract-artifacts/`.

## Scope note

The mutation was executed with the operator's existing cluster-admin context.
A production-grade deployment should bind the agent to a scoped
ServiceAccount/namespace role; that hardening remains an operational follow-up,
not a runtime gap.
