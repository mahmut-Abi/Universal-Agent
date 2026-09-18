/* Dashboard 共享状态与动作：从 App.vue 拆出（模块级单例，所有视图组件共享）。
 * 数据全部来自真实 agentd API（见 api.js）；App.vue 只保留外壳。 */
import { reactive, ref, computed, nextTick } from "vue";
import {
  API_BASE,
  apiGet,
  createState,
  normStatus,
  STATUS_MAP,
  sessionTranscript,
  loadOverview,
  loadSessions as apiLoadSessions,
  loadMetrics,
  loadSessionDetail,
  loadConfig,
  loadEval,
  loadCluster,
  loadMemory,
  memoryAdd,
  memoryRemove,
  loadCost,
  loadLogs,
  loadK8sOps,
  loadEcosystem,
  loadAudit,
  loadMulti,
  loadHealth,
  loadModelInfo,
  loadRuntimeConfig,
  createSession,
  sendMessage,
  pauseSession,
  resumeSession,
  cancelSession as apiCancelSession,
  profileCreate,
  profilePatch,
  profileRemove,
  putConfig,
  runEval,
} from "./api.js";

/* ── 全局状态（由真实 agentd API 填充） ── */
export const m = reactive(createState());
export const view = ref("overview");
export const OPS_VIEWS = {
  eval: "评估工作台",
  cluster: "分布式集群",
  memory: "记忆管理",
  cost: "成本分析",
  logs: "日志与追踪",
  k8sops: "K8s 运维",
  ecosystem: "生态与包",
  audit: "审计中心",
  multiagent: "多智能体",
  health: "健康中心",
};
export const opsOpen = ref(false);
export const apiHost = API_BASE.replace(/^https?:\/\//, "");
export const fatal = ref([]);

/* toast */
export const toastMsg = ref("");
export let toastTimer = null;
export function toast(msg) {
  toastMsg.value = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toastMsg.value = ""), 2200);
}

/* 视图切换：首次进入某视图时拉取真实数据 */
export const MAIN_VIEWS = ["overview", "session", "config", "chat"];
export const VIEW_LOADERS = {
  overview: () => loadOverview(m),
  config: () =>
    Promise.all([loadConfig(m), apiLoadSessions(m)]).then(() =>
      Promise.all([loadModelInfo(m), loadRuntimeConfig(m)]),
    ),
  eval: () => loadEval(m),
  cluster: () => loadCluster(m),
  memory: () => loadMemory(m),
  cost: () => loadCost(m),
  logs: () => loadLogs(m),
  k8sops: () => loadK8sOps(m),
  ecosystem: () => loadEcosystem(m),
  audit: () => loadAudit(m),
  multiagent: () => loadMulti(m),
  health: () => loadHealth(m),
  chat: () => loadSessions(m),
};
export const loadedViews = new Set();
export function switchView(name) {
  view.value = name;
  opsOpen.value = false;
  localStorage.setItem("ua-view", name);
  if (VIEW_LOADERS[name] && !loadedViews.has(name)) {
    loadedViews.add(name);
    VIEW_LOADERS[name]().catch((e) => toast("加载失败：" + e.message));
  }
  if (name === "chat") nextTick(scrollChat);
}
export function reload(name) {
  if (VIEW_LOADERS[name])
    VIEW_LOADERS[name]().catch((e) => toast("加载失败：" + e.message));
}
export const opsBtnLabel = computed(() => OPS_VIEWS[view.value] || "运维中心");

/* 状态徽标：normStatus 已把 agentd goal_status 归一到 UI 的五种状态 */
export function statusCls(s) {
  return (STATUS_MAP[normStatus(s)] || ["st-paused"])[0];
}
export function statusLabel(s) {
  return (STATUS_MAP[normStatus(s)] || [null, s])[1];
}

