# Config-Management API (write plane)

agentd's config-management write surface manages **persisted configuration**:
profile configs stored in the profiles directory (`--profiles-dir`, default
`$AGENT_CONFIG_DIR/profiles` or `./universal-agent/profiles`).

Key semantics:

- **Validation before persist** — every write validates the full profile
  config through the same parsing the runtime uses; the store can never hold
  an invalid profile. `POST /v1/config/validate` runs the same validation
  without persisting (保存前预检).
- **Runtime read models stay authoritative** — `GET /v1/profiles` and
  `GET /v1/profiles/{name}` return the profiles loaded into the *running*
  service. New or changed profile configs apply on restart/reload.
- **Audit** — every successful mutation appends a record (actor, action,
  resource, details) to the config audit log, exposed via
  `GET /v1/config/audit`. Action-audit records at `/v1/audit` are
  session/action-scoped and intentionally remain separate.
- **Actor** — mutations record the acting principal from the
  `X-Acting-Principal` header (default `api`). All routes require the agentd
  bearer token when auth is enabled.
- **Credentials** — profile configs reference secrets by environment-variable
  name only; secret values never appear in API payloads or the audit log.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v1/profiles` | Create a profile config (body = full profile document) |
| GET | `/v1/config/audit` | Config-change audit records (newest first, `?limit=`) |
| PATCH | `/v1/profiles/{name}` | Partial update (RFC 7386 JSON Merge Patch); name immutable |
| DELETE | `/v1/profiles/{name}` | Delete the stored profile config |
| PUT | `/v1/domains/{name}/profiles` | Bind/unbind a domain on profiles: `{"add": [...], "remove": [...], "settings": {...}}` |
| POST | `/v1/config/validate` | Dry-run: `{kind: "profile", payload}` → `{status}` / `{status, errors[]}` |

Errors: `409` duplicate create, `404` unknown profile, `400` with
`error.errors[]` (one structured entry per invalid field) on validation
failure.

Policies CRUD (`/v1/policies`) is deferred: policy rules are domain-authored
code today; a config-declared declarative-policy type needs a kernel design
decision first (tracked as the follow-up to UA-CS-005).

## Example

```bash
# Create
curl -X POST http://agentd:8765/v1/profiles \
  -H "authorization: Bearer $TOKEN" -H "x-acting-principal: alice" \
  -H "content-type: application/json" \
  -d @checkout-sre.profile.json

# Dry-run validation
curl -X POST http://agentd:8765/v1/config/validate \
  -H "authorization: Bearer $TOKEN" -H "content-type: application/json" \
  -d '{"kind": "profile", "payload": {...}}'

# Bind a domain
curl -X PUT http://agentd:8765/v1/domains/kubernetes/profiles \
  -H "authorization: Bearer $TOKEN" -H "content-type: application/json" \
  -d '{"add": ["checkout-sre"]}'

# Who changed what
curl http://agentd:8765/v1/config/audit -H "authorization: Bearer $TOKEN"
```
