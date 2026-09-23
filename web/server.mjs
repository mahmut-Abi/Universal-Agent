#!/usr/bin/env node
/**
 * Universal Agent Web — standalone Node.js server (zero npm dependencies).
 *
 * Serves the multi-session chat UI from ./dist and proxies /api/* to a
 * remote agentd Runtime API, injecting the bearer token server-side so the
 * browser never sees credentials and CORS is never an issue.
 *
 * Environment:
 *   PORT                  listen port                     (default 8080)
 *   AGENTD_URL            agentd base URL, e.g. http://agentd:8765   (required)
 *   AGENTD_TOKEN          agentd bearer token             (optional)
 *   WEB_PASSWORD          enables the login gate when set
 *   ALLOW_NO_AUTH         set to 1 to boot WITHOUT a password (fail-closed
 *                         by default); only for network-isolated deployments
 *   SESSION_TTL_MS        login session lifetime ms        (default 12h)
 *   COOKIE_SECURE         set to 0 to omit Secure on the session cookie
 *                         (only for plain-HTTP deployments; prefer HTTPS)
 *   MAX_LOGIN_FAILURES    login brute-force cap per IP per window (default 5)
 *   LOGIN_WINDOW_MS       reset window for login limit    (default 15m)
 *   MAX_BODY_BYTES        max proxied request body bytes  (default 2 MiB)
 *   UPSTREAM_TIMEOUT_MS   agentd request timeout          (default 300s)
 *
 * API surface (proxied):  /api/<agentd path>  →  <AGENTD_URL>/<agentd path>
 *   e.g. /api/v1/sessions → http://agentd:8765/v1/sessions
 *
 * Requires Node.js >= 20 (global fetch, AbortSignal.timeout).
 */

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { fileURLToPath } from "node:url";

import {
  createHmac,
  randomBytes,
  scryptSync,
  timingSafeEqual,
} from "node:crypto";

const PORT = Number(process.env.PORT || 8080);
const AGENTD_URL = (process.env.AGENTD_URL || "").replace(/\/+$/, "");
const AGENTD_TOKEN = process.env.AGENTD_TOKEN || "";

/* ═══════════════════════════════════════════════════════════════════════
 * Authentication gate
 *   H1: fail-closed by default — refuse to boot unauthenticated unless
 *       ALLOW_NO_AUTH=1 is explicitly set.
 *   H2: per-boot random secret ⇒ session cookies are not reusable across
 *       restarts and cannot be forged by knowing only the password;
 *       short-lived TTL; password verified via a slow scrypt hash (no
 *       plainstring equality, resists offline guessing).
 *   M1: per-IP login-failure cap with a bounded in-memory table.
 * ═══════════════════════════════════════════════════════════════════════ */
const WEB_PASSWORD = process.env.WEB_PASSWORD || "";
const ALLOW_NO_AUTH =
  process.env.ALLOW_NO_AUTH === "1" ||
  (process.env.ALLOW_NO_AUTH || "").toLowerCase() === "true";
const SESSION_TTL_MS = Number(process.env.SESSION_TTL_MS || 12 * 60 * 60 * 1000);
// A `Secure` cookie is required to be safe on plain HTTP. Default off so the
// common local / compose (HTTP) deployment keeps working; production behind
// TLS must set COOKIE_SECURE=1 (see boot warning below).
const COOKIE_SECURE = process.env.COOKIE_SECURE !== "0";
const MAX_LOGIN_FAILURES = Number(process.env.MAX_LOGIN_FAILURES || 5);
const LOGIN_WINDOW_MS = Number(process.env.LOGIN_WINDOW_MS || 15 * 60 * 1000);

// M2 / L1: bounds for the proxy.
const MAX_BODY_BYTES = Number(process.env.MAX_BODY_BYTES || 2 * 1024 * 1024);
// 300s default matches the CLI long-run budget: POST /v1/sessions runs the
// goal synchronously and real-model rounds routinely exceed 30s
// (UA-LIVE-2026-09-21: 22-442s per goal). SSE tail relies on its own
// auto-reconnect, so long-lived streams survive this timeout too.
const UPSTREAM_TIMEOUT_MS = Number(process.env.UPSTREAM_TIMEOUT_MS || 300 * 1000);

