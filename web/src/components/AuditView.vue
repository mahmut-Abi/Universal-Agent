<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, CHAT_PROFILES, activeChat, chatInput, sending, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'AuditView' })
</script>
<template>
<!-- 视图十一：审计中心 -->
      <section v-show="view === 'audit'" class="view" :class="{ active: view === 'audit' }" id="view-audit" role="tabpanel">        <div class="grid g-main">
          <div class="card" data-od-id="audit-stream-card">
            <div class="card-head"><h3>审计流</h3>
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
            <div class="card-head"><h3>完整性</h3></div>
            <div id="audit-integrity">
              <div v-if="m.audit.integrity === 'ok'" class="check-row">
                <span class="check-ico ck-ok">✓</span>
                <div><div class="c-name">哈希链完整</div><div class="c-detail">{{ m.audit.recordCount }} 条记录 · root {{ (m.audit.rootHash || '').slice(0, 12) }}…</div></div>
              </div>
              <div v-else class="check-row">
                <span class="check-ico ck-fail">!</span>
                <div><div class="c-name">哈希链存在缺口</div><div class="c-detail">请检查 state-events 存储</div></div>
              </div>
            </div>
            <div class="card-head" style="margin-top:14px"><h3>配置变更历史</h3></div>
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
</template>
