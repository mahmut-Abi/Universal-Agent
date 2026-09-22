<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, CHAT_PROFILES, activeChat, chatInput, sending, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval, statusBreakdown, waitingSessions } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'OverviewView' })
</script>
<template>
<!-- 视图一：总览 -->
      <section v-show="view === 'overview'" class="view" :class="{ active: view === 'overview' }" id="view-overview" role="tabpanel">        <div class="grid g-metrics" id="metrics" data-od-id="metrics-row">
          <div v-for="c in metricCards" :key="c.label" class="card metric" :data-od-id="'metric-' + c.label">
            <div class="m-label">{{ c.label }}</div>
            <div class="m-value num">{{ c.value }}</div>
            <div class="m-sub">
              <span v-if="c.trend === 'up'" class="trend-up">↑</span>
              <span v-else-if="c.trend === 'down'" class="trend-down">↓</span>{{ c.sub }}
            </div>
          </div>
        </div>
        <div v-if="waitingSessions.length" class="confirm-banner" data-od-id="waiting-banner" role="alert">
          <div class="cb-text">
            <span class="cb-title">⏸ {{ waitingSessions.length }} 个会话等待人工确认</span>
            <span class="cb-sub">需要你在「会话详情」中批准或取消操作后再继续</span>
          </div>
          <button class="btn btn-primary btn-sm" data-od-id="waiting-go" @click="openSession(waitingSessions[0])">前往处理</button>
        </div>
        <div class="status-strip" data-od-id="status-strip" aria-label="会话状态分布">
          <span v-for="(v, k) in statusBreakdown" :key="k" class="pill" :class="'pill-' + k">
            <span class="dot" :class="'dot-' + k"></span>{{ (STATUS_MAP[k] && STATUS_MAP[k][1]) || k }} <b>{{ v }}</b>
          </span>
          <span v-if="!m.sessions.length" class="strip-empty">暂无会话数据</span>
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
          <div class="stack">
            <div class="card" data-od-id="runs-chart-card">
              <div class="card-head"><h3>近 7 日任务量</h3></div>
              <div class="bars" id="runs-bars">
                <div v-for="(v, i) in activity.values" :key="i" class="bar" :class="{ hot: v === activity.max && v > 0 }" tabindex="0" role="img"
                  :aria-label="activity.days[i] + '：' + v + ' 个任务'" :style="{ height: Math.round(v / (activity.max || 1) * 100) + '%' }">
                  <span class="tip">{{ v }} 个任务</span>
                </div>
              </div>
              <div class="bars-x" id="runs-x"><span v-for="d in activity.days" :key="d">{{ d }}</span></div>
            </div>
            <div class="card" data-od-id="ov-events-card">
              <div class="card-head"><h3>最近系统事件</h3><button class="btn btn-secondary btn-sm" @click="reload('overview')">↻刷新</button></div>
              <div v-if="!m.logs.length" class="empty">暂无事件记录</div>
              <div v-for="l in m.logs.slice(0, 10)" :key="l.at + l.src + l.text" class="log-line">
                <span class="lt">{{ l.at }}</span>
                <span class="lvl" :class="'lvl-' + l.level">{{ l.level }}</span>
                <span class="lx"><code>{{ l.src }}</code> · {{ l.text }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图二：会话详情 -->
</template>

<style scoped>
.status-strip {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin: 14px 0 0;
}
.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--surface-card, #fff);
  border: 1px solid var(--border, #e6e6e6);
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 12.5px;
  color: var(--text, #222);
}
.pill b {
  font-variant-numeric: tabular-nums;
  margin-left: 2px;
}
.pill .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--muted, #999);
}
.strip-empty {
  color: var(--muted, #888);
  font-size: 12.5px;
}
.pill-running .dot, .dot-running { background: var(--accent); }
.pill-success .dot, .dot-success { background: var(--ok); }
.pill-failed .dot, .dot-failed { background: var(--danger); }
.pill-waiting .dot, .dot-waiting { background: var(--warn); }
.pill-paused .dot, .dot-paused { background: var(--muted); }
</style>
