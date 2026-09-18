<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, CHAT_PROFILES, activeChat, chatInput, sending, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'MultiAgentView' })
</script>
<template>
<!-- 视图十二：多智能体 -->
      <section v-show="view === 'multiagent'" class="view" :class="{ active: view === 'multiagent' }" id="view-multiagent" role="tabpanel">
        <h2 class="viewtitle">多智能体拓扑</h2>
        <div class="card" data-od-id="topo-card">
          <div class="card-head"><h3>协作拓扑</h3></div>
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
</template>
