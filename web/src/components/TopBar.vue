<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, chatMsgs, CHAT_PROFILES, activeChat, chatInput, sending, replyIdx, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'TopBar' })
</script>
<template>
<header class="topbar" data-od-id="topbar">
      <div class="topbar-inner">
        <span class="brand"><span class="brand-mark">UA</span>Universal-Agent</span>
        <nav role="tablist" aria-label="主视图">
          <button role="tab" :aria-selected="view === 'overview'" data-view="overview" @click="switchView('overview')">总览</button>
          <button role="tab" :aria-selected="view === 'chat'" data-view="chat" @click="switchView('chat')">对话</button>
          <button role="tab" :aria-selected="view === 'session'" data-view="session" @click="switchView('session')">会话详情</button>
          <button role="tab" :aria-selected="view === 'config'" data-view="config" @click="switchView('config')">配置与运行时</button>
        </nav>
        <div class="navdrop" id="navdrop">
          <button role="tab" :aria-selected="!!OPS_VIEWS[view]" aria-haspopup="true" :aria-expanded="String(opsOpen)"
            id="nav-ops-btn" data-od-id="nav-ops" @click="opsOpen = !opsOpen">
            {{ opsBtnLabel }}<span class="nav-caret" aria-hidden="true">▾</span>
          </button>
          <div class="nav-menu" :class="{ open: opsOpen }" id="nav-ops-menu" role="menu" aria-label="运维视图">
            <button v-for="(label, name) in OPS_VIEWS" :key="name" role="menuitem"
              :class="{ active: view === name }" :data-view="name" @click="switchView(name)">{{ label }}</button>
          </div>
        </div>
        <span class="spacer"></span>
        <span class="env-pill"><span class="dot"></span>agentd · <span class="num">{{ apiHost }}</span></span>
      </div>
    </header>
</template>
