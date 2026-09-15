<script setup>
import { reactive, ref, computed, onMounted, nextTick } from 'vue'
import {
  API_BASE, createState, normStatus, STATUS_MAP, loadOverview, loadSessions as apiLoadSessions, loadMetrics, loadSessionDetail,
  loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove,
  loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth,
  loadModelInfo, loadRuntimeConfig,
  createSession, sendMessage, pauseSession, resumeSession, cancelSession as apiCancelSession,
  profileCreate, profilePatch, profileRemove, putConfig, runEval,
} from './api.js'

/* ── 全局状态（由真实 agentd API 填充） ── */
const m = reactive(createState())
const view = ref('overview')
const OPS_VIEWS = { eval: '评估工作台', cluster: '分布式集群', memory: '记忆管理', cost: '成本分析', logs: '日志与追踪', k8sops: 'K8s 运维', ecosystem: '生态与包', audit: '审计中心', multiagent: '多智能体', health: '健康中心' }
const opsOpen = ref(false)
const apiHost = API_BASE.replace(/^https?:\/\//, '')
const fatal = ref([])

/* toast */
const toastMsg = ref('')
let toastTimer = null
function toast(msg) {
  toastMsg.value = msg
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toastMsg.value = ''), 2200)
}

/* 视图切换：首次进入某视图时拉取真实数据 */
const MAIN_VIEWS = ['overview', 'session', 'config', 'chat']
const VIEW_LOADERS = {
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
}
const loadedViews = new Set()
function switchView(name) {
  view.value = name
  opsOpen.value = false
  localStorage.setItem('ua-view', name)
  if (VIEW_LOADERS[name] && !loadedViews.has(name)) {
    loadedViews.add(name)
    VIEW_LOADERS[name]().catch((e) => toast('加载失败：' + e.message))
  }
  if (name === 'chat') nextTick(scrollChat)
}
function reload(name) {
  if (VIEW_LOADERS[name]) VIEW_LOADERS[name]().catch((e) => toast('加载失败：' + e.message))
}
const opsBtnLabel = computed(() => OPS_VIEWS[view.value] || '运维中心')

/* 状态徽标：normStatus 已把 agentd goal_status 归一到 UI 的五种状态 */
function statusCls(s) { return (STATUS_MAP[normStatus(s)] || ['st-paused'])[0] }
function statusLabel(s) { return (STATUS_MAP[normStatus(s)] || [null, s])[1] }

/* ── 总览 ── */
const metricCards = computed(() => [
  { label: '会话总数', value: String(m.metrics.sessions), sub: 'GET /v1/sessions', trend: '' },
  { label: '任务成功率', value: Math.round(m.metrics.successRate * 100) + '%', sub: 'goal_completion_rate', trend: 'up' },
  { label: '工具调用', value: String(m.metrics.toolCalls), sub: '累计', trend: '' },
  { label: '平均单次成本', value: '$' + m.metrics.avgCost.toFixed(3), sub: 'GET /v1/cost', trend: '' },
])
const sessionsError = ref('')
const loadingSessions = ref(false)
async function loadSessions() {
  loadingSessions.value = true
  try {
    await apiLoadSessions(m)
    sessionsError.value = ''
  } catch (e) { sessionsError.value = e.message }
  loadingSessions.value = false
}
/* 近 7 日任务量：由真实会话 created_at 聚合（loadActivity 填充） */
const activity = computed(() => m.activity || { values: [0, 0, 0, 0, 0, 0, 0], days: [], max: 0 })
function doRefresh() { reload('overview') }

/* ── 会话详情 ── */
const currentSession = ref(null)
const expanded = reactive({})
function openSession(s) {
  currentSession.value = s
  switchView('session')
  for (const k in expanded) delete expanded[k]
  loadSessionDetail(m, s.id).catch((e) => toast('事件加载失败：' + e.message))
}
function confirmPending(ok) {
  const s = currentSession.value
  resumeSession(s.id, ok ? { confirmed: true } : { confirmed: false })
    .then(() => { s.confirm = false; s.status = ok ? 'running' : 'paused'; toast(ok ? '已发送确认 · POST /resume' : '已拒绝 · POST /resume') })
    .catch((e) => toast('确认失败：' + e.message))
}
function cancelSession() {
  const s = currentSession.value
  apiCancelSession(s.id)
    .then(() => { s.status = 'failed'; toast('POST /cancel 已发送') })
    .catch((e) => toast('取消失败：' + e.message))
}
function lifecycle(action) {
  const s = currentSession.value
  if (!s) return
  const fn = action === 'pause' ? pauseSession : () => resumeSession(s.id)
  fn(s.id)
    .then(() => toast(`POST /${action} 已发送`))
    .catch((e) => toast(`${action} 失败：` + e.message))
}
const sessionSummary = computed(() => {
  const s = currentSession.value
  return s ? [['目标', s.goal], ['Profile', s.profile], ['状态', s.status], ['步骤', String(s.steps)], ['耗时', s.dur]] : []
})

/* ── 对话（真实会话：chat = agentd session，消息 = 会话事件） ── */
const chatFilter = ref('全部')
const activeChatId = ref(null)
const chatSessions = computed(() => m.sessions.map((s) => ({ id: s.id, title: s.goal, profile: s.profile, time: s.raw?.created_at ? new Date(s.raw.created_at).toTimeString().slice(0, 5) : '—', msgs: chatMsgs(s) })))
const chatEventCache = reactive({})
function chatMsgs(chat) {
  const evs = chatEventCache[chat.id] || []
  return [
    { role: 'user', text: chat.title },
    ...evs.map((e) => ({ role: 'agent', text: `<code>${e.type || 'event'}</code>` })),
  ]
}
const CHAT_PROFILES = ['全部', 'default']
const activeChat = computed(() => chatSessions.value.find((c) => c.id === activeChatId.value) || null)
const chatInput = ref('')
const sending = ref(false)
const replyIdx = { i: 0 }
const chatMsgsEl = ref(null)
const chatInputEl = ref(null)
const filteredChats = computed(() => chatSessions.value.filter(c => chatFilter.value === '全部' || c.profile === chatFilter.value))
function scrollChat() { if (chatMsgsEl.value) chatMsgsEl.value.scrollTop = chatMsgsEl.value.scrollHeight }
function newChat() { toast('输入第一条消息后将创建真实会话（POST /v1/sessions）') }
function autoGrow(e) {
  const el = e.target
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 140) + 'px'
}
function sendChat() {
  const text = chatInput.value.trim()
  if (!text || sending.value) return
  sending.value = true
  const ensure = activeChat.value
    ? Promise.resolve(activeChat.value)
    : createSession(text, chatFilter.value === '全部' ? 'default' : chatFilter.value)
        .then((d) => {
          const sid = (d.session && d.session.id) || d.session_id || d.id
          activeChatId.value = sid
          return apiLoadSessions(m).then(() => chatSessions.value.find((c) => c.id === sid))
        })
  ensure
    .then((chat) => {
      if (!chat) return
      return sendMessage(chat.id, text)
        .then(() => apiGetEvents(chat.id))
        .catch((e) => toast('发送失败：' + e.message))
    })
    .catch((e) => toast('会话创建失败：' + e.message))
    .finally(() => {
      sending.value = false
      chatInput.value = ''
      if (chatInputEl.value) chatInputEl.value.style.height = 'auto'
      nextTick(scrollChat)
    })
}
function apiGetEvents(sid) {
  return import('./api.js').then((mod) => mod.apiGet(`/v1/sessions/${sid}/events`)).then((d) => {
    chatEventCache[sid] = (mod.pick ? mod.pick(d, 'events') : d.events || []).slice(-20)
    nextTick(scrollChat)
  })
}
function onChatKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat() }
}