/* ── 总览 ── */
export const metricCards = computed(() => [
  {
    label: "会话总数",
    value: String(m.metrics.sessions),
    sub: "GET /v1/sessions",
    trend: "",
  },
  {
    label: "任务成功率",
    value: Math.round(m.metrics.successRate * 100) + "%",
    sub: "goal_completion_rate",
    trend: "up",
  },
  {
    label: "工具调用",
    value: String(m.metrics.toolCalls),
    sub: "累计",
    trend: "",
  },
  {
    label: "平均单次成本",
    value: "$" + m.metrics.avgCost.toFixed(3),
    sub: "GET /v1/cost",
    trend: "",
  },
]);
export const sessionsError = ref("");
export const loadingSessions = ref(false);
export async function loadSessions() {
  loadingSessions.value = true;
  try {
    await apiLoadSessions(m);
    sessionsError.value = "";
  } catch (e) {
    sessionsError.value = e.message;
  }
  loadingSessions.value = false;
}
/* 近 7 日任务量：由真实会话 created_at 聚合（loadActivity 填充） */
export const activity = computed(
  () => m.activity || { values: [0, 0, 0, 0, 0, 0, 0], days: [], max: 0 },
);
export function doRefresh() {
  reload("overview");
}

/* ── 会话详情 ── */
export const currentSession = ref(null);
export const expanded = reactive({});
export function openSession(s) {
  currentSession.value = s;
  switchView("session");
  for (const k in expanded) delete expanded[k];
  loadSessionDetail(m, s.id).catch((e) => toast("事件加载失败：" + e.message));
}
export function confirmPending(ok) {
  const s = currentSession.value;
  resumeSession(s.id, ok ? { confirmed: true } : { confirmed: false })
    .then(() => {
      s.confirm = false;
      s.status = ok ? "running" : "paused";
      toast(ok ? "已发送确认 · POST /resume" : "已拒绝 · POST /resume");
    })
    .catch((e) => toast("确认失败：" + e.message));
}
export function cancelSession() {
  const s = currentSession.value;
  apiCancelSession(s.id)
    .then(() => {
      s.status = "failed";
      toast("POST /cancel 已发送");
    })
    .catch((e) => toast("取消失败：" + e.message));
}
export function lifecycle(action) {
  const s = currentSession.value;
  if (!s) return;
  const fn = action === "pause" ? pauseSession : () => resumeSession(s.id);
  fn(s.id)
    .then(() => toast(`POST /${action} 已发送`))
    .catch((e) => toast(`${action} 失败：` + e.message));
}
export const sessionSummary = computed(() => {
  const s = currentSession.value;
  return s
    ? [
        ["目标", s.goal],
        ["Profile", s.profile],
        ["状态", s.status],
        ["步骤", String(s.steps)],
        ["耗时", s.dur],
      ]
    : [];
});

/* ── 对话（真实会话：chat = agentd session，转录由事件流重建） ── */
export const chatFilter = ref("全部");
export const activeChatId = ref(null);
/* 会话时间人性化：刚刚 / N 分钟前 / 今天 HH:MM / 昨天 / M/D */
export function fmtRelTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
  if (d.toDateString() === now.toDateString())
    return d.toTimeString().slice(0, 5);
  const yest = new Date(now);
  yest.setDate(now.getDate() - 1);
  if (d.toDateString() === yest.toDateString())
    return "昨天 " + d.toTimeString().slice(0, 5);
  return d.getMonth() + 1 + "/" + d.getDate();
}
export const chatSessions = computed(() =>
  m.sessions.map((s) => ({
    id: s.id,
    title: s.goal,
    profile: s.profile,
    status: s.status,
    time: fmtRelTime(s.raw?.created_at),
    msgs: sessionTranscript(chatEventCache[s.id] || [], s.goal),
  })),
);
/* Profile 过滤项由真实会话推导（不再硬编码 ["全部","default"]） */
export const CHAT_PROFILES = computed(() => [
  "全部",
  ...Array.from(new Set(m.sessions.map((s) => s.profile))),
]);
export const activeChat = computed(
  () => chatSessions.value.find((c) => c.id === activeChatId.value) || null,
);
export const chatInput = ref("");
/* 发送队列：agent 运行中输入的消息排队，结束后自动依次发送 */
export const chatQueue = ref([]);
/* 乐观显示：发送中先展示用户消息，事件回流后由转录接管 */
export const pendingUserMsg = ref("");
export const sending = ref(false);
export const chatMsgsEl = ref(null);
export const chatInputEl = ref(null);
export const filteredChats = computed(() =>
  chatSessions.value.filter(
    (c) => chatFilter.value === "全部" || c.profile === chatFilter.value,
  ),
);
export function scrollChat() {
  if (chatMsgsEl.value)
    chatMsgsEl.value.scrollTop = chatMsgsEl.value.scrollHeight;
}
/* 新建对话：清空选中，让第一条输入创建新会话（而非继续写入旧会话） */
export function newChat() {
  activeChatId.value = null;
  nextTick(() => chatInputEl.value && chatInputEl.value.focus());
}
/* 选中历史会话：首次点击时拉取事件重建转录 */
export function selectChat(id) {
  activeChatId.value = id;
  if (!chatEventCache[id]) {
    apiGetEvents(id).catch((e) => toast("事件加载失败：" + e.message));
  }
  nextTick(scrollChat);
}
export function autoGrow(e) {
  const el = e.target;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 140) + "px";
}
/* 发送：新会话首条消息即 goal（POST /v1/sessions 已执行，不再重发 /messages）；
   既有会话经 POST /messages 续聊后拉取事件刷新转录。
   乐观 UX：pendingUserMsg 立即上屏；纯网络失败时回填输入框避免重打。
   队列：agent 运行中输入的消息进入 chatQueue，当前轮结束后依次自动发送。 */
