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
  rendered login page, session cookie).

## Configuration (environment)

| Variable       | Required | Description                                |
| -------------- | -------- | ------------------------------------------ |
| `AGENTD_URL`   | yes      | agentd base URL, e.g. `http://agentd:8765` |
| `AGENTD_TOKEN` | no       | agentd bearer token                        |
| `PORT`         | no       | listen port (default `8080`)               |
| `WEB_PASSWORD` | no       | enables the login gate when set            |

## Run locally (development)

```bash
npm install
npm run dev            # Vite dev server on :5173 (demo mock data)
```

## Run locally (production build)

```bash
npm install
npm run build          # outputs ./dist
AGENTD_URL=http://127.0.0.1:8765 node server.mjs
# open http://127.0.0.1:8080
```

## Run with Docker Compose

```bash
AGENTD_AUTH_TOKEN=your-token docker compose up --build web
# agentd: http://localhost:8765 · web: http://localhost:8080
```

## UI

- **总览** — metrics, recent sessions, 7-day run chart
- **对话** — multi-session chat over the agentd session/event API
- **会话详情** — event timeline, evidence, confirmation banner, lifecycle controls
- **配置与运行时** — doctor checks, Profile CRUD, Domains/Tools, Policy preferences
- **运维中心** — eval, distributed cluster, memory, cost, logs/traces, K8s ops,
  ecosystem, audit, multi-agent topology, health

Without `UA_API_BASE` the SPA runs on built-in demo mock data; set it (e.g.
`window.UA_API_BASE = '/api'` in `index.html`) to hit the proxied agentd API.