/* ── 配置：Profile CRUD / Domains / Policy ── */
const profileModal = reactive({ open: false, editing: null })
const pmForm = reactive({ name: '', desc: '', domains: [],
  model: { provider: 'scripted', name: '', endpoint: '', api_key_env: '', timeout_seconds: 30 } })
const MODEL_PROVIDERS = ['scripted', 'json_http', 'openai_chat_completions', 'openai_responses']
function openProfileModal(name) {
  profileModal.editing = name || null
  const p = name ? m.profiles.find((x) => x.name === name) : null
  pmForm.name = name || ''
  pmForm.desc = p ? p.desc.replace(/^(K8s 运维|通用离线) Profile · /, '') : ''
  pmForm.domains = m.profiles.find((x) => x.name === name)?.domains?.length ? [...m.profiles.find((x) => x.name === name).domains] : m.domains.map((d) => d.name)
  const existing = p && p.raw && p.raw.runtime && p.raw.runtime.model
  pmForm.model = existing
    ? { provider: existing.provider || 'scripted', name: existing.name || '', endpoint: existing.endpoint || '', api_key_env: existing.api_key_secret || '', timeout_seconds: existing.timeout_seconds || 30 }
    : { provider: 'scripted', name: '', endpoint: '', api_key_env: '', timeout_seconds: 30 }
  pmError.value = ''
  profileModal.open = true
}
function saveProfile() {
  const name = pmForm.name.trim()
  const valid = /^[a-z0-9][a-z0-9-]*$/.test(name)
  const dup = !profileModal.editing && m.profiles.some((p) => p.name === name)
  if (!valid) { pmError.value = '名称必填，且只能含小写字母、数字与连字符'; return }
  if (dup) { pmError.value = '同名 Profile 已存在'; return }
  // agentd 校验要求 profile domain 为 {name, version} 对象
  const domainObjs = pmForm.domains.map((n) => ({
    name: n,
    version: (m.domains.find((d) => d.name === n) || {}).version || '0.1.0',
  }))
  if (pmForm.model.provider !== 'scripted' && !pmForm.model.api_key_env.trim()) {
    pmError.value = '非 scripted Provider 需要填写 API Key 环境变量名'
    return
  }
  const payload = { name, domains: domainObjs, desc: pmForm.desc.trim() || '自定义 Profile · ' + pmForm.domains.join('/'), model: { ...pmForm.model } }
  const op = profileModal.editing ? profilePatch(profileModal.editing, payload) : profileCreate(payload)
  op.then(() =>
    loadConfig(m).then(() => {
      profileModal.open = false
      toast((profileModal.editing ? 'Profile 已更新 · PATCH /v1/profiles/' + name : 'Profile 已创建 · POST /v1/profiles') + '（需重启/重载 agentd 后生效）')
    }))
    .catch((e) => toast('保存失败：' + e.message))
}
const delModal = reactive({ open: false, name: null })
const delDetail = computed(() => {
  if (!delModal.name) return ''
  const p = m.profiles.find(x => x.name === delModal.name)
  return `将删除 Profile「${delModal.name}」（${p ? p.desc : ''}）。关联的运行中会话不受影响，但该配置无法恢复。此操作将写入审计链。`
})
function askDelete(name) { delModal.name = name; delModal.open = true }
function confirmDelete() {
  if (!delModal.name) return
  profileRemove(delModal.name)
    .then(() => loadConfig(m))
    .then(() => { toast('已删除 · DELETE /v1/profiles/' + delModal.name); delModal.open = false; delModal.name = null })
    .catch((e) => toast('删除失败：' + e.message))
}
const domainModal = reactive({ open: false, index: -1 })
const domainDetail = computed(() => domainModal.index >= 0 ? m.domains[domainModal.index] : null)
function openDomainDetail(i) { domainModal.index = i; domainModal.open = true }
function domainUsedBy(d) {
  const used = m.profiles.filter((p) => (p.domains || []).includes(d.name)).map((p) => p.name)
  return used.length ? used : null
}
const policies = reactive([
  { key: 'mutation-confirm', label: '变更动作需人工确认', desc: '删除 / 扩缩容等 mutation 触发 WAITING_FOR_CONFIRMATION', on: true },
  { key: 'dryrun-default', label: '默认 dry-run 预检', desc: '运行前对集群执行只读预检', on: true },
  { key: 'audit-chain', label: '审计哈希链', desc: '会话事件写入可校验的审计链', on: true },
])

function togglePolicy() {
  putConfig(policies).then(() => toast('已保存为本地偏好（部署级 Policy 经 deployment.json 管理）'))
}
function toggleDomain(i, checked) {
  const d = m.domains[i]
  toast('Domain 启用/停用经 profile 绑定管理 · PUT /v1/domains/' + d.name + '/profiles')
  d.active = checked
}

/* ── 评估 ── */
const evalByDataset = computed(() => {
  const byDs = {}
  m.evals.reports.forEach(r => (byDs[r.dataset] = byDs[r.dataset] || []).push(r))
  return byDs
})
function pct(x) { return Math.round(x * 100) }

/* ── 记忆 ── */
const memSearch = ref('')
const memList = computed(() => m.memories.filter(x => !memSearch.value || x.text.includes(memSearch.value)))
function addMemory() {
  memoryAdd('手动新增的记忆 · ' + new Date().toLocaleTimeString())
    .then(() => loadMemory(m))
    .then(() => toast('已新增 · POST /v1/memory'))
    .catch((e) => toast('新增失败：' + e.message))
}
function delMemory(id) {
  memoryRemove(id)
    .then(() => loadMemory(m))
    .then(() => toast('已删除 · DELETE /v1/memory/' + id))
    .catch((e) => toast('删除失败：' + e.message))
}

/* ── 成本 / 日志 / K8s / 生态 ── */
const costMax = computed(() => Math.max(...m.cost.byModel.map(x => x[1])))
function openTraceSession(sid) {
  const s = m.sessions.find((x) => x.id === sid)
  if (s) openSession(s)
  else toast('该会话不存在或已过期')
}
function installPkg(name) {
  import('./api.js').then((mod) => mod.apiPost('/v1/ecosystem/install', { name }))
    .then(() => toast('安装任务已创建 · POST /v1/ecosystem/install ' + name))
    .catch((e) => toast('安装失败：' + e.message))
}

/* ── 审计 ── */
const auditQ = ref('')
const auditAct = ref('全部')
const auditActs = computed(() => ['全部', ...new Set(m.audit.items.map(x => x.act))])
const auditFiltered = computed(() => m.audit.items.filter(x =>
  (auditAct.value === '全部' || x.act === auditAct.value) &&
  (!auditQ.value || (x.actor + x.target + x.act).toLowerCase().includes(auditQ.value.toLowerCase()))))

/* ── 多智能体拓扑（原 SVG 绘制逻辑改为计算几何） ── */
const topo = computed(() => {
  const hub = m.multi.agents.find((a) => a.kind === 'coordinator')
  const workers = m.multi.agents.filter((a) => a.kind === 'worker')
  if (!hub) {
    return { boxes: [], edges: [], summary: '多智能体运行时未启用或数据未加载（GET /v1/multi-agent）' }
  }
  const hx = 60, hy = 105, hw = 170, hh = 64
  const boxes = workers.map((w, i) => ({ x: 460, y: 30 + i * 88, w: 170, h: 64, hub: false, label: w.name, sub: w.role }))
  boxes.unshift({ x: hx, y: hy, w: hw, h: hh, hub: true, label: hub.name, sub: hub.role + ' · 中心调度' })
  const edges = workers.map((w, i) => ({
    x1: hx + hw, y1: hy + hh / 2, x2: 460, y2: 30 + i * 88 + 32, label: w.msgs + ' msgs',
  }))
  return { boxes, edges, summary: `1 个 coordinator + ${workers.length} 个 worker · 消息总量 ${workers.reduce((s, w) => s + w.msgs, 0)} · 通过 POST /v1/multi-agent 派发协作 goal` }
})