export function sendChat() {
  const text = chatInput.value.trim();
  if (!text) return;
  if (sending.value) {
    chatQueue.value.push(text);
    chatInput.value = "";
    if (chatInputEl.value) chatInputEl.value.style.height = "auto";
    nextTick(scrollChat);
    return;
  }
  chatInput.value = "";
  deliverChat(text);
}
function deliverChat(text) {
  sending.value = true;
  pendingUserMsg.value = text;
  const refresh = (sid) =>
    apiLoadSessions(m)
      .then(() => apiGetEvents(sid))
      .then(() => chatSessions.value.find((c) => c.id === sid));
  const existing = activeChat.value;
  const profile =
    chatFilter.value === "全部" ? selectedProfile.value : chatFilter.value;
  const work = existing
    ? sendMessage(existing.id, text)
        .then(() => refresh(existing.id))
        .catch((e) => {
          // 续聊失败但会话仍存在：刷新列表展示失败状态
          return refresh(existing.id).then((chat) => {
            throw Object.assign(e, { recovered: chat });
          });
        })
    : createSession(text, profile)
        .then((d) => {
          const sid =
            (d.result && d.result.session_id) || d.session_id || d.id;
          activeChatId.value = sid;
          return refresh(sid);
        })
        .catch((e) => {
          // 422：会话已创建但首轮执行失败 —— 保留会话并展示失败记录
          const sid = e.body?.result?.session_id;
          if (!sid) throw e;
          activeChatId.value = sid;
          return refresh(sid).then((chat) => {
            throw Object.assign(e, { recovered: chat });
          });
        });
  work
    .then(() => {})
    .catch((e) => {
      toast(
        e.recovered
          ? "本轮执行失败（详见对话记录）：" + (e.message || e)
          : "发送失败：" + (e.message || e),
      );
      if (!e.recovered) chatInput.value = text; // 回填输入，避免重打
    })
    .finally(() => {
      sending.value = false;
      pendingUserMsg.value = "";
      if (chatInputEl.value) chatInputEl.value.style.height = "auto";
      if (chatQueue.value.length) {
        const next = chatQueue.value.shift();
        nextTick(() => deliverChat(next));
      }
      nextTick(scrollChat);
    });
}
/* 侧栏刷新：重拉会话列表（保留事件缓存） */
export function refreshChats() {
  loadSessions().catch((e) => toast("刷新失败：" + e.message));
}
export function apiGetEvents(sid) {
  return apiGet(`/v1/sessions/${sid}/events`).then((d) => {
    chatEventCache[sid] = (d.events || []).slice(-300);
    nextTick(scrollChat);
  });
}
export function onChatKeydown(e) {
  // 中文输入法组合中按 Enter 是确认候选词，不是发送
  if (e.isComposing || e.keyCode === 229) return;
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
}

