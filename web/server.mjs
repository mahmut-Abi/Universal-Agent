#!/usr/bin/env node
/**
 * Universal Agent Web — standalone Node.js server (zero npm dependencies).
 *
 * Serves the multi-session chat UI from ./public and proxies /api/* to a
 * remote agentd Runtime API, injecting the bearer token server-side so the
 * browser never sees credentials and CORS is never an issue.
 *
 * Environment:
 *   PORT          listen port                     (default 8080)
 *   AGENTD_URL    agentd base URL, e.g. http://agentd:8765   (required)
 *   AGENTD_TOKEN  agentd bearer token             (optional)
 *
 * API surface (proxied):  /api/<agentd path>  →  <AGENTD_URL>/<agentd path>
 *   e.g. /api/v1/sessions → http://agentd:8765/v1/sessions
 *
 * Requires Node.js >= 20 (global fetch + web streams).
 */

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { fileURLToPath } from "node:url";

const PORT = Number(process.env.PORT || 8080);
const AGENTD_URL = (process.env.AGENTD_URL || "").replace(/\/+$/, "");
const AGENTD_TOKEN = process.env.AGENTD_TOKEN || "";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = path.join(HERE, "public");

const STATIC_FILES = {
  "/": "index.html",
  "/index.html": "index.html",
  "/app.js": "app.js",
  "/style.css": "style.css",
};

const CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
};

// Hop-by-hop / transport headers that must not be proxied verbatim.
const HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
  "accept-encoding",
]);

function json(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(body);
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks);
}

function proxyTarget(pathname, search) {
  // /api/<agentd path> -> <AGENTD_URL>/<agentd path>
  return AGENTD_URL + pathname.replace(/^\/api/, "") + (search || "");
}

async function proxy(req, res, url) {
  if (!AGENTD_URL) {
    json(res, 503, {
      error: {
        code: "web_not_configured",
        message: "AGENTD_URL is not configured for this web deployment",
      },
    });
    return;
  }
  const target = proxyTarget(url.pathname, url.search);
  const headers = {};
  for (const [key, value] of Object.entries(req.headers)) {
    if (!HOP_HEADERS.has(key.toLowerCase())) headers[key] = value;
  }
  if (AGENTD_TOKEN) headers.authorization = `Bearer ${AGENTD_TOKEN}`;

  let body;
  if (req.method !== "GET" && req.method !== "HEAD") {
    body = await readBody(req);
  }

  let upstream;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body,
      redirect: "manual",
    });
  } catch (error) {
    json(res, 502, {
      error: {
        code: "agentd_unreachable",
        message: `agentd at ${AGENTD_URL} is unreachable: ${error.message}`,
      },
    });
    return;
  }

  const outHeaders = {};
  for (const [key, value] of upstream.headers) {
    if (!HOP_HEADERS.has(key.toLowerCase())) outHeaders[key] = value;
  }
  res.writeHead(upstream.status, outHeaders);
  if (upstream.body) {
    Readable.fromWeb(upstream.body).pipe(res);
  } else {
    res.end();
  }
}

async function serveStatic(res, pathname) {
  const relative = STATIC_FILES[pathname];
  if (!relative) {
    json(res, 404, { error: { code: "not_found", message: `unknown path: ${pathname}` } });
    return;
  }
  try {
    const body = await readFile(path.join(PUBLIC_DIR, relative));
    res.writeHead(200, {
      "content-type": CONTENT_TYPES[path.extname(relative)] || "application/octet-stream",
    });
    res.end(body);
  } catch (error) {
    json(res, 500, {
      error: { code: "web_internal", message: `failed to read ${relative}: ${error.message}` },
    });
  }
}

const server = createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  if (url.pathname === "/api/config") {
    json(res, 200, { server_configured: Boolean(AGENTD_URL), agentd_url: AGENTD_URL || null });
    return;
  }
  if (url.pathname === "/api/health") {
    json(res, 200, { status: "ok", service: "universal-agent-web" });
    return;
  }
  if (url.pathname === "/api/" || url.pathname.startsWith("/api/")) {
    proxy(req, res, url).catch((error) => {
      json(res, 500, { error: { code: "web_internal", message: String(error) } });
    });
    return;
  }
  serveStatic(res, url.pathname).catch((error) => {
    json(res, 500, { error: { code: "web_internal", message: String(error) } });
  });
});

server.listen(PORT, () => {
  console.log(`universal-agent-web listening on http://0.0.0.0:${PORT}`);
  console.log(`  agentd: ${AGENTD_URL || "(not configured — set AGENTD_URL)"}`);
});
