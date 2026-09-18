<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, CHAT_PROFILES, activeChat, chatInput, sending, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'OverviewView' })
</script>
<template>
<!-- 视图一：总览 -->
      <section v-show="view === 'overview'" class="view" :class="{ active: view === 'overview' }" id="view-overview" role="tabpanel">
        <h2 class="viewtitle">总览</h2>
        <div class="grid g-metrics" id="metrics" data-od-id="metrics-row">
          <div v-for="c in metricCards" :key="c.label" class="card metric" :data-od-id="'metric-' + c.label">
            <div class="m-label">{{ c.label }}</div>
            <div class="m-value num">{{ c.value }}</div>
            <div class="m-sub">
              <span v-if="c.trend === 'up'" class="trend-up">↑</span>
              <span v-else-if="c.trend === 'down'" class="trend-down">↓</span>{{ c.sub }}
            </div>
          </div>
        </div>
        <div class="grid g-main" style="margin-top:16px">
          <div class="card" data-od-id="sessions-card">
            <div class="card-head">
              <h3>最近会话</h3>
              <button class="btn btn-secondary btn-sm" id="btn-refresh" @click="doRefresh">刷新</button>
            </div>
            <div v-if="sessionsError" class="net-error" data-od-id="sessions-error" role="alert">
              <span>无法连接 agentd：{{ sessionsError }}</span>
              <button class="btn btn-secondary btn-sm" data-od-id="btn-retry" @click="loadSessions">重试</button>
            </div>
            <div v-else-if="!m.sessions.length" class="empty" data-od-id="sessions-empty">
              <div class="empty-title">暂无会话</div>创建第一个 Agent 会话开始排查任务<br><br>
              <button class="btn btn-secondary btn-sm" data-od-id="empty-new-chat" @click="switchView('chat'); newChat()">新建对话</button>
            </div>
            <div v-else class="ses-list" id="session-rows" data-od-id="session-list">
              <div v-for="r in m.sessions" :key="r.id" class="ses-row" :class="{ selected: currentSession === r }"
                tabindex="0" role="button" @click="openSession(r)"
                @keydown.enter.prevent="openSession(r)" @keydown.space.prevent="openSession(r)">
                <div class="ses-main">
                  <div class="ses-goal">{{ r.goal }}</div>
                  <div class="ses-meta">
                    <span class="ses-profile">{{ r.profile }}</span>
                    <span class="ses-nums">{{ r.steps }} 步 · {{ r.dur }}</span>
                  </div>
                </div>
                <span class="ses-status"><span class="status" :class="statusCls(r.confirm ? 'waiting' : r.status)">{{ statusLabel(r.confirm ? 'waiting' : r.status) }}</span></span>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="runs-chart-card">
            <div class="card-head"><h3>近 7 日任务量</h3><span class="tag">GET /v1/sessions · created_at 聚合</span></div>
            <div class="bars" id="runs-bars">
              <div v-for="(v, i) in activity.values" :key="i" class="bar" :class="{ hot: v === activity.max && v > 0 }" tabindex="0" role="img"
                :aria-label="activity.days[i] + '：' + v + ' 个任务'" :style="{ height: Math.round(v / (activity.max || 1) * 100) + '%' }">
                <span class="tip">{{ v }} 个任务</span>
              </div>
            </div>
            <div class="bars-x" id="runs-x"><span v-for="d in activity.days" :key="d">{{ d }}</span></div>
          </div>
        </div>
      </section>

      <!-- 视图二：会话详情 -->
</template>
