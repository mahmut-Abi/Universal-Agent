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

import { createHmac } from "node:crypto";

const PORT = Number(process.env.PORT || 8080);
const AGENTD_URL = (process.env.AGENTD_URL || "").replace(/\/+$/, "");
const AGENTD_TOKEN = process.env.AGENTD_TOKEN || "";
// Optional login gate: when set, browsers must authenticate once; the
// derived session cookie authorizes all subsequent requests.
const WEB_PASSWORD = process.env.WEB_PASSWORD || "";

function sessionCookieValue() {
  return createHmac("sha256", "universal-agent-web")
    .update(WEB_PASSWORD)
    .digest("hex");
}

function isAuthorized(req) {
  if (!WEB_PASSWORD) return true;
  const cookies = req.headers.cookie || "";
  const match = cookies
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith("ua_web_session="));
  return Boolean(match && match.split("=")[1] === sessionCookieValue());
}

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DIST_DIR = path.join(HERE, "dist");

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
  if (AGENTD_TOKEN && !headers.authorization) {
    // Inject the server-side token only when the browser did not send its
    // own: per-user credentials (admin plane / RBAC) must reach agentd
    // verbatim, while deployments without browser-side auth still work.
    headers.authorization = `Bearer ${AGENTD_TOKEN}`;
  }

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
  // Serves the Vue-built dashboard bundle from ./dist (Vite output).
  // `/` renders the SPA entry; other paths resolve inside dist with a
  // traversal guard; unknown paths fall back to the SPA entry.
  const rel =
    pathname === "/" || pathname === "/index.html"
      ? "index.html"
      : pathname.slice(1);
  const resolved = path.resolve(DIST_DIR, rel);
  if (!resolved.startsWith(DIST_DIR + path.sep) && resolved !== DIST_DIR) {
    json(res, 404, {
      error: { code: "not_found", message: `unknown path: ${pathname}` },
    });
    return;
  }
  try {
    const body = await readFile(resolved);
    res.writeHead(200, {
      "content-type":
        CONTENT_TYPES[path.extname(resolved)] || "application/octet-stream",
    });
    res.end(body);
  } catch {
    // SPA fallback: client-side navigations and unknown paths get the entry.
    try {
      const body = await readFile(path.join(DIST_DIR, "index.html"));
      res.writeHead(200, { "content-type": CONTENT_TYPES[".html"] });
      res.end(body);
    } catch {
      json(res, 404, {
        error: { code: "not_found", message: `unknown path: ${pathname}` },
      });
    }
  }
}

function handleLogin(req, res) {
  if (req.method === "POST") {
    void readBody(req).then((body) => {
      const params = new URLSearchParams(body.toString("utf8"));
      if (params.get("password") === WEB_PASSWORD) {
        res.writeHead(303, {
          location: "/",
          "set-cookie": `ua_web_session=${sessionCookieValue()}; HttpOnly; SameSite=Strict; Path=/`,
        });
        res.end();
        return;
      }
      res.writeHead(303, { location: "/login?error=1" });
      res.end();
    });
    return;
  }
  // Inline login page (the UI itself is the Vue SPA; only this gate is
  // server-rendered).
  const page = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Universal-Agent 登录</title><style>body{font-family:system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;background:#f5f5f7}form{background:#fff;border:1px solid #d2d2d7;border-radius:12px;padding:32px;width:280px}input{width:100%;box-sizing:border-box;padding:9px 12px;border:1px solid #d2d2d7;border-radius:8px;margin-top:6px}button{width:100%;padding:10px;border:0;border-radius:8px;background:#0071e3;color:#fff;font:inherit;margin-top:14px;cursor:pointer}.err{color:#ff3b30;font-size:13px}</style></head><body><form method="POST"><h2 style="margin:0 0 12px;font-size:16px">Universal-Agent</h2>${req.url.includes("error=1") ? '<p class="err">密码错误，请重试</p>' : ""}<input type="password" name="password" placeholder="访问密码" autofocus><button>登录</button></form></body></html>`;
  res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  res.end(page);
}

const server = createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  if (WEB_PASSWORD && !isAuthorized(req)) {
    if (url.pathname === "/login") {
      handleLogin(req, res);
      return;
    }
    if (url.pathname.startsWith("/api/")) {
      json(res, 401, {
        error: { code: "unauthorized", message: "login required" },
      });
      return;
    }
    res.writeHead(303, { location: "/login" });
    res.end();
    return;
  }
  if (url.pathname === "/login") {
    // Already authorized: skip the login page.
    res.writeHead(303, { location: "/" });
    res.end();
    return;
  }
  if (url.pathname === "/api/config") {
    json(res, 200, {
      server_configured: Boolean(AGENTD_URL),
      agentd_url: AGENTD_URL || null,
    });
    return;
  }
  if (url.pathname === "/api/health") {
    json(res, 200, { status: "ok", service: "universal-agent-web" });
    return;
  }
  if (url.pathname === "/api/" || url.pathname.startsWith("/api/")) {
    proxy(req, res, url).catch((error) => {
      json(res, 500, {
        error: { code: "web_internal", message: String(error) },
      });
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
  console.log(
    `  login gate: ${WEB_PASSWORD ? "enabled (WEB_PASSWORD)" : "disabled"}`,
  );
});
