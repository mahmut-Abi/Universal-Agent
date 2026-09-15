/* Real agentd API client + response normalizers.
 *
 * All data comes from the agentd Runtime API (via the web /api proxy or a
 * direct UA_API_BASE). No demo/mock fallback: if a route is unavailable the
 * view shows an empty state or an inline error.
 *
 * GET/POST /v1/sessions · GET /v1/sessions/{id}[/events|/evidence|/diagnostics]
 * POST /v1/sessions/{id}/pause|resume|cancel|messages
 * GET /v1/profiles|domains|tools|doctor|metrics|memory|audit|logs|traces|cost
 * POST/PATCH/DELETE /v1/profiles[/{name}] · GET/POST/DELETE /v1/memory[/{id}]
 * GET /v1/distributed/snapshot · GET /v1/multi-agent · GET /v1/audit/integrity
 */

export const API_BASE = (window.UA_API_BASE || "/api").replace(/\/+$/, "");

async function api(path, opts = {}) {
  const res = await fetch(API_BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  if (!res.ok) {
    let detail = "";
    try {
      const j = await res.json();
      detail =
        j.error?.message ||
        j.message ||
        (j.errors ? JSON.stringify(j.errors) : "");
    } catch {
      /* non-JSON error body */
    }
    throw new Error(
      `${opts.method || "GET"} ${path} → HTTP ${res.status}${detail ? " · " + detail : ""}`,
    );
  }
  if (res.status === 204) return null;
  return res.json();
}

export const apiGet = (p) => api(p);
export const apiPost = (p, body = {}) => api(p, { method: "POST", body });
export const apiPatch = (p, body = {}) => api(p, { method: "PATCH", body });
export const apiDelete = (p) => api(p, { method: "DELETE" });

/* ── UI state shape（与 App.vue 模板绑定的字段一一对应） ── */
export function createState() {
  return {
    metrics: { sessions: 0, successRate: 0, toolCalls: 0, avgCost: 0 },
    sessions: [],
    events: [],
    evidence: [],
    doctor: [],
    profiles: [],
    domains: [],
    evals: { datasets: [], reports: [] },
    cluster: { workers: [], locks: [], goals: [] },
    memories: [],
    cost: { byModel: [], top: [] },
    logs: [],
    traces: [],
    k8sops: { preflight: [], runs: [] },
    ecosystem: { installed: [], catalog: [] },
    audit: { integrity: "", items: [], configHistory: [] },
    multi: { agents: [] },
    modelInfo: { calls: [] },
    runtimeConfig: { available: false, model: null, limits: null },
    activity: { values: [0, 0, 0, 0, 0, 0, 0], days: [], max: 0 },
    health: { checks: [], state: [] },
  };
}

/* ── normalizers ── */
const GOAL_STATUS_MAP = {
  running: "running",
  active: "running",
  executing: "running",
  succeeded: "success",
  success: "success",
  completed: "success",
  failed: "failed",
  error: "failed",
  waiting: "waiting",
  waiting_for_confirmation: "waiting",
  confirm: "waiting",
  paused: "paused",
};
export const STATUS_MAP = {
  running: ["st-running", "运行中"],
  success: ["st-success", "已完成"],
  failed: ["st-failed", "失败"],
  waiting: ["st-waiting", "待确认"],
  paused: ["st-paused", "已暂停"],
};

export function normStatus(s) {
  return GOAL_STATUS_MAP[String(s || "").toLowerCase()] || "paused";
}

function normSession(s) {
  const waiting =
    Boolean(s.pending_action) || normStatus(s.goal_status) === "waiting";
  return {
    id: s.session_id,
    goal: s.goal_description || s.current_task_description || "(无目标描述)",
    profile: s.domain_name || "default",
    status: normStatus(s.goal_status),
    steps: s.iteration ?? s.task_count ?? 0,
    dur: s.termination_reason ? "已结束" : "—",
    confirm: waiting,
    pending: {
      action: s.current_task_description || "待确认动作",
      risk: "mutation",
    },
    raw: s,
  };
}

function toolIsMutation(t) {
  const se = String(t.side_effect || "").toLowerCase();
  return (
    (se !== "none" && se !== "read" && se !== "readonly") ||
    String(t.risk || "").toLowerCase() === "high"
  );
}

export function pick(list, key) {
  const d = list || {};
  if (Array.isArray(d)) return d;
  return d[key] || [];
}

/* ── loaders（每个视图独立加载，失败抛给调用方展示） ── */
export async function loadSessions(state) {
  const d = await apiGet("/v1/sessions");
  state.sessions = pick(d, "sessions").map(normSession);
  return state.sessions;
}

/* 近 7 日任务量：真实会话 created_at 按天聚合（跟随 next_cursor 分页，最多 5 页） */
export async function loadActivity(state) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const buckets = Array.from({ length: 7 }, () => 0);
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(today);
    d.setDate(d.getDate() - (6 - i));
    return `${d.getMonth() + 1}/${d.getDate()}`;
  });
  const start = new Date(today);
  start.setDate(start.getDate() - 6);
  let cursor = "";
  for (let page = 0; page < 5; page += 1) {
    const d = await apiGet(
      `/v1/sessions?limit=200${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
    );
    for (const s of pick(d, "sessions")) {
      const created = s.created_at ? new Date(s.created_at) : null;
      if (created && created >= start)
        buckets[Math.min(6, Math.floor((created - start) / 86400000))] += 1;
    }
    if (!d.next_cursor) break;
    cursor = d.next_cursor;
  }
  state.activity = { values: buckets, days, max: Math.max(...buckets, 0) };
}

export async function loadMetrics(state) {
  const d = await apiGet("/v1/metrics");
  const total = d.session_count || 0;
  state.metrics = {
    sessions: total,
    successRate: d.goal_completion_rate || 0,
    toolCalls: d.action_completed_count || 0,
    avgCost: total ? (d.model_estimated_cost_micros || 0) / 1e6 / total : 0,
  };
}

export async function loadOverview(state) {
  await Promise.all([
    loadSessions(state),
    loadMetrics(state),
    loadActivity(state),
  ]);
}

export async function loadSessionDetail(state, sid) {
  const [ev, evd] = await Promise.all([
    apiGet(`/v1/sessions/${sid}/events`).catch(() => ({})),
    apiGet(`/v1/sessions/${sid}/evidence`).catch(() => ({})),
  ]);
  state.events = pick(ev, "events").map((e) => ({
    t: normEventType(e.type),
    at: fmtTime(e.occurred_at),
    text: eventText(e),
    d: null,
    raw: e,
  }));
  state.evidence = pick(evd, "evidence").map((e) => ({
    name: e.evidence_id,
    detail: `${e.claim || ""}${e.subject ? " · " + e.subject : ""} · 置信度 ${e.confidence ?? "—"}`,
    ok: (e.confidence ?? 0) >= 0.5,
  }));
}

function normEventType(type) {
  const t = String(type || "");
  if (/decision/i.test(t)) return "decision";
  if (/action/i.test(t)) return "action";
  if (/policy|confirm/i.test(t)) return "policy";
  if (/fail|error|invalid|cancel/i.test(t)) return "failure";
  return "evidence";
}
function eventText(e) {
  const data =
    e.data && Object.keys(e.data).length ? JSON.stringify(e.data) : "";
  return `<code>${escapeHtml(e.type || "event")}</code>${data ? " " + escapeHtml(data) : ""}`;
}
function escapeHtml(x) {
  return String(x).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
}
function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? String(iso) : d.toTimeString().slice(0, 8);
}

/* ── 模型配置：运行时级 RuntimeConfig.model（GET /v1/config 可读，API 不可写）──
   provider ∈ scripted | json_http | openai_chat_completions | openai_responses。
   修改途径：agent init --model-provider … / profile-config 的 model 段，重启后生效。 */
export async function loadRuntimeConfig(state) {
  try {
    const d = await apiGet("/v1/config");
    state.runtimeConfig = { available: true, model: d.model || null, limits: d.limits || null };
  } catch {
    // 部署未启用 deployment config store 时 GET /v1/config → 503：展示不可用态
    state.runtimeConfig = { available: false, model: null, limits: null };
  }
}

/* 实际生效的 provider/model 也可从会话 llm-calls 事件观测（最近 8 个会话） */
export async function loadModelInfo(state) {
  const calls = [];
  for (const s of state.sessions.slice(0, 8)) {
    try {
      const d = await apiGet(`/v1/sessions/${s.id}/llm-calls`);
      for (const c of pick(d, "calls")) {
        calls.push({
          sid: s.id,
          provider: c.provider || "—",
          model: c.model || "—",
          tokens: c.total_tokens ?? 0,
          cost: (c.estimated_cost_micros ?? 0) / 1e6,
          at: fmtTime(c.occurred_at),
        });
      }
    } catch {
      /* 会话已过期或路由不可用：跳过 */
    }
  }
  state.modelInfo.calls = calls;
}

export async function loadConfig(state) {
  const [doc, prof, dom, tools] = await Promise.all([
    apiGet("/v1/doctor"),
    apiGet("/v1/profiles"),
    apiGet("/v1/domains"),
    apiGet("/v1/tools"),
  ]);
  state.doctor = pick(doc, "checks").map((c) => ({
    level:
      String(c.status || "ok").toLowerCase() === "ok"
        ? "ok"
        : String(c.status).toLowerCase() === "warn"
          ? "warn"
          : "fail",
    name: c.name,
    detail: c.message || "",
  }));
  state.profiles = pick(prof, "profiles").map((p) => ({
    name: p.name,
    desc: p.description || (p.domains || []).map((d) => d.name).join("/"),
    builtin: p.name === "default",
    domains: (p.domains || []).map((d) => d.name),
    model: p.model || "—",
    version: p.version,
    raw: p,
  }));
  const byDomain = {};
  for (const t of pick(tools, "tools")) {
    (byDomain[t.domain_name] = byDomain[t.domain_name] || []).push({
      name: t.name,
      desc: t.description || "",
      mutation: toolIsMutation(t),
    });
  }
  state.domains = pick(dom, "domains").map((d) => ({
    name: d.name,
    desc: d.description || "",
    active: true,
    version: d.version || "0.1.0",
    tools: byDomain[d.name] || [],
  }));
  state.health.checks = state.doctor;
}

export async function loadEval(state) {
  const [ds, reports] = await Promise.all([
    apiPost("/v1/eval/list").catch(() => ({})),
    apiPost("/v1/eval/reports").catch(() => ({})),
  ]);
  state.evals.datasets = pick(ds, "scenarios").map((s) => ({
    name: s.scenario_name,
    cases: s.case_count || 1,
    desc: s.goal?.description || s.kind || "",
    updated: (s.tags || []).join(","),
  }));
  state.evals.reports = pick(reports, "reports").map((r) => ({
    id: r.report_id || r.id,
    dataset: r.dataset || r.scenario_name || "",
    profile: r.profile || "",
    score: r.score ?? r.pass_rate ?? 0,
    pass: r.pass ?? 0,
    total: r.total ?? 0,
    at: fmtTime(r.created_at) || "",
  }));
}

export async function loadCluster(state) {
  const snap = await apiGet("/v1/distributed/snapshot");
  const w = snap.workers || {};
  state.cluster.workers = pick(w, "workers").map((x) => ({
    name: x.worker_id || x.name || "",
    status: x.status === "online" ? "online" : "offline",
    load: x.load ?? 0,
    sessions: x.session_count ?? 0,
    region: x.region || "—",
  }));
  state.cluster.locks = pick(snap, "locks").map((l) => ({
    res: l.resource || l.res || "",
    holder: l.holder || l.owner || "—",
    ttl: l.ttl == null ? "—" : String(l.ttl),
    held: Boolean(l.held ?? true),
  }));
  state.cluster.goals = pick(snap.work_queue, "items").map((g) => ({
    id: g.work_item_id || g.id || "",
    title: g.description || g.title || "",
    worker: g.worker_id || "—",
    state: normStatus(g.status),
  }));
}

export async function loadMemory(state) {
  const d = await apiGet("/v1/memory");
  state.memories = pick(d, "memories").map((mm) => ({
    id: mm.memory_id || mm.id,
    kind: mm.kind || mm.category || "fact",
    text: mm.text || mm.content || "",
    at: fmtTime(mm.created_at) || "",
  }));
}
export function memoryAdd(text) {
  // agentd MemoryCreatePayload: kind ∈ episodic|semantic|procedural|preference
  return apiPost("/v1/memory", {
    kind: "semantic",
    subject: "dashboard",
    content: text,
    scope: "local",
    confidence: 0.9,
  });
}
export function memoryRemove(id) {
  return apiDelete(`/v1/memory/${encodeURIComponent(id)}`);
}

export async function loadCost(state) {
  const d = await apiGet("/v1/cost");
  const byModel = pick(d, "by_model").map((x) =>
    Array.isArray(x)
      ? x
      : [
          x.model || x.name || "—",
          (x.cost_micros ?? x.estimated_cost_micros ?? 0) / 1e6,
        ],
  );
  state.cost.byModel = byModel;
  state.cost.top = pick(d, "top_sessions").map((t) => ({
    sid: t.session_id || t.sid,
    profile: t.profile || t.domain_name || "",
    cost: (t.cost_micros ?? 0) / 1e6,
    tokens: String(t.total_tokens ?? t.tokens ?? ""),
  }));
}

export async function loadLogs(state) {
  const [lg, tr] = await Promise.all([
    apiGet("/v1/logs"),
    apiGet("/v1/traces"),
  ]);
  state.logs = pick(lg, "logs").map((l) => ({
    at: fmtTime(l.occurred_at || l.timestamp),
    level: String(l.level || "info").toLowerCase(),
    src: l.source || l.src || l.logger || "runtime",
    text: l.message || l.text || "",
  }));
  state.traces = pick(tr, "spans").map((t) => ({
    tid: t.trace_id || t.span_id,
    session: t.session_id || "—",
    spans: t.span_count || 1,
    dur: t.duration_ms == null ? "—" : t.duration_ms + "ms",
    root: t.root_name || t.name || "",
  }));
}

/* K8s 运维视图：当前 agentd 未提供 /v1/kubernetes/* 路由，返回空并保留 UI */
export async function loadK8sOps(state) {
  try {
    // preflight 不带 workload 时必须 skip_cluster，否则 400
    const p = await apiPost("/v1/kubernetes/preflight", { skip_cluster: true });
    state.k8sops.preflight = pick(p, "checks").map((c) => ({
      name: c.name,
      ok: Boolean(c.ok ?? c.status === "ok"),
      detail: c.detail || c.message || "",
    }));
  } catch {
    state.k8sops.preflight = [];
  }
  state.k8sops.runs = [];
}

export async function loadEcosystem(state) {
  const [reg, cat] = await Promise.all([
    apiPost("/v1/ecosystem/registry").catch(() => ({})),
    apiPost("/v1/ecosystem/catalog").catch(() => ({})),
  ]);
  state.ecosystem.installed = pick(reg, "packages").map((p) => ({
    name: p.name,
    ver: p.version || "—",
    sig: p.verified || p.signature === "verified" ? "verified" : "unsigned",
  }));
  state.ecosystem.catalog = pick(cat, "packages").map((p) => ({
    name: p.name,
    desc: p.description || "",
    ver: p.version || "—",
  }));
}

export async function loadAudit(state) {
  const [a, integrity] = await Promise.all([
    apiGet("/v1/audit").catch(() => ({})),
    apiGet("/v1/audit/integrity").catch(() => ({})),
  ]);
  state.audit.items = pick(a, "audit_records").map((x) => ({
    at: fmtTime(x.occurred_at || x.at),
    actor: x.actor || "—",
    act: x.action || x.act || "—",
    target: x.target || "—",
    hash: (x.hash || "").slice(0, 8) + "…",
  }));
  state.audit.integrity = integrity.root_hash ? "ok" : "";
  state.audit.recordCount = integrity.record_count ?? state.audit.items.length;
  state.audit.rootHash = integrity.root_hash || "";
  state.audit.configHistory = [];
}

export async function loadMulti(state) {
  const d = await apiGet("/v1/multi-agent");
  const agents = (d.instances || []).map((x) => ({
    name: x.instance_id || x.name || "worker",
    role: x.profile || x.role || "worker",
    kind: x.kind || (x.role === "coordinator" ? "coordinator" : "worker"),
    msgs: x.message_count ?? 0,
  }));
  if (!agents.some((a) => a.kind === "coordinator")) {
    agents.unshift({ name: "coordinator", role: "调度", kind: "coordinator" });
  }
  state.multi.agents = agents;
}

export async function loadHealth(state) {
  // doctor 检查项已由 loadConfig 填充；状态事件概览来自真实 metrics 计数
  const d = await apiGet("/v1/metrics").catch(() => ({}));
  state.health.state = [
    {
      name: "事件总数",
      detail: `${d.event_count ?? 0} 条运行事件 · ${d.decision_generated_count ?? 0} 决策 · ${d.action_started_count ?? 0} 动作`,
    },
  ];
}

/* ── 会话生命周期 / 对话 ── */
export function createSession(goal, profile) {
  return apiPost("/v1/sessions", {
    goal: {
      description: goal,
      success_criteria: [{ key: "done", expected: true }],
    },
    compile_goal: true,
    ...(profile && profile !== "default" ? { profile } : {}),
  });
}
export function sendMessage(sid, text) {
  return apiPost(`/v1/sessions/${encodeURIComponent(sid)}/messages`, {
    message: text,
  });
}
export function pauseSession(sid) {
  return apiPost(`/v1/sessions/${encodeURIComponent(sid)}/pause`, {});
}
export function resumeSession(sid, body) {
  return apiPost(`/v1/sessions/${encodeURIComponent(sid)}/resume`, body || {});
}
export function cancelSession(sid) {
  return apiPost(`/v1/sessions/${encodeURIComponent(sid)}/cancel`, {});
}

/* ── Profile 配置写入（422 校验错误会带 errors，抛给 toast） ── */
export function profileCreate(payload) {
  return apiPost("/v1/profiles", {
    name: payload.name,
    version: "0.1.0",
    description: payload.desc,
    domains: payload.domains,
  });
}
export function profilePatch(name, payload) {
  return apiPatch(`/v1/profiles/${encodeURIComponent(name)}`, {
    description: payload.desc,
    domains: payload.domains,
  });
}
export function profileRemove(name) {
  return apiDelete(`/v1/profiles/${encodeURIComponent(name)}`);
}

/* Policy 偏好为客户端本地 UI 偏好；部署级 Policy 经 deployment.json /
   PUT /v1/config {policies:[...完整 PolicyRule 规格]} 管理 */
export function putConfig(policyPrefs) {
  const prefs = Object.fromEntries(policyPrefs.map((p) => [p.key, p.on]));
  localStorage.setItem("ua-policy-prefs", JSON.stringify(prefs));
  return Promise.resolve(prefs);
}

export async function runEval(dataset) {
  return apiPost("/v1/eval/run", { dataset: dataset || null });
}
