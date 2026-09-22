<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, CHAT_PROFILES, activeChat, chatInput, sending, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval, busy, autoRefreshOn, toggleAutoRefresh } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'HealthView' })
</script>
<template>
<!-- 视图十三：健康中心 -->
      <section v-show="view === 'health'" class="view" :class="{ active: view === 'health' }" id="view-health" role="tabpanel">
        <div class="card-head" style="margin-bottom:4px">
          <div class="row-actions"><button class="btn btn-secondary btn-sm" id="btn-health-refresh" @click="reload('health')">刷新体检</button></div>
        </div>
        <div class="grid g-config">
          <div class="card" data-od-id="health-checks-card">
            <div class="card-head"><h3>逐项检查</h3></div>
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
</template>
