<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, CHAT_PROFILES, activeChat, chatInput, sending, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval, mdLite } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'SessionView' })
import { ref, watch, computed } from 'vue'
const confirmRemember = ref(false)
// 会话切换器：下拉选择即跳转详情（openSession 会拉取该会话事件/证据/世界模型）
const pickSessionId = ref('')
watch(
  () => currentSession && currentSession.id,
  (id) => { pickSessionId.value = id || '' },
  { immediate: true },
)
function onPickSession(e) {
  const s = m.sessions.find((x) => x.id === e.target.value)
  if (s) openSession(s)
}

// 结果小结：把证据/世界模型/目标状态蒸馏成几条可读结论（纯客户端推导，无新接口）
const conclusions = computed(() => {
  const st = currentSession.value
    ? currentSession.value.confirm
      ? 'waiting'
      : normStatus(currentSession.value.status)
    : 'paused'
  const facts = [...m.world.facts]
    .sort((a, b) => (b.confidence || 0) - (a.confidence || 0))
    .slice(0, 5)
  const factLines = facts.map((f) => `${f.subject}：${f.claim} = ${f.value}`)
  const evCount = m.evidence.length
  const evOk = m.evidence.filter((x) => x.ok).length
  const conflicts = m.world.facts.filter((f) => f.conflicting).length
  const failedEv = m.evidence.filter((x) => !x.ok).slice(0, 3).map((x) => x.name)
  let verdict
  if (st === 'success') verdict = '目标已完成，证据充分'
  else if (st === 'failed') verdict = '未完成——存在失败证据，建议排查'
  else if (st === 'waiting') verdict = '等待人工确认决策'
  else if (st === 'running') verdict = '执行中，世界模型持续更新'
  else verdict = '会话已暂停/未开始'
  return { st, verdict, factLines, evCount, evOk, conflicts, failedEv }
})
</script>
<template>
<!-- 视图二：会话详情 -->
      <section v-show="view === 'session'" class="view" :class="{ active: view === 'session' }" id="view-session" role="tabpanel">
        <div class="card-head sess-switch" style="margin-bottom:4px">
          <select class="input sess-picker" v-model="pickSessionId" aria-label="切换会话" title="在下拉中切换到此会话的详情" @change="onPickSession($event)">
            <option v-if="currentSession && !m.sessions.some(s => s.id === currentSession.id)" :value="currentSession.id">
              {{ (currentSession.goal || '').slice(0, 36) }} · 当前
            </option>
            <option v-for="s in m.sessions" :key="s.id" :value="s.id">
              {{ (s.goal || '').slice(0, 36) }} · {{ s.profile }} · {{ statusLabel(s.confirm ? 'waiting' : s.status) }}
            </option>
          </select>
          <span class="meta num" id="session-id-label">{{ currentSession && currentSession.id }}</span>
          <button class="btn btn-secondary btn-sm" aria-label="刷新会话详情" @click="reload('session')">↻</button>
        </div>
        <div id="confirm-slot" data-od-id="confirm-banner">
          <div v-if="currentSession && currentSession.confirm" class="confirm-banner">
            <div class="cb-text">
              <span class="cb-title">Runtime 请求确认：</span>待执行
              <!-- prettier-ignore -->
              <code class="num" style="font-size:12px;background:var(--bg);border:1px solid var(--border);border-radius:5px;padding:1px 6px">{{ currentSession.pending.action }}</code>（{{ currentSession.pending.risk }} · Policy 拦截）
            </div>
            <label class="cb-remember" title="本次会话内，相同参数的重复操作将不再询问">
              <input type="checkbox" v-model="confirmRemember" /> 记住批准
            </label>
            <button class="btn btn-primary btn-sm" @click="confirmPending(true, confirmRemember)">确认执行</button>
            <button class="btn btn-secondary btn-sm" @click="confirmPending(false)">拒绝</button>
          </div>
        </div>
        <div class="grid g-main">
          <div class="card" data-od-id="timeline-card">
            <div class="card-head">
              <h3>运行事件</h3>
              <span class="tag">事件流</span>
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
                    <!-- LLM 输出经 mdLite 渲染行内代码与代码块 -->
                    <div class="kv"><span class="k">输出</span></div><pre v-html="mdLite(e.d.completion)"></pre>
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
          <div class="stack">
            <div class="card" data-od-id="conclusions-card">
              <div class="card-head"><h3>结果小结</h3><span class="status" :class="statusCls(conclusions.st)">{{ statusLabel(conclusions.st) }}</span></div>
              <div class="conc-body">
                <div class="conc-goal" v-if="currentSession">{{ currentSession.goal }}</div>
                <div class="conc-line conc-verdict"><span class="conc-key">目标</span>{{ conclusions.verdict }}</div>
                <div class="conc-line" v-if="conclusions.evCount"><span class="conc-key">证据</span>{{ conclusions.evOk }}/{{ conclusions.evCount }} 项检查通过</div>
                <div class="conc-line conc-warn" v-if="conclusions.conflicts"><span class="conc-key">冲突</span>{{ conclusions.conflicts }} 处事实取值相互矛盾，需排查</div>
                <template v-if="conclusions.failedEv.length">
                  <div class="conc-sec">失败项</div>
                  <div v-for="fn in conclusions.failedEv" :key="fn" class="conc-fact conc-fail">✗ {{ fn }}</div>
                </template>
                <template v-if="conclusions.factLines.length">
                  <div class="conc-sec">关键事实</div>
                  <div v-for="(fl, i) in conclusions.factLines" :key="i" class="conc-fact">· {{ fl }}</div>
                </template>
                <div v-if="!conclusions.evCount && !conclusions.factLines.length" class="empty">尚无结论——Agent 尚未产出足够证据</div>
              </div>
            </div>
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
              <div class="card-head"><h3>Evidence</h3></div>
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
            <div class="card" data-od-id="world-card">
              <div class="card-head">
                <h3>世界模型</h3>
                <div style="display:flex;gap:6px;align-items:center">
                  <span class="tag" title="World Model：由证据推导的当前事实、实体与关系">facts {{ m.world.facts.length }} · entities {{ m.world.entities.length }} · relations {{ m.world.relations.length }}</span>
                  <button class="btn btn-secondary btn-sm" aria-label="刷新世界模型" @click="reload('session')">↻</button>
                </div>
              </div>
              <div id="world-view">
                <div v-if="!m.world.facts.length && !m.world.entities.length" class="empty">暂无世界模型事实（Agent 执行动作后由证据推导）</div>
                <template v-if="m.world.facts.length">
                  <div class="w-sec">事实</div>
                  <div v-for="f in m.world.facts" :key="f.key" class="w-row">
                    <span class="num w-subject">{{ f.subject }}</span>
                    <span>{{ f.claim }}</span>
                    <span class="num">= {{ f.value }}</span>
                    <span class="meta">conf {{ f.confidence }}</span>
                    <span v-if="f.conflicting" class="w-conflict" title="证据历史中存在相互矛盾的取值">⚠ 冲突</span>
                  </div>
                </template>
                <template v-if="m.world.entities.length">
                  <div class="w-sec">实体</div>
                  <div v-for="e in m.world.entities" :key="e.id" class="w-row">
                    <span class="num w-subject">{{ e.id }}</span>
                    <span class="tag">{{ e.kind }}</span>
                    <span class="meta num">{{ e.attributes }}</span>
                  </div>
                </template>
                <template v-if="m.world.relations.length">
                  <div class="w-sec">关系</div>
                  <div v-for="(r, ri) in m.world.relations" :key="ri" class="w-row num">{{ r.text }}</div>
                </template>
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
</template>

