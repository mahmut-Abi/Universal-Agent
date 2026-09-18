<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, CHAT_PROFILES, activeChat, chatInput, sending, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'EcosystemView' })
</script>
<template>
<!-- 视图十：生态与包 -->
      <section v-show="view === 'ecosystem'" class="view" :class="{ active: view === 'ecosystem' }" id="view-ecosystem" role="tabpanel">        <div class="grid g-config">
          <div class="card" data-od-id="eco-installed-card">
            <div class="card-head"><h3>已安装 Domain 包</h3></div>
            <div id="eco-installed">
              <div v-for="p in m.ecosystem.installed" :key="p.name" class="mono-row">
                <span class="lbl">{{ p.name }}</span><span class="num">v{{ p.ver }}</span>
                <span v-if="p.sig === 'verified'" class="pill pill-ok">✓ 签名校验通过</span>
                <span v-else class="pill pill-err">未签名</span>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="eco-catalog-card">
            <div class="card-head"><h3>目录 · 可安装</h3></div>
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
</template>