/* ── 配置：Profile CRUD / Domains / Policy ── */
/* 当前选中的 Profile（新会话默认使用；localStorage 持久化） */
export const selectedProfile = ref(
  localStorage.getItem("ua-profile") || "default",
);
export function switchProfile(name) {
  selectedProfile.value = name;
  localStorage.setItem("ua-profile", name);
  toast("新会话将使用 Profile「" + name + "」");
}
export const profileModal = reactive({ open: false, editing: null });
export const pmForm = reactive({
  name: "",
  desc: "",
  domains: [],
  model: {
    provider: "scripted",
    name: "",
    endpoint: "",
    api_key_env: "",
    timeout_seconds: 30,
  },
});
export const MODEL_PROVIDERS = [
  "scripted",
  "json_http",
  "openai_chat_completions",
  "openai_responses",
];
export const pmError = ref("");
export function openProfileModal(name) {
  profileModal.editing = name || null;
  const p = name ? m.profiles.find((x) => x.name === name) : null;
  pmForm.name = name || "";
  pmForm.desc = p ? p.desc.replace(/^(K8s 运维|通用离线) Profile · /, "") : "";
  pmForm.domains = m.profiles.find((x) => x.name === name)?.domains?.length
    ? [...m.profiles.find((x) => x.name === name).domains]
    : m.domains.map((d) => d.name);
  const existing = p && p.raw && p.raw.runtime && p.raw.runtime.model;
  pmForm.model = existing
    ? {
        provider: existing.provider || "scripted",
        name: existing.name || "",
        endpoint: existing.endpoint || "",
        api_key_env: existing.api_key_secret || "",
        timeout_seconds: existing.timeout_seconds || 30,
      }
    : {
        provider: "scripted",
        name: "",
        endpoint: "",
        api_key_env: "",
        timeout_seconds: 30,
      };
  pmError.value = "";
  profileModal.open = true;
}
export function saveProfile() {
  const name = pmForm.name.trim();
  const valid = /^[a-z0-9][a-z0-9-]*$/.test(name);
  const dup = !profileModal.editing && m.profiles.some((p) => p.name === name);
  if (!valid) {
    pmError.value = "名称必填，且只能含小写字母、数字与连字符";
    return;
  }
  if (dup) {
    pmError.value = "同名 Profile 已存在";
    return;
  }
  // agentd 校验要求 profile domain 为 {name, version} 对象
  const domainObjs = pmForm.domains.map((n) => ({
    name: n,
    version: (m.domains.find((d) => d.name === n) || {}).version || "0.1.0",
  }));
  if (
    pmForm.model.provider !== "scripted" &&
    !pmForm.model.api_key_env.trim()
  ) {
    pmError.value = "非 scripted Provider 需要填写 API Key 环境变量名";
    return;
  }
  const payload = {
    name,
    domains: domainObjs,
    desc: pmForm.desc.trim() || "自定义 Profile · " + pmForm.domains.join("/"),
    model: { ...pmForm.model },
  };
  const op = profileModal.editing
    ? profilePatch(profileModal.editing, payload)
    : profileCreate(payload);
  op.then(() =>
    loadConfig(m).then(() => {
      profileModal.open = false;
      toast(
        (profileModal.editing
          ? "Profile 已更新 · PATCH /v1/profiles/" + name
          : "Profile 已创建 · POST /v1/profiles") +
          "（需重启/重载 agentd 后生效）",
      );
    }),
  ).catch((e) => toast(profileWriteError(e, "保存失败")));
}
export const delModal = reactive({ open: false, name: null });
/* Profile 写入错误分类：启动配置加载的 profile 不在 --profiles-dir 可编辑存储中 →
   写平面 404 "profile config not found"；builtin 拒改 → "built-in"。 */
