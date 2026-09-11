# Security Production Decisions

> Status: initial production-decision register  
> Last updated: 2026-09-11

This document records security and deployment choices that must be explicit
before Universal-Agent becomes an enterprise or multi-user control plane. It is
not an implementation plan for new production infrastructure; deferred decisions
remain blockers for the corresponding production backlog items.

## Decision Register

| Area | Current status | Selected/default decision | Deferred production decision |
| --- | --- | --- | --- |
| Identity provider | Deferred | Local CLI and loopback `agentd` are single-user by default. Non-loopback `agentd` requires bearer-token configuration. | Choose enterprise IdP/OIDC provider, token issuer, claim mapping, and service-account model. |
| Tenant model | Deferred | Local runtime has one implicit tenant per process/store. Session IDs are not a tenant boundary. | Define tenant identifier, store partitioning, cross-tenant denial rules, and migration semantics. |
| Authorization model | Deferred | Runtime Policy owns action safety; bearer-token auth only gates HTTP access. Domain capability Policy is not user RBAC. | Define user/role/capability authorization, admin/operator/read-only scopes, and audit-visible denial reasons. |
| Secret provider | Deferred | Runtime supports env/file secret references and never persists resolved values. | Select KMS/Vault/secret-manager backend, rotation behavior, access audit, and local-development fallback. |
| Audit storage | Deferred | Local audit/hash-chain projections are useful for development and live drills. | Select durable/tamper-resistant audit storage, retention, export/query model, and integrity verification process. |
| Package trust | Deferred | Domain package install/activation is local metadata validation and should not be trusted for external code by default. | Define signed package acquisition, trust roots, dependency install policy, sandboxing, and revocation. |

## Guardrails

- Do not implement production AuthN/AuthZ, tenancy, KMS/Vault, audit storage, or
  package-trust backends until the corresponding decision is moved out of
  `Deferred`.
- Runtime Policy remains required even after user authorization is added; user
  RBAC decides who may request a capability, while Policy decides whether the
  action is safe in context.
- Secret values must remain absent from configs, logs, events, sessions, doctor
  output, live artifacts, and error output.
- Any future enterprise mode must keep Goal, Task, Decision, Action,
  Observation, Evidence, Policy, Evaluation, Session, Domain, and Profile
  ownership aligned with `docs/RUNTIME_CONTRACT.md`.

## Backlog Mapping

- `UA-PROD-003` is blocked on Identity provider, Tenant model, and
  Authorization model decisions.
- `UA-PROD-004` is blocked on Secret provider selection and rotation semantics.
- `UA-PROD-005` is blocked on Audit storage selection.
- `UA-PROD-007` is blocked on Package trust policy.
