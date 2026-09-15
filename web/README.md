# Universal Agent Web

Standalone **multi-session chat web UI** for a remote agentd Runtime.

This is a separate deployment tier: it talks to agentd **only over its HTTP
API** and is never embedded in the agentd process.

## Architecture

```text
browser ──HTTP──▶ web (this app, Node.js) ──HTTP + Bearer token──▶ agentd API
```

- The web server proxies `/api/*` to the agentd Runtime API and injects the
  bearer token **server-side**, so the browser never sees credentials.
- SSE event streams (`/v1/sessions/{id}/events/stream`) are proxied as live
  streams — the chat transcript updates in real time.
- Zero npm dependencies: only the Node standard library.

## Configuration (environment)

| Variable       | Required | Description                              |
| -------------- | -------- | ---------------------------------------- |
| `AGENTD_URL`   | yes      | agentd base URL, e.g. `http://agentd:8765` |
| `AGENTD_TOKEN` | no       | agentd bearer token                      |
| `PORT`         | no       | listen port (default `8080`)             |

## Run locally

```bash
AGENTD_URL=http://127.0.0.1:8765 node server.mjs
# open http://127.0.0.1:8080
```

## Run with Docker Compose

```bash
AGENTD_AUTH_TOKEN=your-token docker compose up --build
# agentd: http://localhost:8765
# web:    http://localhost:8080
```

## UI

- **Multi-session sidebar** — every chat is one goal execution (Session);
  click to open, `＋ New chat` to start another.
- **Chat transcript** — the user goal renders as the user bubble; runtime
  events (decisions, actions, policy checks, evidence, failures) render as
  agent/system bubbles; updates arrive over SSE.
- **Confirmation banner** — when the Runtime pauses a session with
  `WAITING_FOR_CONFIRMATION`, the pending mutation is shown with
  Confirm/Reject actions (`POST .../resume` with `confirmed`).
- **Pause / Cancel** — session lifecycle controls.