<style scoped>
.sess-switch {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.sess-picker {
  max-width: 480px;
  min-width: 200px;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12.5px;
}
.w-sec {
  font-weight: 600;
  font-size: 12px;
  margin: 10px 0 4px;
  color: var(--muted, #888);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.w-row {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding: 3px 0;
  font-size: 13px;
  flex-wrap: wrap;
}
.w-subject {
  font-weight: 600;
}
.w-conflict {
  color: #f59e0b;
  font-size: 12px;
  border: 1px solid #f59e0b;
  border-radius: 4px;
  padding: 0 4px;
}
.conc-body { display: grid; gap: 6px; }
.conc-goal {
  color: var(--text, #222);
  font-weight: 600;
  font-size: 14px;
  padding-bottom: 6px;
  border-bottom: 1px dashed var(--border, #eee);
}
.conc-line { display: flex; gap: 8px; font-size: 13px; align-items: baseline; }
.conc-key {
  flex: none;
  min-width: 44px;
  font-size: 12px;
  color: var(--muted, #888);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.conc-verdict { color: var(--text, #222); }
.conc-warn { color: #b45309; }
.conc-sec {
  font-weight: 600;
  font-size: 12px;
  margin-top: 6px;
  color: var(--muted, #888);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.conc-fact {
  font-size: 12.5px;
  color: var(--text, #333);
  padding-left: 4px;
}
.conc-fail { color: var(--danger, #c53); }
</style>
