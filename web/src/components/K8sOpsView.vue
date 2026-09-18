<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, CHAT_PROFILES, activeChat, chatInput, sending, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'K8sOpsView' })
</script>
<template>
<!-- 视图九：K8s 运维 -->
      <section v-show="view === 'k8sops'" class="view" :class="{ active: view === 'k8sops' }" id="view-k8sops" role="tabpanel">
        <div class="card-head" style="margin-bottom:4px">
          <div class="row-actions"><button class="btn btn-primary btn-sm" id="btn-k8s-preflight" @click="reload('k8sops'); toast('Preflight 已运行')">运行 Preflight</button></div>
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
            <div class="card-head"><h3>运行记录</h3></div>
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
</template>
