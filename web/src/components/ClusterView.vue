<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, CHAT_PROFILES, activeChat, chatInput, sending, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'ClusterView' })
</script>
<template>
<!-- 视图五：分布式集群 -->
      <section v-show="view === 'cluster'" class="view" :class="{ active: view === 'cluster' }" id="view-cluster" role="tabpanel">
        <h2 class="viewtitle">分布式集群</h2>
        <div class="grid g-main">
          <div class="card" data-od-id="cluster-workers-card">
            <div class="card-head"><h3>Workers</h3></div>
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
              <div class="card-head"><h3>分布式锁</h3></div>
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
              <div class="card-head"><h3>Goal 调度队列</h3></div>
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
</template>
