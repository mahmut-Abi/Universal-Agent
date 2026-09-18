<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, CHAT_PROFILES, activeChat, chatInput, sending, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'CostView' })
</script>
<template>
<!-- 视图七：成本分析 -->
      <section v-show="view === 'cost'" class="view" :class="{ active: view === 'cost' }" id="view-cost" role="tabpanel">
        <h2 class="viewtitle">成本分析</h2>
        <div class="grid g-main">
          <div class="card" data-od-id="cost-models-card">
            <div class="card-head"><h3>按模型分解</h3></div>
            <div id="cost-models">
              <div v-for="[n, v] in m.cost.byModel" :key="n" class="costbar-row">
                <span class="cn">{{ n }}</span>
                <span class="scorebar" role="img" :aria-label="n + '：$' + v.toFixed(2)"><i :style="{ width: Math.round(v / costMax * 100) + '%' }"></i></span>
                <span class="cv">${{ v.toFixed(2) }}</span>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="cost-top-card">
            <div class="card-head"><h3>成本 Top 会话</h3></div>
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
</template>
