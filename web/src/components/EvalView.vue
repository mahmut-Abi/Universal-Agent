<script setup>
// biome-ignore-all lint/correctness/noUnusedImports: shared store bindings
import { m, view, OPS_VIEWS, opsOpen, apiHost, fatal, toastMsg, toastTimer, toast, MAIN_VIEWS, VIEW_LOADERS, loadedViews, switchView, reload, opsBtnLabel, statusCls, statusLabel, metricCards, sessionsError, loadingSessions, loadSessions, activity, doRefresh, currentSession, expanded, openSession, confirmPending, cancelSession, lifecycle, sessionSummary, chatFilter, activeChatId, chatSessions, chatEventCache, CHAT_PROFILES, activeChat, chatInput, sending, chatMsgsEl, chatInputEl, filteredChats, scrollChat, newChat, autoGrow, sendChat, apiGetEvents, onChatKeydown, profileModal, pmForm, MODEL_PROVIDERS, pmError, openProfileModal, saveProfile, delModal, profileWriteError, delDetail, askDelete, confirmDelete, domainModal, domainDetail, openDomainDetail, domainUsedBy, policies, togglePolicy, toggleDomain, evalByDataset, pct, memSearch, memList, addMemory, delMemory, costMax, openTraceSession, installPkg, auditQ, auditAct, auditActs, auditFiltered, topo, closeModal, onOverlayClick, pushFatal, initDashboard, API_BASE, createState, normStatus, STATUS_MAP, loadOverview, apiLoadSessions, loadMetrics, loadSessionDetail, loadConfig, loadEval, loadCluster, loadMemory, memoryAdd, memoryRemove, loadCost, loadLogs, loadK8sOps, loadEcosystem, loadAudit, loadMulti, loadHealth, loadModelInfo, loadRuntimeConfig, createSession, sendMessage, pauseSession, resumeSession, apiCancelSession, profileCreate, profilePatch, profileRemove, putConfig, runEval } from '../store.js'

// biome-ignore-all lint/style/noNonNullAssertion: generated
defineOptions({ name: 'EvalView' })
</script>
<template>
<!-- 视图四：评估工作台 -->
      <section v-show="view === 'eval'" class="view" :class="{ active: view === 'eval' }" id="view-eval" role="tabpanel">
        <div class="card-head" style="margin-bottom:4px">
          <h2 class="viewtitle" style="margin:0">评估工作台</h2>
          <div class="row-actions"><span class="tag">POST /v1/eval/run</span>
            <button class="btn btn-primary btn-sm" id="btn-eval-run" @click="runEval().then(() => toast('评估已启动 · POST /v1/eval/run')).catch((e) => toast('评估启动失败：' + e.message))">▶ 运行评估</button></div>
        </div>
        <div class="grid g-main">
          <div class="card" data-od-id="eval-reports-card">
            <div class="card-head"><h3>评估报告</h3><span class="tag">GET /v1/eval/reports</span></div>
            <div id="eval-reports">
              <div v-for="r in m.evals.reports" :key="r.id" class="eval-row">
                <span class="lbl">{{ r.id }}</span>
                <span>{{ r.dataset }} · {{ r.profile }}</span>
                <span class="scorebar" role="img" :aria-label="'通过率 ' + pct(r.score) + '%'"><i :style="{ width: pct(r.score) + '%' }"></i></span>
                <span class="num">{{ pct(r.score) }}%</span>
                <span class="muted">{{ r.pass }}/{{ r.total }} · {{ r.at }}</span>
              </div>
            </div>
          </div>
          <div class="card" data-od-id="eval-compare-card">
            <div class="card-head"><h3>多 Profile 对比</h3><span class="tag">POST /v1/eval/compare</span></div>
            <div id="eval-compare">
              <template v-for="(rows, ds) in evalByDataset" :key="ds">
                <div class="eval-ds-name">{{ ds }}</div>
                <div v-for="r in rows" :key="r.id" class="eval-compare-row">
                  <span class="lbl">{{ r.profile }}</span>
                  <span class="scorebar" role="img" :aria-label="r.profile + ' 通过率 ' + pct(r.score) + '%'"><i :style="{ width: pct(r.score) + '%' }"></i></span>
                  <span class="num" style="font-weight:600">{{ pct(r.score) }}%</span>
                </div>
              </template>
            </div>
          </div>
          <div class="card" data-od-id="eval-datasets-card">
            <div class="card-head"><h3>数据集</h3><span class="tag">GET /v1/eval/datasets</span></div>
            <div id="eval-datasets">
              <div v-for="d in m.evals.datasets" :key="d.name" class="eval-ds-row">
                <span class="lbl">{{ d.name }}</span>
                <span style="color:var(--muted)">{{ d.desc }}</span>
                <span class="num">{{ d.cases }} 用例</span><span class="muted">{{ d.updated }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 视图五：分布式集群 -->
</template>
