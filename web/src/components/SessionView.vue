<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, CHAT_PROFILES, activeChat, chatInput, sending, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval, mdLite } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'SessionView' })
import { ref } from 'vue'
const confirmRemember = ref(false)
</script>
<template>
<!-- 视图二：会话详情 -->
      <section v-show="view === 'session'" class="view" :class="{ active: view === 'session' }" id="view-session" role="tabpanel">
        <div class="card-head" style="margin-bottom:4px">
          <span class="meta num" id="session-id-label">{{ currentSession && currentSession.id }}</span>
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
</style>
