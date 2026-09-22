<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, CHAT_PROFILES, activeChat, chatInput, sending, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'MemoryView' })
</script>
<template>
<!-- 视图六：记忆管理 -->
      <section v-show="view === 'memory'" class="view" :class="{ active: view === 'memory' }" id="view-memory" role="tabpanel">
        <div class="card" data-od-id="memory-card">
          <div class="card-head"><h3>记忆管理</h3>
            <span class="tag">{{ memList.length }} 条</span>
          </div>
          <div style="display:flex;gap:10px;margin-bottom:8px">
            <input type="text" id="mem-search" v-model="memSearch" placeholder="搜索记忆内容…" aria-label="搜索记忆"
              style="flex:1;padding:8px 10px;font:inherit;font-size:13px;color:var(--fg);background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);outline:none">
            <button class="btn btn-primary btn-sm" id="btn-mem-add" @click="addMemory">＋ 新增记忆</button>
          </div>
          <div id="memory-list">
            <div v-if="!memList.length" class="empty"><div class="empty-title">无匹配记忆</div>调整搜索词，或新增一条记忆</div>
            <div v-for="mem in memList" :key="mem.id" class="mem-row" :data-od-id="'mem-' + mem.id">
              <div class="mt">{{ mem.text }}
                <div class="mm"><span>{{ mem.kind }}</span><span>{{ mem.at }}</span><span>{{ mem.id }}</span></div>
              </div>
              <button class="btn btn-danger btn-sm" :aria-label="'删除记忆 ' + mem.id" @click="delMemory(mem.id)">删除</button>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图七：成本分析 -->
</template>