export function profileWriteError(e, prefix) {
  const msg = e.message || String(e);
  if (msg.includes("profile config not found")) {
    return (
      prefix +
      "：该 Profile 来自启动配置（profile-config / 内置），不在 --profiles-dir 可编辑存储中；请修改启动配置后重启 agentd"
    );
  }
  if (msg.includes("built-in")) {
    return prefix + "：内置 Profile 由部署方所有，不能通过配置 API 修改或删除";
  }
  return prefix + "：" + msg;
}
export const delDetail = computed(() => {
  if (!delModal.name) return "";
  const p = m.profiles.find((x) => x.name === delModal.name);
  return `将删除 Profile「${delModal.name}」（${p ? p.desc : ""}）。关联的运行中会话不受影响，但该配置无法恢复。此操作将写入审计链。`;
});
export function askDelete(name) {
  delModal.name = name;
  delModal.open = true;
}
export function confirmDelete() {
  if (!delModal.name) return;
  profileRemove(delModal.name)
    .then(() => loadConfig(m))
    .then(() => {
      toast("已删除 · DELETE /v1/profiles/" + delModal.name);
      delModal.open = false;
      delModal.name = null;
    })
    .catch((e) => toast(profileWriteError(e, "删除失败")));
}
export const domainModal = reactive({ open: false, index: -1 });
export const domainDetail = computed(() =>
  domainModal.index >= 0 ? m.domains[domainModal.index] : null,
);
export function openDomainDetail(i) {
  domainModal.index = i;
  domainModal.open = true;
}
export function domainUsedBy(d) {
  const used = m.profiles
    .filter((p) => (p.domains || []).includes(d.name))
    .map((p) => p.name);
  return used.length ? used : null;
}
/* Policy 偏好为控制台本地显示偏好（localStorage 持久化，启动时恢复）；
   部署级 Policy 经 deployment.json / PUT /v1/config 管理 */
const POLICY_PREFS_KEY = "ua-policy-prefs";
const savedPolicyPrefs = (() => {
  try {
    return JSON.parse(localStorage.getItem(POLICY_PREFS_KEY) || "{}");
  } catch {
    return {};
  }
})();
export const policies = reactive(
  [
    {
      key: "mutation-confirm",
      label: "变更动作需人工确认",
      desc: "删除 / 扩缩容等 mutation 触发 WAITING_FOR_CONFIRMATION",
      on: true,
    },
    {
      key: "dryrun-default",
      label: "默认 dry-run 预检",
      desc: "运行前对集群执行只读预检",
      on: true,
    },
    {
      key: "audit-chain",
      label: "审计哈希链",
      desc: "会话事件写入可校验的审计链",
      on: true,
    },
  ].map((p) => ({
    ...p,
    on: typeof savedPolicyPrefs[p.key] === "boolean" ? savedPolicyPrefs[p.key] : p.on,
  })),
);

export function togglePolicy() {
  const prefs = Object.fromEntries(policies.map((p) => [p.key, p.on]));
  localStorage.setItem(POLICY_PREFS_KEY, JSON.stringify(prefs));
  toast("已保存为控制台本地偏好");
}
export function toggleDomain(i, checked) {
  const d = m.domains[i];
  toast(
    "Domain 启用/停用经 profile 绑定管理 · PUT /v1/domains/" +
      d.name +
      "/profiles",
  );
  d.active = checked;
}

/* ── 评估 ── */
export const evalByDataset = computed(() => {
  const byDs = {};
  m.evals.reports.forEach((r) =>
    (byDs[r.dataset] = byDs[r.dataset] || []).push(r),
  );
  return byDs;
});
export function pct(x) {
  return Math.round(x * 100);
}

/* ── 记忆 ── */
export const memSearch = ref("");
export const memList = computed(() =>
  m.memories.filter(
    (x) => !memSearch.value || x.text.includes(memSearch.value),
  ),
);
export function addMemory() {
  memoryAdd("手动新增的记忆 · " + new Date().toLocaleTimeString())
    .then(() => loadMemory(m))
    .then(() => toast("已新增 · POST /v1/memory"))
    .catch((e) => toast("新增失败：" + e.message));
}
export function delMemory(id) {
  memoryRemove(id)
    .then(() => loadMemory(m))
    .then(() => toast("已删除 · DELETE /v1/memory/" + id))
    .catch((e) => toast("删除失败：" + e.message));
}

/* ── 成本 / 日志 / K8s / 生态 ── */
export const costMax = computed(() =>
  Math.max(...m.cost.byModel.map((x) => x[1])),
);
export function openTraceSession(sid) {
  const s = m.sessions.find((x) => x.id === sid);
  if (s) openSession(s);
  else toast("该会话不存在或已过期");
}
export function installPkg(name) {
  import("./api.js")
    .then((mod) => mod.apiPost("/v1/ecosystem/install", { name }))
    .then(() => toast("安装任务已创建 · POST /v1/ecosystem/install " + name))
    .catch((e) => toast("安装失败：" + e.message));
}