/* ── 弹窗通用 ── */
function closeModal() {
  profileModal.open = false; delModal.open = false; domainModal.open = false
}
function onOverlayClick(e) { if (e.target === e.currentTarget) closeModal() }

/* ── 全局错误兜底（原 showFatalError） ── */
function pushFatal(err) {
  const msg = (err && err.message) ? err.message : String(err)
  const where = err && err.stack ? '\n  ' + String(err.stack).split('\n').slice(0, 3).join('\n  ') : ''
  fatal.value.push(('[' + (fatal.value.length + 1) + '] ' + msg + where).slice(0, 2000))
  console.error('[UA Runtime]', err)
}
onMounted(() => {
  window.addEventListener('error', e => pushFatal(e.error || e))
  window.addEventListener('unhandledrejection', e => pushFatal(e.reason || e))
  const saved = localStorage.getItem('ua-view')
  const allViews = MAIN_VIEWS.concat(Object.keys(OPS_VIEWS))
  view.value = allViews.includes(saved) ? saved : 'overview'
  loadedViews.add(view.value)
  const loader = VIEW_LOADERS[view.value]
  const run = loader ? loader() : loadOverview(m)
  run.catch((e) => { sessionsError.value = e.message })
})
</script>

<template>
  <div>
    <!-- 全局错误兜底 -->
    <div v-if="fatal.length" class="fatal-error" role="alert" aria-live="assertive">
      <div class="fe-head">
        <span class="fe-title">页面发生错误</span>
        <button type="button" class="fe-close" aria-label="关闭错误提示" @click="fatal = []">✕</button>
      </div>
      <div class="fe-detail">{{ fatal.join('\n') }}</div>
      <div class="fe-hint">功能可能部分不可用；接入真实 agentd 时请把该信息提供给后端排查。</div>
    </div>

    <!-- 顶栏 -->
    <header class="topbar" data-od-id="topbar">
      <div class="topbar-inner">
        <span class="brand"><span class="brand-mark">UA</span>Universal-Agent</span>
        <nav role="tablist" aria-label="主视图">
          <button role="tab" :aria-selected="view === 'overview'" data-view="overview" @click="switchView('overview')">总览</button>
          <button role="tab" :aria-selected="view === 'chat'" data-view="chat" @click="switchView('chat')">对话</button>
          <button role="tab" :aria-selected="view === 'session'" data-view="session" @click="switchView('session')">会话详情</button>
          <button role="tab" :aria-selected="view === 'config'" data-view="config" @click="switchView('config')">配置与运行时</button>
        </nav>
        <div class="navdrop" id="navdrop">
          <button role="tab" :aria-selected="!!OPS_VIEWS[view]" aria-haspopup="true" :aria-expanded="String(opsOpen)"
            id="nav-ops-btn" data-od-id="nav-ops" @click="opsOpen = !opsOpen">
            {{ opsBtnLabel }}<span class="nav-caret" aria-hidden="true">▾</span>
          </button>
          <div class="nav-menu" :class="{ open: opsOpen }" id="nav-ops-menu" role="menu" aria-label="运维视图">
            <button v-for="(label, name) in OPS_VIEWS" :key="name" role="menuitem"
              :class="{ active: view === name }" :data-view="name" @click="switchView(name)">{{ label }}</button>
          </div>
        </div>
        <span class="spacer"></span>
        <span class="env-pill"><span class="dot"></span>agentd · <span class="num">{{ apiHost }}</span></span>
      </div>
    </header>

    <div class="shell">
      <!-- 视图〇：对话 -->
      <section v-show="view === 'chat'" class="view" :class="{ active: view === 'chat' }" id="view-chat" role="tabpanel">
        <div class="chat-layout" data-od-id="chat-layout">
          <aside class="chat-history" data-od-id="chat-history">
            <div class="ch-head">
              <h3>历史对话</h3>
              <button class="btn btn-primary btn-sm" id="btn-new-chat" data-od-id="btn-new-chat" @click="newChat">＋ 新建对话</button>
              <div class="ch-filter" id="chat-profile-filter" role="group" aria-label="按 Profile 过滤">
                <button v-for="p in CHAT_PROFILES" :key="p" :class="{ active: chatFilter === p }" @click="chatFilter = p">{{ p }}</button>
              </div>
            </div>
            <div class="ch-list" id="chat-history-list" data-od-id="chat-history-list">
              <div v-if="!filteredChats.length" class="empty">暂无该 Profile 的对话</div>
              <button v-for="c in filteredChats" :key="c.id" class="ch-item" :class="{ active: activeChatId === c.id }"
                :data-chat="c.id" @click="activeChatId = c.id">
                <div class="t">{{ c.title }}</div>
                <div class="m"><span class="p">{{ c.profile }}</span><span>{{ c.time }}</span></div>
              </button>
            </div>
          </aside>
          <div class="chat-main" data-od-id="chat-main">
            <div class="chat-msgs" id="chat-msgs" ref="chatMsgsEl" data-od-id="chat-msgs" aria-live="polite">
              <div v-if="!activeChat" class="empty" style="margin:auto">选择左侧历史对话，或新建一个对话开始</div>
              <template v-else>
                <div v-for="(msg, mi) in activeChat.msgs" :key="mi" class="msg" :class="msg.role === 'user' ? 'user' : 'agent'">
                  <span class="who">{{ msg.role === 'user' ? '你' : 'Agent · ' + activeChat.profile }}</span>
                  <div v-for="(t, ti) in msg.tools || []" :key="ti" class="msg-tool">⚙ 调用 <b>{{ t }}</b></div>
                  <!-- prettier-ignore -->
                  <div class="bubble" v-html="msg.text"></div>
                </div>
                <div v-if="sending" class="msg agent">
                  <span class="who">Agent · {{ activeChat.profile }}</span>
                  <div class="bubble"><span class="typing-dots"><i></i><i></i><i></i></span></div>
                </div>
              </template>
            </div>
            <div class="chat-inputbar">
              <div class="chat-inputrow">
                <textarea ref="chatInputEl" class="textarea" id="chat-input" rows="1" v-model="chatInput"
                  @keydown="onChatKeydown" @input="autoGrow"
                  placeholder="输入指令，例如：排查 default 命名空间下异常的 Pod…" aria-label="消息输入"></textarea>
                <button class="btn btn-primary" id="btn-chat-send" :disabled="sending" @click="sendChat">发送</button>
              </div>
              <div class="chat-hint">Enter 发送 · Shift+Enter 换行 · 运行事件同步写入 <span class="num">/v1/sessions/{id}/events</span></div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图一：总览 -->
      <section v-show="view === 'overview'" class="view" :class="{ active: view === 'overview' }" id="view-overview" role="tabpanel">
        <h2 class="viewtitle">总览</h2>
        <div class="grid g-metrics" id="metrics" data-od-id="metrics-row">
          <div v-for="c in metricCards" :key="c.label" class="card metric" :data-od-id="'metric-' + c.label">
            <div class="m-label">{{ c.label }}</div>
            <div class="m-value num">{{ c.value }}</div>
            <div class="m-sub">
              <span v-if="c.trend === 'up'" class="trend-up">↑</span>
              <span v-else-if="c.trend === 'down'" class="trend-down">↓</span>{{ c.sub }}
            </div>
          </div>
        </div>
        <div class="grid g-main" style="margin-top:16px">
          <div class="card" data-od-id="sessions-card">
            <div class="card-head">
              <h3>最近会话</h3>
              <button class="btn btn-secondary btn-sm" id="btn-refresh" @click="doRefresh">刷新</button>
            </div>
            <div v-if="sessionsError" class="net-error" data-od-id="sessions-error" role="alert">
              <span>无法连接 agentd：{{ sessionsError }}</span>
              <button class="btn btn-secondary btn-sm" data-od-id="btn-retry" @click="loadSessions">重试</button>
            </div>
            <div v-else-if="!m.sessions.length" class="empty" data-od-id="sessions-empty">
              <div class="empty-title">暂无会话</div>创建第一个 Agent 会话开始排查任务<br><br>
              <button class="btn btn-secondary btn-sm" data-od-id="empty-new-chat" @click="switchView('chat'); newChat()">新建对话</button>
            </div>
            <div v-else class="ses-list" id="session-rows" data-od-id="session-list">
              <div v-for="r in m.sessions" :key="r.id" class="ses-row" :class="{ selected: currentSession === r }"
                tabindex="0" role="button" @click="openSession(r)"
                @keydown.enter.prevent="openSession(r)" @keydown.space.prevent="openSession(r)">
                <div class="ses-main">
                  <div class="ses-goal">{{ r.goal }}</div>
                  <div class="ses-meta">
                    <span class="ses-profile">{{ r.profile }}</span>
                    <span class="ses-nums">{{ r.steps }} 步 · {{ r.dur }}</span>
                  </div>
                </div>
                <span class="ses-status"><span class="status" :class="statusCls(r.confirm ? 'waiting' : r.status)">{{ statusLabel(r.confirm ? 'waiting' : r.status) }}</span></span>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="runs-chart-card">
            <div class="card-head"><h3>近 7 日任务量</h3><span class="tag">GET /v1/sessions · created_at 聚合</span></div>
            <div class="bars" id="runs-bars">
              <div v-for="(v, i) in activity.values" :key="i" class="bar" :class="{ hot: v === activity.max && v > 0 }" tabindex="0" role="img"
                :aria-label="activity.days[i] + '：' + v + ' 个任务'" :style="{ height: Math.round(v / (activity.max || 1) * 100) + '%' }">
                <span class="tip">{{ v }} 个任务</span>
              </div>
            </div>
            <div class="bars-x" id="runs-x"><span v-for="d in activity.days" :key="d">{{ d }}</span></div>
          </div>
        </div>
      </section>

      <!-- 视图二：会话详情 -->
      <section v-show="view === 'session'" class="view" :class="{ active: view === 'session' }" id="view-session" role="tabpanel">
        <div class="card-head" style="margin-bottom:4px">
          <h2 class="viewtitle" style="margin:0">会话详情</h2>
          <span class="meta num" id="session-id-label">{{ currentSession && currentSession.id }}</span>
        </div>
        <div id="confirm-slot" data-od-id="confirm-banner">
          <div v-if="currentSession && currentSession.confirm" class="confirm-banner">
            <div class="cb-text">
              <span class="cb-title">Runtime 请求确认：</span>待执行
              <!-- prettier-ignore -->
              <code class="num" style="font-size:12px;background:var(--bg);border:1px solid var(--border);border-radius:5px;padding:1px 6px">{{ currentSession.pending.action }}</code>（{{ currentSession.pending.risk }} · Policy 拦截）
            </div>
            <button class="btn btn-primary btn-sm" @click="confirmPending(true)">确认执行</button>
            <button class="btn btn-secondary btn-sm" @click="confirmPending(false)">拒绝</button>
          </div>
        </div>
        <div class="grid g-main">
          <div class="card" data-od-id="timeline-card">
            <div class="card-head">
              <h3>运行事件</h3>
              <span class="tag">SSE /v1/sessions/{id}/events/stream</span>
            </div>
            <ul class="timeline" id="timeline">
              <li v-for="(e, i) in m.events" :key="i" :class="'tl-ev-' + e.t">
                <div class="tl-head">
                  <button v-if="e.d" class="tl-toggle" :aria-expanded="String(!!expanded[i])" aria-label="展开详情"
                    @click="expanded[i] = !expanded[i]">▸</button>
                  <span class="tl-type">{{ e.t }}</span><span class="meta">{{ e.at }}</span>
                </div>
                <!-- prettier-ignore -->
                <div class="tl-body" v-html="e.text"></div>
                <div v-if="e.d" v-show="expanded[i]" class="tl-detail" :id="'tl-detail-' + i">
                  <template v-if="e.d.model">
                    <div class="llm-meta"><span>{{ e.d.model }}</span><span>{{ e.d.tokens }}</span><span>{{ e.d.cost }}</span><span>{{ e.d.dur }}</span></div>
                    <div class="kv"><span class="k">Prompt</span></div><pre>{{ e.d.prompt }}</pre>
                    <div class="kv"><span class="k">输出</span></div><pre>{{ e.d.completion }}</pre>
                  </template>
                  <template v-else>
                    <div class="kv"><span class="k">工具</span><span class="num">{{ e.d.tool }}</span></div>
                    <div class="kv"><span class="k">输入</span></div><pre>{{ e.d.input }}</pre>
                    <div class="kv"><span class="k">输出</span></div><pre>{{ e.d.output }}</pre>
                    <div class="kv"><span class="k">耗时</span><span class="num">{{ e.d.dur }}</span><span class="k" style="width:auto">重试</span><span class="num">第 {{ e.d.attempt }} 次</span></div>
                  </template>
                </div>
              </li>
            </ul>
          </div>
          <div class="stack" style="display:flex;flex-direction:column;gap:16px">
            <div class="card" data-od-id="session-summary-card">
              <h3 style="margin-bottom:10px">会话摘要</h3>
              <div id="session-summary">
                <div v-for="[k, v] in sessionSummary" :key="k" class="switch-row">
                  <div><div class="s-label">{{ k }}</div></div>
                  <span class="num" style="font-size:13px;max-width:60%;text-align:right">{{ v }}</span>
                </div>
              </div>
            </div>
            <div class="card" data-od-id="evidence-card">
              <div class="card-head"><h3>Evidence</h3><span class="tag">GET /evidence</span></div>
              <div id="evidence-list">
                <div v-for="ev in m.evidence" :key="ev.name" class="check-row">
                  <span class="check-ico" :class="ev.ok ? 'ck-ok' : 'ck-fail'">{{ ev.ok ? '✓' : '!' }}</span>
                  <div>
                    <div class="c-name num">{{ ev.name }}</div>
                    <div class="c-detail">{{ ev.detail }}</div>
                  </div>
                </div>
              </div>
            </div>
            <div class="card" data-od-id="session-controls-card">
              <h3 style="margin-bottom:12px">生命周期控制</h3>
              <div class="row" style="display:flex;gap:10px;flex-wrap:wrap">
                <button class="btn btn-secondary btn-sm" id="btn-pause" @click="lifecycle('pause')">暂停</button>
                <button class="btn btn-secondary btn-sm" id="btn-resume" @click="lifecycle('resume')">恢复</button>
                <button class="btn btn-danger btn-sm" id="btn-cancel" @click="cancelSession">取消会话</button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图三：配置与运行时 -->
      <section v-show="view === 'config'" class="view" :class="{ active: view === 'config' }" id="view-config" role="tabpanel">
        <h2 class="viewtitle">配置与运行时</h2>
        <div class="grid g-config">
          <div class="card" data-od-id="model-card">
            <div class="card-head"><h3>模型配置 · 运行时</h3><span class="tag">GET /v1/config → model</span></div>
            <template v-if="m.runtimeConfig.available && m.runtimeConfig.model">
              <div class="switch-row"><div class="s-label">Provider</div><span class="num">{{ m.runtimeConfig.model.provider }}</span></div>
              <div class="switch-row"><div class="s-label">模型名称</div><span class="num">{{ m.runtimeConfig.model.name }}</span></div>
              <div v-if="m.runtimeConfig.model.endpoint" class="switch-row"><div class="s-label">Endpoint</div><span class="num" style="max-width:70%;word-break:break-all">{{ m.runtimeConfig.model.endpoint }}</span></div>
              <div v-if="m.runtimeConfig.model.api_key_secret" class="switch-row"><div class="s-label">API Key Secret</div><span class="num">{{ m.runtimeConfig.model.api_key_secret }}</span></div>
              <div class="switch-row"><div class="s-label">超时</div><span class="num">{{ m.runtimeConfig.model.timeout_seconds }}s</span></div>
            </template>
            <div v-else class="empty">
              <div class="empty-title">运行时配置不可读</div>
              GET /v1/config 返回 503（部署未启用 deployment config store）
            </div>
            <div style="font-size:12.5px;color:var(--muted);margin-top:12px">
              修改途径（需重启 agentd 生效）：<code class="num">agent init --model-provider openai_chat_completions --model-name gpt-4o-mini --model-api-key-env OPENAI_API_KEY</code>
              或在 profile-config 的 <code class="num">model</code> 段配置；API 不提供 model 写入。
            </div>
          </div>
          <div class="card" data-od-id="model-observed-card">
            <div class="card-head"><h3>实际生效的模型调用</h3><span class="tag">GET /v1/sessions/{'{id}'}/llm-calls</span></div>
            <div v-if="!m.modelInfo.calls.length" class="empty">暂无 LLM 调用记录（当前为 scripted 离线模型时不产生真实调用）</div>
            <div v-for="(c, i) in m.modelInfo.calls" :key="i" class="mono-row">
              <span class="lbl">{{ c.provider }} / {{ c.model }}</span>
              <span style="color:var(--muted)">{{ c.sid.slice(0, 18) }}…</span>
              <span class="num">{{ c.tokens }} tok · ${{ c.cost.toFixed(4) }}</span>
              <span class="meta">{{ c.at }}</span>
            </div>
          </div>

          <div class="card" data-od-id="doctor-card">
            <div class="card-head"><h3>运行时体检 · doctor</h3><span class="tag">GET /v1/doctor</span></div>
            <div id="doctor-list">
              <div v-for="c in m.doctor" :key="c.name" class="check-row">
                <span class="check-ico" :class="'ck-' + c.level">{{ c.level === 'ok' ? '✓' : c.level === 'warn' ? '!' : '×' }}</span>
                <div><div class="c-name">{{ c.name }}</div><div class="c-detail">{{ c.detail }}</div></div>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="profiles-card">
            <div class="card-head">
              <h3>Agent Profile</h3>
              <div class="row-actions">
                <span class="tag">GET /v1/profiles</span>
                <button class="btn btn-primary btn-sm" id="btn-new-profile" data-od-id="btn-new-profile" @click="openProfileModal(null)">＋ 新建</button>
              </div>
            </div>
            <div id="profile-list">
              <div v-for="(p, i) in m.profiles" :key="p.name" class="conf-row">
                <div class="conf-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="8" r="4"/><path d="M4 20c1.5-3.5 4.5-5 8-5s6.5 1.5 8 5"/></svg></div>
                <div class="grow">
                  <div class="name">{{ p.name }} <span v-if="p.model" class="tag">{{ p.model }}</span> <span v-if="p.builtin" class="tag">内置</span></div>
                  <div class="desc">{{ p.desc }}</div>
                </div>
                <div class="row-actions">
                  <button class="btn btn-secondary btn-sm" @click="openProfileModal(p.name)">编辑</button>
                  <button v-if="!p.builtin" class="btn btn-danger btn-sm" @click="askDelete(p.name)">删除</button>
                  <button class="btn btn-secondary btn-sm" @click="toast('已应用 ' + p.name + ' · 切换 Profile')">{{ i === 0 ? '当前' : '切换' }}</button>
                </div>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="domains-card">
            <div class="card-head"><h3>Domains 与 Tools</h3><span class="tag">GET /v1/domains · /v1/tools</span></div>
            <div id="domain-list">
              <div v-for="(d, i) in m.domains" :key="d.name" class="conf-row clickable" role="button" tabindex="0"
                :data-od-id="'domain-row-' + d.name" aria-label="查看 Domain 详情"
                @click="openDomainDetail(i)" @keydown.enter.prevent="openDomainDetail(i)" @keydown.space.prevent="openDomainDetail(i)">
                <div class="conf-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><circle cx="7.5" cy="7.5" r="1" fill="currentColor" stroke="none"/><circle cx="7.5" cy="16.5" r="1" fill="currentColor" stroke="none"/></svg></div>
                <div class="grow">
                  <div class="name">{{ d.name }} · {{ d.tools ? d.tools.length : 0 }} tools</div>
                  <div class="desc">{{ d.desc }}</div>
                </div>
                <label class="switch" @click.stop>
                  <input type="checkbox" :checked="d.active" :aria-label="'启用 ' + d.name" @change="toggleDomain(i, $event.target.checked)">
                  <span class="track"><span class="knob"></span></span>
                </label>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="policies-card">
            <div class="card-head"><h3>Policy 偏好</h3><span class="tag">PUT /v1/config</span></div>
            <div id="policy-toggles">
              <div v-for="p in policies" :key="p.key" class="switch-row">
                <div><div class="s-label">{{ p.label }}</div><div class="s-desc">{{ p.desc }}</div></div>
                <label class="switch">
                  <input type="checkbox" v-model="p.on" @change="togglePolicy">
                  <span class="track"><span class="knob"></span></span>
                </label>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图四：评估工作台 -->
      <section v-show="view === 'eval'" class="view" :class="{ active: view === 'eval' }" id="view-eval" role="tabpanel">
        <div class="card-head" style="margin-bottom:4px">
          <h2 class="viewtitle" style="margin:0">评估工作台</h2>
          <div class="row-actions"><span class="tag">POST /v1/eval/run</span>
            <button class="btn btn-primary btn-sm" id="btn-eval-run" @click="runEval().then(() => toast('评估已启动 · POST /v1/eval/run')).catch((e) => toast('评估启动失败：' + e.message))">▶ 运行评估</button></div>
        </div>
        <div class="grid g-main">
          <div class="card" data-od-id="eval-reports-card">
            <div class="card-head"><h3>评估报告</h3><span class="tag">GET /v1/eval/reports</span></div>
            <div id="eval-reports">
              <div v-for="r in m.evals.reports" :key="r.id" class="eval-row">
                <span class="lbl">{{ r.id }}</span>
                <span>{{ r.dataset }} · {{ r.profile }}</span>
                <span class="scorebar" role="img" :aria-label="'通过率 ' + pct(r.score) + '%'"><i :style="{ width: pct(r.score) + '%' }"></i></span>
                <span class="num">{{ pct(r.score) }}%</span>
                <span class="muted">{{ r.pass }}/{{ r.total }} · {{ r.at }}</span>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="eval-compare-card">
            <div class="card-head"><h3>多 Profile 对比</h3><span class="tag">POST /v1/eval/compare</span></div>
            <div id="eval-compare">
              <template v-for="(rows, ds) in evalByDataset" :key="ds">
                <div class="eval-ds-name">{{ ds }}</div>
                <div v-for="r in rows" :key="r.id" class="eval-compare-row">
                  <span class="lbl">{{ r.profile }}</span>
                  <span class="scorebar" role="img" :aria-label="r.profile + ' 通过率 ' + pct(r.score) + '%'"><i :style="{ width: pct(r.score) + '%' }"></i></span>
                  <span class="num" style="font-weight:600">{{ pct(r.score) }}%</span>
                </div>
              </template>
            </div>
          </div>
          <div class="card" data-od-id="eval-datasets-card">
            <div class="card-head"><h3>数据集</h3><span class="tag">GET /v1/eval/datasets</span></div>
            <div id="eval-datasets">
              <div v-for="d in m.evals.datasets" :key="d.name" class="eval-ds-row">
                <span class="lbl">{{ d.name }}</span>
                <span style="color:var(--muted)">{{ d.desc }}</span>
                <span class="num">{{ d.cases }} 用例</span><span class="muted">{{ d.updated }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图五：分布式集群 -->
      <section v-show="view === 'cluster'" class="view" :class="{ active: view === 'cluster' }" id="view-cluster" role="tabpanel">
        <h2 class="viewtitle">分布式集群</h2>
        <div class="grid g-main">
          <div class="card" data-od-id="cluster-workers-card">
            <div class="card-head"><h3>Workers</h3><span class="tag">GET /v1/distributed/snapshot</span></div>
            <div id="cluster-workers">
              <div v-for="w in m.cluster.workers" :key="w.name" class="mono-row">
                <span class="lbl">{{ w.name }}</span>
                <span style="color:var(--muted)">{{ w.region }} · {{ w.sessions }} 会话</span>
                <span class="loadbar"><i :class="{ off: w.status !== 'online' }" :style="{ width: Math.round(w.load * 100) + '%' }"></i></span>
                <span v-if="w.status === 'online'" class="pill pill-ok">online</span>
                <span v-else class="pill pill-off">offline</span>
              </div>
            </div>
          </div>
          <div class="stack" style="display:flex;flex-direction:column;gap:16px">
            <div class="card" data-od-id="cluster-locks-card">
              <div class="card-head"><h3>分布式锁</h3><span class="tag">GET /v1/distributed/locks</span></div>
              <div id="cluster-locks">
                <div v-for="l in m.cluster.locks" :key="l.res" class="mono-row">
                  <span class="lbl">{{ l.res }}</span>
                  <span style="color:var(--muted)">{{ l.holder }} · ttl {{ l.ttl }}</span>
                  <span v-if="l.held" class="pill pill-mutation">held</span>
                  <span v-else class="pill pill-readonly">released</span>
                </div>
              </div>
            </div>
            <div class="card" data-od-id="cluster-goals-card">
              <div class="card-head"><h3>Goal 调度队列</h3><span class="tag">GET /v1/distributed/goals</span></div>
              <div id="cluster-goals">
                <div v-for="g in m.cluster.goals" :key="g.id" class="mono-row">
                  <span class="lbl">{{ g.id }}</span><span>{{ g.title }}</span>
                  <span style="color:var(--muted)">{{ g.worker }}</span>
                  <span class="status" :class="statusCls(g.state)">{{ statusLabel(g.state) }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图六：记忆管理 -->
      <section v-show="view === 'memory'" class="view" :class="{ active: view === 'memory' }" id="view-memory" role="tabpanel">
        <div class="card-head" style="margin-bottom:4px">
          <h2 class="viewtitle" style="margin:0">记忆管理</h2>
          <div class="row-actions"><span class="tag">GET · POST · DELETE /v1/memory</span></div>
        </div>
        <div class="card" data-od-id="memory-card">
          <div style="display:flex;gap:10px;margin-bottom:8px">
            <input type="text" id="mem-search" v-model="memSearch" placeholder="搜索记忆内容…" aria-label="搜索记忆"
              style="flex:1;padding:8px 10px;font:inherit;font-size:13px;color:var(--fg);background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);outline:none">
            <button class="btn btn-primary btn-sm" id="btn-mem-add" @click="addMemory">＋ 新增记忆</button>
          </div>
          <div id="memory-list">
            <div v-if="!memList.length" class="empty"><div class="empty-title">无匹配记忆</div>调整搜索词，或新增一条记忆</div>
            <div v-for="mem in memList" :key="mem.id" class="mem-row" :data-od-id="'mem-' + mem.id">
              <div class="mt">{{ mem.text }}
                <div class="mm"><span>{{ mem.kind }}</span><span>{{ mem.at }}</span><span>{{ mem.id }}</span></div>
              </div>
              <button class="btn btn-danger btn-sm" :aria-label="'删除记忆 ' + mem.id" @click="delMemory(mem.id)">删除</button>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图七：成本分析 -->
      <section v-show="view === 'cost'" class="view" :class="{ active: view === 'cost' }" id="view-cost" role="tabpanel">
        <h2 class="viewtitle">成本分析</h2>
        <div class="grid g-main">
          <div class="card" data-od-id="cost-models-card">
            <div class="card-head"><h3>按模型分解</h3><span class="tag">GET /v1/cost</span></div>
            <div id="cost-models">
              <div v-for="[n, v] in m.cost.byModel" :key="n" class="costbar-row">
                <span class="cn">{{ n }}</span>
                <span class="scorebar" role="img" :aria-label="n + '：$' + v.toFixed(2)"><i :style="{ width: Math.round(v / costMax * 100) + '%' }"></i></span>
                <span class="cv">${{ v.toFixed(2) }}</span>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="cost-top-card">
            <div class="card-head"><h3>成本 Top 会话</h3><span class="tag">GET /v1/sessions/{id}/cost</span></div>
            <div id="cost-top">
              <div v-for="t in m.cost.top" :key="t.sid" class="mono-row">
                <span class="lbl">{{ t.sid }}</span>
                <span style="color:var(--muted)">{{ t.profile }} · {{ t.tokens }} tokens</span>
                <span class="num" style="font-weight:600">${{ t.cost.toFixed(2) }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图八：日志与追踪 -->
      <section v-show="view === 'logs'" class="view" :class="{ active: view === 'logs' }" id="view-logs" role="tabpanel">
        <h2 class="viewtitle">日志与追踪</h2>
        <div class="grid g-main">
          <div class="card" data-od-id="logs-card">
            <div class="card-head"><h3>日志流</h3><span class="tag">GET /v1/logs</span></div>
            <div id="logs-list">
              <div v-for="(l, i) in m.logs" :key="i" class="log-line">
                <span class="lt">{{ l.at }}</span>
                <span class="lvl" :class="'lvl-' + l.level">{{ l.level }}</span>
                <span class="lx"><b>{{ l.src }}</b> · {{ l.text }}</span>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="traces-card">
            <div class="card-head"><h3>Trace 查询</h3><span class="tag">GET /v1/traces · OTLP 导出</span></div>
            <div id="traces-list">
              <div v-for="t in m.traces" :key="t.tid" class="mono-row">
                <span class="lbl">{{ t.tid }}</span>
                <span>{{ t.root }}</span>
                <span style="color:var(--muted)">{{ t.session }} · {{ t.spans }} spans · {{ t.dur }}</span>
                <button class="btn btn-secondary btn-sm" @click="openTraceSession(t.session)">查看会话</button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图九：K8s 运维 -->
      <section v-show="view === 'k8sops'" class="view" :class="{ active: view === 'k8sops' }" id="view-k8sops" role="tabpanel">
        <div class="card-head" style="margin-bottom:4px">
          <h2 class="viewtitle" style="margin:0">K8s 运维面板</h2>
          <div class="row-actions"><span class="tag">POST /v1/kubernetes/preflight</span>
            <button class="btn btn-primary btn-sm" id="btn-k8s-preflight" @click="reload('k8sops'); toast('Preflight 已运行 · POST /v1/kubernetes/preflight')">运行 Preflight</button></div>
        </div>
        <div class="grid g-main">
          <div class="card" data-od-id="k8s-preflight-card">
            <div class="card-head"><h3>Preflight 检查</h3><span class="tag">context · prod-cluster</span></div>
            <div id="k8s-preflight">
              <div v-for="p in m.k8sops.preflight" :key="p.name" class="check-row">
                <span class="check-ico" :class="p.ok ? 'ck-ok' : 'ck-fail'">{{ p.ok ? '✓' : '!' }}</span>
                <div><div class="c-name">{{ p.name }}</div><div class="c-detail">{{ p.detail }}</div></div>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="k8s-runs-card">
            <div class="card-head"><h3>运行记录</h3><span class="tag">POST /v1/kubernetes/run|check</span></div>
            <div id="k8s-runs">
              <div v-for="(r, i) in m.k8sops.runs" :key="i" class="mono-row">
                <span>{{ r.name }}</span><span class="tag">{{ r.kind }}</span>
                <span style="color:var(--muted)">{{ r.at }}</span>
                <span class="status" :class="statusCls(r.state)">{{ statusLabel(r.state) }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图十：生态与包 -->
      <section v-show="view === 'ecosystem'" class="view" :class="{ active: view === 'ecosystem' }" id="view-ecosystem" role="tabpanel">
        <h2 class="viewtitle">生态与包管理</h2>
        <div class="grid g-config">
          <div class="card" data-od-id="eco-installed-card">
            <div class="card-head"><h3>已安装 Domain 包</h3><span class="tag">GET /v1/ecosystem/registry</span></div>
            <div id="eco-installed">
              <div v-for="p in m.ecosystem.installed" :key="p.name" class="mono-row">
                <span class="lbl">{{ p.name }}</span><span class="num">v{{ p.ver }}</span>
                <span v-if="p.sig === 'verified'" class="pill pill-ok">✓ 签名校验通过</span>
                <span v-else class="pill pill-err">未签名</span>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="eco-catalog-card">
            <div class="card-head"><h3>目录 · 可安装</h3><span class="tag">GET /v1/ecosystem/catalog</span></div>
            <div id="eco-catalog">
              <div v-for="p in m.ecosystem.catalog" :key="p.name" class="mono-row">
                <div><div class="lbl">{{ p.name }}</div><div style="font-size:12px;color:var(--muted)">{{ p.desc }}</div></div>
                <span class="num">v{{ p.ver }}</span>
                <button class="btn btn-secondary btn-sm" @click="installPkg(p.name)">安装</button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图十一：审计中心 -->
      <section v-show="view === 'audit'" class="view" :class="{ active: view === 'audit' }" id="view-audit" role="tabpanel">
        <h2 class="viewtitle">审计中心</h2>
        <div class="grid g-main">
          <div class="card" data-od-id="audit-stream-card">
            <div class="card-head"><h3>审计流</h3><span class="tag">GET /v1/audit</span>
              <span id="audit-count"><span class="meta">{{ auditFiltered.length }} / {{ m.audit.items.length }} 条</span></span></div>
            <div class="audit-filters">
              <input class="input" id="audit-q" type="search" v-model="auditQ" placeholder="搜索主体、动作或目标…" aria-label="搜索审计流">
              <select class="input" id="audit-act" v-model="auditAct" aria-label="按动作筛选">
                <option v-for="a in auditActs" :key="a">{{ a }}</option>
              </select>
            </div>
            <div id="audit-list">
              <div v-if="!auditFiltered.length" class="empty"><div class="empty-title">无匹配的审计记录</div>调整筛选条件后重试</div>
              <div v-for="(x, i) in auditFiltered" :key="i" class="mono-row">
                <span class="lt" style="color:var(--muted);font-family:var(--font-mono);font-size:12px">{{ x.at }}</span>
                <span>{{ x.actor }}</span><span class="tag">{{ x.act }}</span>
                <span class="lbl" style="color:var(--muted)">{{ x.target }}</span><span class="num">{{ x.hash }}</span>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="audit-integrity-card">
            <div class="card-head"><h3>完整性</h3><span class="tag">GET /v1/audit/integrity</span></div>
            <div id="audit-integrity">
              <div v-if="m.audit.integrity === 'ok'" class="check-row">
                <span class="check-ico ck-ok">✓</span>
                <div><div class="c-name">哈希链完整</div><div class="c-detail">{{ m.audit.recordCount }} 条记录 · root {{ (m.audit.rootHash || '').slice(0, 12) }}… · GET /v1/audit/integrity</div></div>
              </div>
              <div v-else class="check-row">
                <span class="check-ico ck-fail">!</span>
                <div><div class="c-name">哈希链存在缺口</div><div class="c-detail">请检查 state-events 存储</div></div>
              </div>
            </div>
            <div class="card-head" style="margin-top:14px"><h3>配置变更历史</h3><span class="tag">GET /v1/config/audit</span></div>
            <div id="config-audit">
              <div v-for="h in m.audit.configHistory" :key="h.field" class="mono-row">
                <span class="lbl">{{ h.field }}</span>
                <span style="color:var(--muted)" class="num">{{ h.from }} → {{ h.to }}</span>
                <span style="color:var(--muted);font-size:12px">{{ h.at }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图十二：多智能体 -->
      <section v-show="view === 'multiagent'" class="view" :class="{ active: view === 'multiagent' }" id="view-multiagent" role="tabpanel">
        <h2 class="viewtitle">多智能体拓扑</h2>
        <div class="card" data-od-id="topo-card">
          <div class="card-head"><h3>协作拓扑</h3><span class="tag">GET /v1/multi-agent</span></div>
          <svg class="topo" viewBox="0 0 680 280" role="img"
            aria-label="多智能体协作拓扑图：coordinator 连接 triage、ops、audit 三个 worker" id="topo-svg">
            <line v-for="(e, i) in topo.edges" :key="'e' + i" :x1="e.x1" :y1="e.y1" :x2="e.x2" :y2="e.y2" class="edge" />
            <text v-for="(e, i) in topo.edges" :key="'et' + i" :x="(e.x1 + e.x2) / 2" :y="(e.y1 + e.y2) / 2 - 6" text-anchor="middle">{{ e.label }}</text>
            <g v-for="(b, i) in topo.boxes" :key="'b' + i">
              <rect :x="b.x" :y="b.y" :width="b.w" :height="b.h" rx="8" :class="b.hub ? 'node-box hub' : 'node-box'" />
              <text :x="b.x + b.w / 2" :y="b.y + b.h / 2 - 2" text-anchor="middle" class="node-label">{{ b.label }}</text>
              <text :x="b.x + b.w / 2" :y="b.y + b.h / 2 + 16" text-anchor="middle">{{ b.sub }}</text>
            </g>
          </svg>
          <div class="mono-row" style="border-top:1px solid var(--border);margin-top:8px;padding-top:12px">
            <span style="font-size:12.5px;color:var(--muted)" id="topo-summary">{{ topo.summary }}</span>
          </div>
        </div>
      </section>

      <!-- 视图十三：健康中心 -->
      <section v-show="view === 'health'" class="view" :class="{ active: view === 'health' }" id="view-health" role="tabpanel">
        <div class="card-head" style="margin-bottom:4px">
          <h2 class="viewtitle" style="margin:0">健康中心</h2>
          <div class="row-actions"><span class="tag">POST /v1/doctor/state-events/repair</span>
            <button class="btn btn-secondary btn-sm" id="btn-repair" @click="toast('修复任务已创建 · POST /v1/doctor/state-events/repair')">修复状态事件</button></div>
        </div>
        <div class="grid g-config">
          <div class="card" data-od-id="health-checks-card">
            <div class="card-head"><h3>逐项检查</h3><span class="tag">GET /v1/doctor</span></div>
            <div id="health-list">
              <div v-for="c in m.health.checks" :key="c.name" class="check-row">
                <span class="check-ico" :class="'ck-' + c.level">{{ c.level === 'ok' ? '✓' : c.level === 'warn' ? '!' : '×' }}</span>
                <div><div class="c-name">{{ c.name }}</div><div class="c-detail">{{ c.detail }}</div></div>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="health-state-card">
            <div class="card-head"><h3>状态事件存储</h3><span class="tag">state-events</span></div>
            <div id="health-state">
              <div v-for="s in m.health.state" :key="s.name" class="check-row">
                <span class="check-ico" :class="s.warn ? 'ck-warn' : 'ck-ok'">{{ s.warn ? '!' : '✓' }}</span>
                <div><div class="c-name">{{ s.name }}</div><div class="c-detail">{{ s.detail }}</div></div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <div class="toast" id="toast" :class="{ show: !!toastMsg }" role="status">{{ toastMsg }}</div>

    <!-- Profile 新建/编辑弹窗 -->
    <div class="modal-overlay" :class="{ open: profileModal.open }" role="dialog" aria-modal="true" aria-labelledby="pm-title" @click="onOverlayClick">
      <div class="modal">
        <div class="modal-head"><h3 id="pm-title">{{ profileModal.editing ? '编辑 Profile · ' + profileModal.editing : '新建 Profile' }}</h3>
          <button class="modal-x" aria-label="关闭弹窗" @click="closeModal">✕</button></div>
        <div class="modal-body">
          <div class="field" :class="{ invalid: !!pmError }"><label for="pf-name">名称</label>
            <input type="text" id="pf-name" v-model="pmForm.name" :disabled="!!profileModal.editing" placeholder="如 prod-readonly" autocomplete="off" @keydown.enter="saveProfile">
            <div class="f-err">{{ pmError }}</div></div>
          <div class="field"><label>模型（per-profile，可选）</label>
            <select v-model="pmForm.model.provider" class="input" id="pm-model-provider" style="margin-bottom:6px">
              <option v-for="p in MODEL_PROVIDERS" :key="p" :value="p">{{ p }}</option>
            </select>
            <template v-if="pmForm.model.provider !== 'scripted'">
              <input v-model="pmForm.model.name" class="input" placeholder="模型名，如 gpt-4o-mini" style="margin-bottom:6px" id="pm-model-name">
              <input v-model="pmForm.model.endpoint" class="input" placeholder="Endpoint（可选，如 https://api.openai.com/v1）" style="margin-bottom:6px" id="pm-model-endpoint">
              <input v-model="pmForm.model.api_key_env" class="input" placeholder="API Key 环境变量名，如 OPENAI_API_KEY" id="pm-model-key">
            </template>
            <div class="hint">写入 profile 的 runtime.model + runtime.secrets；scrited 表示使用部署默认模型。需重启/重载 agentd 生效。</div>
          </div>
          <div class="field"><label>启用 Domains</label>
            <div id="pf-domains" style="display:grid;gap:6px">
              <label v-for="d in m.domains" :key="d.name" class="check-row" style="cursor:pointer">
                <input type="checkbox" :value="d.name" v-model="pmForm.domains"> <span class="c-name">{{ d.name }}</span>
              </label>
            </div></div>
          <div class="field"><label for="pf-desc">描述</label>
            <textarea id="pf-desc" v-model="pmForm.desc" rows="2" placeholder="Profile 用途说明"></textarea></div>
        </div>
        <div class="modal-foot">
          <button class="btn btn-secondary btn-sm" @click="closeModal">取消</button>
          <button class="btn btn-primary btn-sm" id="pm-save" data-od-id="pm-save" @click="saveProfile">保存</button>
        </div>
      </div>
    </div>

    <!-- 删除确认弹窗 -->
    <div class="modal-overlay" :class="{ open: delModal.open }" role="dialog" aria-modal="true" aria-labelledby="dm-text" @click="onOverlayClick">
      <div class="modal" style="max-width:380px">
        <div class="modal-head"><h3 id="dm-text">确认删除</h3>
          <button class="modal-x" aria-label="关闭弹窗" @click="closeModal">✕</button></div>
        <div class="modal-body"><p style="margin:0;font-size:13.5px;line-height:1.6;color:var(--muted)" id="del-detail">{{ delDetail }}</p></div>
        <div class="modal-foot">
          <button class="btn btn-secondary btn-sm" @click="closeModal">取消</button>
          <button class="btn btn-danger btn-sm" id="dm-confirm" data-od-id="dm-confirm" @click="confirmDelete">删除</button>
        </div>
      </div>
    </div>

    <!-- Domain 详情弹窗 -->
    <div class="modal-overlay" :class="{ open: domainModal.open }" role="dialog" aria-modal="true" aria-labelledby="dom-title" @click="onOverlayClick">
      <div class="modal">
        <div class="modal-head"><h3 id="dom-title">Domain{{ domainDetail ? ' · ' + domainDetail.name : '' }}</h3>
          <button class="modal-x" aria-label="关闭弹窗" @click="closeModal">✕</button></div>
        <div class="modal-body">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap" id="dom-meta">
            <template v-if="domainDetail">
              <span class="status" :class="statusCls(domainDetail.active ? 'success' : 'paused')">{{ statusLabel(domainDetail.active ? 'success' : 'paused') }}</span>
              <span class="tag">GET /v1/tools?domain={{ domainDetail.name }}</span>
            </template>
          </div>
          <div class="field"><label>Tools（<span id="dom-tool-count">{{ domainDetail ? domainDetail.tools.length : 0 }}</span>）</label>
            <div id="dom-tools" style="display:grid;gap:0">
              <div v-for="t in domainDetail ? domainDetail.tools : []" :key="t.name" class="tool-row">
                <span class="t-name">{{ t.name }}</span>
                <span class="t-desc">{{ t.desc }}</span>
                <span v-if="t.mutation" class="pill pill-mutation">mutation</span>
                <span v-else class="pill pill-readonly">只读</span>
              </div>
            </div>
            <div v-if="domainDetail && !domainDetail.tools.length" class="f-err" style="display:block" id="dom-empty">该 Domain 暂无注册工具（GET /v1/tools?domain=…）</div></div>
          <div class="field"><label>引用此 Domain 的 Profiles</label>
            <div id="dom-profiles" style="display:flex;gap:6px;flex-wrap:wrap">
              <template v-if="domainDetail">
                <span v-if="domainUsedBy(domainDetail)" v-for="n in domainUsedBy(domainDetail)" :key="n" class="tag">{{ n }}</span>
                <span v-else style="font-size:12.5px;color:var(--muted)">暂无 Profile 引用</span>
              </template>
            </div></div>
        </div>
        <div class="modal-foot">
          <button class="btn btn-secondary btn-sm" id="dm2-cancel" data-od-id="dom-close-btn" @click="closeModal">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>
