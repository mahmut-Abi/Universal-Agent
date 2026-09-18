<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, CHAT_PROFILES, activeChat, chatInput, sending, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'LogsView' })
</script>
<template>
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
</template>