/* ── 审计 ── */
export const auditQ = ref("");
export const auditAct = ref("全部");
export const auditActs = computed(() => [
  "全部",
  ...new Set(m.audit.items.map((x) => x.act)),
]);
export const auditFiltered = computed(() =>
  m.audit.items.filter(
    (x) =>
      (auditAct.value === "全部" || x.act === auditAct.value) &&
      (!auditQ.value ||
        (x.actor + x.target + x.act)
          .toLowerCase()
          .includes(auditQ.value.toLowerCase())),
  ),
);

/* ── 多智能体拓扑（原 SVG 绘制逻辑改为计算几何） ── */
export const topo = computed(() => {
  const hub = m.multi.agents.find((a) => a.kind === "coordinator");
  const workers = m.multi.agents.filter((a) => a.kind === "worker");
  if (!hub) {
    return {
      boxes: [],
      edges: [],
      summary: "多智能体运行时未启用或数据未加载（GET /v1/multi-agent）",
    };
  }
  const hx = 60,
    hy = 105,
    hw = 170,
    hh = 64;
  const boxes = workers.map((w, i) => ({
    x: 460,
    y: 30 + i * 88,
    w: 170,
    h: 64,
    hub: false,
    label: w.name,
    sub: w.role,
  }));
  boxes.unshift({
    x: hx,
    y: hy,
    w: hw,
    h: hh,
    hub: true,
    label: hub.name,
    sub: hub.role + " · 中心调度",
  });
  const edges = workers.map((w, i) => ({
    x1: hx + hw,
    y1: hy + hh / 2,
    x2: 460,
    y2: 30 + i * 88 + 32,
    label: w.msgs + " msgs",
  }));
  return {
    boxes,
    edges,
    summary: `1 个 coordinator + ${workers.length} 个 worker · 消息总量 ${workers.reduce((s, w) => s + w.msgs, 0)} · 通过 POST /v1/multi-agent 派发协作 goal`,
  };
});

/* ── 弹窗通用 ── */
export function closeModal() {
  profileModal.open = false;
  delModal.open = false;
  domainModal.open = false;
}
export function onOverlayClick(e) {
  if (e.target === e.currentTarget) closeModal();
}

/* ── 全局错误兜底（原 showFatalError） ── */
export function pushFatal(err) {
  const msg = err && err.message ? err.message : String(err);
  const where =
    err && err.stack
      ? "\n  " + String(err.stack).split("\n").slice(0, 3).join("\n  ")
      : "";
  fatal.value.push(
    ("[" + (fatal.value.length + 1) + "] " + msg + where).slice(0, 2000),
  );
  console.error("[UA Runtime]", err);
}
export function initDashboard() {
  window.addEventListener("error", (e) => pushFatal(e.error || e));
  window.addEventListener("unhandledrejection", (e) =>
    pushFatal(e.reason || e),
  );
  const saved = localStorage.getItem("ua-view");
  const allViews = MAIN_VIEWS.concat(Object.keys(OPS_VIEWS));
  view.value = allViews.includes(saved) ? saved : "overview";
  loadedViews.add(view.value);
  const loader = VIEW_LOADERS[view.value];
  const run = loader ? loader() : loadOverview(m);
  run.catch((e) => {
    sessionsError.value = e.message;
  });
}

export const chatEventCache = reactive({});
/* 模板直接使用的 api.js 绑定经由 store 再导出（script setup 语义保持不变） */

// 模板直接使用的 api.js 绑定经由 store 再导出（保持 script setup 语义）
export {
  API_BASE,
  createState,
  normStatus,
  STATUS_MAP,
  loadOverview,
  apiLoadSessions,
  loadMetrics,
  loadSessionDetail,
  loadConfig,
  loadEval,
  loadCluster,
  loadMemory,
  memoryAdd,
  memoryRemove,
  loadCost,
  loadLogs,
  loadK8sOps,
  loadEcosystem,
  loadAudit,
  loadMulti,
  loadHealth,
  loadModelInfo,
  loadRuntimeConfig,
  createSession,
  sendMessage,
  pauseSession,
  resumeSession,
  apiCancelSession,
  profileCreate,
  profilePatch,
  profileRemove,
  putConfig,
  runEval,
};