if (!WEB_PASSWORD) {
  if (!ALLOW_NO_AUTH) {
    console.error(
      "[fatal] WEB_PASSWORD is not set. Refusing to start unauthenticated — " +
        "this server proxies the full agentd control plane",
    );
    console.error(
      "  → set WEB_PASSWORD (recommended), or explicitly set ALLOW_NO_AUTH=1 " +
        "only when network-isolated",
    );
    process.exit(1);
  }
  console.warn(
    "[warn] WEB_PASSWORD not set and ALLOW_NO_AUTH=1: exposing the agentd " +
      "control plane WITHOUT authentication. Ensure network isolation.",
  );
} else if (!COOKIE_SECURE) {
  console.warn(
    "[warn] COOKIE_SECURE=0: session cookie is NOT marked Secure. " +
      "Serve over HTTPS and set COOKIE_SECURE=1 in production.",
  );
}

// Random per-boot secret. Session cookies are signed with it, so a captured
// cookie becomes invalid on restart and cannot be forged without the secret.
const SESSION_SECRET = randomBytes(32);
// Slow hash of WEB_PASSWORD with a boot-random salt. Login compares run
// scrypt over the submitted password, so they are neither plaintext-equality
// nor offline-guessable against a captured cookie.
const LOGIN_SALT = randomBytes(16);
const LOGIN_HASH = WEB_PASSWORD
  ? scryptSync(WEB_PASSWORD, LOGIN_SALT, 64)
  : Buffer.alloc(0);

function verifyPassword(submitted) {
  const derived = scryptSync(String(submitted || ""), LOGIN_SALT, 64);
  return timingSafeEqual(derived, LOGIN_HASH); // always equal length
}

function issueSessionToken() {
  const body = `${Date.now() + SESSION_TTL_MS}`; // absolute expiry (ms)
  const sig = createHmac("sha256", SESSION_SECRET)
    .update(body)
    .digest("base64url");
  return `${body}.${sig}`;
}

/* L4: constant-time, tamper + expiry aware session validation. */
function sessionIsValid(token) {
  if (!token) return false;
  const dot = token.indexOf(".");
  if (dot < 0) return false;
  const expires = Number(token.slice(0, dot));
  const sig = Buffer.from(token.slice(dot + 1), "base64url");
  const expect = createHmac("sha256", SESSION_SECRET)
    .update(token.slice(0, dot))
    .digest();
  if (sig.length !== expect.length || !timingSafeEqual(sig, expect)) {
    return false;
  }
  return Number.isFinite(expires) && expires >= Date.now();
}

function isAuthorized(req) {
  if (!WEB_PASSWORD) return true;
  const cookies = req.headers.cookie || "";
  let token = "";
  for (const part of cookies.split(";")) {
    const t = part.trim();
    if (t.startsWith("ua_web_session=")) token = t.slice("ua_web_session=".length);
  }
  return sessionIsValid(token);
}

/* M1: per-IP login-failure accounting (bounded in-memory). */
const loginFailures = new Map();
function recordLoginAttempt(ip, ok) {
  if (ok) {
    loginFailures.delete(ip);
    return { allowed: true, remaining: MAX_LOGIN_FAILURES };
  }
  const now = Date.now();
  const rec = loginFailures.get(ip);
  if (!rec || now > rec.resetAt) {
    loginFailures.set(ip, { count: 1, resetAt: now + LOGIN_WINDOW_MS });
    return { allowed: true, remaining: MAX_LOGIN_FAILURES - 1 };
  }
  rec.count += 1;
  // Prune stale entries when the table grows large, to keep it bounded.
  if (loginFailures.size > 10000) {
    for (const [k, v] of loginFailures) {
      if (now > v.resetAt) loginFailures.delete(k);
    }
  }
  return {
    allowed: rec.count <= MAX_LOGIN_FAILURES,
    remaining: Math.max(0, MAX_LOGIN_FAILURES - rec.count),
  };
}

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DIST_DIR = path.join(HERE, "dist");

const CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
};

/* M3: defense-in-depth response headers applied to every response.
 * `style-src 'unsafe-inline'` is required by the Vue templates' inline
 * style="" attributes; the production bundle has no inline scripts.
 * `frame-ancestors 'none'` + X-Frame-Options blocks clickjacking. */
const SECURITY_HEADERS = {
  "content-security-policy":
    "default-src 'self'; script-src 'self'; " +
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; " +
    "font-src 'self' data:; connect-src 'self'; " +
    "object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
  "x-content-type-options": "nosniff",
  "x-frame-options": "DENY",
  "referrer-policy": "no-referrer",
  "cross-origin-opener-policy": "same-origin",
  "cache-control": "no-store",
};

// Hop-by-hop / transport headers plus session controls that must not be
// proxied verbatim. M4: `cookie` / `set-cookie` are stripped in both
// directions so the browser session cookie never reaches agentd and agentd
// can never plant cookies on the web origin.
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
  "cookie",
  "cookie2",
  "set-cookie",
]);

function httpError(status, code, message) {
  const err = new Error(message);
  err.statusCode = status;
  err.code = code;
  return err;
}

function json(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    ...SECURITY_HEADERS,
  });
  res.end(body);
}

function redirect(res, location) {
  res.writeHead(303, { location, ...SECURITY_HEADERS });
  res.end();
}

