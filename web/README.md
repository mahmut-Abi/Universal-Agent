# Universal Agent Web

Vue 3 dashboard (multi-view agent console) plus a Node.js static/API proxy
server for a remote agentd Runtime.

This is a separate deployment tier: it talks to agentd **only over its HTTP
API** and is never embedded in the agentd process.

## Architecture

```text
browser ──HTTP──▶ web (this app) ──HTTP + Bearer token──▶ agentd API
```

- `src/` + `index.html` + `vite.config.js` + `styles.css` — Vue 3 SPA
  (Vite build). Built output goes to `dist/`.
- `server.mjs` — zero-dependency Node server: serves `dist/` and proxies
  `/api/*` to agentd, injecting the bearer token **server-side** so the
  browser never sees credentials. SSE streams (`/v1/sessions/{id}/events/stream`)
  are proxied live.
- Optional login gate: set `WEB_PASSWORD` to require a password (server-
  rendered login page, signed short-lived session cookie). The server **fails
  closed**: it refuses to boot without a password unless `ALLOW_NO_AUTH=1` is
  explicitly set (network-isolated deployments only).

## Configuration (environment)

| Variable            | Required | Description                                               |
| ------------------- | -------- | --------------------------------------------------------- |
| `AGENTD_URL`        | yes      | agentd base URL, e.g. `http://agentd:8765`                |
| `AGENTD_TOKEN`      | no       | agentd bearer token                                       |
| `PORT`              | no       | listen port (default `8080`)                              |
| `WEB_PASSWORD`      | no*      | enables the login gate when set (*required unless `ALLOW_NO_AUTH=1`) |
| `ALLOW_NO_AUTH`     | no       | set `1` to boot without a password (fail-closed is default) |
| `SESSION_TTL_MS`    | no       | login session lifetime ms (default 12h)                   |
| `COOKIE_SECURE`     | no       | set `0` to omit `Secure` on the cookie (plain HTTP only)  |
| `MAX_LOGIN_FAILURES`| no       | brute-force cap per IP per window (default 5)             |
| `LOGIN_WINDOW_MS`   | no       | reset window for login limit (default 15m)                |
| `MAX_BODY_BYTES`    | no       | max proxied request body (default 2 MiB)                  |
| `UPSTREAM_TIMEOUT_MS`| no      | agentd request timeout (default 300s — goal runs are synchronous and real-model rounds routinely exceed 30s) |

> **Security**: require HTTPS + `WEB_PASSWORD` (and set `COOKIE_SECURE=1`)
> for anything beyond local development. Unauthenticated runs are only
> permitted with `ALLOW_NO_AUTH=1` and should be network-isolated.

## Run locally (development)

```bash
npm install
npm run dev            # Vite dev server on :5173 (proxies /api → agentd)
```

## Run locally (production build)

```bash
npm install
npm run build          # outputs ./dist
AGENTD_URL=http://127.0.0.1:8765 WEB_PASSWORD=change-me node server.mjs
# open http://127.0.0.1:8080  (login with the WEB_PASSWORD)
```

## Run with Docker Compose

```bash
# local dev (host-local only, no auth) — open http://localhost:8080
AGENTD_AUTH_TOKEN=your-token docker compose up --build web

# production (require a login)
AGENTD_AUTH_TOKEN=your-token WEB_PASSWORD=change-me docker compose up --build web
```

The compose `web` tier defaults to `ALLOW_NO_AUTH=1` so plain `docker compose
up` keeps working; set `WEB_PASSWORD` for an authenticated control plane.

## UI

- **总览** — metrics, recent sessions, 7-day run chart
- **对话** — multi-session chat over the agentd session/event API
- **会话详情** — event timeline, evidence, confirmation banner, lifecycle controls
- **配置与运行时** — doctor checks, Profile CRUD, Domains/Tools, Policy preferences
- **运维中心** — eval, distributed cluster, memory, cost, logs/traces, K8s ops,
  ecosystem, audit, multi-agent topology, health

All data comes from the real agentd API via the `/api` proxy (or
`UA_API_BASE` when the SPA is served from a different origin).