/* M2: read a request body but refuse bodies over MAX_BODY_BYTES. */
async function readBody(req) {
  const declared = Number(req.headers["content-length"]);
  if (declared > MAX_BODY_BYTES) {
    throw httpError(413, "request_body_too_large", "request body too large");
  }
  const chunks = [];
  let total = 0;
  for await (const chunk of req) {
    total += chunk.length;
    if (total > MAX_BODY_BYTES) {
      throw httpError(413, "request_body_too_large", "request body too large");
    }
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

/* L2: validate the proxied path before forwarding to agentd. */
function proxyTarget(pathname, search) {
  const rest = pathname.replace(/^\/api/, "");
  if (!rest.startsWith("/") || rest.includes("..") || /\/\//.test(rest)) {
    throw httpError(400, "invalid_proxy_path", "malformed proxy path");
  }
  return AGENTD_URL + rest + (search || "");
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
  let target;
  try {
    target = proxyTarget(url.pathname, url.search);
  } catch (err) {
    json(res, err.statusCode || 400, {
      error: { code: err.code || "bad_request", message: err.message },
    });
    return;
  }
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
    try {
      body = await readBody(req);
    } catch (err) {
      json(res, err.statusCode || 400, {
        error: { code: err.code || "bad_request", message: err.message },
      });
      return;
    }
  }

  let upstream;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body,
      redirect: "manual", // never leak the token by following upstream redirects
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS), // L1
    });
  } catch (error) {
    const timedOut = error.name === "AbortError";
    json(res, timedOut ? 504 : 502, {
      error: {
        code: timedOut ? "agentd_timeout" : "agentd_unreachable",
        // L3: avoid leaking the internal upstream address to clients.
        message: timedOut
          ? "agentd upstream timed out"
          : `agentd upstream unreachable: ${error.message}`,
      },
    });
    return;
  }

  const outHeaders = {};
  for (const [key, value] of upstream.headers) {
    if (!HOP_HEADERS.has(key.toLowerCase())) outHeaders[key] = value;
  }
  // M3: overlay our security headers so an upstream can never weaken them.
  Object.assign(outHeaders, SECURITY_HEADERS);
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
      ...SECURITY_HEADERS,
    });
    res.end(body);
  } catch {
    // SPA fallback: client-side navigations and unknown paths get the entry.
    try {
      const body = await readFile(path.join(DIST_DIR, "index.html"));
      res.writeHead(200, {
        "content-type": CONTENT_TYPES[".html"],
        ...SECURITY_HEADERS,
      });
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
    void readBody(req)
      .then((body) => {
        const ip = req.socket.remoteAddress || "unknown";
        const params = new URLSearchParams(body.toString("utf8"));
        const ok = verifyPassword(params.get("password"));
        const attempt = recordLoginAttempt(ip, ok);
        if (ok) {
          const secure = COOKIE_SECURE ? "; Secure" : "";
          const cookie =
            `ua_web_session=${issueSessionToken()}; ` +
            `HttpOnly; SameSite=Strict; Path=/; Max-Age=${Math.floor(
              SESSION_TTL_MS / 1000,
            )}${secure}`;
          res.writeHead(303, { location: "/", "set-cookie": cookie });
          res.end();
          return;
        }
        if (!attempt.allowed) {
          console.warn(`[audit] login blocked (rate limit) from ${ip}`);
          res.writeHead(429, {
            "content-type": "application/json; charset=utf-8",
            ...SECURITY_HEADERS,
          });
          res.end(
            JSON.stringify({
              error: {
                code: "login_rate_limited",
                message: "too many failed attempts, try again later",
              },
            }),
          );
          return;
        }
        console.warn(
          `[audit] login failed from ${ip} (${attempt.remaining} attempts left)`,
        );
        redirect(res, "/login?error=1");
      })
      .catch((err) => {
        json(res, err.statusCode || 400, {
          error: { code: err.code || "bad_request", message: err.message },
        });
      });
    return;
  }
  // Inline login page (the UI itself is the Vue SPA; only this gate is
  // server-rendered). `error=1` matches a fixed token — never reflected.
  const page = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Universal-Agent 登录</title><style>body{font-family:system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;background:#f5f5f7}form{background:#fff;border:1px solid #d2d2d7;border-radius:12px;padding:32px;width:280px}input{width:100%;box-sizing:border-box;padding:9px 12px;border:1px solid #d2d2d7;border-radius:8px;margin-top:6px}button{width:100%;padding:10px;border:0;border-radius:8px;background:#0071e3;color:#fff;font:inherit;margin-top:14px;cursor:pointer}.err{color:#ff3b30;font-size:13px}</style></head><body><form method="POST"><h2 style="margin:0 0 12px;font-size:16px">Universal-Agent</h2>${req.url.includes("error=1") ? '<p class="err">密码错误，请重试</p>' : ""}<input type="password" name="password" placeholder="访问密码" autofocus><button>登录</button></form></body></html>`;
  res.writeHead(200, {
    "content-type": "text/html; charset=utf-8",
    ...SECURITY_HEADERS,
  });
  res.end(page);
}

const server = createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);

  // Public liveness probe (checked by the container HEALTHCHECK): exempt
  // from the auth gate so secured deployments still report healthy.
  if (url.pathname === "/api/health") {
    json(res, 200, { status: "ok", service: "universal-agent-web" });
    return;
  }

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
    redirect(res, "/login");
    return;
  }
  if (url.pathname === "/login") {
    // Already authorized: skip the login page.
    redirect(res, "/");
    return;
  }
  if (url.pathname === "/api/config") {
    json(res, 200, {
      server_configured: Boolean(AGENTD_URL),
      // L3: do not leak the upstream address to the browser.
      agentd_url: null,
    });
    return;
  }
  if (url.pathname === "/api/" || url.pathname.startsWith("/api/")) {
    proxy(req, res, url).catch((error) => {
      if (error && error.name === "AbortError") {
        json(res, 504, {
          error: { code: "agentd_timeout", message: "agentd upstream timed out" },
        });
        return;
      }
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
    `  login gate: ${WEB_PASSWORD ? "enabled (WEB_PASSWORD)" : "DISABLED (ALLOW_NO_AUTH)"}`,
  );
  if (WEB_PASSWORD) {
    console.log(`  session TTL: ${Math.round(SESSION_TTL_MS / 1000)}s · cookie Secure: ${COOKIE_SECURE}`);
  }
});